# ADR-0006 — SIC code list (verified against EDGAR) and the seed denylist

**Appendix C item:** 6 · **Status:** Accepted; **owner review of the seed denylist required before PAPER** · **Date:** 2026-09-13

## Context
§4.4 proposes SIC codes "to be verified against the current EDGAR SIC list before use" and asks for a seed denylist.
The list at https://www.sec.gov/info/edgar/siccodes.htm (444 codes) was downloaded and checked on 2026-09-13.

## Findings
Seven of the spec's proposed codes **do not exist** in EDGAR's list: 1241, 1321, 3483, 3489, 3795, 4612, 4613.
EDGAR uses a coarser grouping in those industries: coal is 1220/1221 only; ordnance and ammunition are all 3480;
petroleum pipelines are 4610 ("PIPE LINES (NO NATURAL GAS)"); there is no tank/tracked-vehicle code.

## Decision (`config/sic_backstop.yaml`, version `2026.09.13-seed`)
- **Deny — fossil fuels:** 1220, 1221, 1311, 1381, 1382, 1389, 2911, 4610, 4922.
- **Deny — weapons/defense:** 3480, 3760.
- **Default-deny (NEEDS_ETHICAL_REVIEW unless allowlisted):** 3720, 3721, 3724, 3728, 3812 (as in the spec), plus
  three additions justified by the scope rules: 3730 ship & boat building (naval yards), 3533 oil & gas field
  machinery (oilfield services equipment), 6792 oil royalty traders (producer economics).
- **Review before enabling (not enforced):** 4923, 4924, 5171, 5172 (as in the spec) plus 2990.
- **Universe (not ethical):** 6770, 6221, 6189 → `excluded_universe` (ADR-0005).
- Enforcement order: explicit denylist symbol → deny SIC → universe-exclude SIC → default-deny SIC (allowlist exempts).
  Implemented in `src/tradeagent/exclusions.py`; tests in `tests/test_exclusions.py`.

**Seed denylist** (`config/exclusions.yaml`, 81 entries, 20 flagged `needs_owner_review`): fossil producers, refiners,
oilfield services, pipelines and coal miners; pure-play defense primes, defense IT/services contractors, firearms and
ammunition makers, and conglomerates with large defense segments (BA, RTX, GE, HON, TXT flagged for the owner's call);
private prison operators GEO and CXW. Sources are the issuers' 10-K SIC or segment reporting.

## Alternatives considered
- Enforce the spec's codes verbatim: impossible for the seven non-existent codes; they would silently match nothing.
- Broader defense scope (any defense revenue): rejected by D-14.

## Consequences
- Proposed spec amendment OI-04 replaces the code list in §4.4 with the verified one.
- Every decision records `exclusion_list_version = "<denylist version>+sic:<backstop version>"`; the owner's review
  edit bumps the version and opens a phase.
- Corporate actions (mergers such as HES→CVX, CEIX/ARCH→CNR) are a maintenance burden; the pre-open job flags any
  denylist symbol that Alpaca no longer lists so the owner can prune it.
