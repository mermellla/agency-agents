"""Slice 4 composition root: risk desk, budget, guards, halts, notifier, executor, fill reconstruction, QB-1.0 with
simulated exits, benchmarks, dividends, closed trades, daily digest — built from settings and the environment here and
nowhere else (ADR-0015), and attached to the scheduler's named offsets."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.adapters.alpaca.calendar import ET, StaticCalendar
from tradeagent.adapters.alpaca.client import AlpacaClient
from tradeagent.adapters.alpaca.corporate_actions import AlpacaCorporateActions
from tradeagent.adapters.email.resend import NullSender, ResendSender
from tradeagent.adapters.typesafe.jev import JevClient
from tradeagent.analytics.closed_trades import record_closed_trade
from tradeagent.config import Settings
from tradeagent.exclusions import ExclusionScreen
from tradeagent.execution.executor import Executor
from tradeagent.execution.ledger_apply import AppliedFill
from tradeagent.execution.reconstruction import FillReconstructor, Reconstructed, ReconstructionJob
from tradeagent.fees import FeeSchedules
from tradeagent.interfaces import Broker
from tradeagent.ops.digest import build_digest
from tradeagent.ops.halts import Halter
from tradeagent.ops.notifications import EmailSender, Notifier
from tradeagent.ops.scheduler import Scheduler
from tradeagent.persistence.db import Database
from tradeagent.portfolios.benchmarks import Benchmarks
from tradeagent.portfolios.dividends import apply_dividends
from tradeagent.portfolios.levels import set_levels_on_open
from tradeagent.portfolios.quant_baseline import QuantBaseline
from tradeagent.portfolios.sim_exits import SimulatedExitEngine
from tradeagent.risk.budget import BudgetDesk
from tradeagent.risk.desk import RiskDesk
from tradeagent.risk.guards import Guards
from tradeagent.scanner.runner import ScanRunner

log = logging.getLogger("tradeagent.portfolio_jobs")


@dataclass
class PortfolioServices:
    desk: RiskDesk
    budget: BudgetDesk
    guards: Guards
    halter: Halter
    notifier: Notifier
    executor: Executor
    reconstruction: ReconstructionJob
    exits: SimulatedExitEngine
    qb: QuantBaseline
    benchmarks: Benchmarks
    corporate: AlpacaCorporateActions | None
    jev: JevClient | None
    fees: FeeSchedules
    experiment_id: UUID
    phase_id: UUID
    primary_id: UUID


def make_sender(env: dict[str, str]) -> EmailSender:
    key, sender = env.get("RESEND_API_KEY"), env.get("EMAIL_FROM")
    if key and sender:
        return ResendSender(key, sender)
    return NullSender()


def make_on_applied(db: Database, cal: StaticCalendar) -> Any:
    def on_applied(order: dict[str, Any], applied: AppliedFill, rec: Reconstructed) -> None:
        set_levels_on_open(db, order, applied, rec.price)
        if applied.position_closed:
            record_closed_trade(db, applied.position_id, UUID(str(order["decision_id"])), cal)

    return on_applied


def build_portfolio_services(
    settings: Settings,
    env: dict[str, str],
    db: Database,
    broker: Broker,
    sip: Any,
    cal: StaticCalendar,
    client: AlpacaClient | None,
    experiment_id: UUID,
    phase_id: UUID,
    phase_seq: int,
    prompt_version: str,
) -> PortfolioServices:
    mode = settings.risk.execution.execution_mode
    recipient = env.get("OWNER_EMAIL") or env.get("EMAIL_TO") or "owner@localhost"
    notifier = Notifier(db, make_sender(env), recipient, mode, phase_seq)
    halter = Halter(db, notifier, experiment_id)
    primary = UUID(str(db.primary_portfolio(experiment_id)["id"]))
    screen = ExclusionScreen.from_config(settings.exclusions, settings.sic_backstop)
    desk = RiskDesk(settings, screen)
    budget = BudgetDesk(db, settings.risk.budget, cal, experiment_id)
    guards = Guards(db, settings.risk.guards, halter, experiment_id, primary)
    executor = Executor(db, broker, settings)
    fees = FeeSchedules.from_config(settings.fees, settings.versions.config_version)
    reconstructor = FillReconstructor(sip, settings.risk.simulation)

    def settles_on(fill_at: datetime) -> date | None:
        try:
            return cal.settlement_date(fill_at.astimezone(ET).date())
        except LookupError:
            return None

    reconstruction = ReconstructionJob(db, reconstructor, fees, settles_on, make_on_applied(db, cal))
    exits = SimulatedExitEngine(db, executor, experiment_id, phase_id)

    def halted() -> tuple[bool, bool]:
        st = halter.state()
        return st.all_halted, st.entries_halted

    qb = QuantBaseline(db, desk, executor, exits, settings, cal, experiment_id, phase_id, halted=halted)
    benchmarks = Benchmarks(db, sip, experiment_id, phase_id)
    corporate = AlpacaCorporateActions(client) if client is not None and client.authenticated else None
    jev = JevClient(
        env.get("TYPESAFE_API_KEY"),
        model=settings.risk.budget.model_triage,
        prompt_version=prompt_version,
        cost_per_1k_tokens_usd=Decimal(str(settings.risk.budget.jev_cost_per_1k_tokens_usd)),
    )
    return PortfolioServices(
        desk,
        budget,
        guards,
        halter,
        notifier,
        executor,
        reconstruction,
        exits,
        qb,
        benchmarks,
        corporate,
        jev if jev.available else None,
        fees,
        experiment_id,
        phase_id,
        primary,
    )


def register_portfolio_jobs(
    scheduler: Scheduler,
    runner: ScanRunner,
    svc: PortfolioServices,
    cal: StaticCalendar,
    db: Database,
    started_on: date,
) -> None:
    """Scan ticks: scan → reconstruct pending fills → QB-1.0 (exits, then entry). After the close: reconstruction,
    benchmarks, dividends, digest."""

    def session_close(d: date) -> datetime | None:
        s = cal.session_on(d)
        return s.close_at.astimezone(UTC) if s else None

    async def scan(kind: str, at: datetime) -> None:
        now = at.astimezone(UTC)
        out = runner.run_and_persist(kind, now, at.date())
        outcomes = svc.reconstruction.run(svc.experiment_id, now)
        if outcomes:
            log.info("reconstruction: %s", outcomes)
        inputs = runner.last_inputs
        if inputs is None:
            return
        report = await svc.qb.on_scan(out, inputs, session_close(at.date()))
        log.info("QB-1.0 %s: %s %s exits=%d", kind, report.status, report.candidate, len(report.exits))
        svc.guards.consecutive_rejects_ok(svc.primary_id)

    async def preopen(at: datetime) -> None:
        await scan("preopen", at)

    async def intraday(at: datetime) -> None:
        await scan("intraday", at)

    async def post_close(at: datetime) -> None:
        now = at.astimezone(UTC)
        d = at.astimezone(ET).date()
        svc.reconstruction.run(svc.experiment_id, now)
        latest = svc.benchmarks.mark(d)
        svc.benchmarks.ensure_started(started_on)
        marks = {s: c for s, (_, c) in latest.items()}
        for p in db.portfolios(svc.experiment_id):
            for pos in db.open_positions(UUID(str(p["id"]))):
                marks.setdefault(str(pos["symbol"]), Decimal(str(pos["avg_cost"] or 0)))
        if svc.corporate is not None:
            symbols = sorted(
                {
                    str(pos["symbol"])
                    for p in db.portfolios(svc.experiment_id)
                    for pos in db.open_positions(UUID(str(p["id"])))
                }
            )
            if symbols:
                divs = svc.corporate.dividends_for(symbols, d - timedelta(days=45), d + timedelta(days=1))
                with db.transaction():
                    written = apply_dividends(
                        db, svc.experiment_id, svc.phase_id, db.portfolios(svc.experiment_id), divs, d
                    )
                if written:
                    log.info("dividends credited: %s", written)
        digest = build_digest(
            db,
            svc.experiment_id,
            d,
            marks,
            svc.jev,
            Decimal(str(db.primary_portfolio(svc.experiment_id)["equity_start_usd"])),
        )
        with db.transaction():
            for call in digest.calls:
                db.insert_llm_call(call, svc.experiment_id, svc.phase_id, prompt_version=call.prompt_version)
            svc.notifier.send("daily_digest", digest.subject, digest.text, digest.html, digest.payload)

    scheduler.register("preopen_scan", preopen)
    scheduler.register("intraday_scan", intraday)
    scheduler.register("post_close_jobs", post_close)
