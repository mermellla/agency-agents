# Interface and typed-model inventory

Source: `src/tradeagent/interfaces/__init__.py` (Protocols) and `src/tradeagent/domain/models.py` (models).
Every Protocol is implemented in the slice shown; tests can mock any of them today.

## Adapters (§5.2 source registry)
| Protocol | Methods | Spec | Implementations (slice) |
|---|---|---|---|
| `HealthCheckable` | `health() → SourceStamp` | §5.2 | every adapter |
| `MarketDataAdapter` | `bars`, `latest_quotes`, `latest_trades`, `quotes`, `trades`; attr `tier` | §5.1, §5.6 | `AlpacaSipDelayed` (refuses `end > now−15m`), `AlpacaIexRealtime`, `AlpacaSipRealtime` (S2; last one dormant) |
| `MarketStream` | `subscribe`, `unsubscribe`, `events()`, `close`; `max_symbols` | §5.6 | `AlpacaIexStream` (cap 30) (S6), `AlpacaSipStream` (dormant) |
| `NewsAdapter` | `headlines` | §5.2, §7.2 | `AlpacaNews` (S5) |
| `FilingsAdapter` | `company_metadata`, `recent_8k_items`, `latest_10q_10k_highlights` | §5.2, §7.2 | `EdgarFilings` (S2 metadata, S5 items) |
| `FundamentalsAdapter` | `facts` | §7.2 | `EdgarXbrl` (S5) |
| `EarningsCalendarAdapter` | `next_earnings_date`, `last_surprise` | §5.2, ADR-0007 | `FinnhubEarnings`, `Edgar8kItem202` (S2) |
| `TradingCalendar` | `sessions`, `is_session`, `add_trading_days`, `settlement_date` | §3.3, §11 | `AlpacaCalendar` (S1) |
| `CorporateActionsAdapter` | `actions` | §15 | `AlpacaCorporateActions` (S3) |

## Broker (§8, ADR-0020)
| Protocol | Methods | Implementations |
|---|---|---|
| `Broker` | `submit` (idempotent on client_order_id), `cancel`, `replace`, `order_status`, `open_orders`, `positions`, `activities_since`, `account_configuration`, `write_account_configuration`; attrs `mode`, `observe_only` | `NullBroker` (DRY_RUN, S1), `AlpacaPaperBroker` (S3). **No live implementation.** |
| `LiveLockedOutError` | raised by the broker factory for `ExecutionMode.LIVE` | S1 |

## Pipeline (§6, §7)
| Protocol | Methods | Slice |
|---|---|---|
| `RegimeClassifier` | `classify(trade_date) → (MarketRegime, inputs)` | S2 |
| `UniverseBuilder` | `build(as_of) → [Instrument]` | S2 |
| `ExclusionLayer` | `screen(symbol, sic) → (UniverseStatus, reason)`; `version` | P0 (`ExclusionScreen`) |
| `Scanner` / `EventDrivenScanner` | `scan(kind, now)`, `on_event(event)` | S2 / dormant |
| `DossierBuilder` | `slim`, `full` | S5 |
| `LLMClient` | `triage`, `decide`, `critique`, `finalize`, `daily_review` → (result, `LLMCall`) | S5 |

## Risk, budget, execution (§8, §9)
| Protocol | Methods | Slice |
|---|---|---|
| `RiskDesk` | `validate(decision, virtual_cash, open_positions) → RejectCode|None`, `construct_orders` | S4 |
| `BudgetDesk` | `state`, `charge` | S4 |
| `FillReconstructor` | `reconstruct(request, sim_fill_delay, slippage_bps) → Fill|None` | S4 |
| `StopArmer` | `rearm(session_date)`, `verify_post_open(session_date)` | S3 |
| `Reconciler` | `reconcile() → (ReconcileResult, diff)` | S3 |
| `BrokerPolicyEnforcer` | `enforce(policy, enforce_writes)` | S3 |
| `FocusSetManager` | `select(positions, candidates, cap) → [symbol]` | S6 |

## Persistence, analytics, ops
| Protocol | Methods | Slice |
|---|---|---|
| `Ledger` | `record_decision`, `update_decision_status`, `record_order`, `transition_order`, `record_fill`, `append_cash`, `virtual_cash`, `open_positions`, `freeze_forecast`, `record_forecast_resolution`, `record_closed_trade` | S1 |
| `ForecastResolver` | `resolve(decision, fill_at)` | S7 |
| `CandidateOutcomeJob` | `compute(scan_id)` | S7 |
| `AnalysisJob` | `report(experiment_id)` | S7 |
| `Notifier` | `send(kind, subject, html, text) → provider id` | S4 |
| `Scheduler` | `run_forever()` | S1 |

## Typed models (`domain/models.py`)
| Model | Frozen | Encodes |
|---|---|---|
| `SourceStamp`, `Bar`, `Quote`, `Trade` | yes | provenance + trust grade; `Bar.retrievable_at ≥ end` (§5.1); `Quote.usable`, `half_spread` (ADR-0017) |
| `Instrument`, `SignalValue`, `Candidate`, `ScanResult` | mixed | §6 outputs; `signal_observed_at ≥ signal_bar_time`; `bars_end_at ≤ started_at`; drift fields (D-50) |
| `Evidence`, `TriageItem`, `TriageResult`, `CritiqueResult`, `Proposal` | yes | §7.1–7.3 I/O; `Proposal` enforces NO_ACTION reason, earnings plan, entry completeness |
| `Timeline`, `ForecastContract`, `Decision` | mixed | §7.6 record; `order_eligible_at` property = `risk_validation_completed_at`; anti-look-ahead validators; SHORT/COVER rule |
| `OrderRequest`, `OrderState`, `Fill`, `Position`, `CashEvent`, `Fee` | mixed | §8 execution; qty xor notional; entries regular-session buys; ext-hours Day limit; fractional Day-only; spread never double-counted; `paper_fill_minus_shadow_estimate_bps` |
| `ForecastResolution`, `ClosedTrade` | yes | §7.4, §10.1 analytics rows |
| `BrokerPolicy`, `AccountConfiguration` | yes | §8.10; policy literal-typed to 1x/no-short/level 0 and cannot be loosened in code |
| `LLMCall`, `BudgetState` | yes | §9 |
| `client_order_id(decision_id, leg_seq)`, `context_hash(bytes)` | — | §8.3, §10.4 |

Enum parity between `domain/enums.py` and the Postgres enums is asserted by `tests/test_migrations.py::test_enums_match_python`.
