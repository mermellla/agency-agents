"""Trading calendar from Alpaca GET /v2/calendar and /v2/clock (§11 scheduling, §3.3 settlement, §3.5 trading-day maths)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from tradeagent.adapters.alpaca.client import AlpacaClient

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Session:
    day: date
    open_at: datetime  # tz-aware ET
    close_at: datetime

    @property
    def is_half_day(self) -> bool:
        return self.close_at.time() < time(16, 0)


class StaticCalendar:
    """In-memory calendar (tests, offline DRY_RUN). Sessions must be contiguous trading days in ascending order."""

    def __init__(self, sessions: list[Session]):
        self._sessions = sorted(sessions, key=lambda s: s.day)
        self._by_day = {s.day: s for s in self._sessions}

    def sessions_between(self, start: date, end: date) -> list[Session]:
        return [s for s in self._sessions if start <= s.day <= end]

    def is_session(self, d: date) -> bool:
        return d in self._by_day

    def session_on(self, d: date) -> Session | None:
        return self._by_day.get(d)

    def next_session(self, after: date) -> Session:
        for s in self._sessions:
            if s.day > after:
                return s
        raise LookupError(f"no session after {after} in the loaded calendar")

    def add_trading_days(self, from_dt: datetime, n: int) -> datetime:
        """n trading days after from_dt, at the same wall-clock time; n=0 returns from_dt."""
        d = from_dt.astimezone(ET).date()
        remaining = n
        while remaining > 0:
            d = self.next_session(d).day
            remaining -= 1
        return datetime.combine(d, from_dt.astimezone(ET).timetz())

    def settlement_date(self, trade_date: date) -> date:
        """T+1 (§3.3): the next session after the trade date."""
        return self.next_session(trade_date).day


class AlpacaCalendar(StaticCalendar):
    def __init__(self, client: AlpacaClient, start: date, end: date):
        rows = client.get("/v2/calendar", {"start": start.isoformat(), "end": end.isoformat()})
        sessions = []
        for r in rows:
            day = date.fromisoformat(r["date"])
            o = datetime.combine(day, time.fromisoformat(r["open"]), tzinfo=ET)
            c = datetime.combine(day, time.fromisoformat(r["close"]), tzinfo=ET)
            sessions.append(Session(day, o, c))
        super().__init__(sessions)


def default_window(today: date) -> tuple[date, date]:
    return today - timedelta(days=45), today + timedelta(days=60)
