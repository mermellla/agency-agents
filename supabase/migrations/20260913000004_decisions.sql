-- 0004: decisions group — §7.6 schema, §7.3 critique, §7.4 forecast contract, §9 cost ledger, §10.4 context retention.

create table data_snapshots (
  id                  uuid primary key default gen_random_uuid(),
  created_at          timestamptz not null default now(),
  context_hash        text not null,                -- permanent (§10.4)
  extract             jsonb not null,               -- signals seen, evidence lists, sources with grades, headlines
  storage_bucket      text not null default 'decision-context',
  storage_path        text,                         -- compressed blob; pruned after CONTEXT_RETENTION_DAYS
  storage_pruned_at   timestamptz,
  size_bytes          integer,
  token_estimate      integer
);

create table decisions (
  decision_id                 uuid primary key default gen_random_uuid(),
  experiment_id               uuid not null references experiments(id),
  experiment_phase_id         uuid not null references experiment_phases(id),
  portfolio_id                uuid not null references portfolios(id),
  scan_id                     uuid references scans(id),
  candidate_id                uuid references candidates(id),
  data_snapshot_id            uuid references data_snapshots(id),
  position_id                 uuid,                 -- fk added in 0005 (reviews and exits reference an open position)
  origin                      decision_origin not null,
  kind                        decision_kind not null,
  trigger_reason              text,                 -- §7.5 triggered reviews / §4.3 verified triggers
  status                      decision_status not null default 'proposed',
  reject_code                 text,                 -- REJECTED_* codes (§4.4, §5.5, §7.6, §15) or NEEDS_ETHICAL_REVIEW

  -- timeline (all UTC; §7.6, §8.9)
  signal_observed_at          timestamptz,
  scanner_completed_at        timestamptz,
  triage_completed_at         timestamptz,
  decision_completed_at       timestamptz,
  critique_completed_at       timestamptz,
  risk_validation_completed_at timestamptz,
  order_eligible_at           timestamptz,          -- = risk_validation_completed_at (earliest legal fill time)
  order_submitted_at          timestamptz,
  fill_at                     timestamptz,          -- denormalized from the first fill (broker or reconstructed)

  -- signal drift (advisory, D-48)
  sip_signal_price            numeric(14,4),
  sip_signal_timestamp        timestamptz,
  iex_price_at_triage         numeric(14,4),
  iex_quote_age_sec_at_triage integer,
  iex_price_at_decision       numeric(14,4),
  iex_quote_age_sec_at_decision integer,
  iex_price_at_order          numeric(14,4),
  iex_quote_age_sec_at_order  integer,
  signal_to_decision_move_pct numeric(10,6),
  signal_to_order_move_pct    numeric(10,6),
  blind_interval_move_pct     numeric(10,6),        -- post-hoc from SIP
  no_action_reason            text,

  -- the decision
  decision                    decision_type not null,
  ticker                      text references instruments(symbol),
  strategy                    text,
  direction                   text check (direction in ('long', 'short')),
  proposed_notional           numeric(12,2) check (proposed_notional is null or proposed_notional > 0),
  entry_type                  text,
  entry_price_or_range        text,
  target_price                numeric(14,4),
  invalidation_price          numeric(14,4),
  time_stop_at                timestamptz,
  expected_holding_period     interval,
  confidence                  numeric(5,4) check (confidence is null or (confidence >= 0 and confidence <= 1)),

  -- forecast contract (§7.4, D-55) — frozen at entry, immutable (trigger in 0007)
  forecast_event              text not null default 'TARGET_BEFORE_INVALIDATION_OR_TIME_STOP',
  forecast_target_price_at_entry        numeric(14,4),
  forecast_invalidation_price_at_entry  numeric(14,4),
  forecast_time_stop_at_entry           timestamptz,
  forecast_outcome            forecast_outcome,     -- filled post-hoc by the resolution job
  probability_pre_critique    numeric(5,4) check (probability_pre_critique is null or (probability_pre_critique >= 0 and probability_pre_critique <= 1)),
  probability_post_critique   numeric(5,4) check (probability_post_critique is null or (probability_post_critique >= 0 and probability_post_critique <= 1)),
  critique_recommendation     critique_recommendation,
  critique_changed_proposal   boolean not null default false,   -- D-56: spawns CRITIQUE_SHADOW rows
  pre_critique_proposal       jsonb,                -- the proposal before critique, for the paired counterfactual

  expected_upside_percent     numeric(10,6),
  expected_downside_percent   numeric(10,6),
  expected_value              numeric(10,6),
  reward_risk_ratio           numeric(10,6),
  catalyst                    text,
  thesis                      text,
  supporting_evidence         jsonb not null default '[]'::jsonb,
  contradicting_evidence      jsonb not null default '[]'::jsonb,
  earnings_in_window          boolean,
  earnings_plan               earnings_plan,
  earnings_plan_reason        text,
  market_regime               market_regime,
  reason_for_entry            text,
  reason_for_exit_if_existing_position text,
  conditions_to_exit_early    jsonb not null default '[]'::jsonb,
  sources                     jsonb not null default '[]'::jsonb,   -- each with trust grade (§5.3)

  -- versions in force (§10.2: non-null on every decision)
  prompt_version              text not null references prompt_versions(version),
  exclusion_list_version      text not null references exclusion_list_versions(version),
  config_version              text not null references config_versions(version),
  scanner_version             text not null references scanner_versions(version),
  qb_rules_version            text not null references qb_rules_versions(version),
  model_triage                text,
  model_decision              text,
  model_critique              text,
  tokens_in                   integer not null default 0,
  tokens_out                  integer not null default 0,
  tokens_cached               integer not null default 0,
  cost_usd                    numeric(10,6) not null default 0,
  raw_model_output            jsonb,
  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now(),

  -- §8.9 anti-look-ahead: order_eligible_at ≥ signal_observed_at on every decision
  constraint decisions_eligible_after_signal
    check (order_eligible_at is null or signal_observed_at is null or order_eligible_at >= signal_observed_at),
  -- §7.6 order_eligible_at = risk_validation_completed_at
  constraint decisions_eligible_equals_validation
    check (order_eligible_at is null or risk_validation_completed_at is null or order_eligible_at = risk_validation_completed_at),
  -- §7.6 no_action_reason required when decision = NO_ACTION
  constraint decisions_no_action_reason
    check (decision <> 'NO_ACTION' or no_action_reason is not null),
  -- §3.1 SHORT/COVER always rejected in V1
  constraint decisions_short_cover_rejected
    check (decision not in ('SHORT', 'COVER') or (status = 'rejected' and reject_code = 'REJECTED_ACCOUNT_INELIGIBLE')),
  -- §7.6 earnings_plan required when earnings_in_window
  constraint decisions_earnings_plan_required
    check (earnings_in_window is distinct from true or (earnings_plan is not null and earnings_plan <> 'n_a' and earnings_plan_reason is not null)),
  -- rejected decisions carry a code
  constraint decisions_rejected_has_code
    check (status <> 'rejected' or reject_code is not null),
  -- a validated/executed BUY/ADD carries the frozen forecast contract
  constraint decisions_entry_has_forecast
    check (not (decision in ('BUY', 'ADD') and status in ('validated', 'executed'))
           or (forecast_target_price_at_entry is not null and forecast_invalidation_price_at_entry is not null and forecast_time_stop_at_entry is not null
               and probability_pre_critique is not null))
);
create index decisions_portfolio_created on decisions (portfolio_id, created_at desc);
create index decisions_phase on decisions (experiment_phase_id);
create index decisions_candidate on decisions (candidate_id);
create trigger decisions_updated_at before update on decisions for each row execute function set_updated_at();

create table decision_sources (
  id           bigserial primary key,
  decision_id  uuid not null references decisions(decision_id),
  kind         text not null check (kind in ('supporting', 'contradicting', 'dossier', 'critique')),
  source       text not null,
  grade        trust_grade not null,
  claim        text,
  url          text,
  observed_at  timestamptz
);
create index decision_sources_decision on decision_sources (decision_id);

create table llm_calls (
  id                   uuid primary key default gen_random_uuid(),
  experiment_id        uuid not null references experiments(id),
  experiment_phase_id  uuid not null references experiment_phases(id),
  decision_id          uuid references decisions(decision_id),
  stage                llm_stage not null,
  bucket               budget_bucket not null,
  model                text not null,
  prompt_version       text not null references prompt_versions(version),
  tokens_in            integer not null default 0,
  tokens_out           integer not null default 0,
  tokens_cached_read   integer not null default 0,
  tokens_cached_write  integer not null default 0,
  cost_usd             numeric(10,6) not null default 0,
  latency_ms           integer,
  request_hash         text,
  response_hash        text,
  status               text not null check (status in ('ok', 'invalid_output', 'error', 'refused')),
  error                text,
  created_at           timestamptz not null default now()
);
create index llm_calls_created on llm_calls (created_at desc);
create index llm_calls_decision on llm_calls (decision_id);

create trigger decisions_no_delete before delete on decisions for each row execute function forbid_delete();
create trigger decision_sources_no_delete before delete on decision_sources for each row execute function forbid_delete();
create trigger llm_calls_no_delete before delete on llm_calls for each row execute function forbid_delete();
create trigger llm_calls_no_update before update on llm_calls for each row execute function forbid_update();
create trigger data_snapshots_no_delete before delete on data_snapshots for each row execute function forbid_delete();
