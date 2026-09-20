"""Deterministic exits for simulated portfolios (quant shadow now; critique and deterministic-exit twins in Slice 6):
stop, target and time stop evaluated on retrievable SIP bars, exits before entries (§12, §14 rule 1). The exit becomes
eligible when the engine observes the touch, never at the bar itself (§8.9). In PAPER the primary's stops live at the
broker (Slice 3) and its discretionary exits arrive with the review pipeline (Slice 6)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.domain.enums import OrderPurpose, OrderSide, OrderType, TimeInForce
from tradeagent.domain.models import Bar, OrderRequest
from tradeagent.execution.executor import Executor, SystemDecisionRef
from tradeagent.persistence.db import Database

log = logging.getLogger("tradeagent.sim_exits")
PRIORITY = {"stop": 0, "time_stop": 1, "target": 2}  # §14 rule 4 tie-break within one observation


@dataclass(frozen=True)
class ExitAction:
    symbol: str
    position_id: UUID
    trigger: str  # stop | target | time_stop
    observed_at: datetime
    order_id: UUID | None


class SimulatedExitEngine:
    def __init__(
        self,
        db: Database,
        executor: Executor,
        experiment_id: UUID,
        phase_id: UUID,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db, self.executor = db, executor
        self.experiment_id, self.phase_id = experiment_id, phase_id
        self.clock = clock or (lambda: datetime.now(tz=UTC))

    @staticmethod
    def trigger_for(pos: dict[str, Any], bars: list[Bar], now: datetime) -> tuple[str, datetime] | None:
        inv = (
            Decimal(str(pos["working_invalidation_price"]))
            if pos.get("working_invalidation_price") is not None
            else None
        )
        tgt = Decimal(str(pos["working_target_price"])) if pos.get("working_target_price") is not None else None
        ts: datetime | None = pos.get("working_time_stop_at")
        opened: datetime | None = pos.get("opened_at")
        events: list[tuple[datetime, int, str]] = []
        for b in sorted(bars, key=lambda x: x.end):
            if opened is not None and b.end <= opened:
                continue
            if b.retrievable_at > now:
                break
            if inv is not None and b.low <= inv:
                events.append((b.retrievable_at, PRIORITY["stop"], "stop"))
                break
            if tgt is not None and b.high >= tgt:
                events.append((b.retrievable_at, PRIORITY["target"], "target"))
                break
        if ts is not None and now >= ts:
            events.append((ts, PRIORITY["time_stop"], "time_stop"))
        if not events:
            return None
        at, _, name = min(events)
        return name, at

    async def check(
        self, portfolio_id: UUID, bars15: dict[str, list[Bar]], expires_at: datetime | None
    ) -> list[ExitAction]:
        now = self.clock()
        actions: list[ExitAction] = []
        for pos in self.db.open_positions(portfolio_id):
            pid = UUID(str(pos["id"]))
            if self.db.open_orders_for_position(pid):
                continue
            trig = self.trigger_for(pos, bars15.get(str(pos["symbol"]), []), now)
            if trig is None:
                continue
            name, observed = trig
            reason = f"{name}: observed {observed.isoformat()}"
            with self.db.transaction():
                did = self.db.create_system_decision(
                    self.experiment_id,
                    self.phase_id,
                    portfolio_id,
                    str(pos["symbol"]),
                    "SELL",
                    pid,
                    name,
                    self.db.phase_versions(self.phase_id),
                    now,
                )
                req = OrderRequest(
                    decision_id=did,
                    portfolio_id=portfolio_id,
                    purpose=OrderPurpose.EXIT,
                    symbol=str(pos["symbol"]),
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    time_in_force=TimeInForce.DAY,
                    qty=Decimal(str(pos["qty"])),
                    is_simulated=True,
                    order_eligible_at=now,
                    expires_at=expires_at,
                )
                stub = SystemDecisionRef(did, portfolio_id, self.experiment_id, self.phase_id)
                placement = await self.executor.place(stub, [req], None, position_id=pid)
            oid = placement.order_ids[0] if placement.order_ids else None
            log.info("simulated exit %s %s (%s)", pos["symbol"], name, reason)
            actions.append(ExitAction(str(pos["symbol"]), pid, name, observed, oid))
        return actions
