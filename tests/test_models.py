from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tradeagent.domain import (
    AccountConfiguration,
    BrokerPolicy,
    DecisionType,
    EarningsPlan,
    Fill,
    FillSource,
    OrderPurpose,
    OrderRequest,
    OrderSide,
    OrderType,
    Proposal,
    ReconstructionBasis,
    TimeInForce,
    Timeline,
    client_order_id,
)

T0 = datetime(2026, 9, 14, 14, 30, tzinfo=UTC)


def entry(**kw) -> dict:
    base = dict(
        decision=DecisionType.BUY,
        ticker="AAPL",
        strategy="momentum",
        direction="long",
        proposed_notional=Decimal("100"),
        entry_type="market",
        entry_price_or_range="mkt",
        target_price=Decimal("110"),
        invalidation_price=Decimal("95"),
        time_stop_at=T0 + timedelta(days=5),
        expected_holding_period=timedelta(days=2),
        probability=Decimal("0.55"),
    )
    base.update(kw)
    return base


def test_no_action_requires_reason():
    with pytest.raises(ValidationError):
        Proposal(
            decision=DecisionType.NO_ACTION,
            ticker=None,
            strategy=None,
            direction=None,
            entry_type=None,
            entry_price_or_range=None,
            target_price=None,
            invalidation_price=None,
            time_stop_at=None,
            expected_holding_period=None,
        )
    Proposal(
        decision=DecisionType.NO_ACTION,
        ticker=None,
        strategy=None,
        direction=None,
        entry_type=None,
        entry_price_or_range=None,
        target_price=None,
        invalidation_price=None,
        time_stop_at=None,
        expected_holding_period=None,
        no_action_reason="chased_too_far",
    )


def test_entry_requires_levels_and_probability():
    with pytest.raises(ValidationError):
        Proposal(**entry(probability=None))
    with pytest.raises(ValidationError):
        Proposal(**entry(invalidation_price=Decimal("120")))


def test_earnings_plan_required():
    with pytest.raises(ValidationError):
        Proposal(**entry(earnings_in_window=True))
    Proposal(
        **entry(
            earnings_in_window=True,
            earnings_plan=EarningsPlan.HOLD_THROUGH,
            earnings_plan_reason="guidance already given",
        )
    )


def test_timeline_anti_look_ahead():
    with pytest.raises(ValidationError):
        Timeline(signal_observed_at=T0, risk_validation_completed_at=T0 - timedelta(seconds=1))
    t = Timeline(signal_observed_at=T0, risk_validation_completed_at=T0 + timedelta(minutes=4))
    assert t.order_eligible_at == T0 + timedelta(minutes=4)
    with pytest.raises(ValidationError):
        Timeline(
            signal_observed_at=T0,
            risk_validation_completed_at=T0 + timedelta(minutes=4),
            fill_at=T0 + timedelta(minutes=3),
        )


def test_client_order_id_deterministic_and_bounded():
    d = uuid4()
    assert client_order_id(d) == client_order_id(d, 1) == f"{d}:1"
    assert len(client_order_id(d, 999)) <= 128
    with pytest.raises(ValueError):
        client_order_id(d, 0)


def test_order_request_rules():
    base = dict(decision_id=uuid4(), portfolio_id=uuid4(), symbol="AAPL", is_simulated=True, order_eligible_at=T0)
    with pytest.raises(ValidationError):  # qty xor notional
        OrderRequest(
            **base,
            purpose=OrderPurpose.ENTRY,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )
    with pytest.raises(ValidationError):  # entry must be a regular-session buy
        OrderRequest(
            **base,
            purpose=OrderPurpose.ENTRY,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            notional=Decimal("100"),
        )
    with pytest.raises(ValidationError):  # ext hours exit must be Day limit
        OrderRequest(
            **base,
            purpose=OrderPurpose.EXT_HOURS_EXIT,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            qty=Decimal("1"),
            extended_hours=True,
        )
    with pytest.raises(ValidationError):  # fractional qty is Day-only
        OrderRequest(
            **base,
            purpose=OrderPurpose.PROTECTIVE_STOP,
            side=OrderSide.SELL,
            order_type=OrderType.STOP,
            time_in_force=TimeInForce.GTC,
            qty=Decimal("0.5"),
            stop_price=Decimal("95"),
        )
    ok = OrderRequest(
        **base,
        purpose=OrderPurpose.PROTECTIVE_STOP,
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        time_in_force=TimeInForce.DAY,
        qty=Decimal("0.5"),
        stop_price=Decimal("95"),
    )
    assert ok.client_order_id.endswith(":1")


def test_fill_spread_rules():
    base = dict(
        order_id=uuid4(),
        portfolio_id=uuid4(),
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=Decimal("1"),
        price=Decimal("100"),
        fill_at=T0,
    )
    with pytest.raises(ValidationError):
        Fill(**base, source=FillSource.RECONSTRUCTED)  # basis required
    with pytest.raises(ValidationError):
        Fill(
            **base,
            source=FillSource.RECONSTRUCTED,
            reconstruction_basis=ReconstructionBasis.ASK,
            half_spread_estimate=Decimal("0.01"),
        )
    f = Fill(
        **base,
        source=FillSource.RECONSTRUCTED,
        reconstruction_basis=ReconstructionBasis.TRADE_PLUS_HALF_SPREAD,
        half_spread_estimate=Decimal("0.01"),
        shadow_estimate_price=Decimal("99.5"),
    )
    assert round(f.paper_fill_minus_shadow_estimate_bps, 2) == Decimal("50.25")
    with pytest.raises(ValidationError):
        Fill(**base, source=FillSource.BROKER)  # broker fill id required


def test_broker_policy_match():
    p = BrokerPolicy()
    good = dict(
        max_margin_multiplier="1",
        no_shorting=True,
        max_options_trading_level=0,
        fractional_trading=True,
        disable_overnight_trading=True,
    )
    assert p.matches(AccountConfiguration(**good))
    assert not p.matches(AccountConfiguration(**{**good, "max_margin_multiplier": "2"}))
    assert not p.matches(AccountConfiguration(**{**good, "no_shorting": False}))
    assert not p.matches(AccountConfiguration(**{**good, "disable_overnight_trading": False}))
    assert not p.matches(AccountConfiguration(**{**good, "fractional_trading": None}))  # unknown ≠ verified
    assert p.as_patch() == good
    for bad in (
        {"max_margin_multiplier": "2"},
        {"no_shorting": False},
        {"fractional_trading": False},
        {"disable_overnight_trading": False},
    ):
        with pytest.raises(ValidationError):
            BrokerPolicy(**bad)  # the policy itself cannot be loosened in code
