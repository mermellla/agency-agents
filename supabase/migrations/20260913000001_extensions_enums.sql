-- 0001: extensions, enums, shared trigger functions.
-- Spec v2.3 §10. Design decisions: ADR-0002 (ledger style), ADR-0019 (versioning), ADR-0020 (mode lockout).
create extension if not exists "pgcrypto";

-- ---------- enums ----------
create type execution_mode as enum ('DRY_RUN', 'PAPER', 'LIVE');

create type portfolio_kind as enum (
  'llm_primary', 'llm_variant', 'quant_shadow',
  'benchmark_cash', 'benchmark_spy', 'benchmark_vti',
  'critique_shadow_paired', 'critique_shadow_parallel', 'deterministic_exit_shadow'
);

create type phase_category as enum ('initial', 'strategy', 'correctness', 'safety');

create type trust_grade as enum ('execution', 'research');

create type source_health as enum ('ok', 'degraded', 'down');

create type feed_tier as enum ('SIP_DELAYED', 'IEX_REALTIME', 'SIP_REALTIME');

create type market_regime as enum (
  'TRENDING_BULL', 'TRENDING_BEAR', 'RANGE_BOUND', 'HIGH_VOLATILITY', 'LOW_VOLATILITY', 'RISK_OFF', 'UNKNOWN'
);

create type decision_type as enum ('BUY', 'SELL', 'ADD', 'REDUCE', 'HOLD', 'NO_ACTION', 'SHORT', 'COVER');

create type decision_origin as enum ('llm', 'quant', 'system', 'owner');

create type decision_kind as enum ('entry', 'daily_review', 'triggered_review', 'system_exit', 'owner_resolution');

create type decision_status as enum ('proposed', 'validated', 'rejected', 'executed', 'no_action');

create type critique_recommendation as enum ('proceed', 'reduce', 'abandon');

create type earnings_plan as enum ('hold_through', 'exit_before', 'n_a');

create type forecast_outcome as enum ('TARGET', 'INVALIDATION', 'TIME_STOP', 'AMBIGUOUS');

create type order_status as enum (
  'PROPOSED', 'VALIDATED', 'PENDING_APPROVAL', 'SUBMITTED', 'PARTIALLY_FILLED',
  'FILLED', 'CANCELLED', 'REJECTED', 'EXPIRED', 'FILL_PENDING_RECONSTRUCTION'
);

create type order_purpose as enum (
  'entry', 'exit', 'reduce', 'protective_stop', 'stop_rearm', 'target', 'bracket_entry',
  'ext_hours_exit', 'software_stop', 'force_close', 'cancel_replace'
);

create type order_side as enum ('buy', 'sell');

create type order_type as enum ('market', 'limit', 'stop', 'stop_limit');

create type time_in_force as enum ('day', 'gtc');

create type fill_source as enum ('broker', 'reconstructed');

create type reconstruction_basis as enum ('ask', 'bid', 'trade_plus_half_spread', 'trade_minus_half_spread');

create type cash_event_kind as enum (
  'initial_equity', 'buy', 'sell', 'fee', 'dividend', 'benchmark_mark', 'reconcile_repair', 'owner_resolution', 'adjustment'
);

create type fee_kind as enum ('sec_section_31', 'finra_taf', 'cat', 'broker_commission', 'other');

create type llm_stage as enum ('triage', 'decision', 'critique', 'daily_review', 'triggered_review');

create type budget_bucket as enum ('entries', 'exits_reviews');

create type halt_scope as enum ('all', 'entries');

create type reconcile_result as enum ('agree', 'repaired', 'halt');

create type broker_policy_result as enum ('match', 'applied', 'halt');

create type shadow_link_kind as enum ('critique_paired', 'critique_parallel', 'deterministic_exit_twin');

create type holding_bucket as enum ('intraday', 'overnight', 'multi_day');

create type position_status as enum ('open', 'closed');

create type universe_status as enum ('eligible', 'excluded_universe', 'excluded_ethical', 'needs_ethical_review', 'excluded_floor');

-- ---------- shared trigger functions ----------

-- §10.2 "No physical deletes on decisions, orders, fills, or cash ledger" (applied more widely, ADR-0002).
create or replace function forbid_delete() returns trigger language plpgsql as $$
begin
  raise exception 'DELETE_FORBIDDEN: physical deletes are not allowed on %', tg_table_name
    using errcode = 'restrict_violation';
end $$;

-- Append-only tables: no updates at all.
create or replace function forbid_update() returns trigger language plpgsql as $$
begin
  raise exception 'UPDATE_FORBIDDEN: % is append-only', tg_table_name
    using errcode = 'restrict_violation';
end $$;

create or replace function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;
