-- DEFERRED to S9 (LIVE). Do not apply before the LIVE slice and the spec v2.4 amendment that unlocks LIVE (ADR-0020).
set search_path = trading, public;

-- §11 LIVE day-one approvals (designed; LIVE locked out in this phase, ADR-0020).
create table approvals (
  id           uuid primary key default gen_random_uuid(),
  order_id     uuid not null unique references orders(id),
  token_hash   text not null unique,
  expires_at   timestamptz not null,
  decided_at   timestamptz,
  outcome      text check (outcome in ('approve', 'reject', 'expired')),
  decided_via  text,
  created_at   timestamptz not null default now()
);

create trigger approvals_no_delete before delete on approvals for each row execute function forbid_delete();
