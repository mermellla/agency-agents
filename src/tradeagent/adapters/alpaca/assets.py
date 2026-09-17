"""Alpaca assets master (§6.1 step 1). Verified 2026-09-13: no ETF/ADR/SPAC flag; attributes enum includes PTP flags."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tradeagent.adapters.alpaca.client import AlpacaClient

SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")
PTP_ATTRS = {"ptp_no_exception", "ptp_with_exception"}


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    exchange: str
    tradable: bool
    fractionable: bool
    attributes: tuple[str, ...]

    @property
    def common_stock_shape(self) -> bool:
        """ADR-0005 step 1: plain symbol, no partnership units, not an IPO placeholder."""
        return (
            bool(SYMBOL_RE.match(self.symbol))
            and not (set(self.attributes) & PTP_ATTRS)
            and "ipo" not in self.attributes
        )


def parse_assets(rows: list[dict[str, Any]]) -> list[Asset]:
    return [
        Asset(
            r["symbol"],
            r.get("name") or "",
            r.get("exchange") or "",
            bool(r.get("tradable")),
            bool(r.get("fractionable")),
            tuple(r.get("attributes") or ()),
        )
        for r in rows
        if r.get("class") == "us_equity" and r.get("status") == "active"
    ]


class AlpacaAssets:
    def __init__(self, client: AlpacaClient):
        self.client = client

    def active_us_equities(self) -> list[Asset]:
        return parse_assets(self.client.get("/v2/assets", {"status": "active", "asset_class": "us_equity"}))
