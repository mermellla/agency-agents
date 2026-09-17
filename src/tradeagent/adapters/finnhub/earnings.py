"""Finnhub earnings calendar (ADR-0007): research-grade; one date-range call per pre-open pass."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from tradeagent.domain.enums import SourceHealth, TrustGrade
from tradeagent.domain.models import SourceStamp

BASE = "https://finnhub.io/api/v1"


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    on: date
    hour: str  # bmo | amc | dmh | ""
    eps_actual: float | None
    eps_estimate: float | None

    @property
    def surprise_pct(self) -> float | None:
        if self.eps_actual is None or not self.eps_estimate:
            return None
        return (self.eps_actual - self.eps_estimate) / abs(self.eps_estimate) * 100


class FinnhubEarnings:
    domain = "earnings_calendar"
    source = "finnhub"
    grade = TrustGrade.RESEARCH

    def __init__(self, api_key: str | None, http: httpx.Client | None = None):
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=15.0)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def calendar(self, start: date, end: date) -> dict[str, list[EarningsEvent]]:
        if not self.api_key:
            raise RuntimeError("FINNHUB_API_KEY missing")
        r = self.http.get(
            f"{BASE}/calendar/earnings",
            params={"from": start.isoformat(), "to": end.isoformat(), "token": self.api_key},
        )
        r.raise_for_status()
        out: dict[str, list[EarningsEvent]] = {}
        for e in r.json().get("earningsCalendar") or []:
            ev = EarningsEvent(
                str(e["symbol"]).upper(),
                date.fromisoformat(e["date"]),
                e.get("hour") or "",
                _f(e.get("epsActual")),
                _f(e.get("epsEstimate")),
            )
            out.setdefault(ev.symbol, []).append(ev)
        return out

    def health(self) -> SourceStamp:
        now = datetime.now(tz=UTC)
        h = SourceHealth.OK if self.api_key else SourceHealth.DOWN
        return SourceStamp(domain=self.domain, source=self.source, grade=self.grade, observed_at=now, health=h)


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
