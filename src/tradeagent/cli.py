"""`tradeagent` command line: boot-check | run | verify-projections | probe-fractional-stop | digest."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from tradeagent.adapters.alpaca.calendar import StaticCalendar, default_window
from tradeagent.config import LiveLockedOut, load_settings
from tradeagent.execution.broker_factory import make_broker, paper_credentials
from tradeagent.ops.boot import BootHalt, boot
from tradeagent.ops.scheduler import Scheduler
from tradeagent.persistence.db import Database, connect

log = logging.getLogger("tradeagent")


def _code_version(env: dict[str, str]) -> str:
    return env.get("RAILWAY_GIT_COMMIT_SHA") or env.get("TRADEAGENT_CODE_VERSION") or "dev"


def _calendar(env: dict[str, str]) -> StaticCalendar:
    from tradeagent.adapters.alpaca.calendar import AlpacaCalendar
    from tradeagent.adapters.alpaca.client import AlpacaClient

    creds = paper_credentials(env)
    if creds is None:
        raise SystemExit("ALPACA_PAPER_KEY/SECRET are required to load the trading calendar (§11); DRY_RUN reads only")
    start, end = default_window(date.today())
    return AlpacaCalendar(AlpacaClient(creds), start, end)


def cmd_boot_check(env: dict[str, str]) -> int:
    settings = load_settings(env=env)
    db = Database(connect(env["DATABASE_URL"]))
    broker = make_broker(settings, env)
    try:
        report = boot(settings, env, db, broker, code_version=_code_version(env))
    except BootHalt as halt:
        log.error("HALT %s (%s): %s", halt.code, halt.scope.value, halt.detail)
        return 2
    log.info(
        "boot ok: experiment=%s phase=%s (seq %s, new=%s) portfolios=%s opening_balances=%s",
        report.experiment_id,
        report.phase_id,
        report.phase_seq,
        report.opened_new_phase,
        report.portfolios,
        report.opening_balances_written,
    )
    for c in report.checks:
        log.info("check %s: %s", c.name, c.detail)
    for n in report.notes:
        log.warning(n)
    return 0


def cmd_run(env: dict[str, str]) -> int:
    """Boot, then wire every job the delivered slices provide and run the calendar-driven scheduler."""
    from tradeagent.adapters.alpaca.client import AlpacaClient
    from tradeagent.adapters.alpaca.market_data import RequestBudget, make_market_data
    from tradeagent.ops.jobs import build_runner, register_execution_jobs
    from tradeagent.ops.portfolio_jobs import build_portfolio_services, register_portfolio_jobs

    settings = load_settings(env=env)
    db = Database(connect(env["DATABASE_URL"]))
    broker = make_broker(settings, env)
    try:
        report = boot(settings, env, db, broker, code_version=_code_version(env))
    except BootHalt as halt:
        log.error("HALT %s (%s): %s", halt.code, halt.scope.value, halt.detail)
        return 2
    for n in report.notes:
        log.warning(n)
    creds = paper_credentials(env)
    client = AlpacaClient(creds)
    calendar = _calendar(env)
    scheduler = Scheduler(calendar, settings.risk.scanner.scan_interval_min)
    prompt_version = settings.versions.prompt_version or "0.0.0"
    runner = build_runner(settings, env, db, client, report.experiment_id, report.phase_id, prompt_version)
    sip, iex = make_market_data(client, settings.risk.market_data.market_data_plan, RequestBudget())
    svc = build_portfolio_services(
        settings,
        env,
        db,
        broker,
        sip,
        calendar,
        client,
        report.experiment_id,
        report.phase_id,
        report.phase_seq,
        prompt_version,
    )
    exp = db.get_experiment(env.get("EXPERIMENT_ID") or "") or {}
    started_on = exp.get("started_on") or date.today()
    register_portfolio_jobs(scheduler, runner, svc, calendar, db, started_on)
    register_execution_jobs(scheduler, db, broker, client, iex, report.experiment_id, report.phase_id, sip)
    log.info(
        "scheduler started with %d job(s); next event %s", len(scheduler.jobs), scheduler.next_event(scheduler.clock())
    )
    asyncio.run(scheduler.run_forever())
    return 0


def cmd_digest(env: dict[str, str]) -> int:
    """Build and send (or print) the daily digest for TRADEAGENT_DIGEST_DATE (default: today, ET)."""
    from tradeagent.adapters.alpaca.calendar import ET
    from tradeagent.adapters.email.resend import NullSender
    from tradeagent.ops.digest import build_digest
    from tradeagent.ops.notifications import Notifier
    from tradeagent.ops.portfolio_jobs import make_sender

    settings = load_settings(env=env)
    db = Database(connect(env["DATABASE_URL"]))
    exp = db.get_experiment(env.get("EXPERIMENT_ID") or "")
    if exp is None:
        log.error("EXPERIMENT_ID not found")
        return 2
    d = (
        date.fromisoformat(env["TRADEAGENT_DIGEST_DATE"])
        if env.get("TRADEAGENT_DIGEST_DATE")
        else datetime.now(tz=ET).date()
    )
    eid = UUID(str(exp["id"]))
    digest = build_digest(db, eid, d, {}, None, Decimal(str(exp["equity_start_usd"])))
    phase = db.open_phase(eid)
    sender = make_sender(env)
    notifier = Notifier(
        db,
        sender,
        env.get("OWNER_EMAIL") or "owner@localhost",
        settings.risk.execution.execution_mode,
        int(phase["seq"]) if phase else 0,
    )
    with db.transaction():
        notifier.send("daily_digest", digest.subject, digest.text, digest.html, digest.payload)
    if isinstance(sender, NullSender):
        print(digest.subject)
        print(digest.text)
    return 0


def cmd_probe_fractional_stop(env: dict[str, str]) -> int:
    """ADR-0013 empirical probe (OI-01): one fractional Day stop on the paper account, status sequence recorded, then cancelled.
    Requires PAPER keys and --confirm; never runs in this codebase's LIVE (there is none)."""
    import json
    import time as _time

    from tradeagent.adapters.alpaca.broker_paper import AlpacaPaperBroker
    from tradeagent.adapters.alpaca.client import AlpacaClient, AlpacaHttpError

    if env.get("TRADEAGENT_PROBE_CONFIRM") != "yes":
        raise SystemExit("set TRADEAGENT_PROBE_CONFIRM=yes to submit one fractional stop order to the PAPER account")
    creds = paper_credentials(env)
    if creds is None:
        raise SystemExit("ALPACA_PAPER_KEY/SECRET required")
    client = AlpacaClient(creds)
    broker = AlpacaPaperBroker(client)
    symbol = env.get("TRADEAGENT_PROBE_SYMBOL", "AAPL")
    # a fractional long must exist to sell against: buy 0.5 share notional-free market order first, then arm a far stop
    trace: list[dict[str, object]] = []
    try:
        buy = client.post(
            "/v2/orders",
            {
                "symbol": symbol,
                "qty": "0.5",
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "client_order_id": f"probe-buy-{int(_time.time())}",
            },
        )
        trace.append({"step": "buy", "status": buy.get("status"), "id": buy.get("id")})
        _time.sleep(3)
        last = client.get(
            "/v2/stocks/trades/latest", {"symbols": symbol, "feed": "iex"}, base="https://data.alpaca.markets"
        )["trades"][symbol]["p"]
        stop = client.post(
            "/v2/orders",
            {
                "symbol": symbol,
                "qty": "0.5",
                "side": "sell",
                "type": "stop",
                "time_in_force": "day",
                "stop_price": f"{float(last) * 0.5:.2f}",
                "client_order_id": f"probe-stop-{int(_time.time())}",
            },
        )
        trace.append({"step": "stop_submit", "status": stop.get("status"), "id": stop.get("id")})
        for i in range(3):
            _time.sleep(2)
            st = asyncio.run(broker.order_by_broker_id(str(stop["id"])))
            trace.append({"step": f"stop_status_{i}", "status": st.status_reason if st else None})
        client.delete(f"/v2/orders/{stop['id']}")
        trace.append({"step": "stop_cancel", "ok": True})
    except AlpacaHttpError as exc:
        trace.append({"step": "error", "status": exc.status, "body": exc.body})
    print(json.dumps({"symbol": symbol, "trace": trace}, indent=2))
    return 0


def cmd_verify_projections(env: dict[str, str]) -> int:
    db = Database(connect(env["DATABASE_URL"]))
    rows = db.conn.execute("select id, name from portfolios").fetchall()
    bad = [r["name"] for r in rows if not db.verify_cash_chain(UUID(str(r["id"])))]
    print("PROJECTION_DRIFT" if bad else "ok", bad)
    return 2 if bad else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="tradeagent")
    parser.add_argument(
        "command", choices=["boot-check", "run", "verify-projections", "probe-fractional-stop", "digest"]
    )
    args = parser.parse_args(argv)
    env = dict(os.environ)
    try:
        commands = {
            "boot-check": cmd_boot_check,
            "run": cmd_run,
            "verify-projections": cmd_verify_projections,
            "probe-fractional-stop": cmd_probe_fractional_stop,
            "digest": cmd_digest,
        }
        return commands[args.command](env)
    except LiveLockedOut as exc:
        log.error("%s", exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
