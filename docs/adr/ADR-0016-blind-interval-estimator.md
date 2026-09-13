# ADR-0016 — Estimator for the incremental gross return attributable to the blind interval

**Appendix C item:** 16 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§13.6 (D-59) upgrades market data only when (a) equity > `DATA_UPGRADE_REVIEW_EQUITY_USD` **and** (b) the estimated
incremental gross return from eliminating the blind interval exceeds the feed cost by `DATA_UPGRADE_BENEFIT_FACTOR` (2×).
§13.3 already measures `signal_to_order_move_pct`, `blind_interval_move_pct`, and forward returns of declined candidates.

## Decision
Monthly estimate `B` (USD), computed by the analysis job over the trailing 3 months of the primary portfolio:

1. **Executed entries — timing component.** For each executed entry *i*, build a counterfactual twin that enters at the
   first SIP print after `signal_bar_time + 2 min` (what a real-time feed plus the same pipeline would allow once the
   15-minute embargo is gone; the LLM latency itself stays) and exits by the same deterministic rules as the
   `DETERMINISTIC_EXIT_SHADOW` twin. `Δ_i = twin_pnl_i − deterministic_twin_pnl_i` in dollars at the actual size. Using
   the deterministic twin on both sides removes the LLM's discretionary exit from the comparison.
2. **Declined-for-drift component.** For each candidate the agent declined with `no_action_reason = chased_too_far`,
   `Δ_j = size_ref × forward_return(1d)` from `candidate_outcomes`, with `size_ref` = midpoint of
   `NORMAL_POSITION_RANGE_PCT` × equity, capped at the position limit. This assumes the agent would have entered at the
   real-time price; it is an upper bound and is reported separately.
3. `B_point = (Σ Δ_i + Σ Δ_j) / months`. Confidence interval by **block bootstrap over trading weeks** (§13.3, D-47),
   1,000 resamples.
4. **Safety factor:** the decision statistic is the **lower bound of the 80% interval**, `B_low`. Condition (b) holds iff
   `B_low ≥ DATA_UPGRADE_BENEFIT_FACTOR × 99`. Both (a) and (b) are printed in the report with the inputs.

## Alternatives considered
- Point estimate only: at n≈100 it is dominated by a few gap trades.
- Using MFE of declined candidates: rewards hindsight; forward return at a fixed horizon does not.

## Consequences
- Requires the deterministic-exit twin and `candidate_outcomes` job to be running from day one (they are in Slice 5–6).
- The estimator is versioned with the analysis code (`analysis_reports.code_version`).
