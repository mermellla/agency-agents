"""T-14b fill model (ADR-0017): ask/bid with no added spread; trade fallback ± median half-spread; slippage separate
and labelled; first eligible print after eligibility; no print → stays pending, expires after expires_at."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from psycopg.rows import dict_row

from tests.fakes_market import FakeSip, quote, trade
from tests.seed import NOW
from tradeagent.config import load_settings
from tradeagent.domain.enums import ReconstructionBasis
from tradeagent.execution.reconstruction import FillReconstructor, ReconstructionJob, usable_quote
from tradeagent.persistence.db import Database
from tradeagent.portfolios.levels import set_levels_on_open

ELIGIBLE = NOW + timedelta(minutes=4)
LATER = ELIGIBLE + timedelta(minutes=40)  # past delay (15) + margin (2) + embargo (15)
SIM = load_settings(env={}).risk.simulation


def order(**over):
    base = dict(
        id="o",
        symbol="AAPL",
        side="buy",
        order_type="market",
        order_eligible_at=ELIGIBLE,
        expires_at=None,
        notional=Decimal("100"),
        qty=None,
        stop_price=None,
        limit_price=None,
    )
    base.update(over)
    return base


def rc(sip: FakeSip, slippage: float = 0.0, now: datetime = LATER) -> FillReconstructor:
    cfg = SIM.model_copy(update={"sim_additional_slippage_bps": slippage})
    return FillReconstructor(sip, cfg, clock=lambda: now)


def test_reconstruct_ask_fill():
    sip = FakeSip(
        quotes=[
            quote("AAPL", ELIGIBLE - timedelta(seconds=5), "99.90", "100.10"),
            quote("AAPL", ELIGIBLE + timedelta(seconds=3), "100.00", "100.20"),
        ]
    )
    r = rc(sip).reconstruct(order())
    assert (
        r is not None
        and r.price == Decimal("100.200000")
        and r.basis == ReconstructionBasis.ASK
        and r.half_spread is None
    )
    assert r.fill_at == ELIGIBLE + timedelta(seconds=3)  # the quote before eligibility is never used
    s = rc(sip).reconstruct(order(side="sell", qty=Decimal("1"), notional=None))
    assert s is not None and s.price == Decimal("100.000000") and s.basis == ReconstructionBasis.BID


def test_reconstruct_trade_fallback():
    # no usable quote within 60 s (crossed / zero size), but usable quotes in ±5 min give the half-spread
    sip = FakeSip(
        quotes=[
            quote("AAPL", ELIGIBLE + timedelta(seconds=1), "100.30", "100.10"),
            quote("AAPL", ELIGIBLE + timedelta(minutes=2), "100.00", "100.30"),
            quote("AAPL", ELIGIBLE + timedelta(minutes=3), "100.00", "100.10"),
        ],
        trades=[
            trade("AAPL", ELIGIBLE - timedelta(seconds=1), "99.00"),
            trade("AAPL", ELIGIBLE + timedelta(seconds=30), "100.05"),
        ],
    )
    r = rc(sip).reconstruct(order())
    assert r is not None and r.basis == ReconstructionBasis.TRADE_PLUS_HALF_SPREAD
    assert r.half_spread == Decimal("0.100000") and r.price == Decimal("100.150000")  # median of 0.15 and 0.05
    s = rc(sip).reconstruct(order(side="sell", qty=Decimal("1"), notional=None))
    assert s is not None and s.basis == ReconstructionBasis.TRADE_MINUS_HALF_SPREAD and s.price == Decimal("99.950000")


def test_reconstruct_slippage_separate():
    sip = FakeSip(quotes=[quote("AAPL", ELIGIBLE + timedelta(seconds=1), "100.00", "100.20")])
    r = rc(sip, slippage=10).reconstruct(order())
    assert (
        r is not None
        and r.raw_price == Decimal("100.20")
        and r.price == Decimal("100.300200")
        and r.slippage_bps == Decimal("10")
    )
    assert r.basis == ReconstructionBasis.ASK and r.half_spread is None  # slippage is not spread


def test_reconstruct_first_eligible_print():
    sip = FakeSip(
        quotes=[
            quote("AAPL", ELIGIBLE + timedelta(seconds=10), "100.00", "100.20"),
            quote("AAPL", ELIGIBLE + timedelta(seconds=2), "101.00", "101.20"),
        ]
    )
    r = rc(sip).reconstruct(order())
    assert r is not None and r.fill_at == ELIGIBLE + timedelta(seconds=2) and r.price == Decimal("101.200000")
    assert (
        rc(sip, now=ELIGIBLE + timedelta(minutes=10)).reconstruct(order()) is None
    )  # before delay + margin: nothing yet
    assert not usable_quote(quote("AAPL", ELIGIBLE, "100", "102"))  # 2% spread is not usable


def test_reconstruct_expires_without_print(db, seed):
    db.row_factory = dict_row
    dbx = Database(db)
    d = seed.decision(portfolio_id=seed.shadow_id)
    o = seed.order(d, portfolio_id=seed.shadow_id, expires_at=ELIGIBLE + timedelta(hours=1))
    seed.transition(o, "VALIDATED")
    seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    job = ReconstructionJob(dbx, rc(FakeSip()), None)
    assert job.run(seed.experiment_id, ELIGIBLE + timedelta(minutes=30)) == []  # still pending inside the window
    out = job.run(seed.experiment_id, ELIGIBLE + timedelta(hours=2))
    assert out == [(o, "expired")]
    assert dbx.order(o)["status"] == "EXPIRED"


def test_reconstruction_job_fills_and_sets_levels(db, seed):
    db.row_factory = dict_row
    dbx = Database(db)
    d = seed.decision(
        portfolio_id=seed.shadow_id,
        origin="quant",
        strategy="QB-1.0",
        sip_signal_price=100,
        invalidation_price=96,
        target_price=108,
    )
    o = seed.order(d, portfolio_id=seed.shadow_id, notional=Decimal("100"), reserved_notional_usd=Decimal("100"))
    seed.transition(o, "VALIDATED")
    seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    sip = FakeSip(quotes=[quote("AAPL", ELIGIBLE + timedelta(seconds=1), "101.80", "102.00")])
    job = ReconstructionJob(
        dbx, rc(sip), None, on_applied=lambda order, applied, rec: set_levels_on_open(dbx, order, applied, rec.price)
    )
    assert job.run(seed.experiment_id, LATER) == [(o, "filled")]
    orow = dbx.order(o)
    assert orow["status"] == "FILLED" and orow["reserved_notional_usd"] == 0
    pos = dbx.open_positions(seed.shadow_id)[0]
    assert pos["symbol"] == "AAPL" and pos["qty"] == Decimal("0.980392156") and pos["is_fractional"] is True
    # quant levels re-based on the fill: stop 4 below, target 8 above the actual entry (§12)
    assert pos["working_invalidation_price"] == Decimal("98") and pos["working_target_price"] == Decimal("110")
    assert dbx.decision(d)["status"] == "executed" and dbx.decision(d)["fill_at"] == ELIGIBLE + timedelta(seconds=1)
    assert dbx.cash_balance(seed.shadow_id) == Decimal("400")  # 0.980392156 × 102 = 100.000000 at ledger precision
    assert job.run(seed.experiment_id, LATER) == []  # nothing pending: idempotent


def test_reconstruction_job_is_idempotent(db, seed):
    db.row_factory = dict_row
    dbx = Database(db)
    job = ReconstructionJob(dbx, rc(FakeSip()), None)
    assert job.run(seed.experiment_id, LATER) == []
