# Implementation sequence — vertical slices

Each slice ends with a runnable worker and its tests green; nothing later depends on an unbuilt earlier piece. LIVE is
not in any slice before S9, and S9 is gated on the owner's explicit go plus a spec amendment (ADR-0020).

| Slice | Goal (what runs end to end) | Builds | Exit criteria / tests |
|---|---|---|---|
| **S0 — this package** | Schema, models, interfaces, config, ADRs, tests | done | 64 tests green; migrations apply clean |
| **S1 — Boot and ledger spine** | `tradeagent boot-check` connects to Supabase, applies migrations, registers versions, opens/continues a phase, writes an `initial_equity` cash event for each portfolio, runs the calendar-driven scheduler with no jobs; DRY_RUN with `NullBroker` | `versioning/`, `persistence/`, `ops/boot.py`, `ops/scheduler.py`, `adapters/alpaca/calendar.py`, `cli.py`, `NullBroker`, broker factory with LIVE refusal | T-20, T-21, T-35, T-54, T-55 (exposure and bucket checks); boot on a fresh Supabase project as `trading_worker` |
| **S2 — Universe and scanner (no LLM, no orders)** | Every 15 min in session: universe → floors → exclusions → regime → signals → composite → `scans`/`candidates` rows with correct `signal_observed_at`; `source_status` stamped; pre-open pass | `adapters/alpaca/market_data.py` (SIP_DELAYED + IEX_REALTIME), `adapters/edgar/{tickers,submissions}.py`, `adapters/finnhub`, `data/registry.py`, `universe/`, `scanner/` | T-06 (universe half), T-08, T-09, T-11, T-19, T-24; one week of DRY_RUN scans reviewed by the owner |
| **S3 — Broker gateway, policy, reconciliation, stops (PAPER-capable, still no LLM)** | `AlpacaPaperBroker`; boot applies `BROKER_POLICY` and reconciles; pre-open stop re-arm + post-open verification against real paper positions created by a test order; **fractional Day-stop probe (ADR-0013)** | `execution/broker_policy.py`, `execution/reconcile.py`, `execution/stops.py`, `adapters/alpaca/{broker_paper,activities,corporate_actions}.py` | T-04, T-10, T-13, T-16, T-22, edge cases T-40..44; probe result recorded in `08-api-verification-log.md`; OI-01 resolved |
| **S4 — Risk desk, budget, fill model, QB-1.0, benchmarks, fees, digest** | Quant baseline trades in DRY_RUN with reconstructed fills; cash cap and limits enforced through the atomic reservation path (ADR-0021); budget ledger live (charged by a mock LLM cost); fees/dividends applied; daily digest emails; halts and guards wired | `risk/`, `execution/reconstruction.py`, `execution/executor.py`, `portfolios/{quant_baseline,benchmarks}.py`, `analytics/fees.py`, `adapters/email/`, `ops/{halts,notifications,digest}.py` | T-01, T-02, T-03, T-05, T-07, T-12, T-14b, T-15, T-17, T-32, T-33, T-36, T-38; §17 virtual-cash cap test on the paper account |
| **S5 — LLM pipeline (entries)** | Triage → decision → critique → finalize → risk desk → execute, with prompts v1.0.0, dossiers, structured outputs, cost accounting, context blobs; intraday setups tagged | `agent/`, `adapters/alpaca/news.py`, `adapters/edgar/{filings,xbrl}.py`, `analytics/context_prune`, prompts | T-23, T-25, T-26, T-30, T-34; first DRY_RUN week with real LLM spend under the cap |
| **S6 — Reviews, exits, stream, shadows** | Daily and triggered reviews; discretionary exits with cancel-and-replace; extended-hours exits; IEX focus-set stream with fallback; critique shadows (paired + parallel) and deterministic-exit twins | `agent/reviews.py`, `execution/ext_hours.py`, `adapters/alpaca/stream.py`, `portfolios/{critique_shadow,deterministic_exit_shadow}.py`, focus-set manager | T-18, T-27, T-28, T-31, T-39, remaining edge cases |
| **S7 — Post-hoc analytics and the pre-registered report** | Forecast resolution (bars → ticks → AMBIGUOUS), candidate outcomes, closed trades with MFE/MAE/benchmarks/costs, §13.3 report on demand with block bootstrap, quintile calibration, ADR-0016 estimator | `analytics/` | T-14, T-29, T-53 |
| **S8 — PAPER launch package** | DRY_RUN → PAPER switch; launch instructions; example decision, executed paper trade, rejected trade; paper-vs-shadow fill deltas flowing; owner has reviewed the denylist (OI-07) and fee rate (OI-06) | docs, runbooks | §17 non-test deliverables; two weeks of PAPER without halts caused by bugs |
| **S9 — LIVE (gated, not scheduled)** | Approval endpoint, live broker class, day-one approvals, LIVE boot assertions | requires spec v2.4 + migration dropping `live_locked_out` + owner go | T-37 |

## Order rationale
- S1–S4 give a complete **quant-only** experiment (QB-1.0 vs benchmarks) with every engineering control in place before
  a single LLM token is spent; this is where the cap, reconciliation, timestamps and fill model are proven (§16 DRY_RUN
  purpose).
- The fractional-stop probe sits in S3 because its outcome (OI-01) could change sizing rules in S4.
- Shadows (S6) come after the primary pipeline (S5) because they replay primary proposals; the deterministic exit
  logic they need already exists from QB-1.0 (S4).
- Analytics (S7) is last among the experiment features but is exercised throughout by DRY_RUN data, so the 13.3 report
  is available before PAPER begins.

## Tests that can be written before their slice (from `06-test-plan.md`)
All T-xx marked "yes" under Pre-impl are writable now against Protocol mocks and synthetic data; the highest-value ones to
write next, in order: T-14 (forecast resolution), T-14b (fill model), T-01/T-02/T-03 (risk desk), T-10 (reconciliation),
T-16 (stops), T-09 (composite score), T-29 (report on a synthetic cohort).
