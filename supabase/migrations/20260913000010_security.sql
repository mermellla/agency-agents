-- 0010: Supabase security posture (ADR-0022).
-- 1. All trading state is in schema `trading`, which is NOT listed among the Data API "exposed schemas" (owner step,
--    verified at boot by SUPABASE_EXPOSURE_CHECK). PostgREST cannot reach it regardless of policies.
-- 2. Defence in depth: the Supabase client roles hold no privileges on the schema, and RLS is enabled deny-by-default
--    on every table, so even a mis-exposed schema returns nothing.
-- 3. The worker connects as `trading_worker`, a dedicated role with privileges on schema `trading` only. The role is
--    created NOLOGIN here; the owner grants LOGIN and sets the password out of band (Railway secret DATABASE_URL).
set search_path = trading, public;

do $$
declare t record;
begin
  for t in select tablename from pg_tables where schemaname = 'trading' loop
    execute format('alter table trading.%I enable row level security', t.tablename);
  end loop;
end $$;

do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on schema trading from %I', r);
      execute format('revoke all on all tables in schema trading from %I', r);
      execute format('revoke all on all sequences in schema trading from %I', r);
      execute format('revoke all on all functions in schema trading from %I', r);
      execute format('alter default privileges in schema trading revoke all on tables from %I', r);
      execute format('alter default privileges in schema trading revoke all on sequences from %I', r);
      execute format('alter default privileges in schema trading revoke all on functions from %I', r);
    end if;
  end loop;
end $$;
revoke all on schema trading from public;
alter default privileges in schema trading revoke all on tables from public;
alter default privileges in schema trading revoke all on functions from public;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'trading_worker') then
    create role trading_worker nologin;
  end if;
end $$;
grant usage on schema trading to trading_worker;
grant select, insert, update on all tables in schema trading to trading_worker;   -- no DELETE, ever (ADR-0002)
grant usage, select on all sequences in schema trading to trading_worker;
grant execute on all functions in schema trading to trading_worker;
alter default privileges in schema trading grant select, insert, update on tables to trading_worker;
alter default privileges in schema trading grant usage, select on sequences to trading_worker;
alter default privileges in schema trading grant execute on functions to trading_worker;
-- RLS applies to trading_worker too (it is not a superuser and does not own the tables): policies grant it full access.
do $$
declare t record;
begin
  for t in select tablename from pg_tables where schemaname = 'trading' loop
    if not exists (select 1 from pg_policies where schemaname = 'trading' and tablename = t.tablename and policyname = 'worker_all') then
      execute format('create policy worker_all on trading.%I for all to trading_worker using (true) with check (true)', t.tablename);
    end if;
  end loop;
end $$;

-- Storage: bucket "decision-context" is created PRIVATE by the bootstrap job (storage.buckets.public = false) and the
-- boot check halts (STORAGE_EXPOSURE_HALT) if it is ever public. Not expressible here because storage.* is Supabase-managed.
