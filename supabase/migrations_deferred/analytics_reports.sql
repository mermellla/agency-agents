-- DEFERRED to S7 (analytics). Apply when the pre-registered report job is built; opens a phase (§10.5).
set search_path = trading, public;

-- §9 cost categories per period (LLM per call lives in llm_calls; these are the period-level costs).
create table cost_periods (
  id            bigserial primary key,
  experiment_id uuid not null references experiments(id),
  period_start  date not null,
  period_end    date not null,
  category      text not null check (category in ('llm_cost', 'market_data_cost', 'incremental_infrastructure_cost', 'broker_fees', 'regulatory_fees')),
  amount_usd    numeric(12,6) not null,
  source        text not null,
  created_at    timestamptz not null default now(),
  unique (experiment_id, period_start, period_end, category),
  check (period_end >= period_start)
);

-- §13.3 pre-registered analysis reports, produced on demand.
create table analysis_reports (
  id                 uuid primary key default gen_random_uuid(),
  experiment_id      uuid not null references experiments(id),
  generated_at       timestamptz not null default now(),
  closed_trade_count integer not null,
  code_version       text not null,
  report             jsonb not null
);

create trigger cost_periods_no_delete before delete on cost_periods for each row execute function forbid_delete();
create trigger analysis_reports_no_delete before delete on analysis_reports for each row execute function forbid_delete();
