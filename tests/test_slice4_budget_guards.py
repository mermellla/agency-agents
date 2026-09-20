"""T-05 budget ledger (entries exhausted → entries blocked, exits still run, BUDGET_OVERAGE_EXIT logged) and
T-17 runaway guards (halt + email audit row)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from psycopg.rows import dict_row

from tests.seed import NOW
from tests.test_slice4_risk import ctx, decision, desk, held
from tradeagent.adapters.alpaca.calendar import ET, Session, StaticCalendar
from tradeagent.adapters.email.resend import NullSender
from tradeagent.config import load_settings
from tradeagent.domain.enums import BudgetBucket, DecisionType, ExecutionMode, HaltScope, LLMStage, RejectCode
from tradeagent.domain.models import LLMCall
from tradeagent.ops.halts import Halter
from tradeagent.ops.notifications import Notifier
from tradeagent.persistence.db import Database
from tradeagent.risk.budget import BudgetDesk
from tradeagent.risk.guards import Guards

SETTINGS = load_settings(env={})


def sessions(month: date, n: int) -> StaticCalendar:
    out, d = [], month.replace(day=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(
                Session(
                    d,
                    datetime.combine(d, datetime.min.time().replace(hour=9, minute=30), tzinfo=ET),
                    datetime.combine(d, datetime.min.time().replace(hour=16), tzinfo=ET),
                )
            )
        d += timedelta(days=1)
    return StaticCalendar(out)


def call(cost: str, bucket: BudgetBucket = BudgetBucket.ENTRIES, stage: LLMStage = LLMStage.DECISION) -> LLMCall:
    return LLMCall(
        stage=stage,
        bucket=bucket,
        model="claude-sonnet-5",
        prompt_version="0.1.0",
        tokens_in=10,
        tokens_out=5,
        cost_usd=Decimal(cost),
    )


def test_allowance_prorates_and_carries_within_month(db, seed):
    db.row_factory = dict_row
    dbx = Database(db)
    cal = sessions(date(2026, 9, 1), 21)  # 21 sessions in September → $10 × 60% / 21
    bd = BudgetDesk(dbx, SETTINGS.risk.budget, cal, seed.experiment_id, clock=lambda: NOW)
    d1, d2, oct1 = date(2026, 9, 14), date(2026, 9, 15), date(2026, 10, 1)
    s1 = bd.state(d1, BudgetBucket.ENTRIES)
    assert s1.allowance_usd == Decimal("0.285714") and s1.carried_in_usd == 0
    bd.charge(call("0.10"), d1)
    s2 = bd.state(d2, BudgetBucket.ENTRIES)
    assert s2.carried_in_usd == Decimal("0.185714")  # unspent carries within the month
    assert bd.state(oct1, BudgetBucket.ENTRIES).carried_in_usd == 0  # resets on the 1st
    assert bd.state(d1, BudgetBucket.EXITS_REVIEWS).allowance_usd == Decimal("0.190476")


def test_budget_entry_exhaustion_blocks_entries_not_exits(db, seed):
    db.row_factory = dict_row
    dbx = Database(db)
    bd = BudgetDesk(dbx, SETTINGS.risk.budget, sessions(date(2026, 9, 1), 21), seed.experiment_id, clock=lambda: NOW)
    d = date(2026, 9, 14)
    state, events = bd.charge(call("0.25"), d)
    assert "budget_80pct" in events and not state.exhausted
    state, events = bd.charge(call("0.05"), d)
    assert state.exhausted and "budget_exhausted" in events
    assert bd.entries_exhausted(d)
    # the desk refuses new LLM entries but an exit passes
    assert desk().validate(decision(), ctx(entry_budget_exhausted=True)).reject_code == RejectCode.REJECTED_BUDGET
    assert desk().validate(decision(DecisionType.SELL), ctx(entry_budget_exhausted=True, open_positions=[held()])).ok
    # exits/reviews bucket: overage allowed and logged
    ex, events = bd.charge(call("0.30", BudgetBucket.EXITS_REVIEWS, LLMStage.TRIGGERED_REVIEW), d)
    assert "BUDGET_OVERAGE_EXIT" in events and ex.overage_usd == Decimal("0.109524")
    row = dbx.budget_row(seed.experiment_id, d, "entries")
    assert row["exhausted_at"] is not None


def test_guards_halt_and_email(db, seed):
    db.row_factory = dict_row
    dbx = Database(db)
    sender = NullSender()
    notifier = Notifier(dbx, sender, "owner@example.test", ExecutionMode.DRY_RUN, 1)
    halter = Halter(dbx, notifier, seed.experiment_id)
    cfg = SETTINGS.risk.guards.model_copy(
        update={"max_orders_per_day": 2, "halt_after_consecutive_rejects": 2, "max_llm_calls_per_hour": 1}
    )
    guards = Guards(dbx, cfg, halter, seed.experiment_id, seed.primary_id)
    now = datetime.now(tz=UTC)
    assert guards.orders_today_ok(now)
    for _ in range(2):
        d = seed.decision()
        seed.order(d, notional=50, reserved_notional_usd=50)
    assert not guards.orders_today_ok(now)
    assert not guards.orders_today_ok(now)  # a second check does not duplicate the halt
    st = halter.state()
    assert st.entries_halted and not st.all_halted and st.codes == ("MAX_ORDERS_PER_DAY",)
    assert sender.sent[0][1].startswith("[TA:DRY_RUN:1] HALT MAX_ORDERS_PER_DAY")
    rows = dbx.conn.execute("select kind, status from notifications order by id").fetchall()
    assert [(r["kind"], r["status"]) for r in rows] == [("halt", "sent")]
    # consecutive rejects on the primary
    for i in range(2):  # explicit created_at: every row in one test transaction shares now()
        seed.decision(
            status="rejected", reject_code="REJECTED_STALE_DATA", created_at=now + timedelta(hours=1, minutes=i)
        )
    assert not guards.consecutive_rejects_ok(seed.primary_id)
    assert "CONSECUTIVE_REJECTS" in halter.state().codes
    # LLM rate: Jev calls are exempt, model calls count
    dbx.insert_llm_call(
        call("0", stage=LLMStage.TRIAGE), seed.experiment_id, seed.phase_id, seed.versions["prompt_version"]
    )
    assert not guards.llm_rate_ok(now + timedelta(seconds=1))
    assert guards.kill_switch_ok(Decimal("500"), Decimal("400"))  # off by default
    assert not Guards(
        dbx, cfg.model_copy(update={"kill_switch_daily_loss_pct": 5.0}), halter, seed.experiment_id, seed.primary_id
    ).kill_switch_ok(Decimal("500"), Decimal("470"))
    assert HaltScope.ENTRIES.value in {str(h["scope"]) for h in dbx.open_halts(seed.experiment_id)}
