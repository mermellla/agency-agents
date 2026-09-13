# ADR-0007 — Earnings-calendar source and its rate limits

**Appendix C item:** 7 · **Status:** Accepted · **Date:** 2026-09-13

## Context
§5.2 lists a third-party free tier (e.g. Finnhub) as the primary earnings-calendar source with EDGAR 8-K Item 2.02
detection as the fallback. §7.2 needs "earnings date and whether it falls inside the expected holding window".

## Decision
- **Primary: Finnhub `GET /api/v1/calendar/earnings?from=&to=`**, free tier: **60 calls/minute** (documented; a
  30 calls/second burst cap is also reported). One un-filtered date-range call per pre-open pass covers the next 15
  trading days for the whole market, so the daily budget is a handful of calls; per-symbol calls are only made for
  candidates missing from the range response. Grade: **research** (§5.3) — it informs the dossier and the
  `earnings_in_window` flag; it never gates execution.
- **Fallback (execution-grade, post-event only):** EDGAR full-text search / submissions for 8-K Item 2.02 filed in the
  last 24 hours → `earnings_surprise` signal and a qualifying-news trigger (§7.5).
- Failover: if Finnhub is down or rate-limited, `earnings_proximity` is marked `signal_unavailable_reason =
  earnings_calendar_down` and `earnings_in_window` is set to `unknown` in the dossier; the prompt instructs the agent to
  treat unknown as a reason for caution, and the risk desk requires an `earnings_plan` only when the flag is true.

## Alternatives considered
- Alpha Vantage / FMP free tiers: lower daily quotas (25/day for Alpha Vantage), unsuitable for a 2,000-name universe.
- EDGAR-only: no forward calendar exists in EDGAR; only post-event detection is possible.

## Consequences
- A Finnhub API key becomes a Railway secret (`FINNHUB_API_KEY`), set by the owner.
- The free tier's actual entitlement for the earnings endpoint must be confirmed with a real key in Slice 2
  (verification item V-11 in `docs/phase0/08-api-verification-log.md`).
