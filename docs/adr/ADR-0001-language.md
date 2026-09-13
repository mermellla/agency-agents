# ADR-0001 — Implementation language and runtime

**Appendix C item:** 1 · **Status:** Accepted · **Date:** 2026-09-13

## Context
The system is one long-running worker (Railway) that scans, calls an LLM, validates, executes against Alpaca,
reconciles, and produces a statistical report (§13.3: block bootstrap, calibration by quintile). The spec leaves
the language to the coding agent (D-16, "likely Python").

## Decision
- **Python 3.11+** (Railway's default Python image; 3.12 preferred when available), `asyncio` throughout.
- Libraries: `pydantic` v2 (typed models, strict JSON parsing of LLM output), `psycopg` 3 (Postgres), `httpx`
  (Alpaca/EDGAR/Finnhub REST; explicit control of `feed`/`end` parameters), `websockets` (Alpaca streams),
  the official `anthropic` SDK (structured outputs, prompt caching), `pyyaml`, `structlog`; `numpy`/`pandas`/`scipy`
  for the §13.3 analysis job only. In-process scheduler is hand-written on the Alpaca calendar (§11) rather than a
  cron library, because the schedule is calendar-driven (half-days, holidays) and must be restart-safe.
- Tooling: `uv` for locking, `ruff`, `mypy --strict`, `pytest`. Source layout `src/tradeagent/`.
- The Vercel approval endpoint (LIVE-only, §11) will be a single Vercel Python function so the project is one language.

## Alternatives considered
- **TypeScript**: natural fit for Vercel and Supabase client libraries, but the statistical analysis (bootstrap,
  calibration) and Alpaca's first-party SDK are Python-native; two languages would double the model definitions.
- **Go**: strong for a restart-safe worker, weak for analysis and LLM tooling.

## Consequences
- One set of typed models (`src/tradeagent/domain/models.py`) is shared by worker, jobs, and tests.
- The database remains the source of truth and the enforcement point for invariants (ADR-0002), so language choice
  does not affect §10.2 guarantees.
- `alpaca-py` is not a dependency: raw REST keeps `feed=sip`/`end` embargo handling explicit and testable.
