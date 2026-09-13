-- 0005: execution group — §8 risk/execution desk, §8.4 state machine, §8.6 reconciliation, §8.10 broker policy,
-- §9 budget, §10.1 "Execution". Ledger style per ADR-0002: append-only event tables + rebuildable projections.
set search_path = trading, public;

create table positions (
  id                          uuid primary key default gen_random_uuid(),
  portfolio_id                uuid not null references portfolios(id),
  experiment_id               uuid not null references experiments(id),
  opened_in_phase_id          uuid not null references experiment_phases(id),
  symbol                      text not null references instruments(symbol),
  status                      position_status not null default 'open',
  entry_decision_id           uuid not null references decisions(decision_id),
  opened_at                   timestamptz,
  closed_at                   timestamptz,
  qty                         numeric(18,9) not null default 0 check (qty >= 0),   -- long-only (§2)
  avg_cost                    numeric(14,6),
  is_fractional               boolean not null default false,
  working_target_price        numeric(14,4),      -- management levels; may change (§7.4)
  working_invalidation_price  numeric(14,4),
  working_time_stop_at        timestamptz,
  ethical_hold                boolean not null default false,     -- §4.4 item 7
  needs_ethical_review        boolean not null default false,     -- §15
  updated_at                  timestamptz not null default now(),
  check (status <> 'closed' or closed_at is not null)
);
create unique index positions_one_open_per_symbol on positions (portfolio_id, symbol) where status = 'open';
create trigger positions_updated_at before update on positions for each row execute function set_updated_at();
alter table decisions add constraint decisions_position_fk foreign key (position_id) references positions(id);

create table orders (
  id                   uuid primary key default gen_random_uuid(),
  decision_id          uuid not null references decisions(decision_id),
  portfolio_id         uuid not null references portfolios(id),
  experiment_id        uuid not null references experiments(id),
  experiment_phase_id  uuid not null references experiment_phases(id),
  position_id          uuid references positions(id),
  client_order_id      text not null unique check (length(client_order_id) between 1 and 128),  -- §2, §8.3; Alpaca max 128
  leg_seq              integer not null default 1 check (leg_seq >= 1),
  purpose              order_purpose not null,
  symbol               text not null references instruments(symbol),
  side                 order_side not null,
  order_type           order_type not null,
  time_in_force        time_in_force not null,
  order_class          text not null default 'simple' check (order_class in ('simple', 'bracket', 'oco', 'oto')),
  qty                  numeric(18,9) check (qty is null or qty > 0),
  notional             numeric(12,2) check (notional is null or notional > 0),
  limit_price          numeric(14,4),
  stop_price           numeric(14,4),
  take_profit_price    numeric(14,4),
  extended_hours       boolean not null default false,
  is_simulated         boolean not null,
  status               order_status not null default 'PROPOSED',
  status_reason        text,
  broker_order_id      text unique,
  replaces_order_id    uuid references orders(id),
  parent_order_id      uuid references orders(id),
  order_eligible_at    timestamptz not null,     -- copied from the decision; earliest legal fill time (§8.9)
  submitted_at         timestamptz,
  expires_at           timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  unique (decision_id, leg_seq),
  check ((qty is null) <> (notional is null)),                                  -- exactly one (Alpaca rule)
  check (purpose <> 'entry' or (side = 'buy' and extended_hours = false)),       -- §4.3 entries regular session only
  check (not extended_hours or (order_type = 'limit' and time_in_force = 'day')),-- §4.3 ext-hours exits are Day limits
  check (order_type not in ('stop', 'stop_limit') or stop_price is not null),
  check (order_type not in ('limit', 'stop_limit') or limit_price is not null),
  check (not is_simulated or broker_order_id is null),
  check (is_simulated or status <> 'FILL_PENDING_RECONSTRUCTION')
);
create index orders_position on orders (position_id);
create index orders_status on orders (status) where status in ('SUBMITTED', 'PARTIALLY_FILLED', 'PENDING_APPROVAL', 'FILL_PENDING_RECONSTRUCTION');
create trigger orders_updated_at before update on orders for each row execute function set_updated_at();

create table order_events (
  id               bigserial primary key,
  order_id         uuid not null references orders(id),
  from_status      order_status,
  to_status        order_status not null,
  at               timestamptz not null default now(),
  reason           text not null,
  broker_event_id  text unique,                   -- §15 idempotent by broker event id
  payload          jsonb
);
create index order_events_order on order_events (order_id, at);

create table fills (
  id                    uuid primary key default gen_random_uuid(),
  order_id              uuid not null references orders(id),
  portfolio_id          uuid not null references portfolios(id),
  experiment_id         uuid not null references experiments(id),
  experiment_phase_id   uuid not null references experiment_phases(id),
  position_id           uuid references positions(id),
  symbol                text not null references instruments(symbol),
  side                  order_side not null,
  qty                   numeric(18,9) not null check (qty > 0),
  price                 numeric(14,6) not null check (price > 0),
  notional              numeric(14,6) not null check (notional > 0),
  fill_at               timestamptz not null,
  fill_source           fill_source not null,
  broker_fill_id        text unique,               -- Alpaca activity id; idempotent replay (§8.6, §15)
  reconstruction_basis  reconstruction_basis,
  half_spread_estimate  numeric(14,6),
  additional_slippage_bps numeric(8,4) not null default 0,   -- §12 reported as slippage, never spread
  shadow_estimate_price numeric(14,6),            -- §12 paper-fill calibration
  paper_fill_minus_shadow_estimate_bps numeric(10,4),
  eligibility_anomaly   boolean not null default false,   -- broker fill stamped before order_eligible_at (clock skew); never for reconstructed
  created_at            timestamptz not null default now(),
  check (fill_source <> 'reconstructed' or reconstruction_basis is not null),
  check (fill_source <> 'broker' or broker_fill_id is not null),
  check (reconstruction_basis is null or reconstruction_basis not in ('trade_plus_half_spread', 'trade_minus_half_spread') or half_spread_estimate is not null)
);
create index fills_order on fills (order_id);
create index fills_position on fills (position_id, fill_at);

create table lots (
  id             uuid primary key default gen_random_uuid(),
  position_id    uuid not null references positions(id),
  portfolio_id   uuid not null references portfolios(id),
  opened_fill_id uuid not null references fills(id),
  qty_opened     numeric(18,9) not null check (qty_opened > 0),
  qty_remaining  numeric(18,9) not null check (qty_remaining >= 0),
  cost_basis     numeric(14,6) not null,
  is_fractional  boolean not null,
  opened_at      timestamptz not null,
  closed_at      timestamptz,
  updated_at     timestamptz not null default now()
);
create trigger lots_updated_at before update on lots for each row execute function set_updated_at();

create table lot_events (
  id          bigserial primary key,
  lot_id      uuid not null references lots(id),
  at          timestamptz not null default now(),
  kind        text not null check (kind in ('open', 'reduce', 'close', 'split_adjust', 'symbol_change', 'force_close', 'dividend')),
  qty_delta   numeric(18,9) not null,
  price       numeric(14,6),
  fill_id     uuid references fills(id),
  reason      text,
  payload     jsonb
);

-- Cash ledger: append-only event log for the virtual portfolio (§3.2, §3.3, §9, ADR-0002).
create table cash_ledger (
  id             bigserial primary key,
  portfolio_id   uuid not null references portfolios(id),
  experiment_id  uuid not null references experiments(id),
  experiment_phase_id uuid not null references experiment_phases(id),
  at             timestamptz not null default now(),
  kind           cash_event_kind not null,
  amount_usd     numeric(14,6) not null,
  balance_after_usd numeric(14,6) not null check (balance_after_usd >= 0),   -- §2: cash can never go negative (no borrowing)
  settles_on     date,                            -- §3.3 accounting only
  fill_id        uuid references fills(id),
  reason         text,
  idempotency_key text not null unique,
  created_at     timestamptz not null default now()
);
create index cash_ledger_portfolio on cash_ledger (portfolio_id, id desc);

create table fees (
  id                bigserial primary key,
  fill_id           uuid not null references fills(id),
  portfolio_id      uuid not null references portfolios(id),
  kind              fee_kind not null,
  amount_usd        numeric(12,6) not null check (amount_usd >= 0),
  rate_basis        jsonb not null,               -- rate, quantity/notional, schedule version (ADR-0012)
  fee_schedule_version text not null,
  created_at        timestamptz not null default now(),
  unique (fill_id, kind)
);

-- §8.3 / D-45: stop coverage per session, with mandatory post-open verification.
create table stop_coverage (
  id                    bigserial primary key,
  session_date          date not null,
  position_id           uuid not null references positions(id),
  lot_id                uuid references lots(id),
  order_id              uuid references orders(id),
  armed_at              timestamptz,
  broker_status_at_arm  text,
  confirmed_active_at   timestamptz,              -- ADR-0013 definition of "confirmed active"
  verified_post_open_at timestamptz,
  verification_result   text check (verification_result in ('live', 'missing', 'software_stop_placed', 'not_applicable')),
  incident_code         text,
  created_at            timestamptz not null default now(),
  unique (session_date, position_id)
);

-- §8.6 replay-or-halt reconciliation.
create table reconciliations (
  id                       uuid primary key default gen_random_uuid(),
  experiment_id            uuid not null references experiments(id),
  ran_at                   timestamptz not null default now(),
  execution_mode           execution_mode not null,
  result                   reconcile_result not null,
  events_replayed          integer not null default 0,
  last_reconciled_event_at timestamptz,
  diff                     jsonb not null default '{}'::jsonb,
  notes                    text
);

create table owner_resolutions (
  id                 uuid primary key default gen_random_uuid(),
  reconciliation_id  uuid not null references reconciliations(id),
  resolved_at        timestamptz not null default now(),
  resolved_by        text not null,
  reason             text not null,
  actions            jsonb not null
);

create table halts (
  id           uuid primary key default gen_random_uuid(),
  experiment_id uuid references experiments(id),
  at           timestamptz not null default now(),
  code         text not null,                     -- RECONCILE_HALT | BROKER_POLICY_HALT | SUPABASE_UNREACHABLE | MODE_MISMATCH | CONSECUTIVE_REJECTS | BUDGET_EXHAUSTED_ENTRIES | BARS_QUOTES_DOWN | BROKER_API_DOWN | PHASE_REASON_MISSING | KILL_SWITCH
  scope        halt_scope not null,
  detail       jsonb not null default '{}'::jsonb,
  emailed_at   timestamptz,
  cleared_at   timestamptz,
  cleared_by   text,
  cleared_reason text
);
create index halts_open on halts (at desc) where cleared_at is null;

-- §8.10 broker policy enforcement log.
create table broker_policy_checks (
  id             bigserial primary key,
  experiment_id  uuid references experiments(id),
  at             timestamptz not null default now(),
  execution_mode execution_mode not null,
  expected       jsonb not null,
  observed_before jsonb,
  observed_after  jsonb,
  result         broker_policy_result not null,
  emailed_at     timestamptz
);

-- §9 budget desk.
create table budget_ledger (
  id             bigserial primary key,
  experiment_id  uuid not null references experiments(id),
  trade_date     date not null,
  bucket         budget_bucket not null,
  allowance_usd  numeric(10,6) not null,
  carried_in_usd numeric(10,6) not null default 0,
  spent_usd      numeric(10,6) not null default 0,
  overage_usd    numeric(10,6) not null default 0,   -- BUDGET_OVERAGE_EXIT
  exhausted_at   timestamptz,
  updated_at     timestamptz not null default now(),
  unique (experiment_id, trade_date, bucket)
);
create trigger budget_ledger_updated_at before update on budget_ledger for each row execute function set_updated_at();

-- Append-only / no-delete enforcement (§10.2, ADR-0002).
create trigger orders_no_delete before delete on orders for each row execute function forbid_delete();
create trigger order_events_no_delete before delete on order_events for each row execute function forbid_delete();
create trigger order_events_no_update before update on order_events for each row execute function forbid_update();
create trigger fills_no_delete before delete on fills for each row execute function forbid_delete();
create trigger lots_no_delete before delete on lots for each row execute function forbid_delete();
create trigger lot_events_no_delete before delete on lot_events for each row execute function forbid_delete();
create trigger lot_events_no_update before update on lot_events for each row execute function forbid_update();
create trigger cash_ledger_no_delete before delete on cash_ledger for each row execute function forbid_delete();
create trigger cash_ledger_no_update before update on cash_ledger for each row execute function forbid_update();
create trigger fees_no_delete before delete on fees for each row execute function forbid_delete();
create trigger positions_no_delete before delete on positions for each row execute function forbid_delete();
create trigger reconciliations_no_delete before delete on reconciliations for each row execute function forbid_delete();
create trigger halts_no_delete before delete on halts for each row execute function forbid_delete();
create trigger broker_policy_checks_no_delete before delete on broker_policy_checks for each row execute function forbid_delete();
create trigger budget_ledger_no_delete before delete on budget_ledger for each row execute function forbid_delete();
