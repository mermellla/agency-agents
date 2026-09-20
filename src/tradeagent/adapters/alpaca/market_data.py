"""Alpaca market data adapters (§5.1, §5.5, §5.6, ADR-0015). Tiered: SIP_DELAYED refuses any `end` inside the 15-minute
embargo; IEX_REALTIME has no embargo but is a single venue and is never a volume source. The SIP_REALTIME adapter
shares the SIP implementation with the embargo removed and is only constructed when MARKET_DATA_PLAN=algo_trader_plus."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tradeagent.adapters.alpaca.client import MARKET_DATA_URL, AlpacaClient
from tradeagent.domain.enums import FeedTier, SourceHealth, TrustGrade
from tradeagent.domain.models import Bar, Quote, SourceStamp, Trade

DOMAIN = "bars_quotes"
EMBARGO = timedelta(minutes=15)
SYMBOLS_PER_REQUEST = 200  # keeps the URL well under limits; ~2,000 symbols → 10 requests per bar pull
MAX_ROWS = 10_000  # verified 2026-09-13: `limit` ≤ 10,000 per response


class EmbargoViolation(ValueError):
    """`end` inside the last 15 minutes on the delayed SIP tier (§5.1): the request would be refused by Alpaca on the
    basic plan and, worse, would silently look ahead if it ever succeeded."""


class RequestBudget:
    """Sliding one-minute window of request timestamps so the scanner can assert it stays under 200/min (§5.1)."""

    def __init__(self, limit_per_min: int = 200, clock: Callable[[], float] = time.monotonic):
        self.limit = limit_per_min
        self.clock = clock
        self._stamps: deque[float] = deque()

    def record(self) -> None:
        now = self.clock()
        self._stamps.append(now)
        while self._stamps and now - self._stamps[0] > 60:
            self._stamps.popleft()

    @property
    def used_last_minute(self) -> int:
        now = self.clock()
        return sum(1 for s in self._stamps if now - s <= 60)

    @property
    def over_limit(self) -> bool:
        return self.used_last_minute > self.limit


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _timeframe_delta(tf: str) -> timedelta:
    return {"1Min": timedelta(minutes=1), "15Min": timedelta(minutes=15), "1Day": timedelta(days=1)}[tf]


class AlpacaBars:
    """Shared implementation for the SIP (delayed / real-time) and IEX bar endpoints."""

    grade = TrustGrade.EXECUTION
    domain = DOMAIN

    def __init__(
        self,
        client: AlpacaClient,
        tier: FeedTier,
        budget: RequestBudget | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.tier = tier
        self.feed = "iex" if tier == FeedTier.IEX_REALTIME else "sip"
        self.budget = budget or RequestBudget()
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.source = f"alpaca-{self.feed}"
        self.last_health: SourceHealth = SourceHealth.OK

    @property
    def embargo(self) -> timedelta:
        return EMBARGO if self.tier == FeedTier.SIP_DELAYED else timedelta(0)

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        self.budget.record()
        return self.client.get(path, params, base=MARKET_DATA_URL)

    def bars(self, symbols: Sequence[str], timeframe: str, start: datetime, end: datetime) -> list[Bar]:
        now = self.clock()
        if end > now - self.embargo:
            raise EmbargoViolation(
                f"end {end.isoformat()} is inside the {self.embargo} embargo (now {now.isoformat()})"
            )
        out: list[Bar] = []
        stamp = SourceStamp(domain=DOMAIN, source=self.source, grade=self.grade, observed_at=now)
        width = _timeframe_delta(timeframe)
        for i in range(0, len(symbols), SYMBOLS_PER_REQUEST):
            chunk = symbols[i : i + SYMBOLS_PER_REQUEST]
            token: str | None = None
            while True:
                params: dict[str, Any] = {
                    "symbols": ",".join(chunk),
                    "timeframe": timeframe,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "limit": MAX_ROWS,
                    "adjustment": "split",
                    "feed": self.feed,
                    "sort": "asc",
                }
                if token:
                    params["page_token"] = token
                data = self._get("/v2/stocks/bars", params)
                fetched_at = self.clock()
                for sym, rows in (data.get("bars") or {}).items():
                    for r in rows:
                        t0 = _ts(r["t"])
                        out.append(
                            Bar(
                                symbol=sym,
                                timeframe=timeframe,
                                start=t0,
                                end=t0 + width,
                                open=Decimal(str(r["o"])),
                                high=Decimal(str(r["h"])),
                                low=Decimal(str(r["l"])),
                                close=Decimal(str(r["c"])),
                                volume=int(r["v"]),
                                feed=self.tier,
                                retrievable_at=max(fetched_at, t0 + width + self.embargo),
                                stamp=stamp,
                            )
                        )
                token = data.get("next_page_token")
                if not token:
                    break
        return out

    def latest_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        now = self.clock()
        stamp = SourceStamp(domain=DOMAIN, source=self.source, grade=self.grade, observed_at=now)
        out: dict[str, Quote] = {}
        for i in range(0, len(symbols), SYMBOLS_PER_REQUEST):
            chunk = symbols[i : i + SYMBOLS_PER_REQUEST]
            data = self._get("/v2/stocks/quotes/latest", {"symbols": ",".join(chunk), "feed": self.feed})
            for sym, q in (data.get("quotes") or {}).items():
                if not q or float(q.get("bp", 0)) <= 0 or float(q.get("ap", 0)) <= 0:
                    continue
                out[sym] = Quote(
                    symbol=sym,
                    at=_ts(q["t"]),
                    bid=Decimal(str(q["bp"])),
                    ask=Decimal(str(q["ap"])),
                    bid_size=int(q.get("bs", 0)),
                    ask_size=int(q.get("as", 0)),
                    feed=self.tier,
                    stamp=stamp,
                )
        return out

    def latest_trades(self, symbols: Sequence[str]) -> dict[str, Trade]:
        now = self.clock()
        stamp = SourceStamp(domain=DOMAIN, source=self.source, grade=self.grade, observed_at=now)
        out: dict[str, Trade] = {}
        for i in range(0, len(symbols), SYMBOLS_PER_REQUEST):
            chunk = symbols[i : i + SYMBOLS_PER_REQUEST]
            data = self._get("/v2/stocks/trades/latest", {"symbols": ",".join(chunk), "feed": self.feed})
            for sym, t in (data.get("trades") or {}).items():
                if not t:
                    continue
                out[sym] = Trade(
                    symbol=sym,
                    at=_ts(t["t"]),
                    price=Decimal(str(t["p"])),
                    size=int(t.get("s", 0)),
                    conditions=tuple(t.get("c") or ()),
                    feed=self.tier,
                    stamp=stamp,
                )
        return out

    def quotes(self, symbol: str, start: datetime, end: datetime) -> list[Quote]:
        """Historical consolidated quotes in [start, end], ascending (ADR-0017 usable-quote search)."""
        now = self.clock()
        if end > now - self.embargo:
            raise EmbargoViolation(f"quotes end {end.isoformat()} is inside the {self.embargo} embargo")
        stamp = SourceStamp(domain=DOMAIN, source=self.source, grade=self.grade, observed_at=now)
        out: list[Quote] = []
        token: str | None = None
        while True:
            params: dict[str, Any] = {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": MAX_ROWS,
                "feed": self.feed,
                "sort": "asc",
            }
            if token:
                params["page_token"] = token
            data = self._get(f"/v2/stocks/{symbol}/quotes", params)
            for q in data.get("quotes") or []:
                if float(q.get("bp", 0)) <= 0 or float(q.get("ap", 0)) <= 0:
                    continue
                out.append(
                    Quote(
                        symbol=symbol,
                        at=_ts(q["t"]),
                        bid=Decimal(str(q["bp"])),
                        ask=Decimal(str(q["ap"])),
                        bid_size=int(q.get("bs", 0)),
                        ask_size=int(q.get("as", 0)),
                        feed=self.tier,
                        stamp=stamp,
                    )
                )
            token = data.get("next_page_token")
            if not token or len(out) >= MAX_ROWS * 3:
                break
        return out

    def trades(self, symbol: str, start: datetime, end: datetime) -> list[Trade]:
        """Historical consolidated trades in [start, end], ascending."""
        now = self.clock()
        if end > now - self.embargo:
            raise EmbargoViolation(f"trades end {end.isoformat()} is inside the {self.embargo} embargo")
        stamp = SourceStamp(domain=DOMAIN, source=self.source, grade=self.grade, observed_at=now)
        out: list[Trade] = []
        token: str | None = None
        while True:
            params: dict[str, Any] = {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": MAX_ROWS,
                "feed": self.feed,
                "sort": "asc",
            }
            if token:
                params["page_token"] = token
            data = self._get(f"/v2/stocks/{symbol}/trades", params)
            for t in data.get("trades") or []:
                out.append(
                    Trade(
                        symbol=symbol,
                        at=_ts(t["t"]),
                        price=Decimal(str(t["p"])),
                        size=int(t.get("s", 0)),
                        conditions=tuple(t.get("c") or ()),
                        feed=self.tier,
                        stamp=stamp,
                    )
                )
            token = data.get("next_page_token")
            if not token or len(out) >= MAX_ROWS * 3:
                break
        return out

    def health(self) -> SourceStamp:
        now = self.clock()
        try:
            self.latest_trades(["SPY"])
            self.last_health = SourceHealth.OK
        except Exception:
            self.last_health = SourceHealth.DOWN
        return SourceStamp(
            domain=DOMAIN, source=self.source, grade=self.grade, observed_at=now, health=self.last_health
        )


def make_market_data(
    client: AlpacaClient, plan: str, budget: RequestBudget | None = None, clock: Callable[[], datetime] | None = None
) -> tuple[AlpacaBars, AlpacaBars]:
    """(authoritative scanner feed, real-time reference feed) for the configured plan (ADR-0015)."""
    budget = budget or RequestBudget()
    if plan == "algo_trader_plus":
        rt = AlpacaBars(client, FeedTier.SIP_REALTIME, budget, clock)
        return rt, rt
    return AlpacaBars(client, FeedTier.SIP_DELAYED, budget, clock), AlpacaBars(
        client, FeedTier.IEX_REALTIME, budget, clock
    )
