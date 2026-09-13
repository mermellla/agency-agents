# ADR-0008 — Composite-score formula (scanner_version 1.0.0)

**Appendix C item:** 8 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§6.4 needs a ranked candidate list with a composite score and per-signal values, computed deterministically on delayed
SIP bars, with thresholds that tie LLM calls to events rather than the clock. Weights live in `config/scanner.yaml`.

## Decision
For each eligible symbol on each scan:
1. Compute the §6.2 signals: `momentum_5d/10d/20d` (close/close−n − 1), `relative_strength_vs_spy_20d`,
   `volatility_expansion` (ATR14 today / ATR14 20 sessions ago), `breakout_daily` (close vs 20-day high, in ATRs),
   `gap_vs_prior_close` (from the 9:30–9:45 bar, available ≥ 10:00 ET), `intraday_setup` strength for
   `ORB_IEX_ASSISTED`, `ORB_SIP_CONFIRMED`, `GAP_CONTINUATION` (ADR-0011), `catalyst` (earnings surprise, 8-K in
   the last 24h, qualifying headline within `max_news_age_hours`), `rsi_14`, `sector_strength` (median 20d return of
   the symbol's SIC 2-digit group).
2. Convert each continuous signal to a **cross-sectional z-score** over the eligible universe for that scan, clipped
   to ±3, mapped to [0, 1] by (z + 3) / 6. Boolean signals map to {0, 1}.
3. `composite = Σ w_i · s_i / Σ w_i` over the signals **available** on this scan (weights renormalised when a signal is
   unavailable, e.g. intraday signals before 10:00 ET or §6.3 signals on the basic plan), minus penalties
   (RSI14 > 80: −0.10; earnings within 2 sessions and no catalyst: −0.05), clamped to [0, 1].
4. Rank descending; ties by 20-session dollar volume. Keep `SCAN_TOP_N = 25`.
5. **Triage threshold:** a candidate enters LLM triage only if `composite ≥ triage_score_threshold (0.60)` and it was
   not triaged in the last `triage_min_interval_min (30)` unless its score rose by ≥ 0.10 or a catalyst appeared.
6. Strategy tags are assigned by rule (`momentum`, `breakout`, `gap_continuation`, `orb_iex_assisted`,
   `orb_sip_confirmed`, `catalyst`) and are what the decision records as `strategy`.

Weights v1.0.0: momentum_20d 0.20, momentum_5d 0.10, RS-vs-SPY 0.15, volatility expansion 0.10, daily breakout 0.15,
gap 0.10, intraday setup 0.10, catalyst 0.10.

## Alternatives considered
- Raw-percentile ranks instead of z-scores: robust, but loses magnitude information used by the penalties.
- ML-fitted weights: violates the "deterministic, no online learning" posture (D-08).

## Consequences
- `scanner_version` bumps on any change to weights, thresholds, or signal code (ADR-0019); each is a phase.
- `candidate_outcomes` forward returns give a scanner-quality score independent of the LLM (§13.3).
