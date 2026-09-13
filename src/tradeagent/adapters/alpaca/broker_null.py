"""NullBroker — the DRY_RUN broker (§16, ADR-0020). Never submits, cancels or replaces. With paper credentials it reads
the paper account (positions, open orders, activities, configuration) so reconciliation and the pre-open probe can run
observe-only; without credentials every read is empty."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.adapters.alpaca.client import AlpacaClient
from tradeagent.domain.enums import ExecutionMode
from tradeagent.domain.models import AccountConfiguration, BrokerPolicy, OrderRequest, OrderState, Position


class NoBrokerInDryRun(RuntimeError):
    """Raised by any write path on the NullBroker. DRY_RUN fills are reconstructed from delayed SIP (§8.9)."""


class NullBroker:
    mode = ExecutionMode.DRY_RUN
    observe_only = True

    def __init__(self, client: AlpacaClient | None = None):
        self.client = client

    async def submit(self, request: OrderRequest) -> OrderState:
        raise NoBrokerInDryRun(f"submit refused in DRY_RUN ({request.client_order_id})")

    async def cancel(self, order_id: UUID) -> OrderState:
        raise NoBrokerInDryRun("cancel refused in DRY_RUN")

    async def replace(self, order_id: UUID, request: OrderRequest) -> OrderState:
        raise NoBrokerInDryRun("replace refused in DRY_RUN")

    async def order_status(self, client_order_id: str) -> OrderState | None:
        return None

    async def open_orders(self) -> list[OrderState]:
        return []

    async def positions(self) -> list[Position]:
        if self.client is None or not self.client.authenticated:
            return []
        rows = self.client.get("/v2/positions")
        # broker positions carry no decision; entry_decision_id is unknown → reconciliation treats them as unexplained
        return [
            Position(
                portfolio_id=UUID(int=0),
                symbol=r["symbol"],
                entry_decision_id=UUID(int=0),
                qty=Decimal(str(r["qty"])),
                avg_cost=Decimal(str(r["avg_entry_price"])),
            )
            for r in rows
        ]

    async def activities_since(self, since: datetime | None) -> list[dict[str, Any]]:
        if self.client is None or not self.client.authenticated:
            return []
        params: dict[str, Any] = {"activity_types": "FILL", "direction": "asc", "page_size": 100}
        if since:
            params["after"] = since.isoformat()
        return list(self.client.get("/v2/account/activities", params))

    async def account_configuration(self) -> AccountConfiguration:
        if self.client is None or not self.client.authenticated:
            raise RuntimeError("ALPACA_CREDENTIALS_MISSING")
        return AccountConfiguration.model_validate(self.client.get("/v2/account/configurations"))

    async def write_account_configuration(self, policy: BrokerPolicy) -> AccountConfiguration:
        raise NoBrokerInDryRun(
            "account configuration is not written in DRY_RUN (§8.10 runs at PAPER/LIVE initialization)"
        )
