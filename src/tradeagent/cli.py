"""`tradeagent` command line: boot-check | run | verify-projections."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date
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
    rc = cmd_boot_check(env)
    if rc != 0:
        return rc
    settings = load_settings(env=env)
    scheduler = Scheduler(_calendar(env), settings.risk.scanner.scan_interval_min)
    log.info(
        "scheduler started with %d job(s); next event %s", len(scheduler.jobs), scheduler.next_event(scheduler.clock())
    )
    asyncio.run(scheduler.run_forever())
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
    parser.add_argument("command", choices=["boot-check", "run", "verify-projections"])
    args = parser.parse_args(argv)
    env = dict(os.environ)
    try:
        return {"boot-check": cmd_boot_check, "run": cmd_run, "verify-projections": cmd_verify_projections}[
            args.command
        ](env)
    except LiveLockedOut as exc:
        log.error("%s", exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
