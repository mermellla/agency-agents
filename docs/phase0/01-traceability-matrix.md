# Requirements-to-implementation traceability matrix

Legend — **Impl**: where the requirement lives (or will live). **Status**: `P0` designed and present in this package
(schema/config/models/tests); `S<n>` scheduled in implementation slice n (`07-implementation-sequence.md`); `OI-nn` open
issue. **Tests**: IDs from `06-test-plan.md` (T-xx) or existing test functions.

## §2 Non-negotiable engineering controls
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-2.1 | Notional capped at virtual cash, broker buying power ignored | `RiskDesk.validate` step "virtual-cash"; `cash_ledger.balance_after_usd >= 0` CHECK + chain trigger | 0002 | test_cash_ledger_never_negative_and_chains; T-01 | P0 schema, S4 desk |
| R-2.2 | No margin, no shorting; broker config enforced and verified on boot | `BrokerPolicyEnforcer`; `BrokerPolicy` model literal-typed to 1x/no-short/level 0; `broker_policy_checks` | — | test_broker_policy_match; T-13 | P0 model, S3 |
| R-2.3 | Unique `client_order_id`, DB-enforced | `orders.client_order_id UNIQUE`; `client_order_id()` deterministic | 0002 | test_client_order_id_unique, test_client_order_id_deterministic_and_bounded | P0 |
| R-2.4 | No malformed orders | `OrderRequest` validators; `orders` CHECKs; `orders_check_decision` | 0002 | test_order_request_rules, test_entry_extended_hours_rejected | P0 |
| R-2.5 | Single U.S. common stocks only; exclusion list enforced | ADR-0005 membership test; `ExclusionScreen` at scanner and desk | 0005, 0006 | tests/test_exclusions.py; T-06 | P0 screen, S2 universe |
| R-2.6 | No credential exposure | Secrets only in Railway env; no secrets in repo; `.gitignore` | 0001 | test_no_live_or_secret_strings | P0 |
| R-2.7 | No deposits/withdrawals | Trading API has no funding endpoints; `Broker` protocol exposes none | — | T-20 | P0 |
| R-2.8 | No LIVE execution while PAPER; separate keys and mode flag | `ExecutionConfig` lockout; `AlpacaPaperBroker` only | 0020 | test_live_refused_by_env, test_live_experiment_rejected, test_pending_approval_unreachable | P0 |
| R-2.9 | Boot reconciliation gates activity; unexplained state halts | `Reconciler`; `reconciliations`, `halts`, `owner_resolutions` | 0002 | T-10 | S3 |
| R-2.10 | No trading on stale data | `RiskDesk` staleness step; `staleness.*` config | — | T-15 | S4 |
| R-2.11 | No executed order differs from the validated decision | `orders_check_decision` trigger (symbol, portfolio, side) | 0002 | test_sell_entry_order_forbidden; T-04 | P0 |
| R-2.12 | No simulated fill before executable (anti-look-ahead) | `fills_check_eligibility`; `decisions_eligible_after_signal`; `Timeline` validator | 0002 | test_simulated_fill_before_eligibility_rejected, test_timeline_anti_look_ahead | P0 |
| R-2.13 | No self-modification | Prompts/config are repo files; the worker has no write path to them; versions on every decision | 0019 | T-21 | P0 design |
| R-2.14 | Runaway guards | `guards.*` config; `halts` table; `MAX_ORDERS_PER_DAY`, `MAX_LLM_CALLS_PER_HOUR`, `HALT_AFTER_CONSECUTIVE_REJECTS` | — | T-17 | S4 |

## §3 Account and capital model
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-3.1a | Risk desk treats the account as cash-only; SHORT/COVER logged `REJECTED_ACCOUNT_INELIGIBLE` | `decisions_short_cover_rejected` CHECK; `Decision` validator | — | test_short_must_be_rejected_ineligible | P0 |
| R-3.1b | 1x/long-only written to the broker and verified every boot | R-2.2 | — | T-13 | S3 |
| R-3.2 | Virtual $500 ledger of record; Alpaca truth for holdings | `experiments.equity_start_usd`, `cash_ledger`, `positions`; reconciliation | 0002 | T-01, T-10 | P0/S3 |
| R-3.3 | `settles_on` recorded (T+1 from calendar); does not gate eligibility | `cash_ledger.settles_on`; `TradingCalendar.settlement_date` | — | T-22 | P0 schema, S3 |
| R-3.4 | Overfill guard for whole-share market orders | `capital.overfill_buffer_pct`; desk sizing | — | T-02 | S4 |
| R-3.5a | Holding period declared per trade within bounds | `Proposal.expected_holding_period`; desk horizon check | 0011 | T-03 | S4 |
| R-3.5b | Latency floor 30 min | `horizon.latency_floor_min`; `REJECTED_LATENCY_FLOOR` | 0011 | T-03 | S4 |
| R-3.5c | Intraday setups in scope with same-day time stop | ADR-0011 definitions | 0011 | T-30 | S5 |
| R-3.5d | Capital recycles immediately on close | proceeds credited at fill; no settlement gate | — | T-01 | S4 |

## §4 Universe and eligibility
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-4.1 | Single stocks only; cash is the only risk-off move (stated in prompt) | ADR-0005; prompt v1 constraint block | 0005 | T-06, T-23 | S2/S5 |
| R-4.2 | Price and liquidity floors, versioned, desk-enforced | `universe.min_price_usd`, `min_avg_dollar_volume_usd` | 0003 | test_spec_defaults; T-06 | P0 config, S2 |
| R-4.3a | Entries regular session only | `orders` CHECK purpose=entry → no extended hours; `OrderRequest` | — | test_entry_extended_hours_rejected | P0 |
| R-4.3b | Extended-hours exits: Day limit, verified trigger, fresh IEX anchor, bounded cross, wait if no quote | `extended_hours.*` config; ext-hours exit job; sessions exclude overnight (OI-02) | 0013 | test_ext_hours_exit_must_be_day_limit; T-31 | P0 schema, S6 |
| R-4.4.1 | Versioned denylist file with required fields; version on every decision | `config/exclusions.yaml`; `decisions.exclusion_list_version` NOT NULL | 0006 | test_exclusions_schema, test_versions_non_null | P0 (owner review pending) |
| R-4.4.2 | SIC backstop verified against EDGAR | `config/sic_backstop.yaml` | 0006 | test_sic_backstop_only_uses_real_edgar_codes | P0, OI-04 |
| R-4.4.3 | Default-deny ambiguous SICs unless allowlisted | `ExclusionScreen.screen` | 0006 | test_default_deny_needs_review_unless_allowlisted | P0 |
| R-4.4.4 | Private prisons manual entries | GEO, CXW in denylist | 0006 | test_exclusions_schema | P0 |
| R-4.4.5 | Enforcement at scanner prefilter and risk desk; never the LLM | `UniverseBuilder` + `RiskDesk` both call `ExclusionScreen` | 0006 | T-06 | S2/S4 |
| R-4.4.6 | `REJECTED_ETHICAL_SCREEN`, `NEEDS_ETHICAL_REVIEW` codes | `RejectCode`, `UniverseStatus` | — | tests/test_exclusions.py | P0 |
| R-4.4.7 | `ETHICAL_HOLD` on held names caught by an update | `positions.ethical_hold`; desk forbids ADD; no re-entry | — | T-07 | P0 schema, S4 |

## §5 Data architecture
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-5.1a | Scanner runs on SIP bars with `end ≤ now − 15 min` | `MarketDataAdapter(SIP_DELAYED)` refuses later `end`; `scans.bars_end_at <= started_at` CHECK | 0015 | T-08 | P0 schema, S2 |
| R-5.1b | `signal_observed_at` = retrievable time, never bar time | `Bar.retrievable_at`, `Candidate` validator, `candidates` CHECK | — | test models; T-08 | P0 |
| R-5.1c | IEX never for volume signals | tier-aware signal library | 0015 | T-09 | S2 |
| R-5.1d | Execution prices from the broker | `fills.fill_source = broker` on PAPER | — | T-10 | S3 |
| R-5.1e | Multi-symbol bar requests; 15-min cadence fits 200/min | ADR-0003 request budget; `scanner.scan_interval_min ≥ 15` validator | 0003 | T-08 | S2 |
| R-5.2a | One interface per domain, primary + fallback, health checks, `source_status` stamped on scans/decisions | `HealthCheckable`, adapter Protocols; `source_status` table; `scans.source_status` | — | T-11 | P0 interfaces, S2 |
| R-5.2b | Domain failover; degrade except bars/quotes which halts entries | source registry; `halts(code=BARS_QUOTES_DOWN, scope=entries)` | — | T-11 | S2 |
| R-5.3 | Two trust grades; desk accepts execution only; `sources[]` records grade | `TrustGrade`, `Evidence.grade`, `decision_sources.grade` | — | T-12 | P0 |
| R-5.4 | No paid feed; optional non-authoritative adapters inform LLM only | registry design | — | — | P0 design |
| R-5.5 | Staleness thresholds; `REJECTED_STALE_DATA` | `staleness.*`; `RejectCode.REJECTED_STALE_DATA` | — | T-15 | S4 |
| R-5.6a | Feed tiers behind one interface; SIP_REALTIME behind `MARKET_DATA_PLAN` | `FeedTier`, `MarketDataAdapter.tier`, `MarketStream.tier` | 0015 | T-19 | P0 |
| R-5.6b | Focus set: positions first, candidates by score, cap ~30; REST fallback on disconnect | `FocusSetManager`; `market_data.iex_stream_max_symbols` (30 verified) | 0014 | T-18 | P0 config, S6 |

## §6 Market scanner
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-6.0 | Deterministic, no LLM; every 15 min in session + pre-open pass | `Scanner`, scheduler; `scans.kind` | 0008 | T-08 | S2 |
| R-6.1 | Universe construction steps 1–4 | `UniverseBuilder` (ADR-0005), floors, exclusions; `universe_memberships` | 0003, 0005, 0006 | T-06 | S2 |
| R-6.2 | V1 signal set incl. ORB variants and gap continuation | signal library; `Candidate.strategy_tags` | 0008, 0011 | T-09, T-30 | S2/S5 |
| R-6.3 | Dropped signals recorded as `signal_unavailable_reason` | `scans.signals_unavailable`, `SignalValue.unavailable_reason` | 0015 | T-09 | P0 schema, S2 |
| R-6.4 | Ranked list, composite score, top N=25, thresholds gate triage | `candidates`; `scanner.triage_score_threshold` | 0008 | T-09 | S2 |
| R-6.5 | Regime classifier daily, recorded on decisions/trades | `regimes`; `decisions.market_regime`; `closed_trades.market_regime` | 0009 | T-24 | S2 |

## §7 The agent
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-7.1 | Two-tier pipeline; stage completion timestamps; model IDs in config; prompt caching | `LLMClient`; `Timeline`; `budget.model_*` | 0004 | T-25 | S5 |
| R-7.2 | Dossier composition (slim/full), token-capped, provenance tagged, drift fields | `DossierBuilder`; `dossier_token_cap`, `dossier_max_headlines` | 0004 | T-26 | S5 |
| R-7.3 | Separate critique call; pre/post probabilities stored; final decision with critique | `CritiqueResult`; `decisions.probability_pre/post_critique`, `critique_recommendation`, `pre_critique_proposal` | — | T-25 | P0 schema, S5 |
| R-7.4a | One forecast event; contract frozen at entry; immutable | `ForecastContract`; `decisions_immutable_columns` trigger | — | test_forecast_contract_immutable, test_working_levels_can_move_but_resolution_uses_entry_levels | P0 |
| R-7.4b | Tape-based post-hoc resolution: 1-min bars → ticks → AMBIGUOUS; calibration on entries | `ForecastResolver`; `forecast_resolutions` + contract trigger | 0017 | T-14 | P0 schema, S7 |
| R-7.5 | Daily review on triage model; triggered reviews on decision model with critique; budgeted separately | `DecisionKind.DAILY_REVIEW/TRIGGERED_REVIEW`; `llm_calls.bucket` | — | T-27 | S6 |
| R-7.6 | Full decision schema | `decisions` columns; `Decision`/`Proposal` models; `REJECTED_INVALID_OUTPUT` | — | test_no_action_requires_reason, test_earnings_plan_required_in_window, tests/test_models.py | P0 |
| R-7.7 | Strict JSON; stable prefix cached; constraints stated; prompt versioned by owner, never edited by agent | structured outputs; `prompt_versions`; `prompts/` (S5) | 0004, 0019 | test_prompt_bump_opens_new_phase_and_decisions_carry_it; T-23 | P0 schema, S5 |

## §8 Risk and execution desk
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-8.1 | Validation order; first failure rejects with code; desk never edits | `RiskDesk.validate`; `RejectCode` | — | T-01…T-05, T-15 | S4 |
| R-8.2 | Position limits 40 / 10–30 / 5; no trade uses the entire balance | `position_limits.*` with validators | — | test_spec_defaults; T-02 | P0 config, S4 |
| R-8.3a | `client_order_id` from `decision_id`; resubmission no-op | `client_order_id()`; `Broker.submit` idempotent | — | test_client_order_id_deterministic_and_bounded; T-04 | P0/S3 |
| R-8.3b | Stops at the broker; fractional Day stops re-armed pre-open; post-open verification; software stop on missing | `StopArmer`; `stop_coverage` | 0013 | T-16 | P0 schema, S3 |
| R-8.3c | Whole-share GTC stops or brackets | `orders.order_class`, `time_in_force=gtc` | 0013 | T-16 | S3 |
| R-8.3d | `overnight_gap_exposure` on every closed trade | `closed_trades.overnight_gap_exposure` NOT NULL | — | schema | P0 |
| R-8.3e | Trailing/partial/stop-raise by cancel-and-replace; siblings cancelled first | `orders.replaces_order_id`; `OrderPurpose.CANCEL_REPLACE` | — | T-28 | S6 |
| R-8.4 | State machine with timestamped reasons; `FILL_PENDING_RECONSTRUCTION` | `orders_validate_transition` → `order_events` | 0002 | test_illegal_transition_rejected, test_legal_transitions_logged, test_transition_requires_reason | P0 |
| R-8.5 | Budget exhaustion blocks entries; exits may exceed (logged) | `BudgetDesk`; `budget_ledger.overage_usd` | 0004 | T-05 | S4 |
| R-8.6 | Replay-or-halt reconciliation on every boot | `Reconciler`; `reconciliations`, `owner_resolutions` | 0002 | T-10 | S3 |
| R-8.7 | Halt classes; guards; every halt emails | `halts`, `Notifier` | 0010 | T-17 | S4 |
| R-8.8 | Restart safety: all state in DB, idempotent submission, boot reconciliation | schema + `Broker.submit` semantics | 0002 | T-10 | S3 |
| R-8.9a | DB-enforced `fill_at ≥ order_eligible_at` (simulated) and `order_eligible_at ≥ signal_observed_at` | triggers/CHECKs | 0002 | anti-look-ahead tests (4) | P0 |
| R-8.9b | Reconstruction after `SIM_FILL_DELAY_MIN` from first eligible SIP quote/trade | `FillReconstructor` | 0017 | T-14b | S4 |
| R-8.9c | MFE/MAE, benchmark windows, forecast resolution start at `fill_at` | `forecast_resolutions_check_contract` (start = fill_at); analytics job | 0017 | test_working_levels…, T-29 | P0/S7 |
| R-8.9d | QB baseline `order_eligible_at` = scanner completion + validation | QB engine writes `risk_validation_completed_at` | — | T-32 | S4 |
| R-8.10 | Broker policy read → write → re-read → halt; virtual cap independent | `BrokerPolicyEnforcer`; `broker_policy_checks` | — | T-13 | S3 |

## §9 Budget and cost accounting
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-9.1 | $10/month prorated daily; two buckets | `budget_ledger`; `budget.*` (proration rule OI-09) | 0004 | T-05 | P0 schema, S4 |
| R-9.2 | Every LLM call records model/tokens/cost; attributed to decision and trade | `llm_calls`; `closed_trades.llm_cost_usd` | 0004 | T-25 | P0 schema |
| R-9.3 | Cost categories per trade and period | `closed_trades.*_usd`, `cost_periods`, `fees` | 0012 | T-29 | P0 schema |
| R-9.4 | Two P&L views together | `closed_trades.net_return_pct` vs `net_return_after_computational_costs_pct`; report | 0012 | T-29 | P0 schema, S7 |
| R-9.5 | Paper simulator lacks fees/dividends → ledger applies them | fee engine on every sell fill; dividends from corporate actions | 0012 | T-33 | S4 |

## §10 Persistence
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-10.1 | Table groups | migrations 0002–0006 | 0002 | test_all_spec_tables_exist | P0 |
| R-10.2 | Invariants schema-enforced (7 bullets) | migrations 0004, 0005, 0007 | 0002 | tests/test_db_invariants.py (28 tests) | P0 |
| R-10.3 | Ledger style | append-only events + projections | 0002 | test_no_physical_deletes | P0 |
| R-10.4 | Context blob 90 days; hash and extract permanent | `data_snapshots` (hash, extract, storage_path, pruned_at); prune job | — | T-34 | P0 schema, S5 |
| R-10.5 | Repo-tracked migrations; schema change opens phase | `supabase/migrations/`; `experiment_phases.migration_version` | 0019 | test_migrations | P0 |

## §11 Infrastructure and security
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-11.1 | Separate Railway/Supabase/Vercel projects | deployment notes in `03-architecture.md` | 0001 | — | owner action |
| R-11.2 | Secrets only in Railway env | `load_settings` reads no secrets; adapters read env at construction | 0001 | T-20 | P0 |
| R-11.3 | Mode switch; LIVE prerequisites | `ExecutionConfig`; lockout | 0020 | test_live_refused_by_env | P0 |
| R-11.4 | In-process scheduler on the Alpaca calendar | `Scheduler`, `TradingCalendar` | 0001 | T-35 | S2 |
| R-11.5 | Email channel: digest, alerts, approvals | `Notifier`; `notifications` | 0010 | T-36 | S4 |
| R-11.6 | Signed one-click approval links bound to `order_id`, expiring with the order | `approvals` table (LIVE-only) | 0020 | T-37 | S9 (LIVE, gated) |

## §12 Baselines and shadows
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-12.1 | Cash, SPY, VTI benchmarks from day one | `portfolios.kind = benchmark_*`; `benchmark_prices` | — | T-38 | S4 |
| R-12.2 | QB-1.0 rules, versioned | `config/qb_rules.yaml`; QB engine | — | T-32 | P0 config, S4 |
| R-12.3 | Fill model: ask/bid, else trade ± half-spread, never double-count; slippage separate | `fills.reconstruction_basis`, `half_spread_estimate`, `additional_slippage_bps`; `SPREAD_DOUBLE_COUNT` trigger | 0017 | test_spread_double_count_rejected, test_fill_spread_rules; T-14b | P0 |
| R-12.4 | Paper-fill calibration column | `fills.paper_fill_minus_shadow_estimate_bps`; `Fill` property | — | test_fill_spread_rules | P0 |
| R-12.5 | CRITIQUE_SHADOW paired + parallel | `portfolio_kind`, `shadow_links`, `decisions.pre_critique_proposal` | 0018 | T-39 | P0 schema, S6 |
| R-12.6 | DETERMINISTIC_EXIT_SHADOW twin | `shadow_links.kind = deterministic_exit_twin` | 0018 | T-39 | P0 schema, S6 |
| R-12.7 | Only the primary touches the broker | `portfolios_one_broker_facing`; `fills_check_broker_portfolio` | 0002 | test_shadow_portfolio_cannot_receive_broker_fill, test_one_broker_facing_portfolio | P0 |

## §13 Experiment design
| ID | Requirement | Impl | ADR | Tests | Status |
|---|---|---|---|---|---|
| R-13.1 | Stopping rule ~100 closed trades; closed-trade definition | `closed_trades` one row per position lifecycle (unique position_id) | — | schema | P0 |
| R-13.2 | Phases: any change opens one with reason and versions; bug fixes too; no dwell rule | `experiment_phases`, `decisions_check_phase`, boot drift check, `phase_change.yaml` | 0019 | test_only_one_open_phase, test_version_drift_from_phase_rejected, test_prompt_bump… | P0 |
| R-13.3 | Pre-registered analysis plan (all bullets) | `AnalysisJob` → `analysis_reports`; columns listed in `05-schema.md` §"analysis inputs" | 0016 | T-29 | S7 |
| R-13.4 | Hypotheses measurable | columns: `news_cited`, `research_grade_cited`, `earnings_plan`, `holding_bucket`, shadow links, `strategy` | — | T-29 | P0 schema |
| R-13.5 | Offline owner-driven learning loop | report on demand; no online update path | 0019 | T-21 | P0 design |
| R-13.6 | Data-upgrade policy: capital AND value-of-information | ADR-0016 estimator in the report | 0016 | T-29 | S7 |

## §14 Conflict resolution rules
| Rule | Impl | Tests | Status |
|---|---|---|---|
| 1 Exits before entries | scheduler cycle order; QB `exits_before_entries` | T-28 | S4 |
| 2 Broker exits win | fills from broker stop → position closed; LLM SELL on closed position → `REJECTED_DUPLICATE` | T-28 | S4 |
| 3 Siblings cancelled before new exit | exit executor | T-28 | S6 |
| 4 invalidation > time stop > rotation | trigger priority in review scheduler | T-27 | S6 |
| 5 Fallback price tolerance | `staleness.fallback_price_tolerance_pct` | T-15 | S4 |
| 6 Replay/repair/halt | R-8.6 | T-10 | S3 |
| 7 Budget exhausted: entries blocked, exits/reviews may exceed | R-8.5 | T-05 | S4 |
| 8 `ETHICAL_HOLD` | R-4.4.7 | T-07 | S4 |
| 9 Unapproved LIVE order expires at close | `approvals.expires_at` | T-37 | S9 |
| 10 Rotation: sell first, buy sized after fill | executor sequencing | T-28 | S6 |
| 11 Simulated order expires unfilled if no eligible print | `FILL_PENDING_RECONSTRUCTION → EXPIRED` transition | T-14b | P0 state machine, S4 |
| 12 Ext-hours exit waits for premarket liquidity | R-4.3b | T-31 | S6 |

## §15 Edge cases → see `06-test-plan.md` table "Edge cases" (T-40…T-52), each mapped to a test and slice.

## §16 Modes
| Mode | Impl | Status |
|---|---|---|
| DRY_RUN | `NullBroker(observe_only=True)`; fills reconstructed; validates cap, budget, reconciliation, timestamps, focus set, pre-open job | S1–S6 |
| PAPER | `AlpacaPaperBroker`; policy applied; shadows alongside; paper vs shadow fill delta | S3+ |
| LIVE | locked out (ADR-0020) | S9, gated on owner go |

## §17 Deliverables → `06-test-plan.md` maps each deliverable to tests; non-test deliverables: architecture (`03-…`), scanner design (ADR-0008/0011), prompts (S5), decision schema (`decisions` + `Decision`), risk config (`config/risk_policy.yaml`), DB schema (`05-…`), examples and launch instructions (S8).
