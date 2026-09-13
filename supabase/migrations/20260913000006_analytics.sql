-- 0006: analytics group — §10.1 "Analytics", §7.4 forecast resolution, §12 shadow links, §13.3 reports.
set search_path = trading, public;

create table forecast_resolutions (
  id                     uuid primary key default gen_random_uuid(),
  decision_id            uuid not null unique references decisions(decision_id),
  position_id            uuid references positions(id),
  resolved_at            timestamptz not null default now(),
  resolution_start_at    timestamptz not null,    -- = fill_at (§8.9)
  target_at_entry        numeric(14,4) not null,  -- copied from the frozen contract; trigger in 0007 enforces equality
  invalidation_at_entry  numeric(14,4) not null,
  time_stop_at_entry     timestamptz not null,
  method                 text not null check (method in ('bars_1m', 'ticks', 'time_stop')),
  outcome                forecast_outcome not null,
  touched_at             timestamptz,
  touch_price            numeric(14,6),
  bars_examined          integer not null default 0,
  ticks_fetched          boolean not null default false,
  ambiguous_reason       text,
  check (outcome <> 'AMBIGUOUS' or ambiguous_reason is not null),
  check (outcome = 'AMBIGUOUS' or outcome = 'TIME_STOP' or touched_at is not null),
  check (touched_at is null or touched_at >= resolution_start_at)
);

create table closed_trades (
  id                           uuid primary key default gen_random_uuid(),
  position_id                  uuid not null unique references positions(id),
  portfolio_id                 uuid not null references portfolios(id),
  experiment_id                uuid not null references experiments(id),
  experiment_phase_id          uuid not null references experiment_phases(id),   -- phase at open
  symbol                       text not null,
  strategy                     text,
  market_regime                market_regime,
  catalyst_type                text,
  entry_decision_id            uuid not null references decisions(decision_id),
  entry_fill_at                timestamptz not null,
  exit_fill_at                 timestamptz not null,
  holding_seconds              integer not null check (holding_seconds >= 0),
  holding_bucket               holding_bucket not null,
  qty                          numeric(18,9) not null,
  entry_price                  numeric(14,6) not null,
  exit_price                   numeric(14,6) not null,
  gross_pnl_usd                numeric(14,6) not null,
  gross_return_pct             numeric(10,6) not null,
  net_return_pct               numeric(10,6) not null,       -- trading P&L: fills and fees only (§9)
  mfe_pct                      numeric(10,6),                -- from bars after fill_at (§8.9)
  mae_pct                      numeric(10,6),
  initial_confidence           numeric(5,4),
  probability_pre_critique     numeric(5,4),
  probability_post_critique    numeric(5,4),
  expected_upside_percent      numeric(10,6),
  expected_downside_percent    numeric(10,6),
  forecast_outcome             forecast_outcome,
  forecast_resolution_id       uuid references forecast_resolutions(id),
  target_reached               boolean,
  invalidation_reached         boolean,
  discretionary_exit           boolean not null,
  exit_reason                  text not null,                -- stop | target | time_stop | discretionary_sell | reduce_to_flat | ext_hours_exit | software_stop | force_close | delisting
  earnings_plan                earnings_plan,
  earnings_outcome             text,
  overnight_gap_exposure       boolean not null,             -- §8.3
  gap_slippage_past_invalidation_pct numeric(10,6),
  benchmark_spy_return_pct     numeric(10,6),                -- over [fill_at, exit_fill_at] (§8.9)
  benchmark_vti_return_pct     numeric(10,6),
  llm_cost_usd                 numeric(10,6) not null default 0,
  regulatory_fees_usd          numeric(10,6) not null default 0,
  broker_fees_usd              numeric(10,6) not null default 0,
  market_data_cost_usd         numeric(10,6) not null default 0,
  incremental_infrastructure_cost_usd numeric(10,6) not null default 0,
  net_return_after_computational_costs_pct numeric(10,6) not null,
  source_grades_used           text[] not null default '{}',
  research_grade_cited         boolean not null default false,
  news_cited                   boolean not null default false,
  post_exit_return_1s_pct      numeric(10,6),
  post_exit_return_5s_pct      numeric(10,6),
  time_in_loss_seconds         integer,
  prompt_version               text not null references prompt_versions(version),
  exclusion_list_version       text not null references exclusion_list_versions(version),
  config_version               text not null references config_versions(version),
  scanner_version              text not null references scanner_versions(version),
  qb_rules_version             text not null references qb_rules_versions(version),
  created_at                   timestamptz not null default now(),
  check (exit_fill_at >= entry_fill_at)
);
create index closed_trades_portfolio on closed_trades (portfolio_id, exit_fill_at);
create index closed_trades_phase on closed_trades (experiment_phase_id);

-- §12 D-56/D-57 counterfactual shadows are linked to the primary decision/position they mirror.
create table shadow_links (
  id                    uuid primary key default gen_random_uuid(),
  kind                  shadow_link_kind not null,
  primary_decision_id   uuid not null references decisions(decision_id),
  primary_position_id   uuid references positions(id),
  shadow_portfolio_id   uuid not null references portfolios(id),
  shadow_decision_id    uuid references decisions(decision_id),
  shadow_position_id    uuid references positions(id),
  limit_breach          jsonb,                    -- paired view may exceed limits; logged not blocked (§12)
  created_at            timestamptz not null default now(),
  unique (kind, primary_decision_id)
);

-- Email / notification audit (§11).
create table notifications (
  id          bigserial primary key,
  at          timestamptz not null default now(),
  kind        text not null,                      -- daily_digest | halt | reconcile | budget | approval | broker_policy | stop_incident
  recipient   text not null,
  subject     text not null,
  provider_message_id text,
  status      text not null check (status in ('sent', 'failed')),
  payload     jsonb
);

create trigger forecast_resolutions_no_delete before delete on forecast_resolutions for each row execute function forbid_delete();
create trigger forecast_resolutions_no_update before update on forecast_resolutions for each row execute function forbid_update();
create trigger closed_trades_no_delete before delete on closed_trades for each row execute function forbid_delete();
create trigger shadow_links_no_delete before delete on shadow_links for each row execute function forbid_delete();
