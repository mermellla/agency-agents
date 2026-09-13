"""Boot sequence (§8.6, §8.8, §13.2, ADR-0019, ADR-0022). Runs on every process start, before any other activity:

  settings (LIVE refused) → schema current → exposure checks → experiment + portfolios + opening balances
  → version registries → phase drift (open or halt) → projection check → reconciliation (replay or halt) → open halts

Every halt is recorded in `halts` and (Slice 4) emailed. Slice 1 delivers the spine; broker policy enforcement and
event replay attach in Slice 3 where the paper broker exists."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from tradeagent.config import Settings
from tradeagent.domain.enums import ExecutionMode, HaltScope, ReconcileResult
from tradeagent.interfaces import Broker
from tradeagent.ops.checks import CheckResult, check_data_api_exposure, ensure_private_bucket
from tradeagent.persistence.db import Database, latest_migration_version
from tradeagent.versioning.phases import PhaseReasonMissing, VersionsInForce, ensure_phase, load_phase_change
from tradeagent.versioning.registry import register_all


class BootHalt(RuntimeError):
    def __init__(
        self, code: str, scope: HaltScope, detail: dict[str, Any], reconciliation: dict[str, Any] | None = None
    ):
        super().__init__(f"{code}: {detail}")
        self.code, self.scope, self.detail, self.reconciliation = code, scope, detail, reconciliation


@dataclass
class BootReport:
    experiment_id: UUID
    phase_id: UUID
    phase_seq: int
    opened_new_phase: bool
    portfolios: int
    opening_balances_written: int
    checks: list[CheckResult] = field(default_factory=list)
    reconciliation: ReconcileResult = ReconcileResult.AGREE
    notes: list[str] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def run_exposure_checks(env: dict[str, str], http: httpx.Client | None = None) -> list[CheckResult]:
    """Mandatory when SUPABASE_URL is set. A local database with no Supabase project must say so explicitly."""
    url = env.get("SUPABASE_URL")
    if not url:
        if env.get("TRADEAGENT_LOCAL_DB", "").lower() != "true":
            raise BootHalt(
                "SUPABASE_EXPOSURE_HALT",
                HaltScope.ALL,
                {"reason": "SUPABASE_URL unset and TRADEAGENT_LOCAL_DB is not 'true'"},
            )
        return [
            CheckResult("SUPABASE_EXPOSURE_CHECK", True, "local database; no Data API"),
            CheckResult("STORAGE_BUCKET_CHECK", True, "local database; no Storage"),
        ]
    anon, service = env.get("SUPABASE_ANON_KEY", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not anon or not service:
        raise BootHalt(
            "SUPABASE_EXPOSURE_HALT",
            HaltScope.ALL,
            {"reason": "SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY missing; checks cannot run"},
        )
    client = http or httpx.Client(timeout=15.0)
    results = [check_data_api_exposure(client, url, anon), ensure_private_bucket(client, url, service)]
    for r in results:
        if not r.ok:
            raise BootHalt(
                "SUPABASE_EXPOSURE_HALT" if r.name == "SUPABASE_EXPOSURE_CHECK" else "STORAGE_EXPOSURE_HALT",
                HaltScope.ALL,
                {"check": r.name, "detail": r.detail},
            )
    return results


async def reconcile_minimal(
    db: Database, broker: Broker, experiment_id: UUID, mode: ExecutionMode
) -> tuple[ReconcileResult, dict[str, Any]]:
    """§8.6 steps 1, 3, 5 for Slice 1: no orders have ever been submitted, so any broker position or open order is
    unexplained → halt. Event replay (steps 2, 4) attaches in Slice 3 with the paper broker."""
    positions = await broker.positions()
    open_orders = await broker.open_orders()
    diff: dict[str, Any] = {
        "broker_positions": [{"symbol": p.symbol, "qty": str(p.qty)} for p in positions],
        "broker_open_orders": [o.client_order_id for o in open_orders],
        "ledger_open_positions": 0,
    }
    result = ReconcileResult.HALT if positions or open_orders else ReconcileResult.AGREE
    db.record_reconciliation(
        experiment_id, mode, result, diff, "slice-1 minimal: broker must be flat before the first phase trades"
    )
    return result, diff


def boot(
    settings: Settings,
    env: dict[str, str],
    db: Database,
    broker: Broker,
    *,
    code_version: str,
    http: httpx.Client | None = None,
    today: date | None = None,
) -> BootReport:
    now = _utcnow()
    today = today or now.date()
    mode = settings.risk.execution.execution_mode
    if mode == ExecutionMode.LIVE:  # unreachable: load_settings already refused it (ADR-0020)
        raise BootHalt("MODE_MISMATCH", HaltScope.ALL, {"mode": mode.value})

    if not db.schema_is_current():
        raise BootHalt("SCHEMA_OUT_OF_DATE", HaltScope.ALL, {"expected_migration": latest_migration_version()})

    checks = run_exposure_checks(env, http)

    experiment_name = env.get("EXPERIMENT_ID") or f"{mode.value.lower()}-{settings.versions.config_version[:8]}"
    # the experiment row is committed on its own so that a halt recorded below can reference it
    with db.transaction():
        exp = db.get_experiment(experiment_name) or db.create_experiment(
            experiment_name, mode, Decimal(str(settings.risk.capital.experiment_equity_start)), today
        )
    experiment_id = UUID(str(exp["id"]))
    try:
        with db.transaction():
            if exp["execution_mode"] != mode.value:
                raise BootHalt(
                    "MODE_MISMATCH",
                    HaltScope.ALL,
                    {"experiment": experiment_name, "recorded": exp["execution_mode"], "configured": mode.value},
                )

            rv = register_all(db, settings)
            models = (
                settings.risk.budget.model_triage,
                settings.risk.budget.model_decision,
                settings.risk.budget.model_critique,
            )
            in_force = VersionsInForce.build(rv, models, latest_migration_version(), code_version)
            try:
                phase, opened = ensure_phase(db, experiment_id, in_force, load_phase_change(), now)
            except PhaseReasonMissing as exc:
                raise BootHalt("PHASE_REASON_MISSING", HaltScope.ALL, {"detail": str(exc)}) from exc
            phase_id = UUID(str(phase["id"]))

            portfolios = db.ensure_portfolios(experiment_id, Decimal(str(exp["equity_start_usd"])))
            written = sum(1 for p in portfolios if db.ensure_initial_equity(experiment_id, phase_id, p, now))

            for p in portfolios:
                if not db.verify_cash_chain(UUID(str(p["id"]))):
                    raise BootHalt("PROJECTION_DRIFT", HaltScope.ALL, {"portfolio": p["name"]})

            result, diff = asyncio.run(reconcile_minimal(db, broker, experiment_id, mode))
            db.record_reconciliation(
                experiment_id, mode, result, diff, "slice-1 minimal: broker must be flat before the first phase trades"
            )
            if result == ReconcileResult.HALT:
                raise BootHalt("RECONCILE_HALT", HaltScope.ALL, diff, reconciliation=diff)
    except BootHalt as halt:
        # the mutating transaction rolled back; the halt (and a halted reconciliation) must survive it (§8.7 "every halt")
        with db.transaction():
            if halt.reconciliation is not None:
                db.record_reconciliation(
                    experiment_id,
                    mode,
                    ReconcileResult.HALT,
                    halt.reconciliation,
                    "slice-1 minimal: unexplained broker state",
                )
            db.record_halt(experiment_id, halt.code, halt.scope, halt.detail)
        raise

    report = BootReport(experiment_id, phase_id, int(phase["seq"]), opened, len(portfolios), written, checks, result)
    open_halts = db.open_halts(experiment_id)
    if open_halts:
        report.notes.append(
            f"{len(open_halts)} uncleared halt(s): {[h['code'] for h in open_halts]} — owner must clear before trading"
        )
    if mode == ExecutionMode.DRY_RUN:
        report.notes.append("DRY_RUN: broker policy enforcement (§8.10) runs at PAPER initialization (Slice 3)")
    return report
