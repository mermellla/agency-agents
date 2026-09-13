-- 0007: cross-table invariants enforced by triggers — §10.2, §8.4, §8.9, §7.4 (D-55).

-- (a) §8.4 order state machine: validated transitions, every transition logged with a reason.
create or replace function order_transition_allowed(from_s order_status, to_s order_status) returns boolean
language sql immutable as $$
  select (from_s, to_s) in (
    ('PROPOSED', 'VALIDATED'), ('PROPOSED', 'REJECTED'),
    ('VALIDATED', 'PENDING_APPROVAL'), ('VALIDATED', 'SUBMITTED'), ('VALIDATED', 'FILL_PENDING_RECONSTRUCTION'), ('VALIDATED', 'REJECTED'),
    ('PENDING_APPROVAL', 'SUBMITTED'), ('PENDING_APPROVAL', 'EXPIRED'), ('PENDING_APPROVAL', 'CANCELLED'),
    ('SUBMITTED', 'PARTIALLY_FILLED'), ('SUBMITTED', 'FILLED'), ('SUBMITTED', 'CANCELLED'), ('SUBMITTED', 'REJECTED'), ('SUBMITTED', 'EXPIRED'),
    ('PARTIALLY_FILLED', 'FILLED'), ('PARTIALLY_FILLED', 'CANCELLED'), ('PARTIALLY_FILLED', 'EXPIRED'),
    ('FILL_PENDING_RECONSTRUCTION', 'FILLED'), ('FILL_PENDING_RECONSTRUCTION', 'EXPIRED'), ('FILL_PENDING_RECONSTRUCTION', 'CANCELLED')
  )
$$;

create or replace function orders_validate_transition() returns trigger language plpgsql as $$
begin
  if new.status is distinct from old.status then
    if not order_transition_allowed(old.status, new.status) then
      raise exception 'ORDER_STATE_VIOLATION: % -> % is not a legal transition (order %)', old.status, new.status, old.id
        using errcode = 'check_violation';
    end if;
    if new.status_reason is null or length(trim(new.status_reason)) = 0 then
      raise exception 'ORDER_STATE_VIOLATION: a status transition requires status_reason (order %)', old.id
        using errcode = 'check_violation';
    end if;
    if new.status = 'PENDING_APPROVAL' then
      raise exception 'LIVE_LOCKED_OUT: PENDING_APPROVAL is a LIVE-only state and LIVE is not enabled in this codebase (ADR-0020)'
        using errcode = 'check_violation';
    end if;
    insert into order_events (order_id, from_status, to_status, at, reason)
      values (old.id, old.status, new.status, now(), new.status_reason);
  end if;
  -- immutable identity fields
  if new.client_order_id <> old.client_order_id or new.decision_id <> old.decision_id or new.order_eligible_at <> old.order_eligible_at
     or new.symbol <> old.symbol or new.side <> old.side or new.is_simulated <> old.is_simulated then
    raise exception 'ORDER_IMMUTABLE_FIELD: client_order_id, decision_id, order_eligible_at, symbol, side, is_simulated cannot change (order %)', old.id
      using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger orders_validate_transition before update on orders for each row execute function orders_validate_transition();

-- (b) An order may only be created against a validated/executed decision whose timeline is complete, and it
--     inherits order_eligible_at from that decision (§7.6, §8.9). No order may differ from its decision's ticker/side (§2).
create or replace function orders_check_decision() returns trigger language plpgsql as $$
declare d decisions%rowtype;
begin
  select * into d from decisions where decision_id = new.decision_id;
  if not found then
    raise exception 'ORDER_WITHOUT_DECISION' using errcode = 'foreign_key_violation';
  end if;
  if d.status not in ('validated', 'executed') then
    raise exception 'ORDER_FOR_UNVALIDATED_DECISION: decision % has status %', d.decision_id, d.status using errcode = 'check_violation';
  end if;
  if d.order_eligible_at is null then
    raise exception 'ORDER_WITHOUT_ELIGIBILITY: decision % has no order_eligible_at', d.decision_id using errcode = 'check_violation';
  end if;
  if new.order_eligible_at <> d.order_eligible_at then
    raise exception 'ORDER_ELIGIBILITY_MISMATCH: order.order_eligible_at must equal decision.order_eligible_at' using errcode = 'check_violation';
  end if;
  if d.ticker is not null and new.symbol <> d.ticker then
    raise exception 'ORDER_DIFFERS_FROM_DECISION: symbol % vs decision ticker %', new.symbol, d.ticker using errcode = 'check_violation';
  end if;
  if new.portfolio_id <> d.portfolio_id then
    raise exception 'ORDER_DIFFERS_FROM_DECISION: portfolio mismatch' using errcode = 'check_violation';
  end if;
  if d.decision in ('SHORT', 'COVER') or new.side = 'sell' and new.purpose = 'entry' then
    raise exception 'SHORT_SELLING_FORBIDDEN' using errcode = 'check_violation';
  end if;
  if new.status <> 'PROPOSED' then
    raise exception 'ORDER_MUST_START_PROPOSED' using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger orders_check_decision before insert on orders for each row execute function orders_check_decision();

-- (c) §8.9 anti-look-ahead: no simulated fill before order_eligible_at. Broker fills earlier than eligibility are
--     flagged (clock skew) rather than rejected, because the broker is authoritative about what happened (§3.2).
create or replace function fills_check_eligibility() returns trigger language plpgsql as $$
declare o orders%rowtype;
begin
  select * into o from orders where id = new.order_id;
  if not found then
    raise exception 'FILL_WITHOUT_ORDER' using errcode = 'foreign_key_violation';
  end if;
  if new.fill_at < o.order_eligible_at then
    if new.fill_source = 'reconstructed' then
      raise exception 'ANTI_LOOK_AHEAD_VIOLATION: simulated fill_at % precedes order_eligible_at % (order %)', new.fill_at, o.order_eligible_at, o.id
        using errcode = 'check_violation';
    else
      new.eligibility_anomaly := true;
    end if;
  end if;
  if new.fill_source = 'reconstructed' and not o.is_simulated then
    raise exception 'FILL_SOURCE_MISMATCH: reconstructed fill on a broker order' using errcode = 'check_violation';
  end if;
  if new.fill_source = 'broker' and o.is_simulated then
    raise exception 'FILL_SOURCE_MISMATCH: broker fill on a simulated order' using errcode = 'check_violation';
  end if;
  if o.status not in ('SUBMITTED', 'PARTIALLY_FILLED', 'FILL_PENDING_RECONSTRUCTION', 'FILLED') then
    raise exception 'FILL_ON_INACTIVE_ORDER: order % is %', o.id, o.status using errcode = 'check_violation';
  end if;
  if new.symbol <> o.symbol or new.side <> o.side or new.portfolio_id <> o.portfolio_id then
    raise exception 'FILL_DIFFERS_FROM_ORDER' using errcode = 'check_violation';
  end if;
  -- §12: never add a half-spread to an ask or bid fill
  if new.reconstruction_basis in ('ask', 'bid') and coalesce(new.half_spread_estimate, 0) <> 0 then
    raise exception 'SPREAD_DOUBLE_COUNT: half_spread_estimate must be null/0 on an ask/bid fill' using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger fills_check_eligibility before insert on fills for each row execute function fills_check_eligibility();
create trigger fills_no_update before update on fills for each row execute function forbid_update();

-- (d) §7.4 / D-55 forecast contract immutability: frozen columns can be set once, never changed.
--     Timeline columns fill monotonically (set once, never overwritten with a different value).
create or replace function decisions_immutable_columns() returns trigger language plpgsql as $$
begin
  if old.forecast_target_price_at_entry is not null and new.forecast_target_price_at_entry is distinct from old.forecast_target_price_at_entry
     or old.forecast_invalidation_price_at_entry is not null and new.forecast_invalidation_price_at_entry is distinct from old.forecast_invalidation_price_at_entry
     or old.forecast_time_stop_at_entry is not null and new.forecast_time_stop_at_entry is distinct from old.forecast_time_stop_at_entry
     or new.forecast_event is distinct from old.forecast_event then
    raise exception 'FORECAST_CONTRACT_IMMUTABLE: forecast_*_at_entry cannot change after entry (decision %)', old.decision_id
      using errcode = 'check_violation';
  end if;
  if old.probability_pre_critique is not null and new.probability_pre_critique is distinct from old.probability_pre_critique
     or old.probability_post_critique is not null and new.probability_post_critique is distinct from old.probability_post_critique then
    raise exception 'FORECAST_PROBABILITY_IMMUTABLE (decision %)', old.decision_id using errcode = 'check_violation';
  end if;
  if old.forecast_outcome is not null and new.forecast_outcome is distinct from old.forecast_outcome then
    raise exception 'FORECAST_OUTCOME_IMMUTABLE (decision %)', old.decision_id using errcode = 'check_violation';
  end if;
  if (old.signal_observed_at is not null and new.signal_observed_at is distinct from old.signal_observed_at)
     or (old.order_eligible_at is not null and new.order_eligible_at is distinct from old.order_eligible_at)
     or (old.risk_validation_completed_at is not null and new.risk_validation_completed_at is distinct from old.risk_validation_completed_at)
     or (old.decision_completed_at is not null and new.decision_completed_at is distinct from old.decision_completed_at)
     or (old.critique_completed_at is not null and new.critique_completed_at is distinct from old.critique_completed_at) then
    raise exception 'TIMELINE_IMMUTABLE: decision timeline timestamps are set once (decision %)', old.decision_id
      using errcode = 'check_violation';
  end if;
  if new.experiment_id <> old.experiment_id or new.experiment_phase_id <> old.experiment_phase_id or new.portfolio_id <> old.portfolio_id
     or new.prompt_version <> old.prompt_version or new.config_version <> old.config_version or new.scanner_version <> old.scanner_version
     or new.qb_rules_version <> old.qb_rules_version or new.exclusion_list_version <> old.exclusion_list_version then
    raise exception 'DECISION_PROVENANCE_IMMUTABLE (decision %)', old.decision_id using errcode = 'check_violation';
  end if;
  if new.status <> old.status and old.status in ('rejected', 'executed', 'no_action') then
    raise exception 'DECISION_TERMINAL: % is terminal (decision %)', old.status, old.decision_id using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger decisions_immutable_columns before update on decisions for each row execute function decisions_immutable_columns();

-- (e) A decision must belong to the open phase of its experiment at insert time and the phase's versions must match
--     what the decision records (§13.2: nothing changes silently).
create or replace function decisions_check_phase() returns trigger language plpgsql as $$
declare p experiment_phases%rowtype;
begin
  select * into p from experiment_phases where id = new.experiment_phase_id;
  if not found or p.experiment_id <> new.experiment_id then
    raise exception 'PHASE_MISMATCH: phase does not belong to experiment' using errcode = 'check_violation';
  end if;
  if p.ended_at is not null then
    raise exception 'PHASE_CLOSED: decisions cannot be recorded against a closed phase' using errcode = 'check_violation';
  end if;
  if new.prompt_version <> p.prompt_version or new.config_version <> p.config_version or new.scanner_version <> p.scanner_version
     or new.qb_rules_version <> p.qb_rules_version or new.exclusion_list_version <> p.exclusion_list_version then
    raise exception 'VERSION_DRIFT: decision versions differ from the versions in force for phase % (open a new phase, ADR-0019)', p.id
      using errcode = 'check_violation';
  end if;
  if not exists (select 1 from portfolios where id = new.portfolio_id and experiment_id = new.experiment_id) then
    raise exception 'PORTFOLIO_MISMATCH' using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger decisions_check_phase before insert on decisions for each row execute function decisions_check_phase();

-- (f) Forecast resolutions must resolve against the frozen entry contract, starting at fill_at (§7.4, §8.9).
create or replace function forecast_resolutions_check_contract() returns trigger language plpgsql as $$
declare d decisions%rowtype;
begin
  select * into d from decisions where decision_id = new.decision_id;
  if d.forecast_target_price_at_entry is null then
    raise exception 'FORECAST_NOT_FROZEN: decision % has no entry contract', new.decision_id using errcode = 'check_violation';
  end if;
  if new.target_at_entry <> d.forecast_target_price_at_entry
     or new.invalidation_at_entry <> d.forecast_invalidation_price_at_entry
     or new.time_stop_at_entry <> d.forecast_time_stop_at_entry then
    raise exception 'FORECAST_RESOLVED_AGAINST_REVISED_LEVELS: resolution must use forecast_*_at_entry (decision %)', d.decision_id
      using errcode = 'check_violation';
  end if;
  if d.fill_at is null or new.resolution_start_at <> d.fill_at then
    raise exception 'FORECAST_RESOLUTION_START: resolution_start_at must equal the decision fill_at (§8.9)' using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger forecast_resolutions_check_contract before insert on forecast_resolutions for each row execute function forecast_resolutions_check_contract();

-- (g) Shadow portfolios never touch the broker: broker fills are only legal on the broker-facing portfolio.
create or replace function fills_check_broker_portfolio() returns trigger language plpgsql as $$
begin
  if new.fill_source = 'broker' and not exists (select 1 from portfolios where id = new.portfolio_id and touches_broker) then
    raise exception 'SHADOW_PORTFOLIO_BROKER_FILL: broker fills are only legal on the broker-facing portfolio' using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger fills_check_broker_portfolio before insert on fills for each row execute function fills_check_broker_portfolio();

-- (h) Cash ledger balances must chain: balance_after = previous balance + amount, per portfolio (ADR-0002 projection check).
create or replace function cash_ledger_check_chain() returns trigger language plpgsql as $$
declare prev numeric(14,6);
begin
  select balance_after_usd into prev from cash_ledger where portfolio_id = new.portfolio_id order by id desc limit 1;
  if prev is null then
    if new.kind <> 'initial_equity' then
      raise exception 'CASH_LEDGER_MUST_START_WITH_INITIAL_EQUITY' using errcode = 'check_violation';
    end if;
    prev := 0;
  end if;
  if new.balance_after_usd <> prev + new.amount_usd then
    raise exception 'CASH_LEDGER_CHAIN_BROKEN: expected balance_after % got %', prev + new.amount_usd, new.balance_after_usd
      using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger cash_ledger_check_chain before insert on cash_ledger for each row execute function cash_ledger_check_chain();
