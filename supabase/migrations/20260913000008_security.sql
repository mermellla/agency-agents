-- 0008: Supabase security posture. The worker connects with the service role / direct Postgres credentials held in
-- Railway (§11). Row Level Security is enabled everywhere with no policies, so the anon and authenticated roles see nothing.
do $$
declare t record;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', t.tablename);
  end loop;
end $$;

-- Storage bucket for §10.4 context blobs is created by the bootstrap job (bucket "decision-context", private).
