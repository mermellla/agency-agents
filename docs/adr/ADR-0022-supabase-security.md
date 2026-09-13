# ADR-0022 — Supabase database and Storage exposure

**Appendix C item:** — (owner review of Phase 0) · **Status:** Accepted · **Date:** 2026-09-13

## Context
Supabase exposes the `public` schema through its Data API (PostgREST) to the `anon` and `authenticated` roles, and
Storage buckets can be public. Trading state, LLM context blobs and (eventually) the audit trail of a brokerage
account must be reachable only by the server-side worker (§11 "no shared database, environment, or credentials").

## Decision
1. **Dedicated schema, not exposed.** Every trading table, enum and function lives in schema `trading`
   (`20260913000001…`). Nothing is created in `public` (tested: `public` has zero tables). The owner leaves `trading`
   out of the project's Data API "exposed schemas" list; the worker's boot check `SUPABASE_EXPOSURE_CHECK` calls
   `GET /rest/v1/decisions` with the anon key and halts (`SUPABASE_EXPOSURE_HALT`) unless the response is 401/404.
2. **Deny-by-default in the database regardless of exposure** (`20260913000010_security.sql`): `anon`, `authenticated`
   and `public` hold no privileges on the schema, its tables, sequences or functions (including default privileges
   for future objects); RLS is enabled on every table with no policy for those roles.
3. **A dedicated worker role.** `trading_worker` (created NOLOGIN by the migration; the owner grants LOGIN and sets the
   password out of band, stored only as the Railway `DATABASE_URL`) has `USAGE` on the schema, `SELECT/INSERT/UPDATE`
   on tables, and **no DELETE** (ADR-0002 in role form). RLS policies `worker_all` grant it row access. The worker never
   uses the `postgres` superuser or the service-role key for data access; the service-role key is used only by the
   bootstrap job for Storage bucket administration.
4. **Private Storage.** The `decision-context` bucket is created with `public = false`; the boot check reads
   `storage.buckets.public` and halts (`STORAGE_EXPOSURE_HALT`) if it is ever true. Objects are read and written only
   with the worker's server-side credentials; no signed URLs are issued.
5. **Migrations run as the schema owner** (`postgres` via the Supabase CLI), never as `trading_worker`.

## Alternatives considered
- Keep `public` + RLS only: one misconfigured policy or a future `grant` reopens the API; a non-exposed schema fails closed.
- Service-role key in the worker: bypasses RLS entirely and is a credential with project-wide power; a scoped role is
  least-privilege.

## Consequences
- `tests/test_security.py` recreates the Supabase client roles locally and proves: `anon`/`authenticated` cannot select,
  insert or call functions on trading state; `trading_worker` can read but cannot delete; `public` is empty.
- Owner steps before Slice 1: create the project, run migrations, `alter role trading_worker login password '…'`,
  set `DATABASE_URL`, confirm the exposed-schemas list, create the private bucket.
- The boot checks (exposure, bucket privacy) are part of Slice 1's `boot-check` command (T-55).
