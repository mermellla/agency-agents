"""§9 / §15 dividends: credited to every ledger (shadow and paper alike, the paper simulator ignores them) on the
payable date with `settles_on` = payable date, for the quantity held at the ex-date. Idempotent per
(portfolio, symbol, ex_date)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.adapters.alpaca.calendar import ET
from tradeagent.adapters.alpaca.corporate_actions import CashDividend
from tradeagent.persistence.db import Database

CENT = Decimal("0.01")


def qty_held_at(db: Database, portfolio_id: UUID, symbol: str, at: datetime) -> Decimal:
    row = db.conn.execute(
        "select coalesce(sum(case when side = 'buy' then qty else -qty end), 0) as q from fills where portfolio_id = %s and symbol = %s and fill_at < %s",
        (portfolio_id, symbol, at),
    ).fetchone()
    assert row is not None
    return Decimal(str(row["q"]))


def apply_dividends(
    db: Database,
    experiment_id: UUID,
    phase_id: UUID,
    portfolios: list[dict[str, Any]],
    dividends: list[CashDividend],
    today: date,
) -> list[tuple[str, str, Decimal]]:
    """Credits due dividends (payable_date ≤ today). Returns (portfolio name, symbol, amount) per credit written."""
    written: list[tuple[str, str, Decimal]] = []
    for div in dividends:
        payable = div.payable_date or div.ex_date
        if payable > today:
            continue
        ex_open = datetime.combine(div.ex_date, time(0, 0), tzinfo=ET).astimezone(UTC)
        credit_at = datetime.combine(payable, time(16, 0), tzinfo=ET).astimezone(UTC)
        for p in portfolios:
            pid = UUID(str(p["id"]))
            qty = qty_held_at(db, pid, div.symbol, ex_open)
            if qty <= 0:
                continue
            amount = (qty * div.rate).quantize(CENT)
            if amount <= 0:
                continue
            key = f"dividend:{pid}:{div.symbol}:{div.ex_date.isoformat()}"
            if db.append_cash_event(
                pid,
                experiment_id,
                phase_id,
                credit_at,
                "dividend",
                amount,
                key,
                f"{div.symbol} dividend {div.rate}/share × {qty} (ex {div.ex_date})",
                settles_on=payable,
            ):
                written.append((str(p["name"]), div.symbol, amount))
    return written
