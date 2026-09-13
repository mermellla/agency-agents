# ADR-0010 — Email provider and template set

**Appendix C item:** 10 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§11 requires an email channel (Gmail or a transactional provider) for the daily digest, halt/reconciliation alerts,
budget alerts, and (LIVE only) signed approval links. Secrets live in Railway only.

## Decision
- **Resend** (transactional API; free tier 3,000 emails/month, 100/day; one API key `RESEND_API_KEY`; sending domain
  verified by the owner). Rationale: no OAuth or app-password lifecycle, delivery webhooks, plain HTTPS from the worker.
- **Fallback:** Gmail SMTP with an app password (`SMTP_*` env vars) if the owner prefers not to verify a domain.
- Every message is also written to the `notifications` table (subject, kind, provider message id, status), so a
  provider outage never loses an alert and the digest can be regenerated.

Template set (plain text + HTML, subject prefixed `[TA:<MODE>:<phase seq>]`):
| Kind | Trigger | Contents |
|---|---|---|
| `daily_digest` | after the close | equity, cash, open positions with stops/time stops, decisions and rejections by code, budget spent, shadows summary, halts |
| `halt` | any §8.7 halt | code, scope, detail, what the owner must do |
| `reconcile_repair` / `reconcile_halt` | §8.6 | events replayed, diff, resolution instructions |
| `broker_policy_applied` / `broker_policy_halt` | §8.10 | observed vs expected configuration |
| `budget_alert` | 80% of a bucket, exhaustion, `BUDGET_OVERAGE_EXIT` | bucket state |
| `stop_incident` | lot without a live stop after the open (§8.3) | lot, software-stop action taken |
| `phase_opened` | ADR-0019 | reason, category, versions before/after |
| `live_approval` | LIVE day one only (§11) | signed approve/reject links bound to `order_id`, expiry |

## Alternatives considered
- Gmail API with OAuth: token refresh and consent-screen maintenance for a single-recipient system.
- Slack/Discord: rejected by D-19.

## Consequences
- `RESEND_API_KEY` (or SMTP credentials) is a Railway secret set by the owner; the coding agent never sees it.
- Reply-to-approve is not implemented (§11); approvals only via the signed Vercel link.
