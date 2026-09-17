"""Synthetic bars for scanner tests. Deterministic, no network."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal

from tradeagent.domain.enums import FeedTier, SourceHealth, TrustGrade
from tradeagent.domain.models import Bar, SourceStamp

ET = timezone(timedelta(hours=-4))
STAMP = SourceStamp(
    domain="bars_quotes",
    source="synthetic",
    grade=TrustGrade.EXECUTION,
    observed_at=datetime(2026, 9, 17, 14, 0, tzinfo=UTC),
    health=SourceHealth.OK,
)


def daily(
    symbol: str,
    n: int = 80,
    start_price: float = 100.0,
    drift: float = 0.0,
    vol: float = 0.01,
    end: date = date(2026, 9, 16),
    spike_last: float = 0.0,
) -> list[Bar]:
    """n daily bars ending on `end`, geometric drift with a deterministic pseudo-random wiggle."""
    bars: list[Bar] = []
    price = start_price
    d = end - timedelta(days=int(n * 1.5))
    i = 0
    while len(bars) < n:
        if d.weekday() < 5:
            wiggle = math.sin(i * 1.7) * vol
            step = drift + wiggle
            if len(bars) == n - 1:
                step += spike_last
            o = price
            c = price * (1 + step)
            h = max(o, c) * (1 + vol / 2)
            low = min(o, c) * (1 - vol / 2)
            t0 = datetime.combine(d, time(4, 0), tzinfo=UTC)
            bars.append(
                Bar(
                    symbol=symbol,
                    timeframe="1Day",
                    start=t0,
                    end=t0 + timedelta(days=1),
                    open=Decimal(f"{o:.4f}"),
                    high=Decimal(f"{h:.4f}"),
                    low=Decimal(f"{low:.4f}"),
                    close=Decimal(f"{c:.4f}"),
                    volume=1_000_000,
                    feed=FeedTier.SIP_DELAYED,
                    retrievable_at=t0 + timedelta(days=1, minutes=15),
                    stamp=STAMP,
                )
            )
            price = c
            i += 1
        d += timedelta(days=1)
    return bars


def bar15(symbol: str, day: date, hhmm: tuple[int, int], o: float, h: float, low: float, c: float) -> Bar:
    t0 = datetime.combine(day, time(*hhmm), tzinfo=ET)
    return Bar(
        symbol=symbol,
        timeframe="15Min",
        start=t0,
        end=t0 + timedelta(minutes=15),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=50_000,
        feed=FeedTier.SIP_DELAYED,
        retrievable_at=t0 + timedelta(minutes=30),
        stamp=STAMP,
    )
