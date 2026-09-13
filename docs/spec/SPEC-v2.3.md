# Experimental Short-Term Trading Agent — Specification v2.3

**Status:** Implementation-ready. Hand off to the coding agent.
**Date:** 2026-09-13
**Supersedes:** v2.2, v2.1 (2026-09-13), v2 (2026-09-12), and the v1 brief
**Modes in scope:** DRY_RUN → PAPER. LIVE is architected but NOT enabled.
**Owner:** Ellen Marie Kimble

How to read this document: sections 1–17 are the spec. Appendix A is the decision history (every choice, the alternatives considered, the consequence, and any supersession). Appendix B consolidates configuration keys. Appendix C lists items delegated to the coding agent, each requiring an ADR before code. Appendix D is the changelog.

Anything in v1 or v2 not contradicted here still stands. Where versions conflict, the newest wins.

---

## 1. Research question and framing

**Primary question.** Can an LLM acting as portfolio manager, trading holds of hours to roughly ten trading days on 15-minute-delayed consolidated market data, with $500 in a 1x limited-margin brokerage account (no borrowing, no short selling), produce returns that beat cash, SPY, VTI, and a deterministic quantitative baseline, net of all costs including LLM spend?

**What bounds the horizon.** Not regulation. FINRA retired the pattern-day-trader rule effective June 4, 2026, and Alpaca removed PDT designation, day-trade counting, and the associated buying-power logic the same day. At 1x buying power with no borrowing, exposure can never exceed equity, so the replacement intraday-margin framework cannot bind either. What bounds the horizon is data latency (the scanner sees the complete consolidated market 15 minutes late, while seeing a single venue, IEX, in real time; see 5.6) and the LLM budget (which caps decisions per day). Both are design constraints, stated in section 3.5, and both could be relaxed in a later phase by paying for data.

**Objective hierarchy (unchanged from v1).** Primary: maximize total portfolio return. Secondary: return vs SPY/VTI, return per unit of drawdown, strategy/prompt learning, no single-position catastrophe, adaptation from prior results. Profitability is an explicit optimization target. Excessive inactivity is measured, not rewarded.

**Risk distinction (unchanged from v1).** Risk from a bad trading decision is acceptable. Risk from a software bug or unauthorized behavior is not. Every control in section 8 is engineering, not investment conservatism.

**Experimental posture.** The system's product is a dataset. Every trade, decision, rejection, and cost is recorded so the question above can be answered at the stopping rule (section 13). The first 100-trade run is **exploratory**: its statistics inform the next phase rather than settle the question.

---

## 2. Non-negotiable engineering controls

- No trade may exceed available experimental capital: notional is capped at virtual cash, independently of any buying-power figure the broker reports.
- No margin borrowing and no short selling. The live account is a 1x limited-margin account; the broker configuration is set to enforce that and verified on every boot (8.10).
- No duplicate orders: every order carries a unique `client_order_id`; the database enforces uniqueness.
- No malformed orders; every order is validated against the schema and risk policy before submission.
- No unsupported securities: single U.S. common stocks only, exclusion list enforced.
- No credential exposure: secrets exist only in Railway environment variables; the coding agent never handles them.
- No deposits or withdrawals initiated by the system.
- No LIVE execution while configured for PAPER; PAPER and LIVE use separate API key pairs and an explicit mode flag.
- No execution when brokerage state is unknown: boot-time reconciliation gates all activity, and unexplained broker state halts (8.6).
- No trading on stale data: per-source staleness thresholds.
- No executed order may differ from the validated agent decision.
- **No simulated fill before the decision was executable** (anti-look-ahead invariant, 8.9). Applies to DRY_RUN, all shadow portfolios, and all post-hoc analytics.
- No self-modification: the agent never rewrites its own prompt, strategy weights, config, or exclusion list.
- Runaway guards: max orders per day, max LLM calls per hour, halt on consecutive order rejections.

---

## 3. Account and capital model

### 3.1 Account

- Broker: Alpaca. All Alpaca brokerage accounts are opened as margin accounts. Accounts with under $2,000 equity are restricted to 1x buying power with no margin borrowing and no short selling.
- The live experiment account is therefore a **1x limited-margin account**. The risk desk treats it as cash-only regardless of what the broker would permit.
- The pattern-day-trader rule no longer exists (see section 1). There is no day-trade count, no $25,000 threshold, and no good-faith-violation regime, because margin accounts may reuse sale proceeds immediately.
- SHORT and COVER remain in the decision enum so that intent is logged as `REJECTED_ACCOUNT_INELIGIBLE`. No short research or execution paths are built in V1.
- The 1x/long-only restriction is not merely observed; it is **written to the broker** as account configuration and verified at every boot (8.10). This closes the case where equity grows past $2,000 and Alpaca automatically extends margin and shorting the experiment does not want.

### 3.2 Virtual experiment equity

- `EXPERIMENT_EQUITY_START = 500.00`.
- All sizing, buying power, P&L, and drawdown compute against the virtual $500 ledger in Supabase. The Alpaca paper account's balance and multiplier are irrelevant to sizing and are expected to be larger than 1x/$500; the cap in section 2 exists precisely because of that.
- Supabase is the ledger of record for the virtual portfolio. Alpaca is the source of truth for what is actually held and what orders are open. Reconciliation (8.6) keeps them aligned or halts.

### 3.3 Settlement metadata (accounting only)

Cash events still record `settles_on` (T+1, from the Alpaca trading calendar) so the ledger can be reconciled against the broker's cash and settled-cash figures and so dividends and fees land on the right date. **Settlement does not govern trade eligibility.** Sale proceeds are available to the virtual portfolio the moment the closing fill is confirmed.

### 3.4 Overfill guard

Notional fractional orders are exact by construction. For whole-share market orders, the risk desk caps notional at virtual cash minus `OVERFILL_BUFFER_PCT`, or requires a limit order, so a fill above virtual cash cannot occur.

### 3.5 Horizon

- Holding period is **declared per trade** by the agent (`expected_holding_period`) and must fall within `MIN_EXPECTED_HOLD` and `MAX_EXPECTED_HOLD` (defaults: 1 hour and 10 trading days).
- **Latency floor:** no entry may rest on a thesis that requires acting inside the data lag. Concretely, the expected move must plausibly persist beyond `LATENCY_FLOOR_MIN` (default 30 minutes: 15 minutes of feed delay plus scan, triage, decision, critique, and validation time). The risk desk rejects entries whose declared holding period is below the floor, and the prompt explains why.
- Intraday setups whose edge survives that lag (opening-range breakouts observable from about 10:00 ET on 15-minute SIP bars, gap continuations, catalyst follow-through) are in scope, with a same-day time stop. Scalps and momentum bursts are not.
- Intraday holds avoid the overnight-gap limitation described in 8.3; multi-day holds accept it. Both are recorded so the trade-off is measurable.
- Capital recycles immediately when a position closes.

---

## 4. Universe and eligibility

### 4.1 Instruments

- **Single U.S. common stocks only.** No ETFs, no inverse or leveraged products, no options, no crypto.
- Consequence: in a RISK_OFF regime the agent's only defensive move is cash. The prompt states this explicitly.

### 4.2 Price and liquidity floors

Delegated to the coding agent (ADR required, Appendix C): a minimum share price and a minimum average daily dollar volume, both in versioned config, both enforced by the risk desk. The two floors travel together because the free-tier data problem (section 5) and the liquidity problem share the same thin names.

### 4.3 Trading hours

- Entries: regular session only.
- Exits: regular session, plus **extended hours for exits only**, under these rules:
  - Broker stop orders do not trigger outside the regular session, so an extended-hours exit is a limit order the software places, with `extended_hours=true`, time-in-force Day.
  - Extended-hours exits fire only on a **verified trigger** (invalidation event, qualifying news or filing hit), never on schedule.
  - The limit price is **anchored to a current IEX bid/ask** that is no older than `EXT_HOURS_MAX_QUOTE_AGE_SEC`, and may cross that quote by at most `EXT_HOURS_MAX_CROSS_PCT`. Distance from the prior consolidated close is logged as an anomaly but does not veto the exit: the events that most justify an after-hours exit are exactly the ones that gap.
  - If no sufficiently fresh IEX quote exists, or the displayed size is far below the lot, the exit waits for premarket liquidity rather than hitting a stale or empty book.

### 4.4 Ethical exclusions

**Scope (decided):**

- Fossil fuels: producers and miners (oil, gas, coal), plus refiners, pipelines, and oilfield services. Utilities and financiers are out of scope.
- Weapons and defense: pure-play defense contractors and firearms manufacturers, plus conglomerates with large defense segments.
- Private prisons and detention operators.

**Mechanism:**

1. **Versioned denylist file** (primary). A repo-tracked file, e.g. `config/exclusions.yaml`, with `version`, `updated_on`, and entries `{symbol, category, reason, source}`. This is the only way to catch conglomerates. Every decision records `exclusion_list_version`. A list change is a phase marker (13.3).
2. **SIC-code backstop** from EDGAR company metadata. Proposed codes, to be verified against the current EDGAR SIC list before use:
   - Fossil: 1311, 1321, 1381, 1382, 1389 (oil and gas extraction and services); 2911 (petroleum refining); 1220, 1221, 1241 (coal); 4612, 4613 (petroleum pipelines); 4922 (natural gas transmission).
   - Review before enabling: 4923, 4924 (gas transmission and distribution mixes utility activity, which is out of scope); 5171, 5172 (petroleum wholesale).
   - Defense: 3480, 3483, 3489 (ordnance, ammunition); 3760 (guided missiles and space vehicles); 3795 (tanks).
3. **Default-deny for ambiguous SICs** unless explicitly allowlisted: 3721, 3724, 3728 (aircraft and parts), 3812 (search, navigation, guidance systems). These industries mix civil and defense revenue and cannot be resolved from free data.
4. **Private prisons:** manual denylist entries only; the set is small.
5. **Enforcement points:** scanner prefilter (excluded names never reach the LLM) and risk desk hard reject (in case one does). The LLM is never the enforcement layer.
6. **Logging:** `REJECTED_ETHICAL_SCREEN` when the LLM proposes an excluded name; `NEEDS_ETHICAL_REVIEW` when a name hits a default-deny SIC and is not on either list, in which case it is skipped, not traded.
7. **Held positions caught by a list update:** flag `ETHICAL_HOLD`. No ADD; REDUCE and SELL only; time stop still applies; no re-entry after exit.

ADRs and the denylist seed are the coding agent's first deliverable in this area; the owner reviews the seed list before PAPER.

---

## 5. Data architecture

### 5.1 Free-tier facts that shape the design

Alpaca's free market-data plan provides:

- Real-time trades and quotes from **IEX only**, roughly 2% of consolidated volume. IEX quotes can differ materially from the NBBO on thin names.
- Full consolidated **SIP** trades, quotes, and bars for anything **older than 15 minutes**. That historical data is identical to the paid plan.
- Rate-limited historical API calls (verify the current limit; assume the order of 200 requests/minute).
- Alpaca's own guidance is that the free plan is intended for testing and debugging rather than live trading decisions. The spec accepts that as a known limitation and records it as such.

Design consequences:

- The scanner runs on SIP bars with `end ≤ now − 15 minutes`. Decisions are made on delayed consolidated data by design.
- **Availability semantics:** a bar's `signal_observed_at` is the moment it became retrievable (bar end plus the 15-minute embargo plus fetch time), never the bar's own timestamp. This is the root of the anti-look-ahead invariant (8.9).
- IEX real-time is used for last-price sanity checks, execution-time reference, and the early-warning stream (5.6). **Never for volume-based signals.**
- Execution prices come from the broker, not from the data feed.
- Scanning uses multi-symbol bar requests; a 15-minute cadence over ~1,500 names fits the rate limit, a 5-minute cadence probably does not.

### 5.2 Source registry

Every data domain is served through one interface with a primary and a fallback adapter:

| Domain | Primary | Fallback / notes |
|---|---|---|
| Bars, quotes | Alpaca (SIP delayed, IEX real-time) | Second free venue-level feed, optional |
| News | Alpaca news endpoint | Research-grade scraped sources, optional |
| Filings | SEC EDGAR (full-text search, submissions API; fair-access policy: identify with a User-Agent, stay under 10 req/s) | none |
| Fundamentals | EDGAR XBRL company-facts API | Third-party free tier |
| Earnings calendar | Third-party free tier (e.g. Finnhub; verify limits) | EDGAR 8-K Item 2.02 detection |
| Trading calendar, corporate actions | Alpaca | none |

Requirements:

- Health check per adapter; `source_status` (source, grade, age, ok/degraded/down) stamped on every scan and every decision.
- Failover is automatic within a domain. A domain with no healthy source degrades the scanner (signals that depend on it are marked unavailable) rather than halting, except bars/quotes, whose loss halts new entries.
- Aggregation purposes (decided): failover, breadth across domains, and optional gap-fill. Cross-checking prices across sources is not a goal.

### 5.3 Trust grades

Every data point carries provenance and one of two grades:

- `execution`: Alpaca, EDGAR. The only grade the risk desk accepts for price validation, staleness checks, sizing, and stop placement.
- `research`: scraped or unofficial sources (e.g. Yahoo-style quote pages), third-party free tiers. Allowed as LLM context, tagged as such. Never used for execution decisions.

The decision schema's `sources[]` records the grade of every claim so the experiment can later test whether research-grade context improved outcomes.

### 5.4 The 15-minute gap

There is no free, licensed, consolidated real-time feed. "Filling the gap" therefore means, at most, an optional non-authoritative adapter (second venue feed or research-grade scrape) that informs the LLM but never the risk desk. The base design assumes decisions on delayed data and execution priced by the broker. The same delay is turned into an asset for simulation: see 8.9.

### 5.5 Staleness

- `MAX_SIP_BAR_AGE_MIN` for scan bars (default 20).
- `MAX_IEX_TRADE_AGE_MIN` per symbol for execution reference (default 5). Thin names routinely exceed this on IEX and are therefore untradeable, which is intended.
- `MAX_NEWS_AGE_HOURS` for catalyst signals.
- Any staleness breach on an execution-grade input blocks the order and is logged as `REJECTED_STALE_DATA`.

### 5.6 Feed tiers and the early-warning stream

Three feed concepts, all behind the bars/quotes adapter interface:

- **SIP_DELAYED** (free): the authoritative scanner input. Complete market, 15 minutes late. All signals, all forecast resolution, all post-hoc analytics.
- **IEX_REALTIME** (free): a single venue, live. Used as an **early-warning stream**, never as a volume source. It answers: has the candidate moved since the authoritative snapshot; has a held position made a fresh high or low, or moved against its thesis; is the price still near what the agent expects. It feeds the dossier (7.2), triggered reviews (7.5), and the execution reference.
- **SIP_REALTIME** (paid, not enabled): complete market, live. Enabled by `MARKET_DATA_PLAN=algo_trader_plus`. Restores the signals dropped in 6.3 and allows an event-driven scanner. The adapter interface and the scanner's event-driven mode are designed now so that flipping the plan is a config change and a new phase, not a rewrite.

Streaming budget: the free plan caps WebSocket subscriptions at roughly 30 symbols (verify the current figure), and the same cap applies to Alpaca's `delayed_sip` stream. The early-warning stream therefore watches a **focus set** only: open positions first, then active candidates by composite score, capped at `IEX_STREAM_MAX_SYMBOLS`. The universe scan remains REST-polled on the free plan. On stream disconnect, the system falls back to REST IEX snapshots for the focus set and logs the gap.

---

## 6. Market scanner

Deterministic, no LLM tokens. Runs every `SCAN_INTERVAL_MIN` (default 15) during the regular session, plus one pre-open pass on the prior day's data.

### 6.1 Universe construction

1. Alpaca active, tradable U.S. common stocks.
2. Remove ETFs, ADR/foreign-domiciled names if the coding agent's ADR excludes them, SPACs, and names with fewer than `MIN_HISTORY_DAYS` of bars.
3. Apply price and liquidity floors (4.2).
4. Apply the ethical exclusion layer (4.4).

### 6.2 Signals kept for V1 (computable on delayed SIP bars)

- Multi-day momentum (5, 10, 20 day), relative strength vs SPY.
- Gap vs prior close (computable from 9:45 ET onward).
- Opening-range breakout, in two explicitly separate variants because of the feed timing (the 9:30–9:45 consolidated bar is retrievable at about 10:00, but a breakout during 9:45–10:00 is not SIP-visible until about 10:15):
  - `ORB_IEX_ASSISTED`: opening range established from delayed consolidated SIP; the current crossing detected on real-time IEX, requiring a fresh IEX quote on a name above the liquidity floor.
  - `ORB_SIP_CONFIRMED`: the breakout itself confirmed once the later consolidated interval becomes retrievable.
  Both are tagged as distinct strategies so the IEX-assisted variant's worth is an empirical question.
- Gap continuation on 15-minute bars, for intraday setups within the latency floor.
- RSI, ATR, moving-average relationships, breakouts/breakdowns on daily and 15-minute bars.
- Volatility expansion on daily bars.
- Earnings proximity (calendar), earnings surprise (post-event), guidance changes and significant news (news endpoint), material filings (8-K).
- Sector strength.

### 6.3 Signals dropped from V1 (not computable on free data)

- Intraday relative or unusual volume.
- Real-time breakouts (anything requiring the last 15 minutes).
- Premarket and post-market movers (IEX is too sparse in those sessions).

These are recorded as `signal_unavailable_reason` so the limitation is visible in the dataset.

### 6.4 Output

A ranked candidate list with a composite score, per-signal values, and `signal_observed_at`, capped at `SCAN_TOP_N` (default 25), written to the `scans`/`candidates` tables. Candidates cross into LLM triage only when they exceed configurable thresholds, which keeps LLM calls tied to events rather than the clock.

### 6.5 Regime classifier

Runs before selection each day: TRENDING_BULL, TRENDING_BEAR, RANGE_BOUND, HIGH_VOLATILITY, LOW_VOLATILITY, RISK_OFF, UNKNOWN. Computed from SPY trend, realized volatility, and breadth on daily bars. Recorded on every decision and trade. Strategy selection may condition on it.

---

## 7. The agent (portfolio manager)

### 7.1 Two-tier pipeline

1. **Scanner** (deterministic) produces the ranked candidate list.
2. **Triage model** (cheap tier) sees a slim dossier per candidate and returns a short structured ranking with a one-line reason. Filters to top K (`TRIAGE_TOP_K`, default 3).
3. **Decision model** (strong tier) sees the full dossier for each of the top K and returns a decision in the schema (7.5).
4. **Self-critique** (strong tier, separate call) on every decision-model output. See 7.3.
5. **Risk desk** validates, then executes or rejects.

Every stage stamps its completion time on the decision (7.5), because those timestamps drive the anti-look-ahead invariant. Model IDs live in config (`MODEL_TRIAGE`, `MODEL_DECISION`, `MODEL_CRITIQUE`). Suggested defaults: a current small model for triage and a current mid-tier model for decisions; the coding agent confirms exact IDs and pricing against the provider's documentation at build time and records them in an ADR. Use provider prompt caching for the stable prefix.

### 7.2 Dossier composition

Assembled deterministically, token-capped, every item tagged with source and trust grade:

- Indicator snapshot and a compact bar summary (daily and 15-minute), with the age of the newest bar.
- Signal drift: `sip_signal_price`, the current IEX price and its age, and `signal_to_now_move_pct`, so the agent can judge whether the setup has already moved too far to enter. This is the agent's call (D-48); the risk desk records it but does not reject on it.
- Recent headlines from the news endpoint (`DOSSIER_MAX_HEADLINES`).
- Latest 8-K items and the most recent 10-Q/10-K highlights from EDGAR.
- A handful of XBRL facts (revenue trend, margins, cash, debt).
- Earnings date and whether it falls inside the expected holding window.
- Regime, virtual cash available, open positions with their time stops and invalidation levels, remaining daily budget.

Slim dossier (triage): indicators, one-line news summary, earnings proximity. Full dossier (decision): everything above.

### 7.3 Adversarial self-critique

- A separate call receives only the proposal and its evidence lists, not the decision model's reasoning.
- It returns `contradicting_evidence[]`, `probability_post_critique`, and `recommendation` (proceed / reduce / abandon).
- The decision record stores `probability_pre_critique` and `probability_post_critique`, both forecasts of the same event (7.4). The decision model then issues a final decision with the critique attached. The experiment compares the calibration of the two forecasts at the stopping rule.

### 7.4 Forecast event (definition required for calibration)

Every entry decision forecasts one specific binary event:

```
forecast_event = TARGET_BEFORE_INVALIDATION_OR_TIME_STOP
probability_pre_critique  = P(target_price is touched before invalidation_price is touched
                              or the time stop expires)
probability_post_critique = same event, after critique
```

**The forecast contract is immutable.** The target, invalidation, and time stop the forecast was made against are frozen at entry as `forecast_target_price_at_entry`, `forecast_invalidation_price_at_entry`, and `forecast_time_stop_at_entry`. The agent may later move the position's working target or stop for management purposes; the forecast is always resolved against the entry values, never the revised ones, so a probability stated before new information cannot be re-scored after it.

Resolution is **tape-based and post-hoc**: the analytics job resolves each forecast against consolidated SIP data (available in full after the 15-minute embargo) using the first touch of the entry target or entry invalidation between `fill_at` and the entry time stop. Resolution proceeds from 1-minute SIP bars; if a single bar touches both levels, the job fetches SIP trades for that minute alone (keeping the free plan's rate limit intact) and resolves by tick order; if ticks are unavailable or still ambiguous, the observation is recorded as `AMBIGUOUS` and excluded from calibration rather than resolved by whichever condition the code checks first. Whether the position was actually exited at that level is recorded separately (`exit_reason`). Calibration in V1 is computed on entry decisions only; review decisions (HOLD, REDUCE, ADD) log a forecast against the same event from the review time but are analyzed separately.

### 7.5 Position reviews

- One scheduled daily review per open position on the **triage model** (cheap), answering: still valid, still the best use of capital, any trigger tripped.
- **Triggered reviews** on the decision model with critique: price within `TRIGGER_INVALIDATION_PROXIMITY_PCT` of the invalidation level, qualifying news or filing hit, time stop within one session (or within one hour for intraday holds), earnings date entering the window, regime change.
- Reviews are budgeted separately from entries (section 9).

### 7.6 Decision schema

All v1 fields are retained. Additions in v2 through v2.3:

```
decision_id                  uuid, idempotency key
experiment_id, experiment_phase_id
portfolio_id
scan_id, candidate_id        link to the triggering signal
data_snapshot_id             what the model saw (section 10.4)

-- timeline (all UTC; drive the anti-look-ahead invariant, 8.9)
signal_observed_at           when the triggering bar/news became retrievable
scanner_completed_at
triage_completed_at
decision_completed_at
critique_completed_at
risk_validation_completed_at
order_eligible_at            = risk_validation_completed_at (earliest legal fill time)
order_submitted_at
fill_at                      from the broker, or from delayed reconstruction (8.9)

-- signal drift (measures what the 15-minute delay costs; advisory, never a rejection)
sip_signal_price, sip_signal_timestamp
iex_price_at_triage, iex_price_at_decision, iex_price_at_order   each with quote age
signal_to_decision_move_pct, signal_to_order_move_pct
blind_interval_move_pct      post-hoc from SIP: move between the signal bar's true time and order_eligible_at
no_action_reason             e.g. chased_too_far, required when decision = NO_ACTION

decision                     BUY | SELL | ADD | REDUCE | HOLD | NO_ACTION | SHORT | COVER
                             (SHORT/COVER always rejected in V1)
ticker, strategy, direction
proposed_notional, entry_type, entry_price_or_range
target_price, invalidation_price, time_stop
expected_holding_period      within [MIN_EXPECTED_HOLD, MAX_EXPECTED_HOLD]; ≥ LATENCY_FLOOR_MIN
confidence
forecast_event               TARGET_BEFORE_INVALIDATION_OR_TIME_STOP
forecast_target_price_at_entry, forecast_invalidation_price_at_entry, forecast_time_stop_at_entry
                             immutable after entry; position-management levels change independently
forecast_outcome             TARGET | INVALIDATION | TIME_STOP | AMBIGUOUS   (filled post-hoc)
probability_pre_critique, probability_post_critique
expected_upside_percent, expected_downside_percent, expected_value, reward_risk_ratio
catalyst, thesis
supporting_evidence[]        each with source and trust grade
contradicting_evidence[]     including critique output
earnings_in_window           bool
earnings_plan                hold_through | exit_before | n/a, with reason (required if true)
market_regime
reason_for_entry, reason_for_exit_if_existing_position
conditions_to_exit_early[]
sources[]                    with trust grade
prompt_version, exclusion_list_version, config_version, scanner_version, qb_rules_version
model_triage, model_decision, model_critique
tokens_in, tokens_out, tokens_cached, cost_usd
```

Invalid or unparseable output results in no execution and a `REJECTED_INVALID_OUTPUT` record.

### 7.7 Prompt structure and versioning

- Strict JSON output against the schema. No prose outside the JSON.
- Stable rules first (cached prefix): objective, expected-value reasoning, constraints below, the forecast-event definition, schema. Candidate context last.
- Constraints stated plainly in the prompt: 1x account, no borrowing, no shorts, sale proceeds reusable immediately; no ETFs; cash is the only risk-off instrument; data is 15-minute delayed, so no thesis may depend on acting inside the lag; holding period must be declared and lie within the configured bounds; extended-hours exits happen only on verified triggers at fresh quotes; overnight positions in fractional lots have no broker stop between sessions; the setup's drift since the authoritative signal is shown and the agent decides whether it has already moved too far, declining with `no_action_reason = chased_too_far` when it has; earnings inside the window require an explicit plan; the daily budget remaining.
- Expected-value framing from v1 is retained verbatim (probabilistic thinking, sub-80% confidence trades are acceptable when the payoff distribution is attractive).
- The prompt is versioned by the owner. The agent never edits it. Every version bump gets a changelog entry, a date, and a new `experiment_phase_id` (13.3). No trade is ever recorded without `prompt_version`.

---

## 8. Risk and execution desk

### 8.1 Validation pipeline

Every decision passes, in order: schema validity → mode check (DRY_RUN/PAPER/LIVE) → exclusion layer → instrument eligibility (stock, floors) → horizon bounds and latency floor → virtual-cash check (notional ≤ virtual cash, broker buying power ignored) → position limits → staleness → budget → duplicate check → drift fields recorded (advisory only, D-48) → order construction. The first failure rejects with a coded reason. The risk desk never edits a decision (no resizing, no repricing); it rejects.

### 8.2 Position limits (config, unchanged from v1)

`MAX_SINGLE_POSITION_PCT = 40`, `NORMAL_POSITION_RANGE_PCT = 10–30`, `MAX_POSITIONS = 5`. Slots need not be filled. No trade may use the entire balance.

### 8.3 Orders and stops

- Every order has a `client_order_id` derived from `decision_id`; the database enforces uniqueness; resubmission with the same id is a no-op.
- **Stops live at the broker.** Broker stops trigger on the consolidated tape the agent cannot see in real time.
  - Fractional lots: Alpaca supports market, limit, stop, and stop-limit for fractional quantities with time-in-force Day only, and does not support bracket/OCO on fractional orders. Therefore a **pre-open job re-arms stop orders for every fractional lot each session**, submitting them so they are confirmed active before the regular session opens wherever the API allows, with a **mandatory verification immediately after the open** that every fractional lot has a live stop. Any lot found without one is stopped by the software at the first eligible price and the incident is logged.
  - Whole-share lots: GTC stops, or bracket orders at entry where the agent supplies both target and invalidation.
- **Known limitation, recorded as a property of the experiment:** fractional overnight positions have no continuous broker-side stop protection between sessions. A gap can execute materially beyond the intended invalidation level. Intraday holds do not carry this exposure. `overnight_gap_exposure` is recorded on every closed trade so the cost of this limitation is measurable.
- Trailing stops, partial exits, and stop-raising are supported as decision types (REDUCE, modified `invalidation_price`) and executed by cancel-and-replace.
- Sibling orders on a position are cancelled before any new exit is submitted.

### 8.4 Order state machine

`PROPOSED → VALIDATED → PENDING_APPROVAL (LIVE day-one only) → SUBMITTED → PARTIALLY_FILLED → FILLED | CANCELLED | REJECTED | EXPIRED`, plus `FILL_PENDING_RECONSTRUCTION` for simulated portfolios (8.9). Every transition is timestamped with a reason. An unapproved day-one order expires at market close.

### 8.5 Budget desk

See section 9. Budget exhaustion blocks entries; exits may exceed the cap (logged).

### 8.6 Boot-time reconciliation: replay or halt

The broker is authoritative about reality; that does not mean whatever is found at the broker gets silently incorporated into the experiment. On every process start, before any other activity:

1. Fetch Alpaca positions, open orders, and all fills/activities since `last_reconciled_event_at`.
2. Replay every broker event whose order maps to a known `client_order_id` into the ledger.
3. If, after replay, ledger and broker agree: proceed.
4. If they differ but the difference is fully explained by replayed events (a fill that arrived while the worker was down, a partial fill, an expired stop): repair automatically, log `RECONCILED_REPAIR`, email.
5. If any difference cannot be explained by known `client_order_id`s and fills (a manual trade, a position in an unknown ticker, a position in an excluded name, quantities that no replay produces): **halt**, log `RECONCILE_HALT` with the full diff, and email. The owner resolves it explicitly; the resolution is recorded as a ledger event with a reason.
6. Only the primary portfolio touches the Alpaca paper account; variant portfolios are shadows (section 12) so reconciliation stays unambiguous.

### 8.7 Halts and runaway guards

- Halt all activity: Supabase unreachable; `RECONCILE_HALT`; mode/credential mismatch; broker-policy verification failure (8.10).
- Halt new entries, keep monitoring: Alpaca API down (broker stops remain live); bars/quotes domain down; budget exhausted.
- Guards in config: `MAX_ORDERS_PER_DAY`, `MAX_LLM_CALLS_PER_HOUR`, `HALT_AFTER_CONSECUTIVE_REJECTS`, optional `KILL_SWITCH_DAILY_LOSS_PCT` (documented as bug containment, default off).
- Every halt emails.

### 8.8 Restart safety

The worker is restart-safe: all in-flight state is in Supabase, all order submission is idempotent, and 8.6 runs on every boot.

### 8.9 Anti-look-ahead invariant and delayed fill reconstruction

**Invariant:** no simulated portfolio may receive a fill at a timestamp earlier than the moment its complete trading decision became executable (`order_eligible_at`). The database enforces `fill_at ≥ order_eligible_at` on every simulated fill, and `order_eligible_at ≥ signal_observed_at` on every decision.

Why it matters: with a 15-minute-delayed feed, "next bar after the signal" can model a purchase at a price that was never available to the system. Every timestamp in 7.6 exists so that this cannot happen silently.

**Reconstruction rule for DRY_RUN and shadow portfolios:** the simulated fill is not estimated at decision time. The order enters `FILL_PENDING_RECONSTRUCTION`. After `SIM_FILL_DELAY_MIN` (default 15, plus a small margin), the consolidated SIP trades and quotes covering `order_eligible_at` have become retrievable; the fill is then computed from the **first eligible SIP quote or trade after `order_eligible_at`**, plus the spread-based slippage model (section 12). This yields a shadow execution model built on real consolidated data without a paid feed.

The same discipline applies downstream: MFE/MAE are computed from bars after `fill_at`; benchmark returns for a trade are measured over `[fill_at, exit_fill_at]`, not from the signal; forecast resolution (7.4) starts at `fill_at`.

For the quant baseline, `order_eligible_at` is `scanner_completed_at` plus its own validation time. The baseline therefore acts earlier than the LLM portfolio on the same signal. That gap is real and is part of what the comparison measures: LLM latency is a cost of LLM judgment.

### 8.10 Broker policy enforcement

Three independent layers keep the experiment at 1x and long-only: the broker's own account configuration, the risk desk, and the $500 virtual ledger. The first is enforced, not just checked:

```
BROKER_POLICY:
  max_margin_multiplier     = 1
  no_shorting               = true
  max_options_trading_level = 0

At PAPER and LIVE initialization, and on every boot:
  read the account configuration
  if it matches BROKER_POLICY: proceed
  if it differs and BROKER_POLICY_ENFORCE = true: write the policy, re-read, log BROKER_POLICY_APPLIED, email
  if it still differs, or cannot be read: halt (BROKER_POLICY_HALT)
The virtual-cash cap remains independently authoritative regardless of broker configuration.
```

The write is a bootstrap action driven by explicit config, not agent self-modification. It runs on every boot because a paper-account reset can revert the configuration.

---

## 9. Budget and cost accounting

- `LLM_MONTHLY_CAP_USD = 10.00`, prorated to a daily allowance.
- Two buckets: **entries** (hard cap; when spent, the agent emits NO_ACTION for new candidates) and **exits/reviews** (soft cap; overage allowed, logged as `BUDGET_OVERAGE_EXIT`).
- Every LLM call records model, tokens in/out/cached, and cost. Cost is attributed to the decision and, on close, to the trade.
- **Cost categories recorded per trade and per period:** `broker_fees` (none expected), `regulatory_fees` (SEC transaction fee and FINRA TAF on sells; the coding agent sources the current schedule), `llm_cost`, `market_data_cost` (zero on the free tier; recorded so a paid phase is comparable), `incremental_infrastructure_cost` (the marginal Railway/Supabase/Vercel cost attributable to this project, prorated).
- **Two P&L views, always reported together:** *Trading P&L* (fills and fees only) and *Experiment P&L after computational costs* (trading P&L minus LLM, data, and infrastructure cost). They answer different questions; "+3.7% trading, +1.6% after AI costs" is a finding, not bookkeeping.
- Alpaca's paper simulator does not model regulatory fees or dividends; the shadow ledger and the paper ledger both apply them from the schedule so DRY_RUN, PAPER, and eventual LIVE are on the same footing.
- Expected shape under the cap: two to four decision-model entries per day, each with critique, from a top-K of about three; daily reviews on the triage model. The budget ledger confirms or refutes this within the first week of DRY_RUN.

---

## 10. Persistence (Supabase Postgres)

### 10.1 Table groups

- **Experiment:** `experiments`, `experiment_phases` (id, started_at, reason, what changed, versions in force), `portfolios` (kind: llm | quant_shadow | benchmark), `prompt_versions`, `config_versions`, `scanner_versions`, `qb_rules_versions`, `exclusion_list_versions`.
- **Market:** `scans`, `candidates` (with `signal_observed_at`, `sip_signal_price`, and the IEX prices at each pipeline stage), `candidate_outcomes` (forward returns for every candidate, traded or not, at `CANDIDATE_OUTCOME_HORIZONS`, computed post-hoc from SIP), `regimes`, `source_status`, `benchmark_prices` (SPY, VTI daily).
- **Decisions:** `decisions` (full timeline of 7.6), `llm_calls` (cost ledger), `data_snapshots`.
- **Execution:** `orders`, `order_events`, `fills` (with `paper_fill_minus_shadow_estimate_bps` on PAPER fills), `lots`, `cash_ledger` (with `settles_on` for accounting), `fees`.
- **Analytics:** `closed_trades` (all v1 metrics: gross/net return, MFE, MAE, holding period, strategy, regime, initial confidence, predicted up/down/probability, target reached, invalidation reached, discretionary exit, benchmark return over `[fill_at, exit_fill_at]`, prompt version; plus: pre/post-critique probabilities and resolved forecast outcome, `exit_reason`, earnings plan and outcome, `overnight_gap_exposure`, cost categories, source grades used, `experiment_phase_id`).

### 10.2 Invariants (schema-enforced, per the owner's "durable schema over process" principle)

- `portfolio_id`, `experiment_id`, and `experiment_phase_id` on every decision, order, fill, and closed trade.
- Unique `client_order_id`.
- No physical deletes on decisions, orders, fills, or cash ledger.
- State transitions validated (check constraints or triggers).
- `fill_at ≥ order_eligible_at` for every simulated fill; `order_eligible_at ≥ signal_observed_at` for every decision.
- All timestamps UTC; display in ET.
- `prompt_version`, `exclusion_list_version`, `config_version`, `scanner_version`, `qb_rules_version` non-null on every decision.

### 10.3 Ledger style

Delegated to the coding agent (append-only event log vs mutable tables with audit trail). Either must satisfy 10.2 and allow full reconstruction of any position's history. ADR required.

### 10.4 Context retention

- Full LLM input context is stored per decision as a compressed blob in Supabase Storage for **90 days**, then pruned by a scheduled job that touches only the blob.
- The hash of the context and a structured extract (signals seen, evidence lists, sources with grades, headlines referenced) are stored in Postgres permanently. The 100-trade analysis depends only on the permanent columns.

### 10.5 Migrations

Repo-tracked Supabase migrations. Schema changes bump `config_version` and open a new phase.

---

## 11. Infrastructure and security

- **Separate projects on existing subscriptions:** one Railway project (worker), one Supabase project (database and storage), one Vercel project (approval endpoint). Isolation is deliberate: no shared database, environment, or credentials with Skywatch or EZ2Book.
- **Secrets:** Alpaca keys, provider API keys, and email credentials live only in Railway environment variables, set by the owner. The coding agent (Claude Code / Codex) never sees or writes them.
- **Mode switch:** `EXECUTION_MODE ∈ {DRY_RUN, PAPER, LIVE}` plus separate key pairs (`ALPACA_PAPER_KEY/SECRET`, `ALPACA_LIVE_KEY/SECRET`). LIVE requires the mode flag, the live key pair, `LIVE_ENABLED=true`, a non-empty `LIVE_APPROVAL_UNTIL`, and a passing broker-policy verification (8.10).
- **Scheduling:** in-process scheduler using the Alpaca trading calendar (holidays, half-days).
- **Email channel** (Gmail or transactional provider): daily digest, halt and reconciliation alerts, budget alerts, and day-one LIVE approvals.
- **Approvals:** email with a signed one-click approve/reject link to the Vercel endpoint; link bound to `order_id`, expires with the order. Reply-to-approve is not used.
- **Approval window:** `LIVE_APPROVAL_UNTIL` (date) or `LIVE_APPROVAL_FIRST_N_ORDERS`; after that, autonomous.

---

## 12. Baselines and shadow portfolios

Run from day one at zero LLM cost:

- **Cash:** $500 flat.
- **SPY** and **VTI:** $500 bought at the experiment start close, daily marked.
- **Quant shadow (QB-1.0):** the same universe, exclusions, scanner, and risk limits, with fixed rules and no LLM. Default rules, versioned; a change creates a new phase:
  - Candidate: highest composite-score name from each scan not already held.
  - Entry: first eligible SIP print after the baseline's `order_eligible_at` (8.9), virtual cash permitting.
  - Size: 20% of portfolio equity.
  - Stop: 2 × ATR(14) below entry. Target: 2R. Time stop: 5 trading days.
  - Max positions: 5. Exits before entries.
  - No drift judgment: QB-1.0 enters at the first eligible print regardless of movement since the signal. The LLM portfolio may decline chased setups (D-48); that difference is part of what the paired comparison measures.
- **Fill model for simulated portfolios** (delayed reconstruction, 8.9), stated so that spread is never counted twice:
  - Preferred: BUY fills at the first eligible consolidated **ask** after `order_eligible_at`; SELL fills at the first eligible consolidated **bid**. The quote already embodies the spread.
  - Fallback when no usable quote exists for that instant: BUY at the first eligible trade **plus** an estimated half-spread; SELL at the first eligible trade **minus** it. Half-spread is estimated from SIP quotes around that time.
  - Never add a half-spread to an ask or bid fill. Any further pessimism is a separate, named config (`SIM_ADDITIONAL_SLIPPAGE_BPS`, default 0), reported as slippage, not spread.
  - The same model is used for the LLM portfolio in DRY_RUN so DRY_RUN and PAPER are comparable.
- **Paper-fill calibration:** for every PAPER fill, the shadow model also computes what it would have estimated, and `paper_fill_minus_shadow_estimate_bps` is stored. At the stopping rule this shows whether the simulated fill model runs systematically optimistic or pessimistic, which is far more useful than the general warning that paper fills ignore latency, queue position, and market impact.
- **Counterfactual shadows** (zero LLM cost; both use the anti-look-ahead fill model):
  - `CRITIQUE_SHADOW`: whenever critique changes a proposal (size, levels, or abandon), the **pre-critique proposal** is executed virtually. Two views are kept: a **per-trade paired counterfactual** (the pre-critique trade at its proposed size, compared trade by trade with the primary's post-critique outcome; this is the pre-registered measurement) and a **parallel portfolio** (what if pre-critique proposals were always executed, with its own virtual cash and position limits; a realistic P&L view that may diverge over time). The paired view may exceed position limits; that is logged, not blocked.
  - `DETERMINISTIC_EXIT_SHADOW`: for every LLM-managed position, an entry-matched twin that exits only by the entry stop, entry target, or entry time stop. Compared with the agent's discretionary REDUCE, SELL, and stop-adjustment behavior, this isolates whether LLM exit management adds value.
- Variant LLM portfolios (prompt or concentration variants) are added later as shadows; only the primary touches the Alpaca paper account.

---

## 13. Experiment design and learning

### 13.1 Stopping rule

The experiment is judged at **~100 closed trades** in the primary portfolio. A closed trade is one position lifecycle, open to flat; partial exits are sub-events of that trade. The resulting statistics are **exploratory**.

### 13.2 Phases (no freeze)

The primary portfolio is **not frozen** for the 100-trade cohort. Instead:

- `EXPERIMENT_ID` identifies the run; `EXPERIMENT_PHASE_ID` identifies the configuration in force.
- **Any** change to the prompt, risk config, scanner weights or version, regime thresholds, QB-1.0 rules, model IDs, or exclusion list creates a new phase with a recorded reason and the full set of versions in force. Bug fixes create phases too, tagged `correctness` or `safety` rather than `strategy`.
- Nothing changes silently. Nothing changes online. The owner makes every change and the system records it.
- No minimum dwell per phase is imposed (D-58). Instead, the analysis report shows closed primary trades per phase so fragmentation is visible when reading results; a phase with very few trades is reported as descriptive only.
- Consequence, stated plainly: the 100-trade comparison against QB-1.0 is a comparison of an **evolving agent** against fixed rules (or against rules that also evolve by phase). Per-phase slices will be too small to support conclusions on their own; the pooled analysis treats phase as a covariate and reports per-phase descriptives for context. Experimental ideas can still be run as shadow variants alongside the primary; that is the way to test a change without spending primary trades on it.

### 13.3 Pre-registered analysis plan

No go/no-go threshold. The following is committed before trade one and computed at the stopping rule:

- Total return, max drawdown, return/drawdown, win rate, average win/loss, expectancy, MFE/MAE distributions.
- Return vs cash, SPY, VTI, and QB-1.0, as Trading P&L and as Experiment P&L after computational costs, each with a **block-bootstrap confidence interval** (blocks by trading week, because trades cluster within regimes and weeks and are not independent) on the difference.
- Paired comparison of the LLM portfolio vs the quant shadow on shared candidates, including the timing gap from LLM latency.
- Calibration: Brier score computed continuously; reliability plotted by **quintile** (deciles are too granular at ~100 observations); pre- vs post-critique on the same forecast event (7.4).
- Performance by strategy, regime, catalyst type, holding-period bucket (intraday vs overnight vs multi-day), earnings plan, and phase.
- Exit diagnostics: return at +1 and +5 sessions after each exit (sells winners early?), time-in-loss before invalidation (holds losers long?), realized slippage past invalidation on overnight fractional lots (the gap cost).
- Did research-grade context correlate with better outcomes?
- Simulated-vs-paper fill error (`paper_fill_minus_shadow_estimate_bps`) distribution.
- **Blind-interval cost:** distributions of `signal_to_order_move_pct` and `blind_interval_move_pct`; forward returns (from `candidate_outcomes`) of candidates the agent declined as `chased_too_far`; share of top-K setups no longer attractive by decision time; a scanner-quality score from candidate forward returns, independent of the LLM.
- **Critique value:** paired comparison of primary (post-critique) outcomes against `CRITIQUE_SHADOW` pre-critique outcomes on the trades critique changed, with block-bootstrap intervals; the parallel critique portfolio's P&L as a secondary view. This separates "critique improves calibration" from "critique improves returns."
- **Exit value:** each LLM-managed position against its `DETERMINISTIC_EXIT_SHADOW` twin.
- Count and share of `AMBIGUOUS` forecast resolutions; closed trades per phase.
- Share of days with zero activity, and NO_ACTION frequency.

### 13.4 Hypotheses (from v1) and how each is measured

| Hypothesis | Measurement |
|---|---|
| Adversarial self-critique improves returns | calibration pre- vs post-critique on the defined forecast event; realized return primary vs `CRITIQUE_SHADOW`, paired |
| News context improves momentum trades | outcomes split by whether news evidence was cited |
| Higher concentration helps or hurts | later variant portfolio at different limits |
| LLM exits beat deterministic stops | each position vs its `DETERMINISTIC_EXIT_SHADOW` twin; MFE/MAE |
| Shorter holds improve capital efficiency | return per hour held, by holding-period bucket, within the cohort |
| Which prompt produces the best risk-adjusted return | prompt-variant shadows and phase comparison |
| Holding through earnings pays | outcomes by `earnings_plan` |
| The LLM adds value over the scanner | paired comparison vs QB-1.0, net of LLM cost and latency |
| Real-time consolidated data would add value net of its cost | blind-interval cost analysis; forward returns of candidates declined for drift; signals unavailable on the free plan |

### 13.5 Learning loop

Offline and owner-driven. A periodic analysis job produces the 13.3 report on demand; the owner decides on changes and each one opens a new phase. The system never reweights or rewrites itself online.

### 13.6 Market-data upgrade policy

Architect for `SIP_REALTIME` now; do not buy it for the $500 run, where $99/month would dwarf the capital. Reconsider the upgrade only when **both** conditions hold: (a) portfolio equity exceeds `DATA_UPGRADE_REVIEW_EQUITY_USD`, and (b) the estimated incremental gross return attributable to eliminating the blind interval, from the 13.3 analysis, exceeds the subscription cost by `DATA_UPGRADE_BENEFIT_FACTOR` (default 2x). Capital alone is not sufficient: at $5,000 the feed is still nearly a 2% monthly hurdle. The estimator behind (b) is an ADR (Appendix C). A plan change opens a new phase, restores the 6.3 signals, and likely retires `SCAN_INTERVAL_MIN` in favor of an event-driven scanner.

---

## 14. Conflict resolution rules (consolidated)

1. Exits are processed before entries in every cycle.
2. Broker-side exits always win over LLM opinions; a stop that fired is final.
3. Sibling orders on a position are cancelled before any new exit is submitted.
4. When triggers collide on one position: invalidation > time stop > opportunity rotation.
5. Execution-grade primary source wins; a fallback price is accepted only within `FALLBACK_PRICE_TOLERANCE_PCT` of the last known price, otherwise no trade.
6. Ledger vs Alpaca on boot: replay known activity and repair; anything unexplained halts (8.6).
7. Budget exhausted with positions open: entries blocked; exits and triggered reviews may exceed the cap, logged.
8. Exclusion-list update catches a held name: `ETHICAL_HOLD`, hold to planned exit.
9. Unapproved day-one LIVE order: expires at market close.
10. Rotation into a better opportunity: the sell is executed first; the buy is sized against virtual cash after the sell fill is confirmed. Proceeds are usable immediately.
11. A simulated fill may never precede `order_eligible_at`; if reconstruction finds no eligible print before the order's expiry, the simulated order expires unfilled.
12. Extended-hours exit with no fresh IEX quote: wait for premarket liquidity; the prior close never vetoes a verified-trigger exit.

---

## 15. Edge cases (consolidated)

- Holidays and half-days: Alpaca calendar endpoint drives the scheduler.
- Trading halts: orders will not fill; the monitor waits; no repricing.
- Splits, reverse splits, symbol changes: applied from Alpaca corporate-actions data before the pre-open stop re-arm.
- Delistings: lot force-closed at last available price, tagged.
- IPOs and short-history names: excluded until `MIN_HISTORY_DAYS`.
- Alpaca rejects for buying power: recorded as `REJECTED_BROKER`, never retried at a different size.
- Supabase outage: full halt. Alpaca API outage: entries halted, broker stops keep guarding.
- Duplicate fill or order events from the broker: idempotent by broker order id.
- Worker crash mid-order: idempotent resubmission is a no-op; reconciliation on boot.
- Worker crash with orders in `FILL_PENDING_RECONSTRUCTION`: reconstruction resumes on boot; it is deterministic given `order_eligible_at`.
- Fractional lot found without a live stop after the open: software stop at first eligible price, incident logged.
- Extended-hours exit with no fresh IEX quote or insufficient displayed size: wait for premarket liquidity.
- Earnings inside the holding window: `earnings_plan` required (7.6).
- Dividends: credited to the cash ledger with their settlement date; applied to shadow and paper ledgers alike, since the paper simulator ignores them.
- Name enters a default-deny SIC after the fact: `NEEDS_ETHICAL_REVIEW`, no ADD.
- Equity crosses $2,000 (deposits are prohibited, so only through gains): Alpaca would normally extend margin and shorting; the broker policy (8.10) and the virtual-cash cap keep the experiment at 1x regardless.
- Paper-account reset reverts the broker configuration: the boot-time policy check re-applies it.
- A forecast's target and invalidation are both touched inside one 1-minute bar with no tick resolution: recorded `AMBIGUOUS`, excluded from calibration.
- Early-warning stream disconnects: fall back to REST IEX snapshots for the focus set; log the gap; triggered reviews continue on snapshots.
- Focus set exceeds the stream cap: positions first, then candidates by score; the remainder are polled.

---

## 16. Modes and launch

- **DRY_RUN:** full pipeline, fills reconstructed from delayed SIP data (8.9), no broker orders. Used to validate the virtual-cash cap, budget ledger, reconciliation, timestamp discipline, focus-set management for the early-warning stream, and the pre-open stop job (against the paper API without submitting).
- **PAPER:** initialization applies `BROKER_POLICY` (8.10); orders to the Alpaca paper account; the virtual $500 ledger governs sizing regardless of the paper account's balance or multiplier; shadows run alongside, including the critique and deterministic-exit shadows; paper fills are compared to the shadow estimate.
- **LIVE:** not enabled in V1. Requires `EXECUTION_MODE=LIVE`, `LIVE_ENABLED=true`, live key pair, `LIVE_APPROVAL_UNTIL` set, a passing broker-policy verification, and a fresh reconciliation. Day-one approvals by email link, then autonomous.

---

## 17. V1 deliverables

Unchanged from v1, plus:

- System architecture; scanner design; agent prompt(s) with version; decision schema; risk-policy configuration; database schema; example decision; example executed paper trade; example rejected trade; how the $500 virtual portfolio is enforced; how strategy performance is measured; tests and results; DRY_RUN and PAPER launch instructions; highest-value next improvement.
- ADRs for every delegated item in Appendix C.
- Virtual-cash cap test against a paper account whose multiplier and balance exceed the experiment's (order above virtual cash is rejected even though the broker would accept it).
- Anti-look-ahead tests: a simulated fill before `order_eligible_at` is rejected by the database; reconstruction picks the first eligible SIP print after eligibility; MFE/MAE and benchmark windows start at `fill_at`.
- Budget ledger test that exhausts the entry bucket and confirms exits still work.
- Exclusion layer with the seed denylist, SIC backstop, and a test that an excluded name is rejected at both enforcement points.
- Reconciliation tests: replay repairs a missed fill; unknown ticker halts; excluded-name position halts; manual trade halts.
- Pre-open stop re-arm job with post-open verification, and a test for the lot-without-stop path.
- Phase test: a prompt-version bump opens a new phase and every subsequent decision carries it.
- Forecast-resolution tests against synthetic SIP data: target first, invalidation first, time stop, both-in-one-minute resolved by ticks, both-in-one-minute with no ticks → `AMBIGUOUS`.
- Forecast-immutability test: moving the working target after entry does not change the resolved outcome.
- Broker-policy tests: matching config proceeds; mismatched config is written and re-read; unreadable config halts.
- Fill-model tests: ask/bid fill with no added spread; trade-fallback with half-spread; `SIM_ADDITIONAL_SLIPPAGE_BPS` applied separately and labeled slippage.
- Shadow tests: a critique-changed proposal spawns a paired counterfactual and a parallel-portfolio entry; a discretionary exit leaves the deterministic-exit twin running to its own exit.
- Daily digest email and the approval-link flow (Vercel endpoint) with an expiry test.
- The pre-registered analysis job producing the 13.3 report on demand, including block-bootstrap intervals and quintile calibration.
- `candidate_outcomes` job, with a test that every candidate, traded or not, receives forward returns at each horizon.
- Early-warning stream: focus-set cap test (positions before candidates) and disconnect-fallback test.
- `MARKET_DATA_PLAN` switch test: swapping the adapter to a mocked real-time SIP source changes no scanner code.

---

## Appendix A. Decision history

Each entry: what was decided, alternatives considered, and the consequence for the design. Pass 1 was a critique of the v1 brief; rounds 1–9 were question rounds; R (review) entries come from the external review of v2 and the two rounds that followed. Superseded entries are kept for history.

| ID | Topic | Decided | Alternatives considered | Consequence |
|---|---|---|---|---|
| D-01 | Account type | ~~Alpaca cash account, no margin~~ **Superseded by D-38** | Margin | See D-38 |
| D-02 | Shorting | Out of V1 scope; enum kept for logging | Build short paths for paper | Under $2,000 Alpaca disables shorting anyway |
| D-03 | Fractional shares vs brackets | Stops at broker; fractional lots get Day stops re-armed each session; whole-share lots GTC/bracket | Cap universe by price to force whole shares; software-only stops | Alpaca fractional supports market/limit/stop/stop-limit Day only, no bracket; see D-45 for timing |
| D-04 | Market data | Free tier only | Paid consolidated feed | Scan on 15-min-delayed SIP; IEX for sanity only; volume signals dropped |
| D-05 | LLM cost | Counted as a trading cost | Ignore | Net-of-LLM reporting; budget ledger; extended by D-46 |
| D-06 | Statistical power | 100-trade stopping rule; one LLM portfolio + free shadows | Calendar-based; many variants at launch | Sample-based judgment; variants added later |
| D-07 | Quant baseline | Explicit rules QB-1.0 | Leave to implementation | Clean LLM-vs-rules comparison |
| D-08 | Learning loop | Offline, versioned, owner-driven | Online adaptation | Avoids chasing noise on small samples |
| D-09 | Bug containment | client_order_id, reconciliation, runaway guards | Rely on broker | Engineering, not conservatism |
| D-10 | Multi-source purpose | Failover, breadth per domain, optional gap-fill | Cross-check prices | Source registry with primary/fallback per domain |
| D-11 | Fossil-fuel scope | Producers, miners, refiners, pipelines, oilfield services | Producers only; plus utilities; plus financiers | SIC backstop covers most; denylist for the rest |
| D-12 | Other exclusions | Weapons/defense, private prisons | Tobacco/alcohol/gambling; none | Defense needs a maintained list |
| D-13 | Unofficial sources | Allowed for research context only, never execution | Official only; allowed everywhere | Two trust grades |
| D-14 | Defense scope | Pure-play plus conglomerates with big defense arms | Pure-play only; any defense revenue | Versioned denylist primary; aerospace SICs default-deny |
| D-15 | ETFs | Single stocks only | ETFs with or without look-through | Cash is the only risk-off move |
| D-16 | Language | Coding agent chooses, ADR required | Python; TypeScript | Likely Python |
| D-17 | Hosting | Railway worker + Supabase Postgres, separate projects on existing subscriptions | Local + SQLite | Restart-safe worker; boot reconciliation |
| D-18 | Human in the loop (LIVE) | Approve only on day one, then autonomous | Every order; size threshold; fully autonomous | PENDING_APPROVAL state; approval window config |
| D-19 | Channel | Email | Slack/Discord; dashboard; CLI | Signed approve links via Vercel; digest and alerts by email |
| D-20 | Portfolios at launch | One LLM portfolio, add variants later | Multiple variants at launch | Shadows (cash, SPY, VTI, QB-1.0) still run from day one |
| D-21 | LLM spend cap | $10/month (clarified: AI usage, not portfolio) | $25; $50; track only | Two-tier models; two-bucket budget |
| D-22 | Duration | Until ~100 closed trades | 1 month; 1 quarter; open-ended | Closed-trade definition fixed; results exploratory (D-43) |
| D-23 | Win criterion | No threshold; learn | Beat SPY; beat quant; beat both | Pre-registered comparisons with confidence intervals instead |
| D-24 | Model tiering | Cheap triage + strong decisions | One mid-tier; strong only | Pipeline in 7.1 |
| D-25 | Context retention | Full for 90 days, then prune | Full forever; hashes only | Permanent structured extract; blob in Storage |
| D-26 | Ledger style | Coding agent decides, ADR required | Event log; mutable + audit | Invariants in 10.2 apply either way |
| D-27 | Shadow fill model | Spread-based estimate | Next-bar open; fixed bps | Timing corrected by D-39 |
| D-28 | Ledger vs Alpaca on boot | ~~Adopt broker state, email~~ **Superseded by D-42** | Halt; halt beyond tolerance | See D-42 |
| D-29 | Budget exhausted | Overage allowed for exits only | Deterministic only; reserved slice | Two buckets |
| D-30 | Exclusion update on held name | Hold to planned exit | Sell at open; email and wait | ETHICAL_HOLD flag |
| D-31 | Trading hours | Extended hours for exits only | Regular only; full extended | Guard logic replaced by D-44 |
| D-32 | Earnings in window | Agent decides per trade | Always exit before; reduced size | `earnings_plan` required; outcome tracked |
| D-33 | Price floor | Coding agent sets, ADR required | Exclude under $5; allow $1+ | Paired with liquidity floor |
| D-34 | Self-critique | On every decision-model output | Entries only; test later | Pre/post probabilities stored; event defined by D-41 |
| D-35 | Dossier depth | Technicals + news + filings/fundamentals | Technicals only; plus news | Slim vs full dossier by tier |
| D-36 | Review cadence | Once daily + triggers | Triggered only; twice daily | Daily on triage model; triggers on decision model |
| D-37 | Infrastructure ownership | Separate Railway/Supabase/Vercel projects on existing subscriptions; owner sets secrets | Shared projects | Isolation from Skywatch and EZ2Book |
| D-38 (R) | Account model, corrected | Alpaca 1x limited-margin account: no borrowing, no shorting; PDT rule retired 2026-06-04, so no day-trade limits | True cash account (not offered by Alpaca) | Settlement engine removed; proceeds reusable immediately; virtual-cash cap enforced independently of broker buying power; LIVE boot assertion |
| D-39 (R) | Anti-look-ahead | No simulated fill before `order_eligible_at`; full decision timeline stored; DRY_RUN and shadow fills reconstructed 15+ minutes later from real SIP data | Estimate fills at decision time from delayed bars | Database-enforced invariant; MFE/MAE, benchmark windows, and forecast resolution start at `fill_at` |
| D-40 (R) | Horizon | Per-trade, hours to ~10 trading days, within config bounds and a latency floor | Keep swing only; intraday-first | Intraday setups in scope; holding period becomes a within-cohort variable |
| D-41 (R) | Forecast event | `TARGET_BEFORE_INVALIDATION_OR_TIME_STOP`, resolved post-hoc against SIP bars; calibration on entries only in V1 | Leave "success" undefined | Brier score and calibration are well-defined |
| D-42 (R) | Reconciliation | Replay known broker activity and repair; anything unexplained halts | Adopt and email (D-28); adopt if small | Manual account changes cannot silently enter the experiment |
| D-43 (R) | Cohort freeze | No freeze; every change opens a new `EXPERIMENT_PHASE_ID` | Freeze all; freeze prompt and risk config only | Evolving-agent comparison; phase as covariate; results exploratory; shadows for A/B |
| D-44 (R) | Extended-hours guard | Anchor to a fresh IEX quote with a bounded cross; prior close logs an anomaly but never vetoes a verified-trigger exit; no fresh quote → wait for premarket | Percentage band from prior close | Gap-inducing catalysts can be acted on |
| D-45 (R) | Fractional stop timing | Re-arm before the open where possible, mandatory post-open verification; overnight-gap exposure recorded as an experiment property | Re-arm by 9:35 ET | Minimizes the naked window; makes the gap cost measurable |
| D-46 (R) | Cost accounting | Broker, regulatory, LLM, data, and infrastructure costs recorded; Trading P&L and Experiment P&L after computational costs reported together | LLM cost only | Answers two questions instead of one |
| D-47 (R) | Analysis statistics | Brier continuous; calibration by quintile; block bootstrap by trading week; paper-vs-shadow fill delta stored per fill | Deciles; IID bootstrap | Harder to fool at n≈100 |
| D-48 (R) | Drift ("don't chase") check | LLM judgment only; drift fields recorded, no risk-desk rejection | Hard ceiling plus LLM view; strict ceiling only | `no_action_reason = chased_too_far`; QB-1.0 has no equivalent, which the paired comparison measures |
| D-49 (R) | Feed tiers | SIP_DELAYED authoritative; IEX_REALTIME early-warning on a ~30-symbol focus set; SIP_REALTIME behind `MARKET_DATA_PLAN` | Buy Algo Trader Plus now | Free V1 stays viable; upgrade is a config change and a phase |
| D-50 (R) | Delay-cost measurement | Per-candidate prices at each pipeline stage plus `candidate_outcomes` forward returns for every candidate | Trade-level only | Blind-interval cost and scanner quality become measurable |
| D-51 (R) | Data upgrade trigger | ~~Capital threshold alone~~ **Superseded by D-59** | After 50 trades; at 100 trades; never | See D-59 |
| D-52 (R) | Broker policy | 1x/long-only written to Alpaca account configuration and verified on every boot; halt on mismatch | Check only | Three layers: broker, risk desk, virtual ledger |
| D-53 (R) | Opening-range breakout | Split into `ORB_IEX_ASSISTED` and `ORB_SIP_CONFIRMED` | One ORB strategy | Feed timing made explicit; IEX-assisted worth is testable |
| D-54 (R) | Simulated fill formula | Ask/bid preferred; trade ± half-spread as fallback; extra slippage named separately | Quote or trade plus half-spread (ambiguous) | Spread never counted twice |
| D-55 (R) | Forecast contract | Entry target/invalidation/time stop frozen for resolution; 1-minute bars then ticks; `AMBIGUOUS` when unresolvable | Resolve against revised levels; resolve by check order | Calibration cannot be re-scored after new information |
| D-56 (R) | Critique shadow | Both a per-trade paired counterfactual (the measurement) and a parallel pre-critique portfolio | Pairing only; parallel only | Answers whether critique improves returns, not just calibration |
| D-57 (R) | Exit shadow | Entry-matched deterministic-exit twin per LLM-managed position | Compare exit types across trades | Isolates LLM exit-management value |
| D-58 (R) | Phase dwell rule | Declined; report trades per phase instead | Warn under 15 trades; block under 15 | Fragmentation visible in the report, not prevented |
| D-59 (R) | Data upgrade trigger, revised | Capital threshold **and** estimated blind-interval benefit ≥ `DATA_UPGRADE_BENEFIT_FACTOR` × feed cost | Capital alone (D-51) | Value-of-information rule |

## Appendix B. Configuration keys

```
EXECUTION_MODE                DRY_RUN | PAPER | LIVE
LIVE_ENABLED                  false
LIVE_APPROVAL_UNTIL           date, or LIVE_APPROVAL_FIRST_N_ORDERS
BROKER_POLICY_ENFORCE         true
BROKER_POLICY                 max_margin_multiplier=1, no_shorting=true, max_options_trading_level=0
EXPERIMENT_ID
EXPERIMENT_PHASE_ID           opened by the owner on any change
EXPERIMENT_EQUITY_START       500.00
OVERFILL_BUFFER_PCT
MAX_SINGLE_POSITION_PCT       40
NORMAL_POSITION_RANGE_PCT     10–30
MAX_POSITIONS                 5
MIN_EXPECTED_HOLD             1h
MAX_EXPECTED_HOLD             10 trading days
LATENCY_FLOOR_MIN             30
MIN_PRICE, MIN_AVG_DOLLAR_VOLUME     (ADR)
MIN_HISTORY_DAYS
SCAN_INTERVAL_MIN             15
SCAN_TOP_N                    25
TRIAGE_TOP_K                  3
MAX_SIP_BAR_AGE_MIN           20
MAX_IEX_TRADE_AGE_MIN         5
MAX_NEWS_AGE_HOURS
FALLBACK_PRICE_TOLERANCE_PCT
EXT_HOURS_MAX_QUOTE_AGE_SEC
EXT_HOURS_MAX_CROSS_PCT
TRIGGER_INVALIDATION_PROXIMITY_PCT
SIM_FILL_DELAY_MIN            15 (+ margin)
MARKET_DATA_PLAN              basic | algo_trader_plus
IEX_STREAM_MAX_SYMBOLS        ~30 on basic (verify)
CANDIDATE_OUTCOME_HORIZONS    15m, 1h, 1d, 5d
DATA_UPGRADE_REVIEW_EQUITY_USD
DATA_UPGRADE_BENEFIT_FACTOR   2.0
SIM_ADDITIONAL_SLIPPAGE_BPS   0
FORECAST_RESOLUTION           1-minute SIP bars, ticks for ambiguous minutes
LLM_MONTHLY_CAP_USD           10.00
LLM_ENTRY_BUCKET_PCT
MODEL_TRIAGE, MODEL_DECISION, MODEL_CRITIQUE
DOSSIER_MAX_HEADLINES, DOSSIER_TOKEN_CAP
MAX_ORDERS_PER_DAY
MAX_LLM_CALLS_PER_HOUR
HALT_AFTER_CONSECUTIVE_REJECTS
KILL_SWITCH_DAILY_LOSS_PCT    off
CONTEXT_RETENTION_DAYS        90
EXCLUSION_LIST_PATH           config/exclusions.yaml
PROMPT_VERSION, CONFIG_VERSION, SCANNER_VERSION, QB_RULES_VERSION
```

## Appendix C. Delegated to the coding agent (ADR required before code)

1. Implementation language and rationale.
2. Ledger style (event log vs mutable + audit) and how invariants in 10.2 are enforced.
3. Price and liquidity floors, with the data-quality argument.
4. Exact model IDs for triage, decision, and critique, with current pricing from provider documentation and an estimate of calls per day under the $10 cap.
5. Whether ADRs/foreign-domiciled names are in the universe.
6. The SIC code list, verified against EDGAR, and the seed denylist (owner reviews before PAPER).
7. Third-party earnings-calendar source and its rate limits.
8. Composite-score formula for the scanner (versioned).
9. Regime classifier thresholds (versioned).
10. Email provider and template set.
11. Definitions of the V1 intraday setups (opening-range breakout, gap continuation) and the default `LATENCY_FLOOR_MIN`, with the argument for why each setup's edge survives the lag.
12. Current regulatory fee schedule (SEC transaction fee, FINRA TAF) and where it is sourced.
13. How the pre-open stop re-arm is sequenced against the Alpaca API's accepted submission window, and what "confirmed active" means in API terms.
14. Focus-set selection and refresh cadence for the early-warning stream under the free-plan symbol cap.
15. The event-driven scanner interface (designed, not built) and exactly what changes when `MARKET_DATA_PLAN` flips.
16. The estimator for incremental gross return attributable to the blind interval (13.6), and how the safety factor is applied.
17. Tick-fetch strategy for forecast resolution under the free plan's rate limit, and the definition of a "usable quote" for the fill model.
18. How the parallel critique portfolio handles position-limit and cash conflicts that the primary never faced.

Each ADR is a short file in `docs/adr/` with context, decision, alternatives, and consequences, matching the format of Appendix A.

## Appendix D. Changelog

### v2.2 → v2.3

Source: final external review pass, plus one owner round.

1. Broker policy enforced at Alpaca (8.10): 1x, no shorting, no options, written at initialization and verified every boot. (D-52)
2. Opening-range breakout split into IEX-assisted and SIP-confirmed variants. (D-53)
3. Simulated fill formula made unambiguous; additional slippage named separately. (D-54)
4. Forecast contract frozen at entry; resolution from 1-minute bars then ticks; `AMBIGUOUS` outcome added. (D-55)
5. `CRITIQUE_SHADOW` (paired plus parallel) and `DETERMINISTIC_EXIT_SHADOW` added, with matching analysis rows. (D-56, D-57)
6. Phase dwell rule declined; trades-per-phase reported instead. (D-58)
7. Data-upgrade trigger changed to capital plus value-of-information. (D-59)
8. Edge cases, deliverables, config keys, and Appendix C extended accordingly.

### v2.1 → v2.2

Source: external review of the data architecture, plus one owner round.

1. Feed tiers formalized (5.6): SIP_DELAYED, IEX_REALTIME early-warning stream on a focus set, SIP_REALTIME behind `MARKET_DATA_PLAN`. (D-49)
2. Signal-drift fields added to the dossier, decision schema, and candidates; drift is the agent's judgment, recorded but never a rejection. (D-48)
3. `candidate_outcomes` table and blind-interval cost analysis added; new hypothesis row on real-time data value. (D-50)
4. Market-data upgrade policy added (13.6) with a capital-based review trigger. (D-51)
5. Edge cases for stream disconnect and focus-set cap; deliverables and Appendix C extended.

### v2 → v2.1

Source: external review of v2, plus two owner rounds.

1. Account model corrected from "cash account" to Alpaca 1x limited-margin account; PDT retirement (2026-06-04) noted; settlement engine (`SETTLEMENT_MODE`, `earliest_sell_date`, good-faith-violation logic) removed; settlement kept as accounting metadata only. Virtual-cash cap made explicitly independent of broker buying power. LIVE boot assertion added. (D-38)
2. Anti-look-ahead invariant added with full decision timeline and database enforcement; DRY_RUN and shadow fills now reconstructed from real SIP data after the 15-minute embargo. (D-39)
3. Horizon changed from "overnight to ~10 days" to per-trade, hours to ~10 days, with config bounds and a latency floor; intraday setups added to the scanner. (D-40)
4. Forecast event defined; calibration scope set to entries. (D-41)
5. Reconciliation changed from adopt-and-email to replay-or-halt. (D-42)
6. Cohort freeze declined; `EXPERIMENT_ID`/`EXPERIMENT_PHASE_ID` added; every change opens a phase; results labeled exploratory. (D-43)
7. Extended-hours exit guard re-anchored to fresh IEX quotes; prior close no longer vetoes. (D-44)
8. Fractional stop re-arm timing tightened; overnight-gap exposure recorded. (D-45)
9. Cost categories and two P&L views added; paper simulator's omission of fees and dividends compensated in both ledgers. (D-46)
10. Analysis plan: continuous Brier, quintile calibration, block bootstrap by week, paper-vs-shadow fill delta. (D-47)
11. Conflict rules 11–12, edge cases for reconstruction resume, lot-without-stop, and the $2,000 crossing added. Deliverables and Appendix C extended accordingly.
