# ADR-0005 — ADRs, foreign-domiciled names, ETFs, SPACs: universe membership test

**Appendix C item:** 5 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§4.1 restricts the universe to single U.S. common stocks; §6.1 removes ETFs, SPACs and (if this ADR says so) ADR/foreign
names. Verified 2026-09-13: Alpaca's `/v2/assets` object has **no field identifying ETFs, ADRs, or SPACs** (attributes
are limited to `ptp_no_exception`, `ptp_with_exception`, `ipo`, `has_options`, `options_late_close`,
`fractional_eh_enabled`, `overnight_tradable`, `overnight_halted`). A second, free, authoritative source is needed.

## Decision
**ADRs and foreign-domiciled issuers are excluded.** Membership is decided by a *domestic operating company* test built
from EDGAR (execution-grade) plus Alpaca asset flags:

1. Alpaca: `class = us_equity`, `status = active`, `tradable = true`, symbol matches `^[A-Z]{1,5}$` (drops units,
   warrants, preferreds, rights), no `ptp_no_exception` / `ptp_with_exception` attribute (partnership units are not
   common stock), not `ipo`-flagged.
2. EDGAR `company_tickers.json` maps the symbol to a CIK; a symbol with no EDGAR match is `excluded_universe:no_edgar_match`.
3. EDGAR submissions: the issuer has filed a **10-K within the last 15 months** (domestic operating companies file
   10-K; foreign private issuers file 20-F/40-F; ETFs, closed-end funds and trusts file N-CSR/N-PORT; none of those
   file 10-K). SIC must be present and not in `universe_exclude` (`6770` blank checks = SPACs, `6221`, `6189`).
4. Then the ethical layer (§4.4) and the floors (ADR-0003).

Result recorded per symbol in `instruments.universe_status` / `status_reason` and per scan in `universe_memberships`.

## Alternatives considered
- Name heuristics ("ETF", "Trust", "Fund", "Acquisition Corp") only: cheap but leaky in both directions.
- Include ADRs: they pass the liquidity floors, but foreign-domiciled issuers have different disclosure cadence
  (no 8-K stream, XBRL facts often absent), which would blank half the dossier and confound the news-context hypothesis.

## Consequences
- REITs and other 10-K-filing common stocks remain eligible; MLPs, ETFs, ADRs, SPACs, funds are out.
- EDGAR lookups are cached daily (bulk `submissions.zip` nightly, or per-CIK on demand under 10 req/s with a User-Agent).
- The rule is data-grounded and testable; a later phase can flip `universe.include_adr_foreign` (new phase).
