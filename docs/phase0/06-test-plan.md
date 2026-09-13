# Test plan and test matrix

Levels: **DB** (schema under test, no app code), **Unit** (pure Python), **Contract** (adapter against recorded or mocked
HTTP), **Integration** (worker components against the local database with mocked adapters), **Paper** (against the
Alpaca paper API with paper keys, owner-run, DRY_RUN observe-only or PAPER). **Pre-impl** = writable before feature code
exists; ✔ = already written and passing (64 tests in `tests/`).

## §17 deliverables → tests
| T | Deliverable / invariant (§17, §10.2) | Level | Pre-impl | Test(s) | Slice |
|---|---|---|---|---|---|
| T-01 | Virtual-cash cap: order above virtual cash rejected although the broker would accept it | Integration + Paper | partly ✔ (DB `balance_after_usd ≥ 0`) | test_cash_ledger_never_negative_and_chains ✔; `test_risk_desk_virtual_cash_cap` (mock broker reporting $100k BP); paper run in S3 | S4 |
| T-02 | Position limits 40 / 10–30 / 5; overfill guard | Unit | yes | `test_sizing_limits`, `test_overfill_buffer_whole_share` | S4 |
| T-03 | Horizon bounds and latency floor rejections | Unit | yes | `test_horizon_bounds`, `test_latency_floor_rejects_under_30min` | S4 |
| T-04 | Executed order never differs from validated decision; idempotent resubmission | DB + Unit | ✔ | test_sell_entry_order_forbidden ✔, test_order_eligibility_must_match_decision ✔; `test_broker_submit_idempotent` | S3 |
| T-05 | Budget ledger: exhaust entry bucket → NO_ACTION for entries, exits still run, `BUDGET_OVERAGE_EXIT` logged | Integration | yes (mock LLM cost) | `test_budget_entry_exhaustion_blocks_entries_not_exits` | S4 |
| T-06 | Exclusion layer: seed denylist + SIC backstop; excluded name rejected at scanner prefilter **and** risk desk | Unit + Integration | ✔ screen; desk pending | tests/test_exclusions.py ✔; `test_universe_builder_drops_excluded`, `test_risk_desk_rejects_excluded_even_if_proposed` | S2/S4 |
| T-07 | `ETHICAL_HOLD`: list update catches a held name → no ADD, REDUCE/SELL allowed, no re-entry | Integration | yes | `test_ethical_hold_semantics` | S4 |
| T-08 | Scanner embargo: SIP adapter refuses `end > now−15m`; `signal_observed_at` = retrievable time; 15-min cadence request budget | Unit + Contract | ✔ model; adapter pending | Bar/Candidate validators ✔; `test_sip_delayed_adapter_embargo`, `test_scan_request_budget_under_200_per_min` | S2 |
| T-09 | Signals: tier-aware availability; dropped signals recorded as `signal_unavailable_reason`; composite formula fixtures | Unit | yes (synthetic bars) | `test_composite_score_v1`, `test_signals_unavailable_on_basic` | S2 |
| T-10 | Reconciliation: replay repairs a missed fill; unknown ticker halts; excluded-name position halts; manual trade halts; duplicate broker events idempotent | Integration | yes (mock activities) | `test_reconcile_replay_missed_fill`, `test_reconcile_unknown_ticker_halts`, `test_reconcile_excluded_name_halts`, `test_reconcile_manual_trade_halts`; test_duplicate_broker_fill_rejected ✔ | S3 |
| T-11 | Source registry: failover within a domain; bars/quotes down halts entries; `source_status` stamped on every scan and decision | Integration | yes | `test_registry_failover`, `test_bars_down_halts_entries_only` | S2 |
| T-12 | Trust grades: research-grade price never used for sizing/stops/staleness | Unit | yes | `test_risk_desk_ignores_research_grade_prices` | S4 |
| T-13 | Broker policy: matching config proceeds; mismatch written and re-read; unreadable halts | Integration + Paper | yes (mock broker) | `test_broker_policy_match`, `test_broker_policy_apply_and_reread`, `test_broker_policy_unreadable_halts`; test_broker_policy_match (model) ✔ | S3 |
| T-14 | Forecast resolution on synthetic SIP data: target first; invalidation first; time stop; both-in-one-minute resolved by ticks; both with no ticks → AMBIGUOUS; immutability | Unit + DB | yes (synthetic bars/ticks) | `test_resolve_target_first`, `test_resolve_invalidation_first`, `test_resolve_time_stop`, `test_resolve_both_touch_by_ticks`, `test_resolve_both_touch_no_ticks_ambiguous`; test_working_levels_can_move… ✔, test_forecast_contract_immutable ✔ | S7 |
| T-14b | Fill model: ask/bid with no added spread; trade fallback ± half-spread; `SIM_ADDITIONAL_SLIPPAGE_BPS` separate and labeled; reconstruction picks first eligible print after eligibility; no print → expires | Unit + DB | ✔ DB rules; unit pending | test_spread_double_count_rejected ✔, test_fill_spread_rules ✔; `test_reconstruct_ask_fill`, `test_reconstruct_trade_fallback`, `test_reconstruct_slippage_separate`, `test_reconstruct_first_eligible_print`, `test_reconstruct_expires_without_print` | S4 |
| T-15 | Staleness: `REJECTED_STALE_DATA` on stale SIP bar / IEX trade; fallback price tolerance | Unit | yes | `test_staleness_rejections`, `test_fallback_tolerance` | S4 |
| T-16 | Pre-open stop re-arm with post-open verification; lot-without-stop → software stop + incident | Integration + Paper probe | yes (mock broker statuses accepted/new) | `test_rearm_all_unprotected_lots`, `test_post_open_missing_stop_triggers_software_stop`, `test_gap_below_invalidation_market_sell`; paper probe (ADR-0013) | S3 |
| T-17 | Runaway guards: max orders/day, LLM calls/hour, consecutive rejects halt; every halt emails | Integration | yes | `test_guards_halt_and_email` | S4 |
| T-18 | Early-warning stream: focus-set cap (positions before candidates, hysteresis); disconnect → REST fallback + gap logged | Unit + Integration | yes | `test_focus_set_cap_positions_first`, `test_stream_disconnect_fallback` | S6 |
| T-19 | `MARKET_DATA_PLAN` switch: mocked real-time SIP adapter changes no scanner code | Unit (import graph / file hash) | yes | `test_plan_flip_changes_no_scanner_module` | S2 |
| T-20 | No secrets, no live URL, no funding endpoint, no live key env read in the codebase | Unit (grep) | ✔ | test_no_live_or_secret_strings ✔ | P0 |
| T-21 | No self-modification: worker has no write path to prompts/config; boot halts on version drift without a reason | Integration | yes | `test_boot_halts_without_phase_reason`, `test_boot_opens_phase_with_reason` | S1 |
| T-22 | `settles_on` = T+1 from the calendar; not used for eligibility | Unit | yes | `test_settlement_date_metadata_only` | S3 |
| T-23 | Prompt v1 states every §7.7 constraint; strict JSON; invalid output → `REJECTED_INVALID_OUTPUT` | Unit | yes (golden prompt test) | `test_prompt_constraints_present`, `test_invalid_output_rejected` | S5 |
| T-24 | Regime classifier fixtures for all seven outcomes | Unit | yes | `test_regime_classifier_v1` | S2 |
| T-25 | Pipeline timestamps stamped in order; `llm_calls` rows with cost per call from `usage` | Integration (mock LLM) | yes | `test_pipeline_timeline_and_costs` | S5 |
| T-26 | Dossier: token cap, provenance tags, drift fields, slim vs full | Unit | yes | `test_dossier_composition` | S5 |
| T-27 | Reviews: daily on triage model; triggers (proximity, news, time stop, earnings, regime); trigger priority | Integration | yes | `test_daily_review_uses_triage_model`, `test_triggered_review_priority` | S6 |
| T-28 | Exits before entries; broker stop wins; siblings cancelled; rotation sells first | Integration | yes | `test_cycle_order_exits_first`, `test_broker_stop_final`, `test_sibling_cancel_before_exit`, `test_rotation_sequence` | S6 |
| T-29 | Analysis job: §13.3 report with block-bootstrap intervals, quintile calibration, per-phase counts, both P&L views, ADR-0016 estimator | Unit (synthetic cohort) | yes | `test_report_sections_present`, `test_block_bootstrap_weekly`, `test_calibration_quintiles`, `test_two_pnl_views` | S7 |
| T-30 | Intraday setups: ORB variants and gap continuation definitions on synthetic 15-min bars; same-day time stop | Unit | yes | `test_orb_sip_confirmed`, `test_orb_iex_assisted`, `test_gap_continuation` | S5 |
| T-31 | Extended-hours exit: Day limit anchored to fresh IEX quote, bounded cross, no quote → wait; overnight never | Unit + DB | ✔ DB; unit pending | test_ext_hours_exit_must_be_day_limit ✔; `test_ext_hours_anchor_and_cross`, `test_ext_hours_waits_without_quote` | S6 |
| T-32 | QB-1.0: rules, `order_eligible_at` = scan completion + validation, no drift judgment | Integration | yes | `test_qb_rules_v1`, `test_qb_eligibility_earlier_than_llm` | S4 |
| T-33 | Fees and dividends applied in every mode; two P&L views | Unit | yes | `test_fee_engine_schedule`, `test_dividend_credit` | S4 |
| T-34 | Context blob stored, hash permanent, prune after 90 days touches only the blob | Integration (mock storage) | yes | `test_context_prune` | S5 |
| T-35 | Scheduler follows the Alpaca calendar (holiday, half-day) | Unit | yes | `test_scheduler_calendar` | S1 |
| T-36 | Daily digest email content and notification audit row | Unit | yes | `test_daily_digest` | S4 |
| T-37 | Approval-link flow with expiry (LIVE only) | Integration | yes | `test_approval_link_expiry` | S9 (gated) |
| T-38 | Benchmarks: SPY/VTI bought at start close, marked daily; cash flat | Unit | yes | `test_benchmarks` | S4 |
| T-39 | Shadows: critique-changed proposal spawns paired + parallel entries; discretionary exit leaves the deterministic twin running | Integration | yes | `test_critique_shadow_spawn`, `test_det_exit_twin_independent` | S6 |
| T-40..52 | §15 edge cases: holidays/half-days; halted stock (no repricing); split/symbol change before re-arm; delisting force-close; IPO exclusion; `REJECTED_BROKER` never resized; Supabase outage full halt; Alpaca outage entries-only halt; crash mid-order; crash with pending reconstruction resumes; default-deny after the fact; $2,000 crossing keeps 1x; paper reset re-applies policy | Integration | yes | one test per case, named `test_edge_<case>` | S3–S6 |
| T-53 | Candidate outcomes: every candidate, traded or not, gets forward returns at each horizon | Integration | yes | `test_candidate_outcomes_complete` | S7 |
| T-54 | Phase test: prompt bump opens a phase; subsequent decisions carry it | DB ✔ + Integration | ✔ | test_prompt_bump_opens_new_phase_and_decisions_carry_it ✔; `test_boot_opens_phase_with_reason` | S1 |

## Already written and passing (64)
- `tests/test_migrations.py` (3): all §10.1 tables exist; RLS everywhere; Postgres enums == Python enums.
- `tests/test_db_invariants.py` (39, incl. 7 parametrized no-delete cases): every schema-enforced invariant listed in `05-schema.md`.
- `tests/test_config.py` (8): spec defaults, model IDs, LIVE refused, config hash, versions, fee shape, exclusions schema, SIC codes real.
- `tests/test_models.py` (8): proposal/timeline/order/fill/broker-policy validation.
- `tests/test_exclusions.py` (5): both enforcement paths of the screen.
- `tests/test_lockout_grep.py` (1): T-20 — no live URL, live key names, funding endpoints, or secret-looking literals in code/config.

## Running
```
pg_ctlcluster 16 main start   # or any PostgreSQL 16 with createdb rights
TRADEAGENT_TEST_ADMIN_URL=postgresql://postgres:postgres@localhost:5432/postgres python -m pytest -q
```
DB tests create a throw-away database per session, apply every migration, and run each test in a rolled-back transaction.
