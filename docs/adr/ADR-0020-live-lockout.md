# ADR-0020 — LIVE lockout: no code path capable of live execution in this phase

**Appendix C item:** — (required by the owner's Phase 0 instruction and §16) · **Status:** Accepted · **Date:** 2026-09-13

## Context
Spec v2.3 architects LIVE (mode flag, separate key pair, `LIVE_ENABLED`, `LIVE_APPROVAL_UNTIL`, day-one approvals) but
does not enable it. The owner's instruction for this phase: "No code path should be capable of live execution."

## Decision
LIVE stays in the type system and the schema (so the design is complete) and is made unreachable at five independent layers:
1. **Config:** `load_settings` raises `LiveLockedOut` for `EXECUTION_MODE=LIVE` or `LIVE_ENABLED=true`, from YAML or env.
2. **Broker factory:** the only implementations are `NullBroker` (DRY_RUN; observe-only reads with paper keys for the
   pre-open probe) and `AlpacaPaperBroker` (base URL `https://paper-api.alpaca.markets` hard-coded). No class, string,
   or env-var read for the live base URL or `ALPACA_LIVE_KEY/SECRET` exists in the codebase; a test greps for them.
3. **Database:** `experiments.live_locked_out` CHECK refuses `execution_mode = 'LIVE'`; the order state machine refuses the
   LIVE-only `PENDING_APPROVAL` state (`LIVE_LOCKED_OUT`). Both are tested.
4. **Approval endpoint (Vercel):** not deployed in this phase; when built it can only write `approvals` rows and has no
   broker credentials.
5. **Process:** enabling LIVE requires a spec amendment (v2.4), a migration that drops the CHECK, the live broker class,
   the approval flow, and a new phase — a reviewable diff, never a config flip.

## Alternatives considered
- Keep a live client behind a feature flag: a flag is one mis-set environment variable away from live orders.
- Remove LIVE from the enums entirely: would force schema churn later and hide the design from review.

## Consequences
- DRY_RUN and PAPER are fully buildable; every LIVE-specific deliverable in §17 (approval-link flow, expiry test) is
  scheduled as the last slice and gated on the owner's explicit go.
- The lockout tests are part of the default suite and would fail the moment a live code path appears.
