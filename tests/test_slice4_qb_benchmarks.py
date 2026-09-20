"""T-32 QB-1.0 rules and eligibility earlier than the LLM; deterministic simulated exits; T-38 benchmarks;
T-33 fees and dividends in every mode with the two P&L views on closed_trades."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from psycopg.rows import dict_row

from tests.fakes import FakeBroker
from tests.fakes_market import FakeSip, daily_bar, quote
from tests.seed import NOW, Seed
from tests.synthetic import bar15, daily
from tests.test_slice4_budget_guards import sessions
from tradeagent.adapters.alpaca.corporate_actions import CashDividend
from tradeagent.analytics.closed_trades import record_closed_trade
from tradeagent.config import load_settings
from tradeagent.domain.enums import ExecutionMode, MarketRegime
from tradeagent.domain.models import Candidate, ScanResult
from tradeagent.exclusions import ExclusionScreen
from tradeagent.execution.executor import Executor
from tradeagent.execution.reconstruction import FillReconstructor, ReconstructionJob
from tradeagent.fees import FeeSchedules
from tradeagent.ops.portfolio_jobs import make_on_applied
from tradeagent.persistence.db import Database
from tradeagent.portfolios.benchmarks import Benchmarks
from tradeagent.portfolios.dividends import apply_dividends
from tradeagent.portfolios.quant_baseline import QuantBaseline
from tradeagent.portfolios.sim_exits import SimulatedExitEngine
from tradeagent.risk.desk import RiskDesk
from tradeagent.scanner.scanner import ScanInputs, ScanOutput
from tradeagent.scanner.signals.technical import atr

SETTINGS = load_settings(env={})
DAY = date(2026, 9, 14)
SCAN_DONE = NOW + timedelta(seconds=20)
T0 = NOW + timedelta(minutes=1)  # QB validates one minute after the scan


class DryBroker(FakeBroker):
    mode = ExecutionMode.DRY_RUN
    observe_only = True


def make(db, seed: Seed, clock_at: datetime):
    db.row_factory = dict_row
    dbx = Database(db)
    cal = sessions(date(2026, 9, 1), 30)
    clock = lambda: clock_at  # noqa: E731
    desk = RiskDesk(SETTINGS, ExclusionScreen.from_config(SETTINGS.exclusions, SETTINGS.sic_backstop), clock=clock)
    executor = Executor(dbx, DryBroker(), SETTINGS, clock=clock)
    exits = SimulatedExitEngine(dbx, executor, seed.experiment_id, seed.phase_id, clock=clock)
    qb = QuantBaseline(dbx, desk, executor, exits, SETTINGS, cal, seed.experiment_id, seed.phase_id, clock=clock)
    return dbx, qb, cal


def scan_output(seed: Seed, symbol: str = "AAPL", price: str = "100") -> tuple[ScanOutput, ScanInputs]:
    cand = Candidate(
        candidate_id=seed.candidate_id,
        scan_id=seed.scan_id,
        symbol=symbol,
        rank=1,
        composite_score=Decimal("0.8"),
        signals=[],
        signal_bar_time=NOW - timedelta(minutes=16),
        signal_observed_at=NOW,
        sip_signal_price=Decimal(price),
        sip_signal_timestamp=NOW - timedelta(minutes=16),
    )
    result = ScanResult(
        scan_id=seed.scan_id,
        kind="intraday",
        started_at=NOW,
        scanner_completed_at=SCAN_DONE,
        bars_end_at=NOW - timedelta(minutes=16),
        regime=MarketRegime.RANGE_BOUND,
        scanner_version=seed.versions["scanner_version"],
        exclusion_list_version=seed.versions["exclusion_list_version"],
        universe_size=1,
        candidates=[cand],
        source_status=[],
    )
    bars = daily(symbol, n=40, start_price=100.0, vol=0.01, end=DAY - timedelta(days=1))
    inputs = ScanInputs(
        kind="intraday",
        now=NOW,
        session_date=DAY,
        universe=[],
        daily={symbol: bars, "SPY": bars},
        bars_end_at=NOW - timedelta(minutes=16),
    )
    return ScanOutput(result, {}, {}, [], []), inputs


def test_qb_rules_v1_and_eligibility_earlier_than_llm(db, seed):
    dbx, qb, cal = make(db, seed, T0)
    out, inputs = scan_output(seed)
    report = asyncio.run(qb.on_scan(out, inputs, NOW + timedelta(hours=5)))
    assert report.status == "validated", report
    d = dbx.decision(report.decision_id)
    assert (
        d["origin"] == "quant" and d["strategy"] == "QB-1.0" and d["decision"] == "BUY" and d["model_decision"] is None
    )
    # size 20% of $500 equity; stop 2×ATR below the signal price; target 2R; time stop 5 trading days
    a = Decimal(str(round(atr(inputs.daily["AAPL"], 14), 6)))
    assert d["proposed_notional"] == Decimal("100.00")
    assert d["invalidation_price"] == (Decimal("100") - 2 * a).quantize(Decimal("0.01"))
    assert d["target_price"] == (Decimal("100") + 4 * a).quantize(Decimal("0.01"))
    assert d["forecast_target_price_at_entry"] == d["target_price"] and d["probability_pre_critique"] == Decimal("0.5")
    assert d["time_stop_at"] == cal.add_trading_days(T0, 5)
    # order_eligible_at = scan completion + the baseline's own validation, earlier than any LLM path could be (§8.9)
    assert SCAN_DONE < d["order_eligible_at"] == T0 < T0 + timedelta(minutes=5)
    o = dbx.orders_for_decision(report.decision_id)[0]
    assert (
        o["status"] == "FILL_PENDING_RECONSTRUCTION"
        and o["is_simulated"]
        and o["reserved_notional_usd"] == Decimal("100.00")
    )
    assert dbx.available_cash(seed.shadow_id) == Decimal("400")
    # a second scan with the same candidate is a duplicate (open entry order), recorded as such
    report2 = asyncio.run(qb.on_scan(out, inputs, None))
    assert report2.status == "rejected" and report2.reject_code == "REJECTED_DUPLICATE"


def test_qb_exit_on_stop_then_closed_trade_with_fees_and_benchmarks(db, seed):
    dbx, qb, cal = make(db, seed, T0)
    out, inputs = scan_output(seed)
    report = asyncio.run(qb.on_scan(out, inputs, NOW + timedelta(hours=5)))
    assert report.status == "validated"
    # reconstruct the entry at the ask
    fees = FeeSchedules.from_config(SETTINGS.fees, SETTINGS.versions.config_version)
    sip = FakeSip(quotes=[quote("AAPL", T0 + timedelta(seconds=2), "99.80", "100.00")])
    later = T0 + timedelta(minutes=40)
    job = ReconstructionJob(
        dbx,
        FillReconstructor(sip, SETTINGS.risk.simulation, clock=lambda: later),
        fees,
        on_applied=make_on_applied(dbx, cal),
    )
    assert [s for _, s in job.run(seed.experiment_id, later)] == ["filled"]
    pos = dbx.open_positions(seed.shadow_id)[0]
    assert (
        pos["qty"] == Decimal("1") and pos["working_invalidation_price"] < Decimal("100") < pos["working_target_price"]
    )
    # benchmarks bought at the start close so the closed trade can carry SPY/VTI windows
    dbx.ensure_portfolios(seed.experiment_id, Decimal("500"))
    for p in dbx.portfolios(seed.experiment_id):
        if dbx.cash_balance(uuid.UUID(str(p["id"]))) == 0:  # the seed already funded primary and qb-1.0
            dbx.ensure_initial_equity(seed.experiment_id, seed.phase_id, p, NOW - timedelta(days=1))
    bars = [daily_bar(s, datetime(2026, 9, 14, tzinfo=UTC), c) for s, c in (("SPY", "500"), ("VTI", "250"))]
    bars += [daily_bar(s, datetime(2026, 9, 15, tzinfo=UTC), c) for s, c in (("SPY", "505"), ("VTI", "252.5"))]
    bm = Benchmarks(
        dbx, FakeSip(bars=bars), seed.experiment_id, seed.phase_id, clock=lambda: datetime(2026, 9, 16, 12, tzinfo=UTC)
    )
    assert bm.ensure_started(DAY) == ["SPY", "VTI"] and bm.ensure_started(DAY) == []
    spy = dbx.open_positions(uuid.UUID(str(dbx.portfolio_by_kind(seed.experiment_id, "benchmark_spy")["id"])))[0]
    assert spy["qty"] == Decimal("1") and spy["avg_cost"] == Decimal("500")
    latest = bm.mark(date(2026, 9, 15))
    assert latest["SPY"] == (date(2026, 9, 15), Decimal("505")) and bm.equity("benchmark_spy", {}) == Decimal("505.00")
    assert bm.equity("benchmark_cash", {}) == Decimal("500.00")
    # next session: a 15-minute bar prints below the stop → system SELL, eligible when observed, then a bid fill
    stop = pos["working_invalidation_price"]
    next_day = date(2026, 9, 15)
    b = bar15("AAPL", next_day, (10, 0), 99, 99.5, float(stop) - 1, float(stop) - 0.5)
    observe = b.retrievable_at + timedelta(minutes=1)
    qb.clock = qb.exits.clock = qb.executor.clock = lambda: observe
    actions = asyncio.run(qb.exits.check(seed.shadow_id, {"AAPL": [b]}, None))
    assert [a.trigger for a in actions] == ["stop"] and actions[0].observed_at == b.retrievable_at
    exit_order = dbx.order(actions[0].order_id)
    assert (
        exit_order["side"] == "sell"
        and exit_order["order_eligible_at"] == observe
        and exit_order["status"] == "FILL_PENDING_RECONSTRUCTION"
    )
    assert len(asyncio.run(qb.exits.check(seed.shadow_id, {"AAPL": [b]}, None))) == 0  # already exiting
    sip2 = FakeSip(quotes=[quote("AAPL", observe + timedelta(seconds=1), "95.00", "95.10")])
    later2 = observe + timedelta(minutes=40)
    job2 = ReconstructionJob(
        dbx,
        FillReconstructor(sip2, SETTINGS.risk.simulation, clock=lambda: later2),
        fees,
        on_applied=make_on_applied(dbx, cal),
    )
    assert [s for _, s in job2.run(seed.experiment_id, later2)] == ["filled"]
    assert dbx.open_positions(seed.shadow_id) == []
    ct = dbx.conn.execute("select * from closed_trades where position_id = %s", (pos["id"],)).fetchone()
    assert ct is not None and ct["exit_reason"] == "stop" and ct["discretionary_exit"] is False
    assert ct["entry_price"] == Decimal("100") and ct["exit_price"] == Decimal("95")
    assert ct["gross_pnl_usd"] == Decimal("-5") and ct["gross_return_pct"] == Decimal("-5")
    # regulatory fees on the sell: SEC $20.60/M × $95 → $0.00; TAF $0.000195 × 1 → $0.00; CAT is pass-through-unverified
    assert ct["regulatory_fees_usd"] == Decimal("0") and ct["unverified_pass_through_fees_usd"] >= 0
    assert (
        ct["net_return_pct"] == ct["gross_return_pct"]
        and ct["net_return_after_computational_costs_pct"] == ct["net_return_pct"]
    )
    assert ct["holding_bucket"] == "overnight" and ct["overnight_gap_exposure"] is True
    assert ct["benchmark_spy_return_pct"] == Decimal("1") and ct["benchmark_vti_return_pct"] == Decimal("1")
    assert ct["qb_rules_version"] == seed.versions["qb_rules_version"]
    assert record_closed_trade(dbx, uuid.UUID(str(pos["id"])), None, cal) == ct["id"]  # idempotent


def test_fee_engine_schedule_on_reconstructed_sell(db, seed):
    """T-33: fees apply in DRY_RUN exactly as they would at the broker; a $10,000 sale shows non-zero lines."""
    fees = FeeSchedules.from_config(SETTINGS.fees, SETTINGS.versions.config_version)
    lines = fees.for_sell(date(2026, 9, 15), Decimal("100"), Decimal("10000"))
    by_kind = {ln.kind.value: ln for ln in lines}
    assert by_kind["sec_section_31"].amount_usd == Decimal("0.21") and by_kind["finra_taf"].amount_usd == Decimal(
        "0.02"
    )
    assert by_kind["cat"].classification == "pass_through_unverified"


def test_dividend_credit(db, seed):
    db.row_factory = dict_row
    dbx = Database(db)
    d = seed.decision(portfolio_id=seed.shadow_id)
    o = seed.order(d, portfolio_id=seed.shadow_id)
    seed.transition(o, "VALIDATED")
    seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    seed.fill(o, portfolio_id=seed.shadow_id, fill_at=NOW + timedelta(minutes=5))  # 1 share of AAPL
    div = CashDividend("AAPL", Decimal("0.26"), date(2026, 9, 16), date(2026, 9, 30), date(2026, 9, 16))
    ports = dbx.portfolios(seed.experiment_id)
    assert (
        apply_dividends(dbx, seed.experiment_id, seed.phase_id, ports, [div], date(2026, 9, 20)) == []
    )  # not payable yet
    written = apply_dividends(dbx, seed.experiment_id, seed.phase_id, ports, [div], date(2026, 9, 30))
    assert written == [("qb-1.0", "AAPL", Decimal("0.26"))]  # only the holder at the ex-date
    assert apply_dividends(dbx, seed.experiment_id, seed.phase_id, ports, [div], date(2026, 9, 30)) == []  # idempotent
    row = dbx.conn.execute(
        "select kind, amount_usd, settles_on, balance_after_usd from cash_ledger where portfolio_id = %s order by id desc limit 1",
        (seed.shadow_id,),
    ).fetchone()
    assert row["kind"] == "dividend" and row["amount_usd"] == Decimal("0.26") and row["settles_on"] == date(2026, 9, 30)
    assert dbx.verify_cash_chain(seed.shadow_id)
