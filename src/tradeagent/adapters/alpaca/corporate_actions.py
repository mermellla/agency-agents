"""Corporate actions (§15): splits and symbol changes from GET https://data.alpaca.markets/v1/corporate-actions (verified 2026-09-13)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from tradeagent.adapters.alpaca.client import MARKET_DATA_URL, AlpacaClient


@dataclass(frozen=True)
class SplitAction:
    symbol: str
    kind: str  # forward_split | reverse_split
    ex_date: date
    new_rate: Decimal
    old_rate: Decimal

    @property
    def ratio(self) -> Decimal:
        """shares after = shares before × ratio"""
        return self.new_rate / self.old_rate


@dataclass(frozen=True)
class CashDividend:
    symbol: str
    rate: Decimal  # USD per share
    ex_date: date
    payable_date: date | None
    record_date: date | None


def parse_dividends(data: dict[str, Any]) -> list[CashDividend]:
    ca = data.get("corporate_actions") or {}
    out: list[CashDividend] = []
    for r in ca.get("cash_dividends") or []:
        if not r.get("ex_date") or r.get("rate") is None:
            continue
        out.append(
            CashDividend(
                str(r["symbol"]),
                Decimal(str(r["rate"])),
                date.fromisoformat(r["ex_date"]),
                date.fromisoformat(r["payable_date"]) if r.get("payable_date") else None,
                date.fromisoformat(r["record_date"]) if r.get("record_date") else None,
            )
        )
    return out


@dataclass(frozen=True)
class SymbolChange:
    old_symbol: str
    new_symbol: str
    process_date: date


def parse_actions(data: dict[str, Any]) -> tuple[list[SplitAction], list[SymbolChange]]:
    ca = data.get("corporate_actions") or {}
    splits: list[SplitAction] = []
    for kind in ("forward_splits", "reverse_splits"):
        for r in ca.get(kind) or []:
            splits.append(
                SplitAction(
                    str(r["symbol"]),
                    kind[:-1],
                    date.fromisoformat(r["ex_date"]),
                    Decimal(str(r["new_rate"])),
                    Decimal(str(r["old_rate"])),
                )
            )
    changes = [
        SymbolChange(str(r["old_symbol"]), str(r["new_symbol"]), date.fromisoformat(r["process_date"]))
        for r in ca.get("name_changes") or []
        if r.get("old_symbol") and r.get("new_symbol") and r["old_symbol"] != r["new_symbol"]
    ]
    return splits, changes


class AlpacaCorporateActions:
    def __init__(self, client: AlpacaClient):
        self.client = client

    def for_symbols(self, symbols: list[str], start: date, end: date) -> tuple[list[SplitAction], list[SymbolChange]]:
        if not symbols:
            return [], []
        data = self.client.get(
            "/v1/corporate-actions",
            {
                "symbols": ",".join(symbols),
                "types": "forward_split,reverse_split,name_change",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 1000,
            },
            base=MARKET_DATA_URL,
        )
        return parse_actions(data)

    def dividends_for(self, symbols: list[str], start: date, end: date) -> list[CashDividend]:
        if not symbols:
            return []
        data = self.client.get(
            "/v1/corporate-actions",
            {
                "symbols": ",".join(symbols),
                "types": "cash_dividend",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 1000,
            },
            base=MARKET_DATA_URL,
        )
        return parse_dividends(data)
