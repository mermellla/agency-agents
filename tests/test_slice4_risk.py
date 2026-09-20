"""§8.1 risk desk: T-01 (virtual-cash cap ignores broker buying power), T-02 (limits, overfill cap), T-03 (horizon,
latency floor), T-06 desk half (excluded name rejected even if proposed), T-07 (ETHICAL_HOLD semantics), T-12
(research-grade price never used), T-15 (staleness, fallback tolerance); rejection order; drift advisory."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from tradeagent.config import load_settings
from tradeagent.domain.enums import (
    DecisionKind,
    DecisionOrigin,
    DecisionStatus,
    DecisionType,
    ExecutionMode,
    RejectCode,
    TrustGrade,
)
from tradeagent.domain.models import Decision, ForecastContract, Proposal, Timeline
from tradeagent.exclusions import ExclusionScreen
from tradeagent.risk.desk import ReferencePrice, RiskDesk, ValidationContext, parse_hold
from tradeagent.risk.sizing import position_limit_breach, reservation_for, whole_share_cap

NOW = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)  # 11:00 ET
VERSIONS = dict(
    prompt_version="0.1.0",
    exclusion_list_version="2026.09.13-r1",
    config_version="c",
    scanner_version="1.0.0",
    qb_rules_version="1.0.0",
)
SETTINGS = load_settings(env={})


def desk() -> RiskDesk:
    return RiskDesk(
        SETTINGS, ExclusionScreen.from_config(SETTINGS.exclusions, SETTINGS.sic_backstop), clock=lambda: NOW
    )


def instrument(symbol: str = "AAPL", **over: Any) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "tradable": True,
        "fractionable": True,
        "universe_status": "eligible",
        "sic": 3571,
        **over,
    }


def ref(
    price: str = "100",
    age_min: int = 1,
    grade: TrustGrade = TrustGrade.EXECUTION,
    source: str = "alpaca-iex",
    fallback: bool = False,
):
    return ReferencePrice(Decimal(price), NOW - timedelta(minutes=age_min), grade, source, fallback)


def decision(
    kind: DecisionType = DecisionType.BUY,
    notional: str = "100",
    hold: timedelta = timedelta(days=2),
    time_stop: datetime | None = None,
    origin: DecisionOrigin = DecisionOrigin.LLM,
    symbol: str = "AAPL",
) -> Decision:
    ts = time_stop or NOW + timedelta(days=5)
    entry = kind in (DecisionType.BUY, DecisionType.ADD)
    short = kind in (DecisionType.SHORT, DecisionType.COVER)
    proposal = Proposal(
        decision=kind,
        ticker=symbol,
        strategy="test",
        direction="long",
        proposed_notional=Decimal(notional) if kind != DecisionType.SELL else None,
        entry_type="market",
        entry_price_or_range=None,
        target_price=Decimal("110") if entry else None,
        invalidation_price=Decimal("95") if entry else None,
        time_stop_at=ts if entry else None,
        expected_holding_period=hold if entry else None,
        probability=Decimal("0.55") if entry else None,
    )
    return Decision(
        experiment_id=uuid.uuid4(),
        experiment_phase_id=uuid.uuid4(),
        portfolio_id=uuid.uuid4(),
        origin=origin,
        kind=DecisionKind.ENTRY if entry else DecisionKind.TRIGGERED_REVIEW,
        status=DecisionStatus.REJECTED if short else DecisionStatus.PROPOSED,
        reject_code=RejectCode.REJECTED_ACCOUNT_INELIGIBLE if short else None,
        timeline=Timeline(signal_observed_at=NOW - timedelta(minutes=20), risk_validation_completed_at=NOW),
        sip_signal_price=Decimal("99"),
        proposal=proposal,
        forecast=ForecastContract(
            target_price_at_entry=Decimal("110"),
            invalidation_price_at_entry=Decimal("95"),
            time_stop_at_entry=ts,
            probability_pre_critique=Decimal("0.55"),
        )
        if entry
        else None,
        **VERSIONS,
    )


def ctx(**over: Any) -> ValidationContext:
    base: dict[str, Any] = dict(
        now=NOW,
        mode=ExecutionMode.DRY_RUN,
        instrument=instrument(),
        available_cash=Decimal("500"),
        equity=Decimal("500"),
        open_positions=[],
        marks={},
        reference=ref(),
        sip_bar_end=NOW - timedelta(minutes=16),
    )
    base.update(over)
    return ValidationContext(**base)


def held(symbol: str = "AAPL", qty: str = "1", **over: Any) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "qty": Decimal(qty),
        "avg_cost": Decimal("100"),
        "ethical_hold": False,
        "needs_ethical_review": False,
        **over,
    }


# ---- T-01 virtual-cash cap: the context carries no broker buying power at all
def test_virtual_cash_cap_ignores_broker_buying_power():
    v = desk().validate(decision(notional="600"), ctx(available_cash=Decimal("500"), equity=Decimal("100000")))
    assert v.reject_code == RejectCode.REJECTED_VIRTUAL_CASH
    assert desk().validate(decision(notional="400"), ctx(equity=Decimal("1000"))).ok
    # reserved capital counts: $500 cash with $450 reserved leaves $50
    assert (
        desk().validate(decision(notional="100"), ctx(available_cash=Decimal("50"))).reject_code
        == RejectCode.REJECTED_VIRTUAL_CASH
    )


# ---- T-02 position limits and overfill guard
def test_sizing_limits():
    limits = SETTINGS.risk.position_limits
    assert position_limit_breach(Decimal("250"), Decimal("500"), [], "AAPL", limits, {}) is not None  # 50% > 40%
    assert position_limit_breach(Decimal("150"), Decimal("500"), [], "AAPL", limits, {}) is None
    assert "entire balance" in (position_limit_breach(Decimal("500"), Decimal("500"), [], "AAPL", limits, {}) or "")
    five = [held(s) for s in ("A", "B", "C", "D", "E")]
    assert "MAX_POSITIONS" in (position_limit_breach(Decimal("50"), Decimal("500"), five, "F", limits, {}) or "")
    assert (
        position_limit_breach(Decimal("50"), Decimal("500"), five, "A", limits, {"A": Decimal("100")}) is None
    )  # ADD to a held name
    assert "MAX_SINGLE" in (
        position_limit_breach(Decimal("150"), Decimal("500"), [held("A")], "A", limits, {"A": Decimal("100")}) or ""
    )
    v = desk().validate(decision(notional="250"), ctx())
    assert v.reject_code == RejectCode.REJECTED_POSITION_LIMIT


def test_overfill_buffer_whole_share():
    from tradeagent.domain.enums import OrderPurpose, OrderSide, OrderType, TimeInForce
    from tradeagent.domain.models import OrderRequest

    assert whole_share_cap(Decimal("500"), 1.0) == Decimal("495.00")
    req = OrderRequest(
        decision_id=uuid.uuid4(),
        portfolio_id=uuid.uuid4(),
        purpose=OrderPurpose.ENTRY,
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        qty=Decimal("3"),
        is_simulated=True,
        order_eligible_at=NOW,
    )
    assert reservation_for(req, Decimal("100"), 1.0) == Decimal("303.00")  # qty × ref × (1 + 1%)
    with pytest.raises(ValueError):
        reservation_for(req, None, 1.0)
    # a non-fractionable instrument gets a whole-share order capped at cash × (1 − buffer)
    d = decision(notional="200")
    orders = desk().construct_orders(
        d,
        ctx(instrument=instrument(fractionable=False), available_cash=Decimal("150")),
        is_simulated=True,
        expires_at=None,
    )
    assert orders[0].qty == Decimal("1") and orders[0].notional is None  # 148.50 / 100 → 1 share
    orders = desk().construct_orders(d, ctx(), is_simulated=True, expires_at=None)
    assert orders[0].notional == Decimal("200.00") and orders[0].qty is None


# ---- T-03 horizon bounds and latency floor
def test_horizon_bounds():
    assert parse_hold("1h") == timedelta(hours=1) and parse_hold("10 trading days") == timedelta(days=14)
    assert desk().validate(decision(hold=timedelta(minutes=45)), ctx()).reject_code == RejectCode.REJECTED_HORIZON
    assert desk().validate(decision(hold=timedelta(days=20)), ctx()).reject_code == RejectCode.REJECTED_HORIZON


def test_latency_floor_rejects_under_30min():
    d = desk()
    v = d.validate(decision(hold=timedelta(hours=1), time_stop=NOW + timedelta(minutes=20)), ctx())
    assert v.reject_code == RejectCode.REJECTED_LATENCY_FLOOR
    late = NOW.replace(hour=19, minute=10)  # 15:10 ET: a same-day time stop cannot satisfy MIN_EXPECTED_HOLD (ADR-0011)
    v = d.validate(
        decision(hold=timedelta(hours=1), time_stop=late + timedelta(minutes=35)),
        ctx(
            now=late,
            sip_bar_end=late - timedelta(minutes=16),
            reference=ReferencePrice(Decimal("100"), late - timedelta(minutes=1), TrustGrade.EXECUTION, "alpaca-iex"),
        ),
    )
    assert v.reject_code == RejectCode.REJECTED_LATENCY_FLOOR
    assert d.validate(decision(hold=timedelta(hours=2), time_stop=NOW + timedelta(hours=3)), ctx()).ok


# ---- T-06 desk half: excluded even if proposed; NEEDS_ETHICAL_REVIEW for default-deny SIC
def test_risk_desk_rejects_excluded_even_if_proposed():
    v = desk().validate(decision(symbol="XOM"), ctx(instrument=instrument("XOM", sic=2911)))
    assert v.reject_code == RejectCode.REJECTED_ETHICAL_SCREEN
    v = desk().validate(decision(symbol="ZZZ"), ctx(instrument=instrument("ZZZ", sic=4924)))
    assert v.reject_code == RejectCode.NEEDS_ETHICAL_REVIEW
    # exits from an excluded name are allowed (§14 rule 8)
    v = desk().validate(
        decision(DecisionType.SELL, symbol="XOM"),
        ctx(instrument=instrument("XOM", sic=2911), open_positions=[held("XOM")]),
    )
    assert v.ok


# ---- T-07 ETHICAL_HOLD: no ADD, SELL/REDUCE allowed, no re-entry
def test_ethical_hold_semantics():
    d = desk()
    pos = [held("AAPL", ethical_hold=True)]
    assert (
        d.validate(decision(DecisionType.ADD), ctx(open_positions=pos)).reject_code
        == RejectCode.REJECTED_ETHICAL_HOLD_ADD
    )
    assert d.validate(decision(DecisionType.SELL), ctx(open_positions=pos)).ok
    assert d.validate(decision(DecisionType.REDUCE, notional="50"), ctx(open_positions=pos)).ok
    # after the exit the name is on the list: re-entry is an ethical-screen rejection
    v = d.validate(decision(symbol="XOM"), ctx(instrument=instrument("XOM", sic=2911)))
    assert v.reject_code == RejectCode.REJECTED_ETHICAL_SCREEN


# ---- T-12 research-grade prices never size, stop, or pass staleness
def test_risk_desk_ignores_research_grade_prices():
    v = desk().validate(decision(), ctx(reference=ref(grade=TrustGrade.RESEARCH, source="yahoo-scrape")))
    assert v.reject_code == RejectCode.REJECTED_STALE_DATA and "research" in v.detail["reason"]
    assert desk().validate(decision(), ctx(reference=None)).reject_code == RejectCode.REJECTED_STALE_DATA


# ---- T-15 staleness and fallback tolerance
def test_staleness_rejections():
    d = desk()
    assert d.validate(decision(), ctx(reference=ref(age_min=6))).reject_code == RejectCode.REJECTED_STALE_DATA
    assert (
        d.validate(decision(), ctx(sip_bar_end=NOW - timedelta(minutes=25))).reject_code
        == RejectCode.REJECTED_STALE_DATA
    )
    sip_ref = ReferencePrice(Decimal("100"), NOW - timedelta(minutes=16), TrustGrade.EXECUTION, "alpaca-sip")
    assert d.validate(
        decision(origin=DecisionOrigin.QUANT), ctx(reference=sip_ref)
    ).ok  # SIP references use the bar rule


def test_fallback_tolerance():
    d = desk()
    ok = d.validate(decision(), ctx(reference=ref("100.5", fallback=True), last_known_price=Decimal("100")))
    assert ok.ok
    bad = d.validate(decision(), ctx(reference=ref("102", fallback=True), last_known_price=Decimal("100")))
    assert bad.reject_code == RejectCode.REJECTED_STALE_DATA


# ---- ordering, budget, duplicate, halts, SHORT, drift
def test_pipeline_order_budget_duplicate_halts_short_and_drift():
    d = desk()
    assert d.validate(decision(DecisionType.SHORT), ctx()).reject_code == RejectCode.REJECTED_ACCOUNT_INELIGIBLE
    assert d.validate(decision(), ctx(entries_halted=True)).reject_code == RejectCode.REJECTED_HALTED
    assert d.validate(
        decision(DecisionType.SELL), ctx(entries_halted=True, open_positions=[held()])
    ).ok  # exits keep running
    assert (
        d.validate(decision(DecisionType.SELL), ctx(all_halted=True, open_positions=[held()])).reject_code
        == RejectCode.REJECTED_HALTED
    )
    assert d.validate(decision(), ctx(entry_budget_exhausted=True)).reject_code == RejectCode.REJECTED_BUDGET
    assert d.validate(decision(origin=DecisionOrigin.QUANT), ctx(entry_budget_exhausted=True)).ok  # zero-cost baseline
    assert d.validate(decision(), ctx(duplicate=True)).reject_code == RejectCode.REJECTED_DUPLICATE
    # the exclusion layer precedes eligibility, cash and staleness: an excluded name with every other failure still reports the screen
    v = d.validate(
        decision(symbol="XOM", notional="900"),
        ctx(instrument=instrument("XOM", sic=2911, tradable=False), reference=None),
    )
    assert v.reject_code == RejectCode.REJECTED_ETHICAL_SCREEN
    # drift fields are advisory: a 1% move since the signal is recorded, never a rejection
    v = d.validate(decision(), ctx(reference=ref("100")))
    assert v.ok and v.drift["signal_to_order_move_pct"] == Decimal("1.010101")
