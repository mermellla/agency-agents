# ADR-0017 — Tick-fetch strategy for forecast resolution and the "usable quote" definition

**Appendix C item:** 17 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§7.4 resolves forecasts on 1-minute SIP bars and fetches SIP trades only for a minute that touched both levels, keeping
the 200 req/min limit intact. §12 fills at the first eligible consolidated ask/bid when a *usable* quote exists, otherwise
trade ± half-spread.

## Decision
**Resolution (`ForecastResolver`):**
1. Fetch 1-minute SIP bars for `[fill_at, time_stop_at_entry]` via the multi-symbol bars endpoint (10,000 rows per
   request; a 10-trading-day hold is ~3,900 bars → one request). Bars are examined in order; the first bar with
   `high ≥ target` or `low ≤ invalidation` decides, unless it touches **both**.
2. For a both-touch minute, fetch SIP trades for exactly that minute
   (`/v2/stocks/{symbol}/trades?start=<minute>&end=<minute+1min>&feed=sip&limit=10000`), at most 3 pages.
   Only trades whose conditions update the consolidated high/low are considered (the same exclusion list Alpaca uses
   for bar aggregation, cached from the market-data FAQ); trades are ordered by timestamp then by tape sequence. The
   first trade at or beyond either level resolves; if the first qualifying trade sits at both levels (only possible when
   target = invalidation, which the schema forbids) or the trade set is empty/truncated, the outcome is `AMBIGUOUS`
   with `ambiguous_reason`.
3. Rate budget: the resolution job runs after the close, spends at most 50 tick requests per run and defers the rest,
   and never runs during the scan cadence. Every resolution records `bars_examined` and `ticks_fetched`.

**Usable quote (fill model, `FillReconstructor`):** the first SIP quote with `timestamp ≥ order_eligible_at` and within
60 s of it such that: two-sided (`bid > 0`, `ask > 0`), not crossed (`ask ≥ bid`), `bid_size ≥ 1` and `ask_size ≥ 1`
(sizes are in round lots on the SIP feed), and spread ≤ max(1.0% of mid, 5 × tick). BUY fills at its ask, SELL at its
bid, no half-spread added (§12, D-54).
**Fallback:** the first SIP trade with `timestamp ≥ order_eligible_at`, plus (BUY) or minus (SELL) the **median
half-spread of usable quotes in `[order_eligible_at − 5 min, order_eligible_at + 5 min]`**. If no usable quote exists in
that window either, the order stays `FILL_PENDING_RECONSTRUCTION` and expires unfilled at the order's `expires_at`
(rule 11). `SIM_ADDITIONAL_SLIPPAGE_BPS` is applied afterwards and stored in its own column.

## Alternatives considered
- Resolve both-touch bars by bar open/close heuristics: exactly the "whichever condition the code checks first" the
  spec forbids.
- Fetch ticks for every touching minute: ~100× the request budget for no gain.

## Consequences
- `forecast_resolutions` is append-only; an `AMBIGUOUS` row is excluded from calibration and counted in the report.
- The fill model's parameters (60 s window, 1% spread bound, 5-minute half-spread window) live in config and are part of
  `config_version`.
