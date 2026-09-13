# ADR-0004 — Model IDs, pricing, and the $10/month budget

**Appendix C item:** 4 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§7.1 asks for a current small model for triage and a current mid-tier model for decisions and critique, with exact IDs
and pricing confirmed against provider documentation, and an estimate of calls per day under `LLM_MONTHLY_CAP_USD = 10`.

## Verified pricing (platform.claude.com/docs/en/about-claude/pricing, fetched 2026-09-13)
| Model | ID | Input $/MTok | Cache read $/MTok | Output $/MTok |
|---|---|---|---|---|
| Claude Haiku 4.5 | `claude-haiku-4-5` | 1.00 | 0.10 | 5.00 |
| Claude Sonnet 5 | `claude-sonnet-5` | 2.00 (now standard, not introductory) | 0.20 | 10.00 |
| Claude Opus 5 | `claude-opus-5` | 5.00 | 0.50 | 25.00 |

## Decision
- `MODEL_TRIAGE = claude-haiku-4-5` (no extended thinking).
- `MODEL_DECISION = claude-sonnet-5`, `MODEL_CRITIQUE = claude-sonnet-5`, adaptive thinking with
  `output_config.effort = "medium"` for decisions and `"low"` for critique; structured outputs
  (`output_config.format`) for the §7.6 schema, so `REJECTED_INVALID_OUTPUT` is the exception path, not the norm.
- Prompt caching on the stable prefix (§7.7), 1-hour cache for the decision prompt (calls are spaced by scan cadence).
- Batch API is not used (decisions are latency-bound to the scan cycle).

## Budget arithmetic (per trading day, ~21 days/month → $0.476/day)
| Call | Model | Tokens (in uncached / cached / out) | Cost |
|---|---|---|---|
| Triage pass, 25 slim dossiers | Haiku 4.5 | 18k / 2k / 1.5k | $0.026 |
| Decision, one full dossier | Sonnet 5 | 2.5k / 4k / 1.2k | $0.018 |
| Critique | Sonnet 5 | 2k / 1.5k / 0.6k | $0.010 |
| Finalize with critique attached | Sonnet 5 | 1k / 5.5k / 0.6k | $0.009 |
| Daily review, one position | Haiku 4.5 | 2.5k / 1k / 0.3k | $0.004 |
| Triggered review + critique | Sonnet 5 | as decision + critique | $0.028 |

Expected day: 3 triage passes ($0.08) + 3 decision bundles ($0.11) + 5 daily reviews ($0.02) + 2 triggered
reviews ($0.06) ≈ **$0.27/day ≈ $5.7/month**, leaving ~40% headroom for the entry bucket to absorb busy days.
Entry bucket: 60% of the daily allowance (`LLM_ENTRY_BUCKET_PCT = 60`), which funds 3–4 decision bundles per day —
consistent with the spec's expected shape of two to four decision-model entries per day.

## Alternatives considered
- **Opus 5 for decisions and critique**: ≈ 2.5× the decision component → ≈ $0.43/day ≈ $9/month with no headroom;
  a single busy week would exhaust the cap. Recorded as the first candidate for a shadow prompt-variant phase if the
  budget is ever raised.
- **Haiku 4.5 for everything**: ≈ $2/month, but the experiment is about mid-tier judgment; not chosen.
- **Fable 5.1**: $10/$50 per MTok; out of budget by an order of magnitude.

## Consequences
- Model IDs live in `config/risk_policy.yaml` (`budget.*`) and are recorded on every phase and decision; changing one
  opens a phase.
- The budget ledger (§9) will confirm or refute the arithmetic in the first DRY_RUN week; the numbers above are the
  pre-registered expectation.
- Cost per call is computed from `usage` fields returned by the API (input, cache read, cache write, output) using
  the rates in this ADR, stored in `llm_calls`.
