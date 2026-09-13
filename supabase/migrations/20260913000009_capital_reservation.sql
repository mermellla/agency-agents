-- 0009: Atomic buying-power reservation and portfolio concurrency (ADR-0021).
-- Two concurrent BUY validations must not collectively reserve more virtual capital than the portfolio has.
-- available_virtual_cash = latest cash_ledger balance − Σ reserved_notional_usd over open BUY orders.
-- Reservation is taken at order insert under a per-portfolio advisory transaction lock, reduced by each fill's
-- notional, and released when the order reaches a terminal state.
set search_path = trading, public;

alter table orders add column reserved_notional_usd numeric(14,6) not null default 0 check (reserved_notional_usd >= 0);

create or replace function order_is_open(s order_status) returns boolean language sql immutable set search_path = trading, public as $$
  select s in ('PROPOSED', 'VALIDATED', 'PENDING_APPROVAL', 'SUBMITTED', 'PARTIALLY_FILLED', 'FILL_PENDING_RECONSTRUCTION')
$$;

create or replace function portfolio_cash_balance(p uuid) returns numeric language sql stable set search_path = trading, public as $$
  select coalesce((select balance_after_usd from cash_ledger where portfolio_id = p order by id desc limit 1), 0)
$$;

create or replace function portfolio_reserved_cash(p uuid) returns numeric language sql stable set search_path = trading, public as $$
  select coalesce(sum(reserved_notional_usd), 0) from orders where portfolio_id = p and side = 'buy' and order_is_open(status)
$$;

create or replace function portfolio_available_cash(p uuid) returns numeric language sql stable set search_path = trading, public as $$
  select portfolio_cash_balance(p) - portfolio_reserved_cash(p)
$$;

-- Serialises every capital-affecting write for one portfolio within the calling transaction.
create or replace function lock_portfolio(p uuid) returns void language sql volatile set search_path = trading, public as $$
  select pg_advisory_xact_lock(hashtext('trading.portfolio:' || p::text))
$$;

create or replace function orders_reserve_capital() returns trigger language plpgsql set search_path = trading, public as $$
declare required numeric; available numeric;
begin
  if new.side <> 'buy' then
    if new.reserved_notional_usd <> 0 then
      raise exception 'RESERVATION_ON_SELL: sell orders reserve nothing' using errcode = 'check_violation';
    end if;
    return new;
  end if;
  perform lock_portfolio(new.portfolio_id);
  -- the minimum a BUY can be worth: notional, or qty × the price it is capped at (limit / stop); market qty orders
  -- must carry an application-supplied reservation (reference price × qty × (1 + OVERFILL_BUFFER_PCT), §3.4)
  required := coalesce(new.notional, new.qty * coalesce(new.limit_price, new.stop_price, 0));
  if new.reserved_notional_usd <= 0 or new.reserved_notional_usd < required then
    raise exception 'RESERVATION_TOO_SMALL: reserved % < required % (order %)', new.reserved_notional_usd, required, new.client_order_id
      using errcode = 'check_violation';
  end if;
  available := portfolio_available_cash(new.portfolio_id);
  if new.reserved_notional_usd > available then
    raise exception 'INSUFFICIENT_VIRTUAL_CASH: reserving % but only % available in portfolio % (cash % − reserved %)',
      new.reserved_notional_usd, available, new.portfolio_id, portfolio_cash_balance(new.portfolio_id), portfolio_reserved_cash(new.portfolio_id)
      using errcode = 'check_violation';
  end if;
  return new;
end $$;
-- runs after orders_check_decision (alphabetical order of BEFORE INSERT triggers)
create trigger orders_reserve_capital before insert on orders for each row execute function orders_reserve_capital();

create or replace function orders_release_reservation() returns trigger language plpgsql set search_path = trading, public as $$
begin
  if new.status is distinct from old.status and not order_is_open(new.status) then
    new.reserved_notional_usd := 0;
  elsif new.reserved_notional_usd > old.reserved_notional_usd then
    raise exception 'RESERVATION_INCREASE_FORBIDDEN: reservations only shrink after insert (order %)', old.id using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger orders_release_reservation before update on orders for each row execute function orders_release_reservation();

-- A fill converts reservation into cost basis: shrink the reservation by the fill notional (under the portfolio lock).
create or replace function fills_consume_reservation() returns trigger language plpgsql set search_path = trading, public as $$
begin
  if new.side = 'buy' then
    perform lock_portfolio(new.portfolio_id);
    update orders set reserved_notional_usd = greatest(0, reserved_notional_usd - new.notional) where id = new.order_id;
  end if;
  return new;
end $$;
create trigger fills_consume_reservation after insert on fills for each row execute function fills_consume_reservation();

-- Cash events on a portfolio also take the lock, so the balance read by a concurrent reservation is never mid-update.
create or replace function cash_ledger_lock() returns trigger language plpgsql set search_path = trading, public as $$
begin
  perform lock_portfolio(new.portfolio_id);
  return new;
end $$;
-- runs before cash_ledger_check_chain (alphabetical)
create trigger cash_ledger_a_lock before insert on cash_ledger for each row execute function cash_ledger_lock();
