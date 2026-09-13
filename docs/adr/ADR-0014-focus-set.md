# ADR-0014 — Focus-set selection and refresh cadence under the 30-symbol cap

**Appendix C item:** 14 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§5.6: the free plan caps WebSocket subscriptions at 30 symbols (verified 2026-09-13: "Basic: 30 symbols"; the
`v2/delayed_sip` stream exists and shares the cap). The early-warning stream watches a focus set: positions first, then
candidates by composite score.

## Decision
Selection (deterministic, `FocusSetManager.select`):
1. All open positions in the primary portfolio (≤ `MAX_POSITIONS` = 5).
2. Symbols with a decision in flight (triage/decision/critique/validation) or an order in
   `SUBMITTED`/`PARTIALLY_FILLED`/`FILL_PENDING_RECONSTRUCTION`.
3. Remaining slots: candidates from the latest scan by composite score, with **hysteresis** — a symbol admitted in the
   previous refresh keeps its slot for two further scans unless its score drops below `triage_score_threshold`, so the
   set does not churn on rank noise.
Cap = `IEX_STREAM_MAX_SYMBOLS` (30 on basic; unlimited on algo_trader_plus). Symbols beyond the cap are REST-polled with
the snapshot endpoint every 5 minutes (§15 "the remainder are polled").

Refresh cadence: on every scan completion (15 min), on every fill or position change (event-driven), and on stream
reconnect. Subscription changes are sent as deltas (`unsubscribe` then `subscribe`).

Disconnect fallback: on socket loss the manager switches to REST IEX snapshots — positions every 60 s, candidates every
5 min — logs `source_status(domain=bars_quotes, source=iex_stream, health=degraded)` with the gap length, and triggered
reviews continue on snapshots (§15). Reconnect uses exponential backoff capped at 60 s.

Shadow portfolios never add symbols to the focus set; their fills are reconstructed from delayed SIP (§8.9).

## Alternatives considered
- Positions only: wastes 25 slots and blinds the drift fields for candidates (D-48/D-50).
- Pure top-N by score every scan: churn burns subscribe/unsubscribe messages and loses continuity of "fresh high/low".

## Consequences
- The focus-set cap test (§17) is: 5 positions + 25 candidates, a 6th position evicts the lowest candidate, never a position.
- Flipping `MARKET_DATA_PLAN` only changes `max_symbols` and the stream tier (ADR-0015).
