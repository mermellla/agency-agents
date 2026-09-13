# ADR-0002 — Ledger style and how §10.2 invariants are enforced

**Appendix C item:** 2 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§10.3 delegates the ledger style (append-only event log vs mutable tables with audit trail). Either must satisfy the
§10.2 invariants and allow full reconstruction of any position's history. The owner's principle is "durable schema
over process".

## Decision
**Append-only event tables per aggregate, plus rebuildable projections.**

| Truth (append-only, no UPDATE/DELETE) | Projection (mutable, rebuildable) |
|---|---|
| `cash_ledger` (every cash movement, `balance_after_usd` chained by trigger) | `budget_ledger` counters |
| `order_events` (every §8.4 transition, written by trigger from `orders.status`) | `orders.status` (current state) |
| `fills` (broker or reconstructed) | `positions`, `lots` (qty, avg cost, working levels) |
| `lot_events` (open/reduce/close/split/force-close) | — |
| `forecast_resolutions`, `llm_calls`, `fees`, `closed_trades` | — |

`decisions` and `orders` are insert-mostly: status and post-hoc analytics columns may be filled, but a trigger makes
identity, provenance, the frozen forecast contract, and every timeline timestamp write-once.

**Enforcement of §10.2, all in `supabase/migrations/`:**
- `portfolio_id`, `experiment_id`, `experiment_phase_id` NOT NULL on decisions, orders, fills, closed_trades;
  `decisions_check_phase` verifies the phase is open and belongs to the experiment.
- `orders.client_order_id UNIQUE`; `orders_check_decision` refuses an order that differs from its decision (§2).
- `forbid_delete` triggers on decisions, orders, order_events, fills, cash_ledger, lots, lot_events, llm_calls,
  closed_trades, forecast_resolutions, phases, portfolios, version registries; `forbid_update` on the pure event tables.
- `orders_validate_transition` implements the §8.4 state table and logs every transition with a reason.
- `fills_check_eligibility` rejects any reconstructed fill with `fill_at < order_eligible_at` (§8.9) and flags a
  broker fill that would violate it; `decisions_eligible_after_signal` CHECK enforces
  `order_eligible_at ≥ signal_observed_at`.
- All timestamps are `timestamptz`; the application writes UTC only and converts to ET for display.
- The five version columns are NOT NULL with foreign keys to the version registries, and must equal the open phase's versions.

A `verify_projections` job (nightly and on boot) recomputes positions/lots/cash from the event tables and halts on
mismatch (`PROJECTION_DRIFT`); reconciliation (§8.6) runs after that check.

## Alternatives considered
- **Single generic event store** (`events(aggregate, payload)`): maximal flexibility, but the invariants become
  application logic instead of constraints, which contradicts the owner's principle.
- **Mutable tables + audit log written by triggers**: simpler queries, but the audit trail is a side effect that can
  diverge from the business truth, and "no physical deletes" becomes a convention.

## Consequences
- Any position history is a deterministic fold over `fills`, `lot_events`, `cash_ledger`; the projections are caches.
- Tests can target the database directly before application code exists (tests/test_db_invariants.py).
- Writers must supply `balance_after_usd`; the chain trigger makes an arithmetic slip a hard error instead of a silent
  ledger drift, and `balance_after_usd >= 0` is the schema-level form of "no borrowing".
