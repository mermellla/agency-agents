# ADR-0023 — Jev (TypeSafe System One) as the judgment engine for structured model calls

**Appendix C item:** — (owner direction 2026-09-17: "use Jev as much as possible; alpha, no token costs") · **Status:** Accepted · **Date:** 2026-09-17

## Context
Spec v2.3 fixes a two-tier LLM pipeline (§7.1) and forbids LLM tokens in the scanner (§6). The owner is alpha-testing
TypeSafe's Jev, a System One model that returns **typed, calibrated judgments** (Choice, Score, Noul with probability
distributions) instead of generated text, at zero token cost during the alpha, and has directed that development use it
as much as possible. Several places in the design need a *judgment* rather than a generated decision, and today they
are either keyword rules (news catalyst detection, trigger qualification) or a generative model doing classification
work (triage ranking, catalyst tagging).

## Decision
Jev is the engine for every **structured judgment** in the system; Claude remains the engine for every **generated
decision** (the §7.6 decision, the critique, the forecast probabilities that calibration is scored on).

| Stage | Judgment | Primitive | Where it lands |
|---|---|---|---|
| Scanner `catalyst` signal (§6.2 "significant news", 8-K materiality) | Is this headline/filing a material catalyst for the symbol; direction; type | Noul + Choice | `candidates.signals.catalyst_*`, answers stored verbatim (probabilities), keyword rule as fallback when Jev is down |
| Triage (§7.1 step 2) | Rank the slim dossiers: setup quality, catalyst strength, risk of chasing | Score per candidate; code ranks | replaces `claude-haiku-4-5` for triage; `MODEL_TRIAGE = jev-latest` |
| Triggered-review gate (§7.5) | Does this news/filing plausibly invalidate the open thesis? | Noul | gates a Sonnet 5 triggered review; probability stored with the review |
| Dossier assembly (§7.2) | Headline relevance to the symbol/thesis | Noul | selects `DOSSIER_MAX_HEADLINES` |
| Analytics tagging (§13.3) | Catalyst type, earnings-plan outcome labels | Choice | `closed_trades.catalyst_type`, `earnings_outcome` |

Not Jev: the decision and critique calls (§7.3, §7.4 calibration is pre- vs post-critique on the same generative
forecast), the ethical screen (§4.4: the model is never the enforcement layer), anything execution-grade (§5.3), and
the composite score arithmetic (§6: deterministic). Jev answers are **research-grade context** stored as data; the
scanner's rule that "no LLM tokens" are spent is preserved in cost terms (alpha: $0) and in kind (no generated text),
and every Jev answer is persisted with its probabilities so a scan is reproducible from the stored signals.

Accounting: every Jev request is an `llm_calls` row with `model = jev-latest`, `stage`, token usage from the response,
and `cost_usd = 0` during the alpha (`budget.jev_cost_per_1k_tokens_usd = 0`, a config key so a price can be set later
and opens a phase). Jev is a `research`-grade source in the registry with its own health check; when it is down the
consumers fall back to their deterministic rule and mark `signal_unavailable_reason = jev_down`.

Question design follows the TypeSafe skill: state as named JSON fields; instructions carry the judgment; criteria define
the answer space including a no-match option; independent questions over the same state are asked together.

## Alternatives considered
- Keep keyword rules for catalysts and Haiku for triage (the Phase 0 design): cheaper to reason about, but the owner's
  alpha access makes a calibrated judgment model free, and the triage/catalyst steps are exactly classification.
- Use Jev for the decision itself: a Choice over BUY/NO_ACTION cannot produce the §7.6 record (levels, thesis, evidence)
  or the immutable forecast contract; rejected.

## Consequences
- Spec amendment A-10 (§6.2 catalyst signal source, §7.1 triage model, §7.5 trigger gate). `MODEL_TRIAGE` changes to
  `jev-latest`; ADR-0004's budget arithmetic drops the Haiku line ($0.026/day per triage pass) while alpha lasts.
- `tradeagent/adapters/typesafe/jev.py` implements `JudgmentEngine`; tests run against a mocked transport; live calls
  require `TYPESAFE_API_KEY` (a Railway secret; absent in the development environment on 2026-09-17).
- If the alpha ends or pricing appears, the config key carries the price and the budget desk enforces it like any other
  model; no code change.
