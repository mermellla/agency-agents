"""T-55: exposure and bucket checks; NullBroker contract; calendar adapter; scheduler (T-35)."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import respx

from tradeagent.adapters.alpaca.broker_null import NoBrokerInDryRun, NullBroker
from tradeagent.adapters.alpaca.calendar import ET, AlpacaCalendar, Session, StaticCalendar
from tradeagent.adapters.alpaca.client import PAPER_TRADING_URL, AlpacaClient, AlpacaCredentials
from tradeagent.domain.enums import OrderPurpose, OrderSide, OrderType, TimeInForce
from tradeagent.domain.models import OrderRequest
from tradeagent.ops.boot import BootHalt, run_exposure_checks
from tradeagent.ops.checks import check_data_api_exposure, ensure_private_bucket
from tradeagent.ops.scheduler import Scheduler, plan_session

SB = "https://example.supabase.co"


@respx.mock
def test_exposed_schema_halts():
    respx.get(f"{SB}/rest/v1/decisions").mock(return_value=httpx.Response(200, json=[]))
    r = check_data_api_exposure(httpx.Client(), SB, "anon")
    assert not r.ok


@respx.mock
def test_unexposed_schema_passes():
    respx.get(f"{SB}/rest/v1/decisions").mock(return_value=httpx.Response(404, json={"message": "relation not found"}))
    assert check_data_api_exposure(httpx.Client(), SB, "anon").ok


@respx.mock
def test_public_bucket_halts_missing_bucket_created_private():
    respx.get(f"{SB}/storage/v1/bucket/decision-context").mock(
        return_value=httpx.Response(200, json={"id": "decision-context", "public": True})
    )
    assert not ensure_private_bucket(httpx.Client(), SB, "svc").ok
    respx.get(f"{SB}/storage/v1/bucket/decision-context").mock(return_value=httpx.Response(404, json={}))
    created = respx.post(f"{SB}/storage/v1/bucket").mock(
        return_value=httpx.Response(200, json={"name": "decision-context"})
    )
    assert ensure_private_bucket(httpx.Client(), SB, "svc").ok
    assert created.called and json.loads(created.calls[0].request.content)["public"] is False


@respx.mock
def test_run_exposure_checks_end_to_end():
    respx.get(f"{SB}/rest/v1/decisions").mock(return_value=httpx.Response(401, json={}))
    respx.get(f"{SB}/storage/v1/bucket/decision-context").mock(return_value=httpx.Response(200, json={"public": False}))
    env = {"SUPABASE_URL": SB, "SUPABASE_ANON_KEY": "a", "SUPABASE_SERVICE_ROLE_KEY": "s"}
    assert all(c.ok for c in run_exposure_checks(env, httpx.Client()))
    respx.get(f"{SB}/rest/v1/decisions").mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(BootHalt) as exc:
        run_exposure_checks(env, httpx.Client())
    assert exc.value.code == "SUPABASE_EXPOSURE_HALT"
    with pytest.raises(BootHalt):
        run_exposure_checks({"SUPABASE_URL": SB}, httpx.Client())  # keys missing → halt, never skip


def test_null_broker_never_writes():
    b = NullBroker()
    req = OrderRequest(
        decision_id=uuid4(),
        portfolio_id=uuid4(),
        purpose=OrderPurpose.ENTRY,
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        notional=Decimal("100"),
        is_simulated=True,
        order_eligible_at=datetime(2026, 9, 14, 14, 30, tzinfo=ET),
    )
    with pytest.raises(NoBrokerInDryRun):
        asyncio.run(b.submit(req))
    with pytest.raises(NoBrokerInDryRun):
        asyncio.run(b.cancel(uuid4()))
    assert (
        asyncio.run(b.positions()) == []
        and asyncio.run(b.open_orders()) == []
        and asyncio.run(b.activities_since(None)) == []
    )


def test_client_refuses_non_paper_host():
    with pytest.raises(ValueError):
        AlpacaClient(AlpacaCredentials("k", "s"), trading_url="https://example.invalid")
    assert AlpacaClient(None).trading_url == PAPER_TRADING_URL


@respx.mock
def test_alpaca_calendar_parses_half_day_and_holiday():
    rows = [
        {"date": "2026-11-25", "open": "09:30", "close": "16:00"},
        {
            "date": "2026-11-27",
            "open": "09:30",
            "close": "13:00",
        },  # day after Thanksgiving: half day; 11-26 absent (holiday)
        {"date": "2026-11-30", "open": "09:30", "close": "16:00"},
    ]
    respx.get(f"{PAPER_TRADING_URL}/v2/calendar").mock(return_value=httpx.Response(200, json=rows))
    cal = AlpacaCalendar(AlpacaClient(AlpacaCredentials("k", "s")), date(2026, 11, 25), date(2026, 11, 30))
    assert not cal.is_session(date(2026, 11, 26)) and cal.session_on(date(2026, 11, 27)).is_half_day
    assert cal.settlement_date(date(2026, 11, 25)) == date(2026, 11, 27)  # T+1 skips the holiday
    assert cal.add_trading_days(datetime(2026, 11, 25, 10, 0, tzinfo=ET), 2).date() == date(2026, 11, 30)


def _cal() -> StaticCalendar:
    def s(d: date, close: time = time(16, 0)) -> Session:
        return Session(d, datetime.combine(d, time(9, 30), tzinfo=ET), datetime.combine(d, close, tzinfo=ET))

    return StaticCalendar([s(date(2026, 11, 25)), s(date(2026, 11, 27), time(13, 0)), s(date(2026, 11, 30))])


def test_scheduler_plans_sessions_and_skips_holidays():
    sch = Scheduler(_cal(), scan_interval_min=15)
    plan = plan_session(_cal().session_on(date(2026, 11, 27)), 15)
    names = [e.name for e in plan.events]
    assert names[:3] == ["corporate_actions", "stop_rearm", "preopen_scan"]
    scans = [e for e in plan.events if e.name == "intraday_scan"]
    assert (
        scans[0].at.time() == time(10, 0) and scans[-1].at < plan.session.close_at and len(scans) == 12
    )  # half day: 10:00 … 12:45
    # Thanksgiving evening → next event is Friday's corporate-actions job, not anything on the holiday
    nxt = sch.next_event(datetime(2026, 11, 26, 18, 0, tzinfo=ET))
    assert nxt.name == "corporate_actions" and nxt.at.date() == date(2026, 11, 27)
    # after Friday's post-close jobs the next event is Monday
    nxt = sch.next_event(datetime(2026, 11, 27, 14, 0, tzinfo=ET))
    assert nxt.at.date() == date(2026, 11, 30)


def test_scheduler_run_once_without_jobs_logs_and_returns():
    sch = Scheduler(_cal(), 15, clock=lambda: datetime(2026, 11, 25, 9, 4, tzinfo=ET))
    e = asyncio.run(sch.run_once(datetime(2026, 11, 25, 9, 4, 59, 999000, tzinfo=ET)))
    assert e.name == "corporate_actions"
