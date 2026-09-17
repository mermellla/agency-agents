"""Composite score v1.0.0 (ADR-0008): cross-sectional z-scores → [0,1], weighted over available signals, penalties."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tradeagent.scanner.signals.technical import zscores


@dataclass(frozen=True)
class CompositeConfig:
    weights: dict[str, float]
    rsi_overbought_above: float
    earnings_penalty: float
    clip_z: float


def _cfg(scanner_cfg: dict[str, Any]) -> CompositeConfig:
    cs = scanner_cfg["composite_score"]
    return CompositeConfig(
        dict(cs["weights"]),
        float(cs["penalties"]["rsi_overbought_above"]),
        float(cs["penalties"]["earnings_within_2_sessions_no_catalyst"]),
        float(cs.get("clip_z", 3.0)),
    )


# raw signal name → composite weight key
FEATURE_MAP = {
    "momentum_20d": "momentum_20d",
    "momentum_5d": "momentum_5d",
    "relative_strength_vs_spy_20d": "relative_strength_vs_spy_20d",
    "volatility_expansion": "volatility_expansion",
    "breakout_daily": "breakout_daily",
    "gap_vs_prior_close": "gap_vs_prior_close",
    "intraday_setup": "intraday_setup",
    "catalyst": "catalyst",
}


def composite_scores(per_symbol: dict[str, dict[str, Any]], scanner_cfg: dict[str, Any]) -> dict[str, float]:
    """per_symbol[symbol] = {signal_name: float | None, 'rsi_14': …, 'earnings_within_2_sessions': bool, 'catalyst_strength': …}."""
    cfg = _cfg(scanner_cfg)
    symbols = list(per_symbol)
    z_by_feature: dict[str, dict[str, float | None]] = {}
    for raw, key in FEATURE_MAP.items():
        if raw in ("intraday_setup", "catalyst"):
            vals = {s: per_symbol[s].get(f"{raw}_strength") for s in symbols}
            z_by_feature[key] = {s: (None if v is None else float(v)) for s, v in vals.items()}  # already 0..1
        else:
            z = zscores({s: per_symbol[s].get(raw) for s in symbols}, cfg.clip_z)
            z_by_feature[key] = {s: (None if v is None else (v + cfg.clip_z) / (2 * cfg.clip_z)) for s, v in z.items()}
    out: dict[str, float] = {}
    for s in symbols:
        num = den = 0.0
        for key, w in cfg.weights.items():
            v = z_by_feature.get(key, {}).get(s)
            if v is None:
                continue  # unavailable → weight renormalised (ADR-0008 step 3)
            num += w * v
            den += w
        score = num / den if den > 0 else 0.0
        r = per_symbol[s].get("rsi_14")
        if r is not None and r > cfg.rsi_overbought_above:
            score -= 0.10
        if per_symbol[s].get("earnings_within_2_sessions") and not per_symbol[s].get("catalyst_strength"):
            score -= cfg.earnings_penalty
        out[s] = max(0.0, min(1.0, score))
    return out


def strategy_tags(sig: dict[str, Any]) -> list[str]:
    tags = []
    if (sig.get("momentum_20d") or 0) > 0 and (sig.get("relative_strength_vs_spy_20d") or 0) > 0:
        tags.append("momentum")
    if (sig.get("breakout_daily") or 0) > 0:
        tags.append("breakout")
    setup = sig.get("intraday_setup")
    if setup:
        tags.append(str(setup).lower())
    if (sig.get("catalyst_strength") or 0) > 0:
        tags.append("catalyst")
    return tags
