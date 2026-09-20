"""Order placement after risk validation (§8.3, §8.4, ADR-0021). One transaction per decision under the portfolio
lock: record every leg with its capital reservation → VALIDATED → SUBMITTED (broker) or FILL_PENDING_RECONSTRUCTION
(simulated). A broker rejection is recorded as REJECTED_BROKER and never retried at a different size (§15)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from tradeagent.adapters.alpaca.broker_null import NoBrokerInDryRun
from tradeagent.adapters.alpaca.client import AlpacaHttpError
from tradeagent.config import Settings
from tradeagent.domain.enums import OrderStatus, RejectCode
from tradeagent.domain.models import OrderRequest
from tradeagent.interfaces import Broker
from tradeagent.persistence.db import Database
from tradeagent.risk.sizing import reservation_for

log = logging.getLogger("tradeagent.executor")


class DecisionRef(Protocol):
    """What placement needs from a decision: identity and provenance (a full Decision or a persisted system decision)."""

    @property
    def decision_id(self) -> UUID: ...
    @property
    def portfolio_id(self) -> UUID: ...
    @property
    def experiment_id(self) -> UUID: ...
    @property
    def experiment_phase_id(self) -> UUID: ...


@dataclass(frozen=True)
class SystemDecisionRef:
    decision_id: UUID
    portfolio_id: UUID
    experiment_id: UUID
    experiment_phase_id: UUID


@dataclass
class Placement:
    decision_id: UUID
    order_ids: list[UUID] = field(default_factory=list)
    status: str = "none"  # pending_reconstruction | submitted | rejected_broker | none
    detail: dict[str, Any] = field(default_factory=dict)


class Executor:
    def __init__(self, db: Database, broker: Broker, settings: Settings, clock: Callable[[], datetime] | None = None):
        self.db, self.broker, self.settings = db, broker, settings
        self.clock = clock or (lambda: datetime.now(tz=UTC))

    async def place(
        self,
        decision: DecisionRef,
        requests: list[OrderRequest],
        reference_price: Decimal | None,
        position_id: UUID | None = None,
    ) -> Placement:
        """Caller holds the transaction; this method takes the portfolio lock first (ADR-0021)."""
        placement = Placement(decision.decision_id)
        if not requests:
            return placement
        self.db.conn.execute("select lock_portfolio(%s)", (decision.portfolio_id,))
        buffer = self.settings.risk.capital.overfill_buffer_pct
        for req in requests:
            reserved = reservation_for(req, reference_price, buffer)
            oid = self.db.record_order(req, decision.experiment_id, decision.experiment_phase_id, position_id, reserved)
            self.db.transition_order(oid, OrderStatus.VALIDATED, "risk desk validated (§8.1)")
            placement.order_ids.append(oid)
            if req.is_simulated:
                self.db.transition_order(
                    oid,
                    OrderStatus.FILL_PENDING_RECONSTRUCTION,
                    "simulated: fill reconstructed after the SIP embargo (§8.9)",
                )
                placement.status = "pending_reconstruction"
                continue
            try:
                state = await self.broker.submit(req)
            except NoBrokerInDryRun as exc:  # a broker-touching order in DRY_RUN cannot exist; record and refuse
                self.db.transition_order(oid, OrderStatus.REJECTED, f"dry_run: {exc}")
                placement.status, placement.detail = "rejected_broker", {"reason": str(exc)}
                self.db.set_decision_status(decision.decision_id, "rejected", RejectCode.REJECTED_BROKER.value)
                return placement
            except AlpacaHttpError as exc:
                self.db.transition_order(oid, OrderStatus.REJECTED, f"broker {exc.status}: {exc.body[:200]}")
                placement.status, placement.detail = "rejected_broker", {"status": exc.status, "body": exc.body[:500]}
                self.db.set_decision_status(decision.decision_id, "rejected", RejectCode.REJECTED_BROKER.value)
                log.warning("REJECTED_BROKER %s: %s", req.client_order_id, exc)
                return placement
            self.db.transition_order(
                oid,
                OrderStatus.SUBMITTED,
                state.status_reason or "submitted",
                broker_order_id=state.broker_order_id,
                submitted_at=state.submitted_at or self.clock(),
            )
            self.db.set_decision_submitted(decision.decision_id, state.submitted_at or self.clock())
            placement.status = "submitted"
        return placement

    async def cancel_siblings(self, position_id: UUID, reason: str) -> list[UUID]:
        """§14 rule 3: sibling orders on a position are cancelled before any new exit is submitted."""
        cancelled: list[UUID] = []
        for o in self.db.open_orders_for_position(position_id):
            oid = UUID(str(o["id"]))
            if o["status"] in ("PROPOSED", "VALIDATED"):
                self.db.transition_order(oid, OrderStatus.REJECTED, reason)
                cancelled.append(oid)
                continue
            if not o["is_simulated"]:
                try:
                    await self.broker.cancel(oid)
                except NotImplementedError:
                    log.warning("broker cancel unavailable for %s; left open", oid)
                    continue
                except AlpacaHttpError as exc:
                    log.warning("broker cancel %s failed: %s", oid, exc)
                    continue
            self.db.transition_order(oid, OrderStatus.CANCELLED, reason)
            cancelled.append(oid)
        return cancelled
