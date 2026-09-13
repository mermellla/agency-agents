# ADR-0012 — Regulatory fee schedule and its source

**Appendix C item:** 12 · **Status:** Accepted; OI-06 resolved 2026-09-13 (schedules are effective-dated data) · **Date:** 2026-09-13

## Context
§9 records `regulatory_fees` (SEC transaction fee and FINRA TAF on sells) and applies them in every mode because
Alpaca's paper simulator does not model them (verified 2026-09-13: paper trading "does not simulate … Regulatory fees,
Dividends, Price slippage due to latency").

## Decision (`config/fees.yaml`, part of `config_version`; `tradeagent/fees.py` picks the row whose `effective_from ≤ charge date`, and raises rather than assuming zero when no row applies)
| Fee | Rate used | Applies to | Source and status |
|---|---|---|---|
| SEC Section 31 | **$20.60 per $1,000,000** of covered sales, charge dates on/after **2026-04-04** (it was $0.00 from 2025-10-01 to 2026-04-03) | sells, rounded to the cent | SEC Fee Rate Advisory FY2026; FINRA Information Notice 2026-03-17 — **verified** |
| FINRA TAF (covered equities) | effective-dated: 2024-01-01 $0.000166 / $8.30; **2026-01-01 $0.000195 / $9.79**; 2027-01-01 $0.000232 / $11.61; 2028-01-01 $0.000240 / $12.05 | sells, rounded to the cent | FINRA SR-FINRA-2024-019 fee-adjustment schedule; SEC Release 34-101696 (approved January 2025) — **verified**; the rendered Schedule A page lags the filing |
| CAT fee | no row yet (engine omits the line until a rate exists; never silently zero) | both | Alpaca lists a `FEE/CAT` activity sub-type; owner supplies the published rate |
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
- Owner action before PAPER: supply the CAT fee rate. The 2027 and 2028 TAF increases are already data, not code changes.
