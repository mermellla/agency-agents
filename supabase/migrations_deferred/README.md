# Deferred migrations

Migrations that have no consumer before the slice named in their header. They are **not** applied by the
Supabase CLI (it only reads `supabase/migrations/`). Move a file into `supabase/migrations/` with a fresh timestamp when
its slice begins; that opens a new experiment phase (§10.5).

| File | First consumer | Why deferred |
|---|---|---|
| `live_approvals.sql` | S9 (LIVE, gated) | LIVE is locked out (ADR-0020); a table for signed approval links has no writer or reader until the LIVE slice |
| `analytics_reports.sql` (`cost_periods`, `analysis_reports`) | S7 | Not named in §10.1; written only by the report job and the period-cost bookkeeping, both S7 (schema-rent audit, `docs/phase0/09-owner-decisions.md` §D) |
