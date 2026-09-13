# ADR-0009 — Regime classifier thresholds (scanner_version 1.0.0)

**Appendix C item:** 9 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§6.5: TRENDING_BULL, TRENDING_BEAR, RANGE_BOUND, HIGH_VOLATILITY, LOW_VOLATILITY, RISK_OFF, UNKNOWN from SPY trend,
realized volatility and breadth on daily bars, computed before selection each day and recorded on every decision.

## Decision (`config/scanner.yaml: regime`)
Inputs (SIP daily bars through the prior close): SPY SMA20, SMA50, 10-day slope of SMA20, 20-day realized volatility
(annualised, close-to-close), SPY drawdown from its 20-day high, breadth = share of the eligible universe above its
SMA50.

Evaluation order (first match wins):
1. `UNKNOWN` if any input is missing or fewer than 60 sessions of SPY history exist.
2. `RISK_OFF` if drawdown from the 20-day high ≥ 5.0% **and** RV20 ≥ 25%.
3. `HIGH_VOLATILITY` if RV20 ≥ 25%.
4. `LOW_VOLATILITY` if RV20 ≤ 10%.
5. `TRENDING_BULL` if SMA20 > SMA50, SMA20 slope > 0, breadth ≥ 60%.
6. `TRENDING_BEAR` if SMA20 < SMA50, SMA20 slope < 0, breadth ≤ 40%.
7. otherwise `RANGE_BOUND`.

A regime change versus the prior session is a triggered-review reason (§7.5). In RISK_OFF the prompt reminds the agent
that cash is the only defensive instrument (§4.1); the scanner does not stop.

## Alternatives considered
- VIX-based thresholds: VIX is not available on Alpaca's free plan; realized volatility is.
- Hidden-Markov regime model: not reproducible enough for a phase-versioned experiment.

## Consequences
- Thresholds are versioned with the scanner; a change opens a phase.
- `regimes` rows are shared across portfolios (not experiment-specific) and keyed by (trade_date, scanner_version).
