"""Working position levels after an opening fill. LLM levels are absolute; the quant baseline's stop and target are
defined as distances from the signal price (2×ATR, 2R) and are re-based on the actual fill (§12)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.execution.ledger_apply import AppliedFill
from tradeagent.persistence.db import Database

QB_STRATEGY = "QB-1.0"


def levels_after_fill(
    decision: dict[str, Any], fill_price: Decimal
) -> tuple[Decimal | None, Decimal | None, datetime | None]:
    target = decision.get("target_price")
    inv = decision.get("invalidation_price")
    ts = decision.get("time_stop_at")
    target = Decimal(str(target)) if target is not None else None
    inv = Decimal(str(inv)) if inv is not None else None
    ref = decision.get("sip_signal_price")
    if str(decision.get("origin")) == "quant" and ref is not None:
        ref_d = Decimal(str(ref))
        if inv is not None:
            inv = fill_price - (ref_d - inv)
        if target is not None:
            target = fill_price + (target - ref_d)
    return target, inv, ts


def set_levels_on_open(db: Database, order: dict[str, Any], applied: AppliedFill, fill_price: Decimal) -> None:
    if not applied.position_opened:
        return
    d = db.decision(UUID(str(order["decision_id"])))
    if d is None:
        return
    target, inv, ts = levels_after_fill(d, fill_price)
    db.set_working_levels(applied.position_id, target, inv, ts)
