# ADR-0018 — How the parallel critique portfolio handles conflicts the primary never faced

**Appendix C item:** 18 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§12 (D-56): whenever critique changes a proposal, the pre-critique proposal is executed virtually in two views — a
**paired** per-trade counterfactual (the measurement; may exceed limits, logged) and a **parallel** portfolio with its own
cash and position limits (a realistic P&L that may diverge). The parallel portfolio will hit cash and slot conflicts
the primary never saw.

## Decision
- The parallel portfolio (`portfolio_kind = critique_shadow_parallel`) has its own $500 ledger and runs the **same risk
  desk code** as the primary against its own cash and positions. When a pre-critique proposal cannot be taken — cash
  short, `MAX_POSITIONS` reached, `MAX_SINGLE_POSITION_PCT` exceeded, symbol already held — it is recorded as a
  rejected shadow decision with the ordinary `REJECTED_*` code. **No resizing, no partial fills, no borrowing**: the risk
  desk never edits a decision (§8.1), and that rule holds for shadows.
- Exits in the parallel portfolio are **deterministic** (entry stop, entry target, entry time stop at the pre-critique
  levels): shadows spend no LLM budget, so there is no discretionary management to mirror. This is stated in the report
  so the parallel P&L is read as "pre-critique entries with mechanical exits".
- When critique recommended **abandon** and the primary did not trade, the parallel portfolio takes the trade if its
  limits allow. When critique changed size or levels, the parallel portfolio uses the pre-critique size and levels.
- The **paired** view ignores limits by design: it records the pre-critique trade at its proposed size even when the
  parallel portfolio could not afford it, and `shadow_links.limit_breach` records which limit would have been breached.
- Both views use the anti-look-ahead fill model with `order_eligible_at` = the primary's `risk_validation_completed_at`
  (the pre-critique proposal was not executable earlier than the post-critique one; both wait for validation).

## Alternatives considered
- Scale the pre-critique size down to fit: turns the counterfactual into a third thing that is neither proposal.
- Let the parallel portfolio borrow: violates §2 for a ledger that shares the experiment's invariants.

## Consequences
- Shadow decisions are ordinary `decisions` rows with `portfolio_id` of the shadow and `origin = system`, linked via
  `shadow_links`, so the same tests and constraints apply.
- The §13.3 critique-value analysis uses the paired view for the pre-registered comparison and reports the parallel
  P&L as the secondary view (D-56).
