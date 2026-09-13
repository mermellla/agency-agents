"""ADR-0021: atomic buying-power reservation. Two concurrent BUY validations cannot collectively reserve more virtual
capital than the portfolio has; open orders reserve capital until fill, cancel, reject, or expiry."""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.seed import NOW, Seed

pytestmark = pytest.mark.db
psycopg = pytest.importorskip("psycopg")


@contextmanager
def raises(db, match):
    with pytest.raises(psycopg.Error, match=match):
        with db.transaction():
            yield


def available(db, pid) -> Decimal:
    return db.execute("select portfolio_available_cash(%s)", (pid,)).fetchone()[0]


def test_reservation_required_and_bounded(db, seed):
    d = seed.decision()
    with raises(db, "RESERVATION_TOO_SMALL"):
        seed.order(d, reserved_notional_usd=0)
    with raises(db, "RESERVATION_TOO_SMALL"):
        seed.order(d, notional=200, reserved_notional_usd=150)
    with raises(db, "INSUFFICIENT_VIRTUAL_CASH"):
        seed.order(
            d, notional=600, reserved_notional_usd=600
        )  # broker would accept ($100k paper account); the ledger will not
    seed.order(d, notional=300, reserved_notional_usd=300)
    assert available(db, seed.primary_id) == Decimal("200")


def test_sequential_over_reservation_rejected(db, seed):
    d1, d2 = seed.decision(), seed.decision()
    seed.order(d1, notional=300, reserved_notional_usd=300)
    with raises(db, "INSUFFICIENT_VIRTUAL_CASH"):
        seed.order(d2, notional=300, reserved_notional_usd=300)
    seed.order(d2, notional=200, reserved_notional_usd=200)
    assert available(db, seed.primary_id) == Decimal("0")


def test_release_on_cancel_reject_expiry(db, seed):
    for terminal, path in (
        ("CANCELLED", ["VALIDATED", "SUBMITTED"]),
        ("REJECTED", ["VALIDATED"]),
        ("EXPIRED", ["VALIDATED", "FILL_PENDING_RECONSTRUCTION"]),
    ):
        d = seed.decision()
        o = seed.order(d, notional=400, reserved_notional_usd=400, is_simulated=(terminal != "CANCELLED"))
        assert available(db, seed.primary_id) == Decimal("100")
        for s in path:
            seed.transition(o, s)
        seed.transition(o, terminal)
        assert available(db, seed.primary_id) == Decimal("500"), terminal


def test_fill_converts_reservation_to_cost_basis(db, seed):
    d = seed.decision()
    o = seed.order(d, notional=300, reserved_notional_usd=300)
    seed.transition(o, "VALIDATED")
    seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    # the executor's atomic unit: fill row (shrinks the reservation) + cash debit, same transaction
    db.execute(
        """insert into fills (order_id, portfolio_id, experiment_id, experiment_phase_id, symbol, side, qty, price, notional, fill_at, fill_source, reconstruction_basis)
                  values (%s, %s, %s, %s, 'AAPL', 'buy', 3, 100, 300, %s, 'reconstructed', 'ask')""",
        (o, seed.primary_id, seed.experiment_id, seed.phase_id, NOW + timedelta(minutes=20)),
    )
    db.execute(
        "insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, kind, amount_usd, balance_after_usd, idempotency_key) values (%s, %s, %s, 'buy', -300, 200, %s)",
        (seed.primary_id, seed.experiment_id, seed.phase_id, uuid.uuid4().hex),
    )
    assert db.execute("select reserved_notional_usd from orders where id = %s", (o,)).fetchone()[0] == 0
    assert available(db, seed.primary_id) == Decimal("200")
    seed.transition(o, "FILLED")
    assert available(db, seed.primary_id) == Decimal("200")


def test_reservation_cannot_grow(db, seed):
    d = seed.decision()
    o = seed.order(d, notional=100, reserved_notional_usd=100)
    with raises(db, "RESERVATION_INCREASE_FORBIDDEN"):
        db.execute("update orders set reserved_notional_usd = 400 where id = %s", (o,))


def test_sell_orders_reserve_nothing(db, seed):
    d = seed.decision(decision="SELL")
    with raises(db, "RESERVATION_ON_SELL"):
        seed.order(d, side="sell", purpose="exit", reserved_notional_usd=50)
    seed.order(d, side="sell", purpose="exit", reserved_notional_usd=0)


def test_concurrent_buys_cannot_over_reserve(migrated_db_url):
    """Two connections, two transactions, each validating a $300 BUY against the same $500 portfolio at the same time.
    Whichever takes the portfolio lock second must see the first reservation and fail."""
    setup = psycopg.connect(migrated_db_url)
    setup.execute("set search_path = trading, public")
    seed = Seed.create(setup)
    d1, d2 = seed.decision(), seed.decision()
    setup.commit()

    results: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def attempt(name: str, decision_id) -> None:
        conn = psycopg.connect(migrated_db_url)
        try:
            conn.execute("set search_path = trading, public")
            barrier.wait(timeout=10)
            conn.execute(
                """insert into orders (decision_id, portfolio_id, experiment_id, experiment_phase_id, client_order_id, purpose, symbol, side, order_type,
                   time_in_force, notional, is_simulated, order_eligible_at, reserved_notional_usd)
                   values (%s, %s, %s, %s, %s, 'entry', 'AAPL', 'buy', 'market', 'day', 300, true, %s, 300)""",
                (
                    decision_id,
                    seed.primary_id,
                    seed.experiment_id,
                    seed.phase_id,
                    f"{decision_id}:1",
                    NOW + timedelta(minutes=4),
                ),
            )
            # hold the transaction open briefly so the other connection must wait on the advisory lock
            time.sleep(0.5)
            conn.commit()
            results[name] = "committed"
        except psycopg.Error as exc:
            conn.rollback()
            results[name] = str(exc).splitlines()[0]
        finally:
            conn.close()

    threads = [threading.Thread(target=attempt, args=("a", d1)), threading.Thread(target=attempt, args=("b", d2))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    outcomes = sorted(results.values())
    assert sum(1 for v in outcomes if v == "committed") == 1, results
    assert any("INSUFFICIENT_VIRTUAL_CASH" in v for v in outcomes), results
    check = psycopg.connect(migrated_db_url)
    check.execute("set search_path = trading, public")
    assert check.execute("select portfolio_reserved_cash(%s)", (seed.primary_id,)).fetchone()[0] == Decimal("300")
    assert check.execute("select portfolio_available_cash(%s)", (seed.primary_id,)).fetchone()[0] == Decimal("200")
    check.close()
    setup.close()
