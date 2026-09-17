"""Regime classifier v1.0.0 (ADR-0009)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from tradeagent.domain.enums import MarketRegime
from tradeagent.domain.models import Bar
from tradeagent.scanner.signals.technical import closes, sma


def realized_vol_annualized(bars: Sequence[Bar], n: int) -> float | None:
    c = closes(bars)
    if len(c) <= n:
        return None
    rets = [math.log(c[i] / c[i - 1]) for i in range(-n, 0)]
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(252) * 100


def classify_regime(
    spy: Sequence[Bar], breadth_pct_above_50sma: float | None, cfg: dict[str, Any]
) -> tuple[MarketRegime, dict[str, Any]]:
    r = cfg["regime"]
    c = closes(spy)
    inputs: dict[str, Any] = {"sessions": len(c)}
    if len(c) < 60 or breadth_pct_above_50sma is None:
        return MarketRegime.UNKNOWN, {**inputs, "reason": "insufficient history or breadth"}
    s20, s50 = sma(c, r["trend_fast_sma"]), sma(c, r["trend_slow_sma"])
    s20_prev = sma(c[: -r["trend_slope_lookback_days"]], r["trend_fast_sma"])
    rv = realized_vol_annualized(spy, r["realized_vol_lookback_days"])
    high20 = max(c[-20:])
    dd = (high20 - c[-1]) / high20 * 100
    inputs.update(
        sma20=s20,
        sma50=s50,
        sma20_slope=(None if s20 is None or s20_prev is None else s20 - s20_prev),
        rv20=rv,
        drawdown_20d_pct=dd,
        breadth=breadth_pct_above_50sma,
    )
    if s20 is None or s50 is None or s20_prev is None or rv is None:
        return MarketRegime.UNKNOWN, {**inputs, "reason": "sma/vol unavailable"}
    slope = s20 - s20_prev
    if dd >= r["risk_off_drawdown_from_20d_high_pct"] and rv >= r["high_vol_annualized_pct"]:
        return MarketRegime.RISK_OFF, inputs
    if rv >= r["high_vol_annualized_pct"]:
        return MarketRegime.HIGH_VOLATILITY, inputs
    if rv <= r["low_vol_annualized_pct"]:
        return MarketRegime.LOW_VOLATILITY, inputs
    if s20 > s50 and slope > 0 and breadth_pct_above_50sma >= r["breadth_bull_pct_above_50sma"]:
        return MarketRegime.TRENDING_BULL, inputs
    if s20 < s50 and slope < 0 and breadth_pct_above_50sma <= r["breadth_bear_pct_above_50sma"]:
        return MarketRegime.TRENDING_BEAR, inputs
    return MarketRegime.RANGE_BOUND, inputs
