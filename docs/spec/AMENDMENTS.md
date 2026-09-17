# Accepted amendments to Specification v2.3 (pending consolidation into v2.4)

The spec file itself is never edited by the coding agent. This log records amendments the owner has accepted; each
becomes text in v2.4 and, once an experiment exists, opens a new phase.

| # | Section | Amendment | Accepted | Implemented in |
|---|---|---|---|---|
| A-01 | §8.10, Appendix B | `BROKER_POLICY` is five fields, all written to the Alpaca account configuration and verified on **every** boot: `max_margin_multiplier=1`, `no_shorting=true`, `max_options_trading_level=0`, `fractional_trading=true`, `disable_overnight_trading=true`. (OI-02/OI-03) | 2026-09-13 | `config/risk_policy.yaml`, `BrokerPolicy`, `BrokerPolicyConfig` |
| A-02 | §4.3 | "Extended hours" for exits means the pre-market (04:00–09:30 ET) and after-hours (16:00–20:00 ET) sessions; Alpaca's overnight session is excluded. (OI-02) | 2026-09-13 | `extended_hours.sessions_allowed_for_exits`, validator |
| A-03 | §8.3, §15 | The pre-open stop job re-arms/repairs **any** open lot lacking its required broker-side protection (fractional or not); post-open verification covers all lots. (OI-05) | 2026-09-13 | ADR-0013 |
| A-04 | §2, §8.1, §10.2 | Validation, capital reservation and order creation are atomic under a per-portfolio lock; open BUY orders reserve their maximum notional until fill, cancel, reject or expiry; `available_virtual_cash = virtual_cash − reserved`. | 2026-09-13 | ADR-0021, migration 0009 |
| A-05 | §10, §11 | Trading state lives in a dedicated Postgres schema not exposed through the Supabase Data API; client roles hold no privileges; RLS deny-by-default; the context bucket is private; the worker uses a dedicated least-privilege role with no DELETE. | 2026-09-13 | ADR-0022, migration 0010 |
| A-06 | §9 | Regulatory fee schedules are effective-dated data (`config/fees.yaml`), not constants. FINRA TAF per SR-FINRA-2024-019 (SEC Release 34-101696): 2026 $0.000195/share max $9.79; 2027 $0.000232/$11.61; 2028 $0.000240/$12.05. SEC Section 31 $20.60 per $1M from 2026-04-04. (OI-06 resolved) | 2026-09-13 | ADR-0012, `tradeagent/fees.py` |

| A-07 | §9 | CAT fee: $0.000001 per executed-equivalent share, effective 2026-05-01 through 2026-12-31, classified `pass_through_unverified` and reported separately from customer-debited fees (and from both P&L views) until Alpaca pass-through is verified. Fee schedules support `effective_to`. | 2026-09-13 | `config/fees.yaml`, `tradeagent/fees.py`, migration 0011 |
| A-08 | §4.4 | SIC screen: 4923 and 4924 default-deny (utilities the owner wants tradable go on the allowlist); 3795 retained on the deny list although absent from EDGAR; verified code list per ADR-0006. Denylist r1: HWM, HON, POWW, HES removed; OSK added; GE, PLTR, LDOS, CACI, SAIC, BAH retained explicitly. Owner review complete (§4.4 "before PAPER"). | 2026-09-13 | `config/sic_backstop.yaml`, `config/exclusions.yaml` (both `2026.09.13-r1`) |
| A-09 | §13.2 | Approved defaults for OI-08…OI-12 recorded in `config/risk_policy.yaml` (marked "approved 2026-09-13") and therefore versioned by `config_version`. `config/phase_change.yaml` carries a `sequence` that must equal the next phase number, so a stale justification can never open a later phase (ADR-0019). | 2026-09-13 | `risk_policy.yaml`, `phase_change.yaml`, `versioning/phases.py` |
| A-10 | §6.2, §7.1, §7.2, §7.5 | Structured judgments run on TypeSafe's Jev (System One) per ADR-0023: catalyst detection in the scanner (with keyword fallback, answers stored with probabilities), triage ranking (`MODEL_TRIAGE = jev-latest`), triggered-review gating, headline relevance, analytics tagging. Generated decisions, critique and forecast probabilities stay on the decision model. Jev cost is a config key (0 during the alpha). | 2026-09-17 (owner direction) | ADR-0023, `adapters/typesafe/`, `scanner/signals/catalyst.py` |

All owner decisions from the Phase 0 review are recorded. OI-01 stays open for the Slice 3 paper probe.
