# ADR-0011 — V1 intraday setups and LATENCY_FLOOR_MIN

**Appendix C item:** 11 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§3.5 puts intraday setups in scope only when their edge survives the data lag, and asks for the setup definitions and
the default latency floor with the argument for each. §6.2 splits the opening-range breakout into two strategies (D-53).

## Decision
`LATENCY_FLOOR_MIN = 30`. Accounting: 15 min SIP embargo + ~3 min fetch/scan + ~5 min triage → decision → critique →
validation (three model calls) + ~7 min margin for LLM latency variance. The risk desk rejects any entry whose declared
`expected_holding_period` is below 30 minutes (`REJECTED_LATENCY_FLOOR`), and rejects intraday entries after 15:00 ET
because the same-day time stop (15:45 ET) would then be under `MIN_EXPECTED_HOLD` = 1 h.

Setups (all on 15-minute consolidated SIP bars; times ET; "retrievable" ≈ bar end + 15 min + fetch):
| Strategy | Definition | Retrievable | Why the edge survives the lag |
|---|---|---|---|
| `ORB_SIP_CONFIRMED` | Opening range (OR) = high/low of the 9:30–9:45 bar. Breakout confirmed when the 9:45–10:00 bar **closes** above OR-high (or below OR-low for exits) with range ≥ 0.75 × ATR14/26 and the gap-day filter below. Entry market; invalidation = OR midpoint; target ≥ 2R; time stop 15:45. | ≈ 10:15 | Opening-range continuation is a session-scale phenomenon: the measured effect is that the direction of the first-hour range tends to persist for hours, not minutes. Acting at 10:20 instead of 10:01 costs part of the move (measured as `blind_interval_move_pct`), not the thesis. |
| `ORB_IEX_ASSISTED` | OR from the delayed SIP 9:30–9:45 bar (retrievable ≈ 10:00). Crossing detected on **real-time IEX**: an IEX trade ≤ 60 s old above OR-high, IEX quote spread ≤ 0.5%, on a name above the liquidity floor. Same levels and time stop as above. | ≈ 10:00 | Same phenomenon, earlier detection on a partial venue; whether the partial view is good enough is the empirical question D-53 isolates. |
| `GAP_CONTINUATION` | Gap ≥ 2% vs prior close at the 9:30–9:45 bar; the following bar holds above the prior close **and** above the gap bar's midpoint; entry on the next retrievable bar; invalidation = gap bar low; target = gap size × 1; time stop 15:45. | ≈ 10:15 | Gap-and-go continuation is driven by the catalyst that caused the gap (earnings, news) and by intraday position building; it is a multi-hour effect. Gaps that fail do so mostly in the first 15 minutes, which the second-bar filter already observes. |

Out of scope, and rejected by the floor: scalps, momentum bursts, anything requiring the last 15 minutes (§6.3).

## Alternatives considered
- Floor of 20 min: leaves no margin for model latency; a slow critique would push a legal thesis into the reject path.
- Floor of 45 min: excludes little extra risk but removes the 10:15 opening-range entries that make intraday holds
  measurable at all.

## Consequences
- `strategy` on each decision records which variant fired, so the IEX-assisted variant's worth is a §13.3 slice.
- `holding_bucket = intraday` trades have `overnight_gap_exposure = false` by construction.
