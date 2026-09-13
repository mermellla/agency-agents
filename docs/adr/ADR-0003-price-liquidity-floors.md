# ADR-0003 — Price and liquidity floors

**Appendix C item:** 3 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§4.2 requires a minimum share price and a minimum average daily dollar volume, both versioned and enforced by the risk
desk, with a data-quality argument: on the free plan the real-time execution reference is IEX only (~2% of consolidated
volume) and the execution-time check requires an IEX trade no older than `MAX_IEX_TRADE_AGE_MIN` = 5 (§5.5).

## Decision
| Key | Value | Where |
|---|---|---|
| `MIN_PRICE` | **$5.00** (last SIP daily close) | `config/risk_policy.yaml: universe.min_price_usd` |
| `MIN_AVG_DOLLAR_VOLUME` | **$10,000,000** (mean of daily close × volume over the last 20 sessions, SIP daily bars) | `universe.min_avg_dollar_volume_usd` |
| `MIN_HISTORY_DAYS` | **60 sessions** of daily bars (enough for SMA50 and ATR14 with warm-up) | `universe.min_history_days` |
| Fractionable required | **yes** (Alpaca `fractionable=true`) | `universe.require_fractionable` |

Enforced at universe construction (§6.1 step 3) and re-checked by the risk desk on every entry (§8.1 "instrument
eligibility"). A held name that falls below a floor is not force-sold; it is flagged and no ADD is allowed.

## Data-quality argument
- If IEX carries ~2% of consolidated volume, a $10M/day name prints roughly $200k/day on IEX, i.e. hundreds of prints
  across the session, so a fresh (≤5 min) IEX reference trade is normally available and the §5.5 staleness check passes.
  At $1M/day, IEX would see ~$20k/day (tens of prints), the staleness check would reject most entries, and the IEX
  quote used for extended-hours exits (§4.3) would be unreliable. The floor therefore removes names the desk could not
  trade anyway, before they consume triage budget.
- Below $5 the minimum tick is ≥ 20 bps, relative spreads are wide, reverse splits and delisting risk are concentrated,
  and stop execution quality on gaps is worst. A $5 floor also keeps every 10–30% position ($50–$150) representable in
  whole shares where fractional trading is unavailable for a name.
- With $500 of capital, market impact is irrelevant; the floors are about the *quality of the data the desk must trust*,
  not capacity.
- Universe size estimate at these floors: roughly 1,800–2,200 U.S. common stocks, which fits the 15-minute cadence
  under the 200 req/min limit (one multi-symbol 15-minute-bar request covers ~2,000 symbols; the daily history refresh
  is ~12 requests).

## Alternatives considered
- $1 / $1M: too many names with unusable IEX data and sub-penny economics.
- $10 / $50M: ~800 names; fewer intraday setups and a less interesting experiment.

## Consequences
- Changing either floor bumps `config_version` and opens a phase (§13.2).
- The `candidate_outcomes` job records forward returns for every candidate, so the cost of the floors is measurable if
  a later phase relaxes them.
