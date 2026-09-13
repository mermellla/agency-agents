-- 0003: market group — §10.1 "Market", §5.2 source registry, §6 scanner outputs, D-50 candidate outcomes.

create table instruments (
  symbol           text primary key,
  alpaca_asset_id  uuid,
  name             text,
  exchange         text,
  cik              text,
  sic              integer,
  tradable         boolean not null default false,
  fractionable     boolean not null default false,
  alpaca_status    text,
  universe_status  universe_status not null default 'excluded_universe',
  status_reason    text,
  exclusion_list_version text references exclusion_list_versions(version),
  last_10k_filed_on date,                          -- ADR-0005 domestic-operating-company test
  as_of            timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create trigger instruments_updated_at before update on instruments for each row execute function set_updated_at();

create table regimes (
  id               uuid primary key default gen_random_uuid(),
  trade_date       date not null,
  regime           market_regime not null,
  scanner_version  text not null references scanner_versions(version),
  inputs           jsonb not null,                -- SPY trend, realized vol, breadth (ADR-0009)
  computed_at      timestamptz not null default now(),
  unique (trade_date, scanner_version)
);

create table scans (
  id                     uuid primary key default gen_random_uuid(),
  experiment_id          uuid not null references experiments(id),
  experiment_phase_id    uuid not null references experiment_phases(id),
  kind                   text not null check (kind in ('preopen', 'intraday')),
  feed_tier              feed_tier not null default 'SIP_DELAYED',
  started_at             timestamptz not null,
  scanner_completed_at   timestamptz not null,
  bars_end_at            timestamptz not null,    -- SIP request `end` (≤ started_at − 15 min on basic plan, §5.1)
  regime_id              uuid references regimes(id),
  scanner_version        text not null references scanner_versions(version),
  exclusion_list_version text not null references exclusion_list_versions(version),
  universe_size          integer not null,
  candidate_count        integer not null,
  source_status          jsonb not null,          -- §5.2 stamped on every scan
  signals_unavailable    jsonb not null default '[]'::jsonb,  -- §6.3 signal_unavailable_reason
  check (scanner_completed_at >= started_at),
  check (bars_end_at <= started_at)
);

create table universe_memberships (
  scan_id          uuid not null references scans(id),
  symbol           text not null references instruments(symbol),
  status           universe_status not null,
  reason           text,
  primary key (scan_id, symbol)
);

create table candidates (
  id                        uuid primary key default gen_random_uuid(),
  scan_id                   uuid not null references scans(id),
  symbol                    text not null references instruments(symbol),
  rank                      integer not null check (rank > 0),
  composite_score           numeric(8,6) not null,
  signals                   jsonb not null,
  strategy_tags             text[] not null default '{}',
  -- D-50 / §7.6 signal drift and availability semantics (§5.1)
  signal_observed_at        timestamptz not null,   -- when the triggering bar became retrievable, never the bar time
  signal_bar_time           timestamptz not null,   -- the bar's own timestamp (for blind_interval_move_pct)
  sip_signal_price          numeric(14,4) not null,
  sip_signal_timestamp      timestamptz not null,
  iex_price_at_scan         numeric(14,4),
  iex_quote_age_sec_at_scan integer,
  iex_price_at_triage       numeric(14,4),
  iex_quote_age_sec_at_triage integer,
  iex_price_at_decision     numeric(14,4),
  iex_quote_age_sec_at_decision integer,
  iex_price_at_order        numeric(14,4),
  iex_quote_age_sec_at_order integer,
  triaged                   boolean not null default false,
  triage_rank               integer,
  triage_reason             text,
  promoted_to_decision      boolean not null default false,
  created_at                timestamptz not null default now(),
  unique (scan_id, symbol),
  check (signal_observed_at >= signal_bar_time)
);

create table candidate_outcomes (
  id                 uuid primary key default gen_random_uuid(),
  candidate_id       uuid not null references candidates(id),
  horizon            text not null check (horizon in ('15m', '1h', '1d', '5d')),
  anchor_at          timestamptz not null,       -- first eligible SIP print after baseline eligibility (OI-12)
  anchor_price       numeric(14,4) not null,
  horizon_at         timestamptz not null,
  horizon_price      numeric(14,4),
  forward_return_pct numeric(10,6),
  status             text not null default 'pending' check (status in ('pending', 'computed', 'unavailable')),
  computed_at        timestamptz,
  unique (candidate_id, horizon),
  check (horizon_at > anchor_at)
);

create table source_status (
  id           bigserial primary key,
  observed_at  timestamptz not null default now(),
  domain       text not null,                  -- bars_quotes | news | filings | fundamentals | earnings_calendar | calendar_corporate_actions
  source       text not null,
  grade        trust_grade not null,
  health       source_health not null,
  age_sec      integer,
  detail       jsonb
);
create index source_status_observed_at on source_status (observed_at desc);

create table benchmark_prices (
  symbol         text not null check (symbol in ('SPY', 'VTI')),
  trade_date     date not null,
  close          numeric(14,4) not null,
  adjusted_close numeric(14,4),
  source         text not null,
  primary key (symbol, trade_date)
);

create trigger scans_no_delete before delete on scans for each row execute function forbid_delete();
create trigger candidates_no_delete before delete on candidates for each row execute function forbid_delete();
create trigger candidate_outcomes_no_delete before delete on candidate_outcomes for each row execute function forbid_delete();
