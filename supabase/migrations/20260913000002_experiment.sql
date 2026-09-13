-- 0002: experiment group — §10.1 "Experiment", §13.2 phases, ADR-0019 versioning.

create table experiments (
  id               uuid primary key default gen_random_uuid(),
  name             text not null unique,
  execution_mode   execution_mode not null,
  equity_start_usd numeric(12,2) not null check (equity_start_usd > 0),
  started_on       date,                       -- first session; benchmarks anchor to its close (§12)
  stopped_on       date,
  notes            text,
  created_at       timestamptz not null default now(),
  -- ADR-0020: the database refuses to record a LIVE experiment while LIVE is locked out in this codebase.
  constraint live_locked_out check (execution_mode <> 'LIVE')
);

-- Version registries (§10.1). Content is stored so any phase can be reproduced exactly.
create table prompt_versions (
  version      text primary key,
  content_hash text not null,
  changelog    text not null,
  created_at   timestamptz not null default now()
);
create table config_versions (
  version      text primary key,               -- sha256 of canonical risk_policy.yaml + fees.yaml (ADR-0019)
  content      jsonb not null,
  created_at   timestamptz not null default now()
);
create table scanner_versions (
  version      text primary key,
  content      jsonb not null,
  created_at   timestamptz not null default now()
);
create table qb_rules_versions (
  version      text primary key,
  content      jsonb not null,
  created_at   timestamptz not null default now()
);
create table exclusion_list_versions (
  version      text primary key,
  content_hash text not null,
  content      jsonb not null,
  entry_count  integer not null,
  created_at   timestamptz not null default now()
);

create table experiment_phases (
  id                     uuid primary key default gen_random_uuid(),
  experiment_id          uuid not null references experiments(id),
  seq                    integer not null,
  category               phase_category not null,
  reason                 text not null check (length(trim(reason)) > 0),
  what_changed           jsonb not null default '{}'::jsonb,
  started_at             timestamptz not null default now(),
  ended_at               timestamptz,
  prompt_version         text not null references prompt_versions(version),
  config_version         text not null references config_versions(version),
  scanner_version        text not null references scanner_versions(version),
  qb_rules_version       text not null references qb_rules_versions(version),
  exclusion_list_version text not null references exclusion_list_versions(version),
  model_triage           text not null,
  model_decision         text not null,
  model_critique         text not null,
  migration_version      text not null,
  code_version           text not null,           -- git SHA of the worker build (ADR-0019: a bug fix opens a phase too)
  unique (experiment_id, seq),
  check (ended_at is null or ended_at >= started_at)
);
-- at most one open phase per experiment
create unique index experiment_phases_one_open on experiment_phases (experiment_id) where ended_at is null;

create table portfolios (
  id               uuid primary key default gen_random_uuid(),
  experiment_id    uuid not null references experiments(id),
  kind             portfolio_kind not null,
  name             text not null,
  touches_broker   boolean not null default false,   -- §8.6 item 6: only the primary touches Alpaca
  equity_start_usd numeric(12,2) not null check (equity_start_usd > 0),
  parent_portfolio_id uuid references portfolios(id),
  created_at       timestamptz not null default now(),
  unique (experiment_id, name),
  check (touches_broker = false or kind = 'llm_primary')
);
create unique index portfolios_one_broker_facing on portfolios (experiment_id) where touches_broker;
create unique index portfolios_one_primary on portfolios (experiment_id) where kind = 'llm_primary';

create trigger experiments_no_delete before delete on experiments for each row execute function forbid_delete();
create trigger experiment_phases_no_delete before delete on experiment_phases for each row execute function forbid_delete();
create trigger portfolios_no_delete before delete on portfolios for each row execute function forbid_delete();
create trigger prompt_versions_no_delete before delete on prompt_versions for each row execute function forbid_delete();
create trigger config_versions_no_delete before delete on config_versions for each row execute function forbid_delete();
create trigger scanner_versions_no_delete before delete on scanner_versions for each row execute function forbid_delete();
create trigger qb_rules_versions_no_delete before delete on qb_rules_versions for each row execute function forbid_delete();
create trigger exclusion_list_versions_no_delete before delete on exclusion_list_versions for each row execute function forbid_delete();
