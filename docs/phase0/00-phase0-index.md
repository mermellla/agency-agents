# Phase 0 engineering package — index

**Spec:** `docs/spec/SPEC-v2.3.md` (authoritative) · **Date:** 2026-09-13 · **Modes buildable:** DRY_RUN, PAPER · **LIVE:** locked out (ADR-0020)

| # | Deliverable (owner's Phase 0 list) | Where |
|---|---|---|
| 1 | Finalized spec under `docs/spec/` | `docs/spec/SPEC-v2.3.md`, `docs/spec/README.md` |
| 2 | Requirements-to-implementation traceability matrix | `01-traceability-matrix.md` |
| 3 | Contradictions, impossible requirements, API assumptions to verify, underspecified behaviour | `02-open-issues.md` (OI-01…OI-16) and `08-api-verification-log.md` (V-01…V-18) |
| 4 | ADRs required by Appendix C | `docs/adr/` — ADR-0001…0018 (one per item) + ADR-0019 (versioning/phases) + ADR-0020 (LIVE lockout) |
| 5 | Module/package architecture | `03-architecture.md` |
| 6 | Major domain interfaces and typed models | `src/tradeagent/domain/{enums,models}.py`, `src/tradeagent/interfaces/__init__.py`; inventory in `04-interfaces.md` |
| 7 | Supabase/Postgres schema | `05-schema.md` |
| 8 | Initial repo-tracked migrations | `supabase/migrations/2026091300000{1..8}_*.sql` (42 tables, 33 enums, 12 trigger functions; applied clean on PostgreSQL 16) |
| 9 | Test plan mapping every §17 deliverable and invariant to tests | `06-test-plan.md` |
| 10 | Tests writable before implementation | `06-test-plan.md` column "Pre-impl"; 64 already written and passing in `tests/` |
| 11 | Implementation sequence as vertical slices | `07-implementation-sequence.md` |

Also produced: versioned config seeds in `config/` (risk policy, fees, SIC backstop, seed denylist, scanner weights,
QB-1.0 rules, phase-change record) and the Python package skeleton (`pyproject.toml`, `src/tradeagent/`).

## What this package does not do
- No feature implementation: no adapters, no scanner, no LLM calls, no order submission. Interfaces are Protocols with
  no implementations; the only executable logic is config loading/validation, the exclusion screen, and model validation.
- No credentials anywhere; no network calls in code or tests.
- No LIVE path: refused by config, schema, and state machine (ADR-0020).

## Status of the verification work
18 external assumptions were checked against current documentation on 2026-09-13 (`08-api-verification-log.md`).
Three require owner decisions (OI-04 SIC codes, OI-06 FINRA TAF rate, OI-02/OI-03 extended-hours and broker-policy
amendments); two require an empirical probe against the paper API in Slice 3 (fractional pre-open stops, ADR-0013).
