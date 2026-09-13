-- 0011: fee classification (owner instruction 2026-09-13). Fees Alpaca is expected to debit from the account are
-- `customer_debited`; fees whose pass-through behaviour is not yet verified (CAT) are `pass_through_unverified` and are
-- reported separately from Trading P&L until verified.
set search_path = trading, public;

alter table fees add column classification text not null default 'customer_debited'
  check (classification in ('customer_debited', 'pass_through_unverified'));

alter table closed_trades add column unverified_pass_through_fees_usd numeric(10,6) not null default 0;
comment on column closed_trades.unverified_pass_through_fees_usd is
  'Fees classified pass_through_unverified (e.g. CAT); excluded from regulatory_fees_usd and from both P&L views until verified.';
