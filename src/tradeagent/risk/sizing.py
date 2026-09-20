"""§3.4, §8.2 pure sizing arithmetic used by the risk desk and the executor."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_DOWN, Decimal
from typing import Any

from tradeagent.config.loader import PositionLimits
from tradeagent.domain.enums import OrderType
from tradeagent.domain.models import OrderRequest

CENT = Decimal("0.01")
SHARE = Decimal("0.000000001")


def reservation_for(req: OrderRequest, reference_price: Decimal | None, overfill_buffer_pct: float) -> Decimal:
    """ADR-0021: the maximum a BUY can be worth. Notional orders are exact; qty orders capped by their limit/stop;
    a whole-share market order carries reference × qty × (1 + OVERFILL_BUFFER_PCT) (§3.4)."""
    if req.side.value != "buy":
        return Decimal(0)
    if req.notional is not None:
        return req.notional
    assert req.qty is not None
    if req.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and req.limit_price is not None:
        return (req.qty * req.limit_price).quantize(CENT)
    if req.order_type == OrderType.STOP and req.stop_price is not None:
        return (req.qty * req.stop_price).quantize(CENT)
    if reference_price is None:
        raise ValueError("a market qty order needs an execution-grade reference price to reserve capital")
    return (req.qty * reference_price * (1 + Decimal(str(overfill_buffer_pct)) / 100)).quantize(CENT)


def whole_share_cap(available_cash: Decimal, overfill_buffer_pct: float) -> Decimal:
    """§3.4: whole-share market orders are capped at cash × (1 − buffer)."""
    return (available_cash * (1 - Decimal(str(overfill_buffer_pct)) / 100)).quantize(CENT, rounding=ROUND_DOWN)


def positions_value(positions: Sequence[dict[str, Any]], marks: dict[str, Decimal]) -> Decimal:
    total = Decimal(0)
    for p in positions:
        price = marks.get(str(p["symbol"]))
        if price is None:
            price = Decimal(str(p["avg_cost"] or 0))
        total += Decimal(str(p["qty"])) * price
    return total.quantize(CENT)


def equity(cash: Decimal, positions: Sequence[dict[str, Any]], marks: dict[str, Decimal]) -> Decimal:
    return cash + positions_value(positions, marks)


def position_limit_breach(
    notional: Decimal,
    equity_usd: Decimal,
    open_positions: Sequence[dict[str, Any]],
    symbol: str,
    limits: PositionLimits,
    marks: dict[str, Decimal],
) -> str | None:
    """§8.2: MAX_SINGLE_POSITION_PCT, MAX_POSITIONS, and 'no trade may use the entire balance'. The normal range
    (10–30%) guides the agent's sizing prompt; it is not a rejection."""
    if equity_usd <= 0:
        return "equity is zero"
    if notional >= equity_usd:
        return f"notional {notional} would use the entire balance {equity_usd}"
    cap = equity_usd * Decimal(str(limits.max_single_position_pct)) / 100
    held = next((p for p in open_positions if p["symbol"] == symbol), None)
    existing = positions_value([held], marks) if held else Decimal(0)
    if existing + notional > cap:
        return f"{symbol}: {existing} held + {notional} exceeds MAX_SINGLE_POSITION_PCT ({cap})"
    if held is None and len(open_positions) >= limits.max_positions:
        return f"MAX_POSITIONS {limits.max_positions} already open"
    return None


def qty_for_notional(notional: Decimal, price: Decimal) -> Decimal:
    return (notional / price).quantize(SHARE, rounding=ROUND_DOWN)
