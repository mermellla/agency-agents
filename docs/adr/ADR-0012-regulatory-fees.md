# ADR-0012 — Regulatory fee schedule and its source

**Appendix C item:** 12 · **Status:** Accepted, FINRA TAF rate unresolved (OI-06) · **Date:** 2026-09-13

## Context
§9 records `regulatory_fees` (SEC transaction fee and FINRA TAF on sells) and applies them in every mode because
Alpaca's paper simulator does not model them (verified 2026-09-13: paper trading "does not simulate … Regulatory fees,
Dividends, Price slippage due to latency").

## Decision (`config/fees.yaml`, part of `config_version`)
| Fee | Rate used | Applies to | Source and status |
|---|---|---|---|
| SEC Section 31 | **$20.60 per $1,000,000** of covered sales, charge dates on/after **2026-04-04** (it was $0.00 from 2025-10-01 to 2026-04-03) | sells, rounded to the cent | SEC Fee Rate Advisory FY2026; FINRA Information Notice 2026-03-17 — **verified** |
| FINRA TAF (covered equities) | **$0.000195 per share, max $9.79 per trade** (reported effective 2026-01-01) | sells, rounded to the cent | FINRA By-Laws Schedule A §1 as rendered on 2026-09-13 still shows $0.000166 / $8.30; secondary sources (broker fee pages, SR-FINRA-2024-019 fee adjustment schedule) give $0.000195 / $9.79 — **unresolved; higher rate adopted pending the owner's confirmation** |
| CAT fee | placeholder 0 | both | Alpaca lists a `FEE/CAT` activity sub-type; rate to be sourced when the owner confirms the TAF row |
| Broker commission | $0 | — | Alpaca |

Application: the fee engine runs on every SELL fill in DRY_RUN, PAPER and (later) LIVE and writes `fees` rows with
`rate_basis` and `fee_schedule_version`. At $50–$150 lots the amounts are cents or zero (TAF on 1.2 shares rounds to
$0.00), but they are recorded so the two P&L views (§9) are on the same footing across modes. On LIVE, broker `FEE`
activities replace the estimates and the difference is logged.

## Alternatives considered
- Ignore fees at this scale: contradicts D-46.
- Fetch rates from a broker's fee page at runtime: unversioned and unverifiable.

## Consequences
- Any rate change bumps `config_version` and opens a phase (category `correctness`).
- Owner action before PAPER: confirm the TAF row against the current Schedule A and set the CAT rate (OI-06).
