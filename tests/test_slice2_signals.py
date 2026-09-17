"""T-09 signals and composite (ADR-0008), T-24 regime (ADR-0009), T-30 intraday setups (ADR-0011), tier availability (§6.3)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.synthetic import bar15, daily
from tradeagent.config import load_settings
from tradeagent.domain.enums import FeedTier, MarketRegime
from tradeagent.scanner.composite import composite_scores, strategy_tags
from tradeagent.scanner.regime import classify_regime
from tradeagent.scanner.signals.technical import (
    atr,
    available_on,
    breakout_daily,
    gap_continuation,
    momentum,
    orb_iex_assisted,
    orb_sip_confirmed,
    rsi,
    zscores,
)

CFG = load_settings(env={}).scanner
DAY = date(2026, 9, 17)


def test_momentum_rsi_atr_breakout_on_synthetic_series():
    up = daily("UP", drift=0.01, vol=0.004)
    down = daily("DN", drift=-0.01, vol=0.004)
    assert momentum(up, 20) > 0.15 and momentum(down, 20) < -0.15
    assert rsi(up) > 70 and rsi(down) < 30
    assert atr(up) is not None and atr(up) > 0
    spiked = daily("SPK", drift=0.0, vol=0.003, spike_last=0.08)
    assert breakout_daily(spiked) > 1.0  # closes well above the prior 20-day high, in ATRs
    assert breakout_daily(daily("FLAT", vol=0.003)) < 0.5


def test_available_signals_by_tier():
    ok, why = available_on(FeedTier.SIP_DELAYED)
    assert "intraday_relative_volume" in why and "realtime_breakout" in why and "premarket_mover" in why
    assert "momentum_20d" in ok
    ok_rt, why_rt = available_on(FeedTier.SIP_REALTIME)
    assert why_rt == {} and "intraday_relative_volume" in ok_rt


def test_orb_sip_confirmed_and_not():
    d = daily("ORB", vol=0.01)
    bars = [bar15("ORB", DAY, (9, 30), 100, 101, 99.5, 100.8), bar15("ORB", DAY, (9, 45), 100.8, 102.5, 100.7, 102.3)]
    s = orb_sip_confirmed(bars, d)
    assert (
        s is not None
        and s.strategy == "ORB_SIP_CONFIRMED"
        and s.invalidation == Decimal("100.25")
        and s.target > Decimal("102.3")
    )
    inside = [bar15("ORB", DAY, (9, 30), 100, 101, 99.5, 100.8), bar15("ORB", DAY, (9, 45), 100.8, 100.9, 100.2, 100.5)]
    assert orb_sip_confirmed(inside, d) is None
    assert orb_sip_confirmed(bars[:1], d) is None  # second bar not yet retrievable


def test_orb_iex_assisted_requires_fresh_tight_quote():
    d = daily("ORB", vol=0.01)
    bars = [bar15("ORB", DAY, (9, 30), 100, 101, 99.5, 100.8)]
    assert orb_iex_assisted(bars, d, Decimal("101.2"), 30, 0.2).strategy == "ORB_IEX_ASSISTED"
    assert orb_iex_assisted(bars, d, Decimal("101.2"), 90, 0.2) is None  # stale
    assert orb_iex_assisted(bars, d, Decimal("101.2"), 30, 0.9) is None  # wide
    assert orb_iex_assisted(bars, d, Decimal("100.9"), 30, 0.2) is None  # inside range


def test_gap_continuation():
    bars = [bar15("GAP", DAY, (9, 30), 104, 105, 103.5, 104.5), bar15("GAP", DAY, (9, 45), 104.5, 105.5, 104.2, 105.2)]
    s = gap_continuation(bars, Decimal("100"))
    assert s is not None and s.strategy == "GAP_CONTINUATION" and s.invalidation == Decimal("103.5")
    assert gap_continuation(bars, Decimal("103")) is None  # gap < 2%
    fade = [bars[0], bar15("GAP", DAY, (9, 45), 104.5, 104.6, 103.0, 103.2)]
    assert gap_continuation(fade, Decimal("100")) is None  # fails to hold the midpoint


def test_zscores_and_composite_renormalise_missing_signals():
    z = zscores({**{f"o{i}": 1.0 for i in range(10)}, "d": None, "e": 1000.0})
    assert z["d"] is None and z["e"] == 3.0 and z["o1"] < 0  # clipped at ±3
    per = {
        "A": {
            "momentum_20d": 0.3,
            "momentum_5d": 0.05,
            "relative_strength_vs_spy_20d": 0.2,
            "volatility_expansion": 1.5,
            "breakout_daily": 1.2,
            "gap_vs_prior_close": 0.03,
            "intraday_setup_strength": 0.8,
            "catalyst_strength": 0.9,
            "rsi_14": 65,
        },
        "B": {
            "momentum_20d": -0.1,
            "momentum_5d": -0.02,
            "relative_strength_vs_spy_20d": -0.1,
            "volatility_expansion": 0.9,
            "breakout_daily": -0.5,
            "gap_vs_prior_close": -0.01,
            "intraday_setup_strength": None,
            "catalyst_strength": None,
            "rsi_14": 40,
        },
        "C": {
            "momentum_20d": 0.1,
            "momentum_5d": 0.0,
            "relative_strength_vs_spy_20d": 0.0,
            "volatility_expansion": 1.0,
            "breakout_daily": 0.0,
            "gap_vs_prior_close": 0.0,
            "intraday_setup_strength": None,
            "catalyst_strength": None,
            "rsi_14": 85,
        },
    }
    s = composite_scores(per, CFG)
    assert s["A"] > s["C"] > s["B"] and 0 <= s["B"] and s["A"] <= 1
    hot = {**per["C"], "rsi_14": 85}
    cool = {**per["C"], "rsi_14": 50}
    assert (
        composite_scores({"A": per["A"], "B": per["B"], "C": hot}, CFG)["C"]
        < composite_scores({"A": per["A"], "B": per["B"], "C": cool}, CFG)["C"]
    )  # RSI penalty
    assert strategy_tags(
        {
            "momentum_20d": 0.2,
            "relative_strength_vs_spy_20d": 0.1,
            "breakout_daily": 0.5,
            "intraday_setup": "ORB_SIP_CONFIRMED",
            "catalyst_strength": 0.7,
        }
    ) == ["momentum", "breakout", "orb_sip_confirmed", "catalyst"]


@pytest.mark.parametrize(
    "drift,vol,breadth,dd,expected",
    [
        (0.004, 0.012, 70, 0.0, MarketRegime.TRENDING_BULL),
        (-0.004, 0.012, 30, 0.0, MarketRegime.TRENDING_BEAR),
        (0.0, 0.012, 50, 0.0, MarketRegime.RANGE_BOUND),
        (0.0, 0.03, 50, 0.0, MarketRegime.HIGH_VOLATILITY),
        (0.0, 0.0004, 50, 0.0, MarketRegime.LOW_VOLATILITY),
    ],
)
def test_regime_classifier_v1(drift, vol, breadth, dd, expected):
    spy = daily("SPY", n=90, drift=drift, vol=vol)
    regime, inputs = classify_regime(spy, breadth, CFG)
    assert regime == expected, inputs


def test_regime_risk_off_and_unknown():
    crash = daily("SPY", n=90, drift=0.0, vol=0.03, spike_last=-0.12)
    assert classify_regime(crash, 20, CFG)[0] == MarketRegime.RISK_OFF
    assert classify_regime(daily("SPY", n=30), 50, CFG)[0] == MarketRegime.UNKNOWN
    assert classify_regime(daily("SPY", n=90), None, CFG)[0] == MarketRegime.UNKNOWN
