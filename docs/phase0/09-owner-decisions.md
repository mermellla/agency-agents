# Owner decisions requested before Slice 1 — **all decided 2026-09-13** (see `docs/spec/AMENDMENTS.md` A-07…A-09)

Decisions: §A approved with amendment (4923/4924 default-deny, 3795 added, utilities via allowlist); §B approved (recommended
cleanup; GE/PLTR/defense-services retained on the explicit denylist); §C approved as recommended; §D actioned. CAT fee:
$0.000001 per executed-equivalent share, 2026-05-01…2026-12-31, classified separately until pass-through is verified.

Four items from the Phase 0 review. §A and §B are policy: the recommendations are the coding agent's reading of the
rules already in the spec (D-11, D-12, D-14), not decisions. §C is a defaults table. §D is the schema-rent audit.
Evidence: EDGAR `submissions` API lookups on 2026-09-13 (`08-api-verification-log.md` V-20).

## A. OI-04 — SIC mapping: every code proposed in §4.4, its EDGAR status, replacement, and what it actually catches

| Spec code (meaning) | In EDGAR? | Replacement in `sic_backstop.yaml` | Representative 10-K filers carrying the code (EDGAR, 2026-09-13) | Hole analysis |
|---|---|---|---|---|
| 1311 oil & gas extraction | yes | 1311 (deny) | OXY, EQT, EXE, AR, RRC, OVV, MTDR, PR, CHRD, SM, NOG, FANG, APA, DVN, EOG, PARR; mineral/royalty partnerships BSM, KRP, DMLP, MNR; VOC Energy Trust | Broadest and most reliable fossil code. No hole. |
| 1321 natural gas liquids | **no** | none needed | NGL producers file under 1311 or 4922 (e.g. TRGP) | Dropping it opens nothing: EDGAR never assigns 1321. |
| 1381 drilling | yes | 1381 (deny) | HP, RIG, VAL, PTEN | No hole. |
| 1382 exploration services | yes | 1382 (deny) | (thin: seismic/exploration services) | No hole. |
| 1389 oilfield services NEC | yes | 1389 (deny) | SLB, HAL, LBRT | Equipment makers are **not** here: BKR, NOV, FTI, WFRD carry **3533** → added as default-deny. |
| 2911 refining | yes | 2911 (deny) | XOM, CVX, COP, MPC, VLO, CVI, PBF, DK | Note the integrated majors sit here, not in 1311. HF Sinclair (DINO) carries **4610**. |
| 1220 / 1221 coal | yes / yes | 1220, 1221 (deny) | HCC, CNR, METC (1220); BTU, AMR, ARLP, NRP (1221) | No hole. (EDGAR's `sicDescription` for 1220 reads "Silver Ores" on some records; the code is coal per the SIC list.) |
| 1241 coal mining services | **no** | none needed | — | No 10-K filer can carry a code EDGAR does not have; coal service firms file under 1220/1221. No hole. |
| 4612 / 4613 crude / refined pipelines | **no** / **no** | **4610** "Pipe lines (no natural gas)" (deny) | PAA, MPLX, DINO | 4610 is the only EDGAR petroleum-pipeline code; it catches what 4612/4613 intended. No hole. |
| 4922 natural gas transmission | yes | 4922 (deny) | KMI, WMB, TRGP, ET, EPD, DTM, KNTK, WES, AM | No hole. |
| 4923 / 4924 gas transmission & distribution / distribution ("review before enabling") | yes / yes | currently **not enforced** | **OKE (4923)**, **LNG (4924, Cheniere LNG export)**; also gas utilities (out of scope) | **Hole:** two in-scope midstream/LNG names sit in the review codes and are caught only by the denylist. **Recommendation:** move 4923 and 4924 to `default_deny` (NEEDS_ETHICAL_REVIEW → skipped, not traded) and allowlist utilities you want tradable, rather than leaving the codes ignored. Fail closed. |
| 5171 / 5172 petroleum wholesale | yes / yes | review (unchanged) | fuel distributors | Not producers/refiners/pipelines/services by the spec's scope; leave for your call. |
| 3480 ordnance | yes | 3480 (deny) | AXON, SWBI, RGR, NPK | Catches firearms and TASER makers. **OLN is 2800 (chemicals)** and Outdoor Holding (ex-AMMO) is 7389 → denylist only. |
| 3483 ammunition / 3489 ordnance NEC | **no** / **no** | folded into 3480 | — | EDGAR has one ordnance code; no hole beyond OLN (denylisted). |
| 3760 guided missiles & space | yes | 3760 (deny) | LMT, KTOS, RKLB | Also catches launch/space companies (RKLB) whether or not you consider them weapons — see §B. |
| 3795 tanks | **no** | none; covered by 3730 default-deny + denylist | **GD is 3730** (ship & boat building), **OSK is 3711** (motor vehicles) | Real hole for tracked/wheeled military vehicles: only GD (3730 default-deny + denylist) is caught by SIC; **Oshkosh (JLTV, ~20% defense) is caught by nothing → added to §B for your decision.** |
| 3721 / 3724 / 3728 / 3720 aircraft (default-deny) | yes | default-deny | BA, AVAV (3721); RTX, HON (3724); TDG (3728); TXT (3720) | Working as designed: these are skipped with NEEDS_ETHICAL_REVIEW unless allowlisted. |
| 3812 search/navigation/guidance (default-deny) | yes | default-deny | **NOC, LHX** | Two pure-play primes live here; default-deny alone would only *skip* them, the denylist makes the exclusion explicit and permanent. |
| — (added) 3730 ship & boat building | yes | default-deny | GD, HII | Naval yards; civil yards need allowlisting. |
| — (added) 3533 oil & gas field machinery | yes | default-deny | BKR, NOV, FTI, WFRD | Oilfield-services equipment (spec scope includes oilfield services). |
| — (added) 6792 oil royalty traders | yes | default-deny | SJT, PBT, CRT, SBR; TPL, LB (land/royalty) | Producer economics; TPL/LandBridge are your call via allowlist. |

Caught by **no** SIC code and therefore denylist-only (the spec anticipated this: "This is the only way to catch
conglomerates"): LDOS, CACI, SAIC (7373), BAH (8742), MRCY (3670), CW (3590), BWXT (3510), HWM (3350), GE (3600),
PLTR (7372), OLN (2800), OSK (3711), GEO (1520), CXW (6798), LNG (4924), OKE (4923).

## B. OI-07 — Seed denylist entries flagged for your review (policy, not engineering)

Rule being applied (D-12/D-14): pure-play defense contractors and firearms manufacturers, plus conglomerates with large
defense segments; fossil producers/miners/refiners/pipelines/oilfield services; private prisons. "Rec." is the coding
agent's recommendation under that rule; "SIC effect" says what happens if the entry is removed.

| Symbol | Category | Why flagged | SIC effect if removed from denylist | Rec. |
|---|---|---|---|---|
| EXE | fossil | Renamed (Expand Energy, ex-CHK); verify symbol | still excluded (1311 deny) | keep |
| HES | fossil | Acquired by CVX 2025; may no longer be listed | n/a if delisted; else 1311 deny | drop if not on Alpaca |
| RIG | fossil | Offshore driller, foreign-domiciled (Swiss) | excluded anyway (1381 deny; ADR-0005 drops foreign issuers) | keep for clarity |
| CNR | fossil | Core Natural Resources (CEIX + ARCH merger) | still excluded (1220 deny) | keep |
| BA | defense | Defense, Space & Security ≈ one-third of revenue | skipped (3721 default-deny) unless allowlisted | keep — "large defense segment" |
| LDOS | defense | ~85%+ revenue from U.S. defense/intelligence customers; services, not weapons | **tradable** (7373 not screened) | keep — pure-play defense contractor by customer; boundary is yours |
| CACI | defense | same profile as LDOS | tradable | keep (same reasoning) |
| SAIC | defense | same profile | tradable | keep (same reasoning) |
| BAH | defense | ~98% U.S. government, large defense/intel share; consulting | tradable | keep (same reasoning) |
| TXT | defense | Bell + Textron Systems ≈ 30% | skipped (3720 default-deny) | keep |
| HWM | defense | Aero structures/fasteners; defense ≈ 15% | tradable (3350) | **drop** — below "large" |
| TDG | defense | ≈ 40–45% defense aftermarket/OEM components | skipped (3728 default-deny) | keep |
| GE | defense | GE Aerospace defense & propulsion ≈ 20%; military engines | tradable (3600) | your call; lean exclude (weapons-platform propulsion) |
| HON | defense | Defense & space ≈ 10% of revenue | skipped (3724 default-deny) | **drop** from denylist; do **not** allowlist (stays skipped) |
| RKLB | defense | Launch + spacecraft; growing national-security share | still excluded (3760 deny) | keep for clarity |
| PLTR | defense | > 50% revenue U.S. government incl. defense/intel; software | tradable (7372) | your call; lean keep (defense contractor by revenue) |
| AXON | weapons | TASER manufacturer (law-enforcement weapons) + cameras/software | still excluded (3480 deny) | keep; note SIC deny cannot be allowlisted |
| OLN | weapons | Winchester (incl. military ammunition) ≈ 25% of sales | tradable (2800) | keep — ammunition manufacturer |
| POWW | weapons | Now Outdoor Holding: sold its ammunition business (2025); marketplace only | tradable (7389) | **drop** after verifying the divestiture closed |
| NPK | weapons | Defense (ordnance) ≈ 70% of revenue | still excluded (3480 deny) | keep |
| OSK (new) | defense | Oshkosh Defense (JLTV, FMTV) ≈ 20% of revenue | tradable (3711) | **add for your decision** — the only vehicle maker the SIC screen cannot see |

Also for your decision (from §A): 4923/4924 → default-deny; utilities you want tradable go on the allowlist.

## C. OI-08 … OI-12 — proposed defaults

| OI | Key(s) | Proposed | Rationale | Alternatives | Effect on the experiment | Rec. |
|---|---|---|---|---|---|---|
| 08 | Benchmark anchor | Each experiment (DRY_RUN, PAPER) anchors SPY/VTI/cash to the close of its own `started_on` | Benchmarks answer "vs buy-and-hold over the same window"; DRY_RUN and PAPER are different windows and different `experiment_id`s | Inherit DRY_RUN's anchor into PAPER | Inheriting would make the PAPER benchmark start before PAPER capital was at risk, biasing the comparison in whichever direction SPY moved during DRY_RUN | own anchor |
| 09 | `LLM_ENTRY_BUCKET_PCT`, proration | 60% entries / 40% exits+reviews; daily allowance = cap ÷ trading days in the calendar month; unspent carries within the month, resets on the 1st | ADR-0004 arithmetic: 3–4 decision bundles/day fit in 60% of $0.476; carry-within-month lets a quiet week fund a busy one without ever exceeding $10 | 50/50; no carry; carry across months | Lower entry share → more `NO_ACTION` (budget) days, fewer primary trades toward the 100-trade rule; carry across months breaks the monthly cap | as proposed |
| 10 | `TRIAGE_SCORE_THRESHOLD`, `TRIAGE_MIN_INTERVAL_MIN` | 0.60; 30 min (re-triage sooner only if score +0.10 or a catalyst) | Ties triage to events not the clock (§6.4); at 0.60 roughly the top 5–10% of a scan crosses | 0.50 (more calls, more budget), 0.70 (some intraday setups never reach the LLM) | Directly sets LLM call count and therefore how many entries the cap allows; measurable from the budget ledger in week 1 of DRY_RUN | 0.60, revisit after week 1 (phase) |
| 11 | `OVERFILL_BUFFER_PCT` | 1.0% | Whole-share market orders rarely fill > 1% above reference in liquid names; reservation uses the same buffer (ADR-0021) | 0.5%, 2% | Too small → rare overfill anomaly; too large → sizing rounds down a share on cheap names | 1.0 |
| 11 | `MIN_HISTORY_DAYS` | 60 sessions | SMA50 + ATR14 warm-up | 30, 120 | Excludes recent IPOs (§15 requires this anyway) | 60 |
| 11 | `MAX_NEWS_AGE_HOURS` | 36 | Covers an overnight/after-hours release for the next session's scans | 24, 72 | Longer → stale catalysts tagged; shorter → misses pre-market news on Monday for Friday-evening filings | 36 |
| 11 | `FALLBACK_PRICE_TOLERANCE_PCT` | 1.0 | §14 rule 5: a fallback price farther than 1% from the last execution-grade price is not trusted | 0.5, 2 | Rarely exercised (fallback bars feed is optional) | 1.0 |
| 11 | `EXT_HOURS_MAX_QUOTE_AGE_SEC`, `EXT_HOURS_MAX_CROSS_PCT` | 60 s; 0.5% | IEX pre-market quotes are sparse; 60 s is "current" for a verified-trigger exit; crossing ≤ 0.5% avoids hitting an empty book (§4.3) | 30 s / 1% | Tighter → more exits wait for premarket liquidity (rule 12); looser → worse fills on thin books | as proposed |
| 11 | `TRIGGER_INVALIDATION_PROXIMITY_PCT` | 1.5% | Triggered review fires when price is within 1.5% of invalidation; with 2×ATR stops that is roughly the last third of the stop distance | 1%, 3% | Sets triggered-review count (exits/reviews bucket) | 1.5 |
| 11 | `DATA_UPGRADE_REVIEW_EQUITY_USD` | 5,000 | §13.6 text: "at $5,000 the feed is still nearly a 2% monthly hurdle" — review begins there, purchase still needs condition (b) | 2,500; 10,000 | Only sets when the ADR-0016 estimate is first reported | 5,000 |
| 11 | `DOSSIER_MAX_HEADLINES`, `DOSSIER_TOKEN_CAP` | 8; 6,000 tokens (slim 900) | ADR-0004 budget arithmetic assumes ~6k input per decision | 5/4,000; 12/9,000 | Larger dossiers raise per-call cost linearly; the cap is what keeps Sonnet 5 inside $10 | as proposed |
| 11 | `MAX_ORDERS_PER_DAY`, `MAX_LLM_CALLS_PER_HOUR`, `HALT_AFTER_CONSECUTIVE_REJECTS` | 12; 40; 3 | Runaway guards sized at ~3× the expected day (4 entries + stops + exits ≈ 10 orders; ~12 calls/hour peak) | tighter/looser | Guards only bite on a bug; false halts email you | as proposed |
| 12 | `candidate_outcomes` anchor | First eligible SIP print after the quant baseline's `order_eligible_at` for that scan | Forward returns then measure what any portfolio *could* have captured, comparable with QB-1.0 entries, never look-ahead | `signal_observed_at` (earlier, slightly optimistic); the LLM's `order_eligible_at` (only exists for triaged names) | Anchoring at the signal would flatter the scanner-quality score by the blind interval the experiment is trying to measure | QB eligibility |

## D. Schema-rent audit — tables without a consumer in Slices 1–4

Method: every table's first writer/reader by slice, from `07-implementation-sequence.md`.

| Table | First consumer | Verdict |
|---|---|---|
| `data_snapshots`, `decision_sources`, `llm_calls` | S5 (LLM pipeline); `llm_calls` is also written by the S4 budget desk with mock costs | **Keep.** Named in §10.1; `decisions` carries FKs to `data_snapshots`; the §7.6 decision record is incomplete without them. Adding them later would open a phase mid-DRY_RUN for no strategic change. |
| `candidate_outcomes` | S7 job; data source (scans/candidates) exists from S2 | **Keep.** Named in §10.1 (D-50); its rows are backfilled from S2 scans when the job lands, so the table must exist before the data it describes accumulates. |
| `forecast_resolutions` | S7 | **Keep.** Named in §7.4/§10.1; its contract-immutability trigger is one of the tested invariants that protect the dataset from day one. |
| `shadow_links` | S6 | **Keep.** Named in §12 (D-56/D-57) and cheap; QB-1.0 (S4) already exercises the shadow-portfolio kinds. |
| `notifications` | S4 (digest/alerts) | Keep (inside the S1–S4 window). |
| `cost_periods`, `analysis_reports` | S7 | **Deferred** → `supabase/migrations_deferred/analytics_reports.sql`. Not named in §10.1; no writer before the report job. |
| `approvals` | S9 (LIVE) | **Deferred** → `supabase/migrations_deferred/live_approvals.sql` (already). |

Result: 38 tables in `supabase/migrations/` (was 41), 3 deferred. All 38 trace to a named §10.1 group or to a
Phase 0 invariant (`stop_coverage`, `reconciliations`, `owner_resolutions`, `halts`, `broker_policy_checks`,
`budget_ledger`, `universe_memberships`, `lot_events` implement §8.3, §8.6, §8.7, §8.10, §9, §6.1 and ADR-0002).
