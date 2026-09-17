"""Deterministic technical signals over daily and 15-minute bars (§6.2). Pure functions; no I/O; Decimal in, float out.

Tier awareness (§6.3, ADR-0015): each signal declares the feed tier it needs via SIGNAL_TIERS; on the basic plan the
scanner reports the SIP_REALTIME-only ones as unavailable instead of computing them from IEX volume."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradeagent.domain.enums import FeedTier
from tradeagent.domain.models import Bar

SIGNAL_TIERS: dict[str, FeedTier] = {
    "momentum_5d": FeedTier.SIP_DELAYED,
    "momentum_10d": FeedTier.SIP_DELAYED,
    "momentum_20d": FeedTier.SIP_DELAYED,
    "relative_strength_vs_spy_20d": FeedTier.SIP_DELAYED,
    "gap_vs_prior_close": FeedTier.SIP_DELAYED,
    "rsi_14": FeedTier.SIP_DELAYED,
    "atr_14": FeedTier.SIP_DELAYED,
    "sma_20_over_50": FeedTier.SIP_DELAYED,
    "breakout_daily": FeedTier.SIP_DELAYED,
    "volatility_expansion": FeedTier.SIP_DELAYED,
    "intraday_relative_volume": FeedTier.SIP_REALTIME,
    "realtime_breakout": FeedTier.SIP_REALTIME,
    "premarket_mover": FeedTier.SIP_REALTIME,
}


def available_on(plan_tier: FeedTier) -> tuple[list[str], dict[str, str]]:
    """(computable signal names, {name: signal_unavailable_reason})."""
    ok, why = [], {}
    for name, need in SIGNAL_TIERS.items():
        if need == FeedTier.SIP_REALTIME and plan_tier != FeedTier.SIP_REALTIME:
            why[name] = "requires SIP_REALTIME (basic plan: last 15 minutes of consolidated data unavailable, §6.3)"
        else:
            ok.append(name)
    return ok, why


def closes(bars: Sequence[Bar]) -> list[float]:
    return [float(b.close) for b in bars]


def momentum(bars: Sequence[Bar], n: int) -> float | None:
    c = closes(bars)
    if len(c) <= n or c[-1 - n] == 0:
        return None
    return c[-1] / c[-1 - n] - 1.0


def sma(values: Sequence[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def rsi(bars: Sequence[Bar], n: int = 14) -> float | None:
    c = closes(bars)
    if len(c) <= n:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = c[i] - c[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


def true_ranges(bars: Sequence[Bar]) -> list[float]:
    out = []
    for i in range(1, len(bars)):
        h, low, pc = float(bars[i].high), float(bars[i].low), float(bars[i - 1].close)
        out.append(max(h - low, abs(h - pc), abs(low - pc)))
    return out


def atr(bars: Sequence[Bar], n: int = 14, offset: int = 0) -> float | None:
    """ATR over the n true ranges ending `offset` bars before the last bar."""
    tr = true_ranges(bars)
    end = len(tr) - offset
    if end < n or end <= 0:
        return None
    return sum(tr[end - n : end]) / n


def volatility_expansion(bars: Sequence[Bar]) -> float | None:
    now, then = atr(bars, 14, 0), atr(bars, 14, 20)
    if now is None or then is None or then == 0:
        return None
    return now / then


def breakout_daily(bars: Sequence[Bar], lookback: int = 20) -> float | None:
    """Close relative to the prior 20-session high, in ATRs (positive = above)."""
    if len(bars) <= lookback:
        return None
    prior_high = max(float(b.high) for b in bars[-1 - lookback : -1])
    a = atr(bars, 14)
    if a is None or a == 0:
        return None
    return (float(bars[-1].close) - prior_high) / a


def sma_relation(bars: Sequence[Bar]) -> float | None:
    c = closes(bars)
    s20, s50 = sma(c, 20), sma(c, 50)
    if s20 is None or s50 is None or s50 == 0:
        return None
    return s20 / s50 - 1.0


def relative_strength(bars: Sequence[Bar], spy: Sequence[Bar], n: int = 20) -> float | None:
    a, b = momentum(bars, n), momentum(spy, n)
    if a is None or b is None:
        return None
    return a - b


def gap_vs_prior_close(opening_bar: Bar | None, prior_close: Decimal | None) -> float | None:
    if opening_bar is None or prior_close is None or prior_close == 0:
        return None
    return float(opening_bar.open) / float(prior_close) - 1.0


@dataclass(frozen=True)
class IntradaySetup:
    strategy: str  # ORB_SIP_CONFIRMED | ORB_IEX_ASSISTED | GAP_CONTINUATION
    strength: float  # 0..1
    invalidation: Decimal
    target: Decimal
    detail: dict[str, float]


def opening_range(bars15: Sequence[Bar]) -> Bar | None:
    """The 9:30–9:45 ET bar of the latest session in the series."""
    if not bars15:
        return None
    last_day = bars15[-1].start.astimezone(bars15[-1].start.tzinfo).date()
    todays = [b for b in bars15 if b.start.date() == last_day]
    return todays[0] if todays else None


def orb_sip_confirmed(bars15: Sequence[Bar], daily: Sequence[Bar]) -> IntradaySetup | None:
    """ADR-0011: the 9:45–10:00 (or later) consolidated bar closes above the OR high with range ≥ 0.75 × ATR14/26."""
    orb = opening_range(bars15)
    if orb is None:
        return None
    todays = [b for b in bars15 if b.start.date() == orb.start.date()]
    if len(todays) < 2:
        return None
    confirm = todays[1]
    a = atr(daily, 14)
    if a is None:
        return None
    bar_atr = a / 26.0
    rng = float(confirm.high - confirm.low)
    if confirm.close > orb.high and rng >= 0.75 * bar_atr:
        mid = (orb.high + orb.low) / 2
        risk = confirm.close - mid
        return IntradaySetup(
            "ORB_SIP_CONFIRMED",
            min(1.0, rng / (2 * bar_atr)),
            mid,
            confirm.close + 2 * risk,
            {"or_high": float(orb.high), "range_atr": rng / bar_atr},
        )
    return None


def orb_iex_assisted(
    bars15: Sequence[Bar],
    daily: Sequence[Bar],
    iex_price: Decimal | None,
    iex_age_sec: int | None,
    iex_spread_pct: float | None,
) -> IntradaySetup | None:
    """ADR-0011: OR from delayed SIP; crossing detected on a fresh (≤60 s), tight (≤0.5%) IEX print."""
    orb = opening_range(bars15)
    if (
        orb is None
        or iex_price is None
        or iex_age_sec is None
        or iex_age_sec > 60
        or iex_spread_pct is None
        or iex_spread_pct > 0.5
    ):
        return None
    if iex_price > orb.high:
        mid = (orb.high + orb.low) / 2
        risk = iex_price - mid
        return IntradaySetup(
            "ORB_IEX_ASSISTED",
            min(1.0, float((iex_price - orb.high) / (orb.high - orb.low or Decimal(1)))),
            mid,
            iex_price + 2 * risk,
            {"or_high": float(orb.high), "iex_age_sec": float(iex_age_sec)},
        )
    return None


def gap_continuation(bars15: Sequence[Bar], prior_close: Decimal | None) -> IntradaySetup | None:
    """ADR-0011: gap ≥ 2% at the 9:30–9:45 bar; the next bar holds above the prior close and the gap bar's midpoint."""
    orb = opening_range(bars15)
    if orb is None or prior_close is None or prior_close == 0:
        return None
    gap = float(orb.open / prior_close) - 1.0
    if gap < 0.02:
        return None
    todays = [b for b in bars15 if b.start.date() == orb.start.date()]
    if len(todays) < 2:
        return None
    nxt = todays[1]
    mid = (orb.high + orb.low) / 2
    if nxt.close > prior_close and nxt.close > mid:
        gap_size = orb.open - prior_close
        return IntradaySetup("GAP_CONTINUATION", min(1.0, gap / 0.06), orb.low, nxt.close + gap_size, {"gap_pct": gap})
    return None


def zscores(values: dict[str, float | None], clip: float = 3.0) -> dict[str, float | None]:
    xs = [v for v in values.values() if v is not None and math.isfinite(v)]
    if len(xs) < 2:
        return {k: (0.0 if v is not None else None) for k, v in values.items()}
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    out: dict[str, float | None] = {}
    for k, v in values.items():
        if v is None or not math.isfinite(v):
            out[k] = None
        elif sd == 0:
            out[k] = 0.0
        else:
            out[k] = max(-clip, min(clip, (v - mean) / sd))
    return out


def session_date(dt: datetime) -> str:
    return dt.date().isoformat()
