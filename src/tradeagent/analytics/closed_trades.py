"""`closed_trades` row when a position closes (§9 two P&L views, §8.3 overnight_gap_exposure, §8.9 benchmark windows
from fill_at). MFE/MAE and post-exit returns are filled by the analytics job (Slice 7) from bars after fill_at."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.adapters.alpaca.calendar import ET, StaticCalendar
from tradeagent.persistence.db import Database

PCT = Decimal("0.000001")


def _vwap(fills: list[dict[str, Any]]) -> tuple[Decimal, Decimal, Decimal]:
    qty = sum((Decimal(str(f["qty"])) for f in fills), Decimal(0))
    notional = sum((Decimal(str(f["notional"])) for f in fills), Decimal(0))
    return qty, notional, (notional / qty if qty else Decimal(0))


def holding_bucket(entry_at: datetime, exit_at: datetime, cal: StaticCalendar | None) -> str:
    d0, d1 = entry_at.astimezone(ET).date(), exit_at.astimezone(ET).date()
    if d0 == d1:
        return "intraday"
    if cal is not None and cal.is_session(d0):
        try:
            if cal.next_session(d0).day == d1:
                return "overnight"
        except LookupError:
            pass
    elif (d1 - d0).days == 1:
        return "overnight"
    return "multi_day"


def benchmark_return(db: Database, symbol: str, entry_at: datetime, exit_at: datetime) -> Decimal | None:
    a = db.benchmark_close_on_or_before(symbol, entry_at.astimezone(ET).date())
    b = db.benchmark_close_on_or_before(symbol, exit_at.astimezone(ET).date())
    if a is None or b is None or a[1] == 0:
        return None
    return ((b[1] - a[1]) / a[1] * 100).quantize(PCT)


def record_closed_trade(
    db: Database, position_id: UUID, exit_decision_id: UUID | None, cal: StaticCalendar | None = None
) -> UUID | None:
    pos = db.position(position_id)
    if pos is None or str(pos["status"]) != "closed":
        return None
    fills = db.fills_for_position(position_id)
    buys = [f for f in fills if str(f["side"]) == "buy"]
    sells = [f for f in fills if str(f["side"]) == "sell"]
    if not buys or not sells:
        return None
    bq, bn, bp = _vwap(buys)
    sq, sn, sp = _vwap(sells)
    entry_at: datetime = min(f["fill_at"] for f in buys)
    exit_at: datetime = max(f["fill_at"] for f in sells)
    entry = db.decision(UUID(str(pos["entry_decision_id"])))
    assert entry is not None
    exit_d = db.decision(exit_decision_id) if exit_decision_id else None
    fees = db.fees_for_position(position_id)
    reg = fees.get("customer_debited", Decimal(0))
    unverified = fees.get("pass_through_unverified", Decimal(0))
    review_ids = [
        UUID(str(r["decision_id"]))
        for r in db.conn_rows("select decision_id from decisions where position_id = %s", (position_id,))
    ]
    llm_cost = db.llm_cost_for_decisions(list({UUID(str(entry["decision_id"])), *review_ids}))
    gross = sn - bn
    gross_pct = (gross / bn * 100).quantize(PCT) if bn else Decimal(0)
    net_pct = ((gross - reg) / bn * 100).quantize(PCT) if bn else Decimal(0)
    net_after = ((gross - reg - llm_cost) / bn * 100).quantize(PCT) if bn else Decimal(0)
    bucket = holding_bucket(entry_at, exit_at, cal)
    portfolio = db.conn.execute(
        "select touches_broker from portfolios where id = %s", (pos["portfolio_id"],)
    ).fetchone()
    touches = bool(portfolio and portfolio["touches_broker"])
    overnight_exposure = bucket != "intraday" and (bool(pos["is_fractional"]) or not touches)
    origin = str(exit_d["origin"]) if exit_d else "system"
    reason = (exit_d.get("trigger_reason") if exit_d else None) or (
        "discretionary_sell" if origin == "llm" else "unknown"
    )
    cols: dict[str, Any] = dict(
        position_id=position_id,
        portfolio_id=pos["portfolio_id"],
        experiment_id=pos["experiment_id"],
        experiment_phase_id=pos["opened_in_phase_id"],
        symbol=pos["symbol"],
        strategy=entry.get("strategy"),
        market_regime=entry.get("market_regime"),
        catalyst_type=entry.get("catalyst"),
        entry_decision_id=entry["decision_id"],
        entry_fill_at=entry_at,
        exit_fill_at=exit_at,
        holding_seconds=int((exit_at - entry_at).total_seconds()),
        holding_bucket=bucket,
        qty=sq,
        entry_price=bp.quantize(PCT),
        exit_price=sp.quantize(PCT),
        gross_pnl_usd=gross.quantize(PCT),
        gross_return_pct=gross_pct,
        net_return_pct=net_pct,
        initial_confidence=entry.get("confidence"),
        probability_pre_critique=entry.get("probability_pre_critique"),
        probability_post_critique=entry.get("probability_post_critique"),
        expected_upside_percent=entry.get("expected_upside_percent"),
        expected_downside_percent=entry.get("expected_downside_percent"),
        forecast_outcome=entry.get("forecast_outcome"),
        discretionary_exit=origin == "llm",
        exit_reason=reason,
        earnings_plan=entry.get("earnings_plan"),
        overnight_gap_exposure=overnight_exposure,
        benchmark_spy_return_pct=benchmark_return(db, "SPY", entry_at, exit_at),
        benchmark_vti_return_pct=benchmark_return(db, "VTI", entry_at, exit_at),
        llm_cost_usd=llm_cost,
        regulatory_fees_usd=reg,
        unverified_pass_through_fees_usd=unverified,
        net_return_after_computational_costs_pct=net_after,
        prompt_version=entry["prompt_version"],
        exclusion_list_version=entry["exclusion_list_version"],
        config_version=entry["config_version"],
        scanner_version=entry["scanner_version"],
        qb_rules_version=entry["qb_rules_version"],
    )
    return db.insert_closed_trade(cols)
