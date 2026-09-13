# ADR-0021 — Atomic buying-power reservation and portfolio concurrency

**Appendix C item:** — (owner review of Phase 0) · **Status:** Accepted · **Date:** 2026-09-13

## Context
§2 caps notional at virtual cash and §10.2 forbids a negative ledger, but a CHECK on `cash_ledger.balance_after_usd`
only sees money once a fill is booked. Two asynchronous decisions finishing together (virtual cash $300, each validating
a $200 BUY) would both pass the risk desk, both submit, and the ledger would go negative only when the second fill
arrives. That is a software bug, not a trading risk, and must be impossible by construction.

## Decision
Validation, reservation and order creation are one atomic unit under a **per-portfolio advisory transaction lock**,
enforced in the database (`supabase/migrations/20260913000009_capital_reservation.sql`) and mirrored in the application.

- `orders.reserved_notional_usd`: every BUY order reserves its maximum possible notional at insert:
  `notional`, or `qty × limit/stop price`; a market whole-share order must carry an application-supplied reservation of
  `qty × reference price × (1 + OVERFILL_BUFFER_PCT)` (§3.4). SELL orders reserve nothing.
- `available_virtual_cash(p) = portfolio_cash_balance(p) − Σ reserved_notional_usd` over **open** BUY orders
  (`PROPOSED … FILL_PENDING_RECONSTRUCTION`). Exposed as `portfolio_available_cash(uuid)` for the risk desk and the tests.
- `orders_reserve_capital` (BEFORE INSERT) takes `pg_advisory_xact_lock(hashtext('trading.portfolio:' || portfolio_id))`,
  then checks `reserved ≥ required` (`RESERVATION_TOO_SMALL`) and `reserved ≤ available` (`INSUFFICIENT_VIRTUAL_CASH`).
  Because the lock is transaction-scoped and READ COMMITTED gives the waiting transaction a fresh snapshot, the second
  of two concurrent inserts always sees the first's committed reservation.
- Reservation lifecycle: each BUY fill shrinks the order's reservation by the fill notional (`fills_consume_reservation`,
  under the same lock) while the executor books the cash debit in the same transaction; a terminal status
  (`FILLED`, `CANCELLED`, `REJECTED`, `EXPIRED`) releases whatever remains (`orders_release_reservation`). Reservations can
  never grow after insert. Cash-ledger inserts take the same lock (`cash_ledger_a_lock`) so a balance is never read
  mid-update.
- Application side: `RiskDesk.validate` and `Ledger.record_order` run inside one transaction that calls
  `lock_portfolio(p)` first; the trigger is the backstop for any code path that forgets.
- Shadow and parallel portfolios use the same mechanism on their own ledgers (ADR-0018).

## Alternatives considered
- Application-level mutex only: lost on restart and across processes; the database is the only shared authority.
- `SELECT … FOR UPDATE` on a portfolio row: works, but the cash balance lives in an append-only table with no row to
  lock; an advisory lock keyed by portfolio id is simpler and covers the ledger insert too.
- Serializable isolation: correct but retries on conflict would re-run LLM-free validation only if carefully scoped;
  the advisory lock makes ordering explicit.

## Consequences
- Proven by `tests/test_reservation.py`: two threads on two connections racing $300 BUYs against $500 leave exactly one
  committed, the other rejected with `INSUFFICIENT_VIRTUAL_CASH`, and reserved/available cash equal $300/$200 afterwards;
  release on cancel/reject/expiry; conversion to cost basis on fill; sells reserve nothing.
- The §17 virtual-cash cap test gains a concurrency case (T-01c in the test plan).
- Open BUY orders reduce buying power for later decisions — the dossier's "virtual cash available" must show
  `portfolio_available_cash`, not the ledger balance.
