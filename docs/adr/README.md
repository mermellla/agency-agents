# Architecture Decision Records

Format matches Spec v2.3 Appendix A: context, decision, alternatives considered, consequences. ADR-0001 to ADR-0018
answer Appendix C items 1–18 in order. ADR-0019 and ADR-0020 cover two delegated-by-implication items the spec relies
on but does not list (how versions and phases are mechanised; how LIVE is kept unreachable in this phase). ADR-0021 and
ADR-0022 come from the owner's Phase 0 review (capital reservation under concurrency; Supabase exposure).

| ADR | Appendix C | Title | Status |
|---|---|---|---|
| [0001](ADR-0001-language.md) | 1 | Implementation language: Python 3.11+, async worker | Accepted |
| [0002](ADR-0002-ledger-style.md) | 2 | Ledger style: append-only event tables + rebuildable projections; invariants in triggers | Accepted |
| [0003](ADR-0003-price-liquidity-floors.md) | 3 | Price and liquidity floors: $5.00, $10M 20-session dollar ADV, 60 sessions, fractionable | Accepted |
| [0004](ADR-0004-model-ids-and-budget.md) | 4 | Model IDs and pricing: Haiku 4.5 triage, Sonnet 5 decision and critique; ≈$5.7/month | Accepted |
| [0005](ADR-0005-universe-membership.md) | 5 | ADRs and foreign-domiciled names excluded; domestic-operating-company test | Accepted |
| [0006](ADR-0006-sic-codes-and-seed-denylist.md) | 6 | SIC codes verified against EDGAR; seed denylist (owner review before PAPER) | Accepted, owner review pending |
| [0007](ADR-0007-earnings-calendar-source.md) | 7 | Earnings calendar: Finnhub free tier, EDGAR 8-K Item 2.02 fallback | Accepted |
| [0008](ADR-0008-composite-score.md) | 8 | Composite-score formula v1.0.0 | Accepted |
| [0009](ADR-0009-regime-thresholds.md) | 9 | Regime classifier thresholds v1.0.0 | Accepted |
| [0010](ADR-0010-email-provider.md) | 10 | Email provider (Resend) and template set | Accepted |
| [0011](ADR-0011-intraday-setups-latency-floor.md) | 11 | Intraday setup definitions and LATENCY_FLOOR_MIN = 30 | Accepted |
| [0012](ADR-0012-regulatory-fees.md) | 12 | Regulatory fee schedule and source | Accepted, one rate unresolved |
| [0013](ADR-0013-stop-rearm-sequencing.md) | 13 | Pre-open stop re-arm sequencing and "confirmed active" | Accepted, empirical probe required |
| [0014](ADR-0014-focus-set.md) | 14 | Focus-set selection and refresh under the 30-symbol cap | Accepted |
| [0015](ADR-0015-event-driven-scanner.md) | 15 | Event-driven scanner interface and the MARKET_DATA_PLAN flip | Accepted (designed, not built) |
| [0016](ADR-0016-blind-interval-estimator.md) | 16 | Blind-interval benefit estimator and safety factor | Accepted |
| [0017](ADR-0017-tick-fetch-and-usable-quote.md) | 17 | Tick-fetch strategy and the "usable quote" definition | Accepted |
| [0018](ADR-0018-parallel-critique-portfolio.md) | 18 | Parallel critique portfolio conflict handling | Accepted |
| [0019](ADR-0019-versioning-and-phases.md) | — | Version computation and phase opening mechanics | Accepted |
| [0020](ADR-0020-live-lockout.md) | — | LIVE lockout: no code path capable of live execution | Accepted |
| [0021](ADR-0021-capital-reservation-concurrency.md) | — | Atomic buying-power reservation and portfolio concurrency | Accepted |
| [0022](ADR-0022-supabase-security.md) | — | Supabase database and Storage exposure: private schema, revoked client roles, least-privilege worker | Accepted |
