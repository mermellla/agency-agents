"""Pre-implementation tests of the schema-enforced invariants (§10.2, §8.4, §8.9, §7.4, ADR-0020).
Every test here is runnable before any application code exists: the database is the system under test."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest

from tests.seed import NOW

pytestmark = pytest.mark.db
psycopg = pytest.importorskip("psycopg")
Err = psycopg.errors.RaiseException


@contextmanager
def raises(db, match):
    """Expect a database error inside a savepoint so the surrounding test transaction stays usable."""
    with pytest.raises(psycopg.Error, match=match):
        with db.transaction():
            yield


# ---- §2 / §8.3 unique client_order_id; §8.8 idempotent resubmission is a no-op

def test_client_order_id_unique(db, seed):
    d = seed.decision()
    seed.order(d)
    with raises(db, "client_order_id|duplicate key"):
        seed.order(d, client_order_id=f"{d}:1", leg_seq=2)


# ---- §10.2 no physical deletes

@pytest.mark.parametrize("table", ["decisions", "orders", "fills", "cash_ledger", "llm_calls", "closed_trades", "order_events"])
def test_no_physical_deletes(db, seed, table):
    d = seed.decision()
    o = seed.order(d)
    seed.transition(o, "VALIDATED"); seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    seed.fill(o, fill_at=NOW + timedelta(minutes=20))
    db.execute("insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, kind, amount_usd, balance_after_usd, idempotency_key) values (%s, %s, %s, 'initial_equity', 500, 500, %s)",
               (seed.primary_id, seed.experiment_id, seed.phase_id, uuid.uuid4().hex))
    db.execute("insert into llm_calls (experiment_id, experiment_phase_id, decision_id, stage, bucket, model, prompt_version, status) values (%s, %s, %s, 'decision', 'entries', 'm', %s, 'ok')",
               (seed.experiment_id, seed.phase_id, d, seed.versions["prompt_version"]))
    if table == "closed_trades":
        pos = uuid.uuid4()
        db.execute("insert into positions (id, portfolio_id, experiment_id, opened_in_phase_id, symbol, entry_decision_id, qty, status, closed_at) values (%s, %s, %s, %s, 'AAPL', %s, 0, 'closed', now())",
                   (pos, seed.primary_id, seed.experiment_id, seed.phase_id, d))
        v = seed.versions
        db.execute(
            """insert into closed_trades (position_id, portfolio_id, experiment_id, experiment_phase_id, symbol, entry_decision_id, entry_fill_at, exit_fill_at, holding_seconds,
               holding_bucket, qty, entry_price, exit_price, gross_pnl_usd, gross_return_pct, net_return_pct, discretionary_exit, exit_reason, overnight_gap_exposure,
               net_return_after_computational_costs_pct, prompt_version, exclusion_list_version, config_version, scanner_version, qb_rules_version)
               values (%s, %s, %s, %s, 'AAPL', %s, %s, %s, 3600, 'intraday', 1, 100, 101, 1, 1, 0.9, false, 'target', false, 0.5, %s, %s, %s, %s, %s)""",
            (pos, seed.primary_id, seed.experiment_id, seed.phase_id, d, NOW + timedelta(minutes=20), NOW + timedelta(minutes=80),
             v["prompt_version"], v["exclusion_list_version"], v["config_version"], v["scanner_version"], v["qb_rules_version"]),
        )
    with raises(db, "DELETE_FORBIDDEN"):
        db.execute(f"delete from {table}")


# ---- §8.9 anti-look-ahead

def test_simulated_fill_before_eligibility_rejected(db, seed):
    d = seed.decision()
    o = seed.order(d)
    seed.transition(o, "VALIDATED"); seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    with raises(db, "ANTI_LOOK_AHEAD_VIOLATION"):
        seed.fill(o, fill_at=NOW + timedelta(minutes=3, seconds=59))


def test_simulated_fill_at_or_after_eligibility_accepted(db, seed):
    d = seed.decision()
    o = seed.order(d)
    seed.transition(o, "VALIDATED"); seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    seed.fill(o, fill_at=NOW + timedelta(minutes=4))  # exactly at eligibility
    assert db.execute("select count(*) from fills").fetchone()[0] == 1


def test_decision_eligible_before_signal_rejected(db, seed):
    with raises(db, "decisions_eligible_after_signal"):
        seed.decision(signal_observed_at=NOW, eligible_at=NOW - timedelta(seconds=1))


def test_order_eligibility_must_match_decision(db, seed):
    d = seed.decision()
    with raises(db, "ORDER_ELIGIBILITY_MISMATCH"):
        seed.order(d, eligible_at=NOW)


def test_order_eligible_equals_risk_validation(db, seed):
    with raises(db, "decisions_eligible_equals_validation"):
        seed.decision(risk_validation_completed_at=NOW + timedelta(minutes=4), order_eligible_at=NOW + timedelta(minutes=5))


def test_broker_fill_before_eligibility_is_flagged_not_rejected(db, seed):
    d = seed.decision()
    o = seed.order(d, is_simulated=False)
    seed.transition(o, "VALIDATED"); seed.transition(o, "SUBMITTED")
    f = seed.fill(o, fill_at=NOW, source="broker", basis=None, broker_fill_id="act-1")
    assert db.execute("select eligibility_anomaly from fills where id = %s", (f,)).fetchone()[0] is True


# ---- §10.2 versions non-null; §13.2 no silent change

def test_versions_non_null(db, seed):
    with raises(db, "null value|not-null"):
        seed.decision(prompt_version=None)


def test_version_drift_from_phase_rejected(db, seed):
    db.execute("insert into prompt_versions (version, content_hash, changelog) values ('p1.0.1', 'h', 'bump')")
    with raises(db, "VERSION_DRIFT"):
        seed.decision(prompt_version="p1.0.1")


def test_only_one_open_phase(db, seed):
    v = seed.versions
    with raises(db, "experiment_phases_one_open"):
        db.execute(
            """insert into experiment_phases (experiment_id, seq, category, reason, prompt_version, config_version, scanner_version, qb_rules_version,
               exclusion_list_version, model_triage, model_decision, model_critique, migration_version, code_version) values (%s, 2, 'strategy', 'x', %s, %s, %s, %s, %s, 'a', 'b', 'c', 'm', 'sha')""",
            (seed.experiment_id, v["prompt_version"], v["config_version"], v["scanner_version"], v["qb_rules_version"], v["exclusion_list_version"]),
        )


def test_prompt_bump_opens_new_phase_and_decisions_carry_it(db, seed):
    """§17 phase test (schema half): close phase 1, open phase 2 with the new prompt, decisions must carry phase 2's versions."""
    v = dict(seed.versions)
    db.execute("insert into prompt_versions (version, content_hash, changelog) values ('p1.0.1', 'h', 'bump')")
    db.execute("update experiment_phases set ended_at = now() where id = %s", (seed.phase_id,))
    p2 = uuid.uuid4()
    db.execute(
        """insert into experiment_phases (id, experiment_id, seq, category, reason, prompt_version, config_version, scanner_version, qb_rules_version,
           exclusion_list_version, model_triage, model_decision, model_critique, migration_version, code_version) values (%s, %s, 2, 'strategy', 'prompt bump', 'p1.0.1', %s, %s, %s, %s, 'a', 'b', 'c', 'm', 'sha')""",
        (p2, seed.experiment_id, v["config_version"], v["scanner_version"], v["qb_rules_version"], v["exclusion_list_version"]),
    )
    with raises(db, "PHASE_CLOSED"):
        seed.decision()  # still pointing at phase 1
    with raises(db, "VERSION_DRIFT"):
        seed.decision(experiment_phase_id=p2)  # phase 2 but old prompt version
    seed.decision(experiment_phase_id=p2, prompt_version="p1.0.1")


# ---- §3.1 SHORT/COVER always rejected

def test_short_must_be_rejected_ineligible(db, seed):
    with raises(db, "decisions_short_cover_rejected"):
        seed.decision(decision="SHORT", status="validated")
    seed.decision(decision="SHORT", status="rejected", reject_code="REJECTED_ACCOUNT_INELIGIBLE",
                  forecast_target_price_at_entry=None, forecast_invalidation_price_at_entry=None, forecast_time_stop_at_entry=None)


def test_sell_entry_order_forbidden(db, seed):
    d = seed.decision()
    with raises(db, "SHORT_SELLING_FORBIDDEN"):
        seed.order(d, side="sell", purpose="entry")


# ---- §8.4 state machine

def test_illegal_transition_rejected(db, seed):
    d = seed.decision()
    o = seed.order(d)
    with raises(db, "ORDER_STATE_VIOLATION"):
        seed.transition(o, "FILLED")


def test_legal_transitions_logged(db, seed):
    d = seed.decision()
    o = seed.order(d)
    for s in ("VALIDATED", "FILL_PENDING_RECONSTRUCTION", "FILLED"):
        seed.transition(o, s, reason=f"to {s}")
    rows = db.execute("select from_status, to_status, reason from order_events where order_id = %s order by id", (o,)).fetchall()
    assert [r[1] for r in rows] == ["VALIDATED", "FILL_PENDING_RECONSTRUCTION", "FILLED"]
    with raises(db, "ORDER_STATE_VIOLATION"):
        seed.transition(o, "CANCELLED")  # terminal


def test_transition_requires_reason(db, seed):
    d = seed.decision()
    o = seed.order(d)
    with raises(db, "requires status_reason"):
        db.execute("update orders set status = 'VALIDATED' where id = %s", (o,))


def test_order_for_unvalidated_decision_rejected(db, seed):
    d = seed.decision(status="proposed")
    with raises(db, "ORDER_FOR_UNVALIDATED_DECISION"):
        seed.order(d)


# ---- ADR-0020 LIVE lockout at the schema

def test_live_experiment_rejected(db):
    with raises(db, "live_locked_out"):
        db.execute("insert into experiments (name, execution_mode, equity_start_usd) values ('live', 'LIVE', 500)")


def test_pending_approval_unreachable(db, seed):
    d = seed.decision()
    o = seed.order(d, is_simulated=False)
    seed.transition(o, "VALIDATED")
    with raises(db, "LIVE_LOCKED_OUT"):
        seed.transition(o, "PENDING_APPROVAL")


# ---- §7.4 forecast contract immutability

def test_forecast_contract_immutable(db, seed):
    d = seed.decision()
    with raises(db, "FORECAST_CONTRACT_IMMUTABLE"):
        db.execute("update decisions set forecast_target_price_at_entry = 120 where decision_id = %s", (d,))
    with raises(db, "FORECAST_PROBABILITY_IMMUTABLE"):
        db.execute("update decisions set probability_pre_critique = 0.9 where decision_id = %s", (d,))


def test_working_levels_can_move_but_resolution_uses_entry_levels(db, seed):
    """§17 forecast-immutability test (schema half): moving the working target does not change what a resolution may cite."""
    d = seed.decision()
    o = seed.order(d)
    seed.transition(o, "VALIDATED"); seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    seed.fill(o, fill_at=NOW + timedelta(minutes=20))
    db.execute("update decisions set fill_at = %s, status = 'executed' where decision_id = %s", (NOW + timedelta(minutes=20), d))
    pos = uuid.uuid4()
    db.execute("insert into positions (id, portfolio_id, experiment_id, opened_in_phase_id, symbol, entry_decision_id, qty, working_target_price) values (%s, %s, %s, %s, 'AAPL', %s, 1, 110)",
               (pos, seed.primary_id, seed.experiment_id, seed.phase_id, d))
    db.execute("update positions set working_target_price = 130 where id = %s", (pos,))  # allowed: management level
    with raises(db, "FORECAST_RESOLVED_AGAINST_REVISED_LEVELS"):
        db.execute("""insert into forecast_resolutions (decision_id, position_id, resolution_start_at, target_at_entry, invalidation_at_entry, time_stop_at_entry, method, outcome, touched_at, touch_price)
                      values (%s, %s, %s, 130, 95, %s, 'bars_1m', 'TARGET', %s, 130)""", (d, pos, NOW + timedelta(minutes=20), NOW + timedelta(days=5), NOW + timedelta(hours=2)))
    with raises(db, "FORECAST_RESOLUTION_START"):
        db.execute("""insert into forecast_resolutions (decision_id, position_id, resolution_start_at, target_at_entry, invalidation_at_entry, time_stop_at_entry, method, outcome, touched_at, touch_price)
                      values (%s, %s, %s, 110, 95, %s, 'bars_1m', 'TARGET', %s, 110)""", (d, pos, NOW, NOW + timedelta(days=5), NOW + timedelta(hours=2)))
    db.execute("""insert into forecast_resolutions (decision_id, position_id, resolution_start_at, target_at_entry, invalidation_at_entry, time_stop_at_entry, method, outcome, touched_at, touch_price)
                  values (%s, %s, %s, 110, 95, %s, 'bars_1m', 'TARGET', %s, 110)""", (d, pos, NOW + timedelta(minutes=20), NOW + timedelta(days=5), NOW + timedelta(hours=2)))
    with raises(db, "UPDATE_FORBIDDEN"):
        db.execute("update forecast_resolutions set outcome = 'INVALIDATION' where decision_id = %s", (d,))


def test_ambiguous_needs_reason(db, seed):
    d = seed.decision()
    db.execute("update decisions set fill_at = %s where decision_id = %s", (NOW + timedelta(minutes=20), d))
    with raises(db, "check"):
        db.execute("""insert into forecast_resolutions (decision_id, resolution_start_at, target_at_entry, invalidation_at_entry, time_stop_at_entry, method, outcome)
                      values (%s, %s, 110, 95, %s, 'ticks', 'AMBIGUOUS')""", (d, NOW + timedelta(minutes=20), NOW + timedelta(days=5)))


# ---- §2 / §3.2 virtual cash can never go negative; ADR-0002 ledger chain

def test_cash_ledger_never_negative_and_chains(db, seed):
    args = (seed.primary_id, seed.experiment_id, seed.phase_id)
    db.execute("insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, kind, amount_usd, balance_after_usd, idempotency_key) values (%s, %s, %s, 'initial_equity', 500, 500, 'k1')", args)
    with raises(db, "balance_after_usd|CASH_LEDGER_CHAIN_BROKEN"):
        db.execute("insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, kind, amount_usd, balance_after_usd, idempotency_key) values (%s, %s, %s, 'buy', -600, -100, 'k2')", args)
    with raises(db, "CASH_LEDGER_CHAIN_BROKEN"):
        db.execute("insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, kind, amount_usd, balance_after_usd, idempotency_key) values (%s, %s, %s, 'buy', -100, 450, 'k3')", args)
    db.execute("insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, kind, amount_usd, balance_after_usd, idempotency_key) values (%s, %s, %s, 'buy', -100, 400, 'k4')", args)
    with raises(db, "UPDATE_FORBIDDEN"):
        db.execute("update cash_ledger set amount_usd = 0")


def test_cash_ledger_must_start_with_initial_equity(db, seed):
    with raises(db, "CASH_LEDGER_MUST_START_WITH_INITIAL_EQUITY"):
        db.execute("insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, kind, amount_usd, balance_after_usd, idempotency_key) values (%s, %s, %s, 'buy', -100, 400, 'k1')",
                   (seed.primary_id, seed.experiment_id, seed.phase_id))


# ---- §12 spread never counted twice; shadows never touch the broker; only one broker-facing portfolio

def test_spread_double_count_rejected(db, seed):
    d = seed.decision()
    o = seed.order(d)
    seed.transition(o, "VALIDATED"); seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    with raises(db, "SPREAD_DOUBLE_COUNT"):
        seed.fill(o, fill_at=NOW + timedelta(minutes=20), basis="ask", half_spread=0.01)
    seed.fill(o, fill_at=NOW + timedelta(minutes=20), basis="trade_plus_half_spread", half_spread=0.01)


def test_shadow_portfolio_cannot_receive_broker_fill(db, seed):
    d = seed.decision(portfolio_id=seed.shadow_id)
    o = seed.order(d, portfolio_id=seed.shadow_id, is_simulated=False)
    seed.transition(o, "VALIDATED"); seed.transition(o, "SUBMITTED")
    with raises(db, "SHADOW_PORTFOLIO_BROKER_FILL"):
        seed.fill(o, portfolio_id=seed.shadow_id, fill_at=NOW + timedelta(minutes=5), source="broker", basis=None, broker_fill_id="act-9")


def test_one_broker_facing_portfolio(db, seed):
    with raises(db, "portfolios_one_broker_facing|portfolios_one_primary|check"):
        db.execute("insert into portfolios (experiment_id, kind, name, touches_broker, equity_start_usd) values (%s, 'llm_variant', 'v2', true, 500)", (seed.experiment_id,))


# ---- §4.3 entries regular session only; ext-hours exits are Day limits

def test_entry_extended_hours_rejected(db, seed):
    d = seed.decision()
    with raises(db, "check"):
        seed.order(d, extended_hours=True)


def test_ext_hours_exit_must_be_day_limit(db, seed):
    d = seed.decision(decision="SELL")
    with raises(db, "check"):
        seed.order(d, side="sell", purpose="ext_hours_exit", extended_hours=True, order_type="market")
    seed.order(d, side="sell", purpose="ext_hours_exit", extended_hours=True, order_type="limit", limit_price=99)


# ---- §7.6 NO_ACTION requires a reason; earnings plan required in window

def test_no_action_requires_reason(db, seed):
    with raises(db, "decisions_no_action_reason"):
        seed.decision(decision="NO_ACTION", status="no_action", forecast_target_price_at_entry=None, forecast_invalidation_price_at_entry=None, forecast_time_stop_at_entry=None)
    seed.decision(decision="NO_ACTION", status="no_action", no_action_reason="chased_too_far", forecast_target_price_at_entry=None,
                  forecast_invalidation_price_at_entry=None, forecast_time_stop_at_entry=None)


def test_earnings_plan_required_in_window(db, seed):
    with raises(db, "decisions_earnings_plan_required"):
        seed.decision(earnings_in_window=True)
    seed.decision(earnings_in_window=True, earnings_plan="exit_before", earnings_plan_reason="binary event")


# ---- §15 idempotent broker events

def test_duplicate_broker_fill_rejected(db, seed):
    d = seed.decision()
    o = seed.order(d, is_simulated=False)
    seed.transition(o, "VALIDATED"); seed.transition(o, "SUBMITTED")
    seed.fill(o, fill_at=NOW + timedelta(minutes=5), source="broker", basis=None, broker_fill_id="act-dup")
    with raises(db, "duplicate key"):
        seed.fill(o, fill_at=NOW + timedelta(minutes=5), source="broker", basis=None, broker_fill_id="act-dup")
