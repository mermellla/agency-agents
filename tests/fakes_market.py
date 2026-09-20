"""In-memory SIP history for fill reconstruction and benchmark tests (quotes, trades, daily bars)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradeagent.domain.enums import FeedTier, SourceHealth, TrustGrade
from tradeagent.domain.models import Bar, Quote, SourceStamp, Trade

STAMP = SourceStamp(
    domain="bars_quotes",
    source="alpaca-sip",
    grade=TrustGrade.EXECUTION,
    observed_at=datetime(2026, 9, 14, tzinfo=UTC),
    health=SourceHealth.OK,
)


def quote(symbol: str, at: datetime, bid: str, ask: str, bs: int = 2, as_: int = 2) -> Quote:
    return Quote(
        symbol=symbol,
        at=at,
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=bs,
        ask_size=as_,
        feed=FeedTier.SIP_DELAYED,
        stamp=STAMP,
    )


def trade(symbol: str, at: datetime, price: str, size: int = 100) -> Trade:
    return Trade(symbol=symbol, at=at, price=Decimal(price), size=size, feed=FeedTier.SIP_DELAYED, stamp=STAMP)


def daily_bar(symbol: str, day: datetime, close: str, embargo: timedelta = timedelta(minutes=15)) -> Bar:
    start = day.replace(hour=4, minute=0, second=0, microsecond=0, tzinfo=UTC)
    c = Decimal(close)
    return Bar(
        symbol=symbol,
        timeframe="1Day",
        start=start,
        end=start + timedelta(days=1),
        open=c,
        high=c + 1,
        low=c - 1,
        close=c,
        volume=1_000_000,
        feed=FeedTier.SIP_DELAYED,
        retrievable_at=start + timedelta(days=1) + embargo,
        stamp=STAMP,
    )


class FakeSip:
    embargo = timedelta(minutes=15)

    def __init__(
        self,
        quotes: list[Quote] | None = None,
        trades: list[Trade] | None = None,
        bars: list[Bar] | None = None,
    ):
        self._quotes, self._trades, self._bars = quotes or [], trades or [], bars or []
        self.calls: list[tuple[str, str, datetime, datetime]] = []

    def quotes(self, symbol: str, start: datetime, end: datetime) -> list[Quote]:
        self.calls.append(("quotes", symbol, start, end))
        return sorted((q for q in self._quotes if q.symbol == symbol and start <= q.at <= end), key=lambda q: q.at)

    def trades(self, symbol: str, start: datetime, end: datetime) -> list[Trade]:
        self.calls.append(("trades", symbol, start, end))
        return sorted((t for t in self._trades if t.symbol == symbol and start <= t.at <= end), key=lambda t: t.at)

    def bars(self, symbols: list[str], timeframe: str, start: datetime, end: datetime) -> list[Bar]:
        return sorted(
            (
                b
                for b in self._bars
                if b.symbol in symbols and b.timeframe == timeframe and start <= b.start and b.start < end
            ),
            key=lambda b: (b.symbol, b.start),
        )
