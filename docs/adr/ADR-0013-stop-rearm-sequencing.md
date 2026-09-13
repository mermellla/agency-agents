# ADR-0013 — Pre-open stop re-arm sequencing and "confirmed active"

**Appendix C item:** 13 · **Status:** Accepted; empirical probe against the paper API required in Slice 3 · **Date:** 2026-09-13

## Context
§8.3: fractional lots can only carry Day stop orders (verified 2026-09-13: Alpaca's fractional order table lists
market, limit, stop and stop-limit as **Yes for DAY only**, No for GTC/IOC/FOK/OPG/CLS), so a pre-open job must re-arm a
stop for every fractional lot each session and verify after the open. Alpaca's order-status semantics (verified):
`accepted` = received by Alpaca, not yet routed ("could be seen often outside trading session hours"); `new` = routed
to venue, live; Day orders submitted after the close are queued for the next session.

## Decision
Sequence each session (ET; all times from the Alpaca calendar, shifted on half-days):
1. **09:05** Apply corporate actions (splits, symbol changes) from the corporate-actions endpoint to lots (§15).
2. **09:10** Re-arm: for each open lot without a live protective order — every fractional lot, and any whole-share lot
   whose GTC stop was cancelled for an extended-hours exit (OI-05) — cancel stale remnants, then submit
   `stop` (or `stop_limit` where the working invalidation sits inside a gap, see below), `time_in_force=day`,
   `qty = lot qty_remaining`, `stop_price = working_invalidation_price`, `client_order_id = <system decision>:<seq>`.
   The system decision row has `origin = system`, `kind = system_exit`, and carries the versions in force.
3. **Confirmed active (pre-open)** := the broker returns the order with `status ∈ {accepted, new}` **and** the echoed
   `stop_price`, `qty`, `side = sell`, `symbol` equal the request. Recorded in `stop_coverage.confirmed_active_at`.
4. **09:31:00 post-open verification** (mandatory, §8.3): re-read every lot's protective order; **confirmed live** :=
   `status = new` (or `partially_filled`/`filled` if the stop already triggered). `accepted` at 09:31 → re-check at
   09:33; still not `new` → treat as **missing**.
5. **Missing** → `stop_incident`: the software becomes the stop for that lot. If the latest IEX trade (≤ 5 min old) is
   at or below the invalidation, submit a market sell now (`purpose = software_stop`); otherwise poll the IEX
   early-warning stream/snapshots and sell at the first print at or below the invalidation, while re-attempting the
   broker stop every 60 s. The incident is logged and emailed.
6. Whole-share lots: GTC stops are verified at the same checkpoints and re-armed only if absent.

Gap handling: if the prior close is already below the working invalidation (overnight gap), the re-arm submits a
**market sell at 09:30** instead of a stop below the market (which Alpaca would reject or fill immediately anyway),
and `gap_slippage_past_invalidation_pct` is recorded on the closed trade.

## Empirical probe (Slice 3, DRY_RUN with paper keys, observe-only broker)
Alpaca's older `POST /v2/orders` field text says fractional `qty` is "fractionable for only market and day order types",
which conflicts with the fractional-trading page. The probe submits one fractional Day stop on the paper account at
09:10 ET, records the status sequence, and cancels it. If pre-open fractional stops are rejected, the fallback is
submission at 09:30:00 with the naked window measured; if fractional stops are rejected outright, the risk desk
restricts entries to whole-share lots (price ≤ $150 for a $150 position) and the owner is asked to amend §8.3.

## Alternatives considered
- Re-arm by 09:35 (spec's rejected alternative, D-45).
- Software-only stops: rejected by D-03; broker stops see the consolidated tape the agent cannot.

## Consequences
- `stop_coverage` rows make the naked window and every incident measurable.
- The DRY_RUN pre-open job runs against the paper API without submitting (§16) except for the single probe order.
