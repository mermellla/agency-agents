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

Pending the owner's decision: OI-04 (SIC list), OI-07 (denylist scope), OI-08–OI-12 (defaults) — see
`docs/phase0/09-owner-decisions.md`. OI-01 stays open for the Slice 3 paper probe.
