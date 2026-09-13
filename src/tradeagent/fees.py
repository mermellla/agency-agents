"""Regulatory fee engine — §9, ADR-0012. Pure functions over the effective-dated schedules in config/fees.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from tradeagent.domain.enums import FeeKind

CENT = Decimal("0.01")


def _to_date(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


FeeClassification = Literal["customer_debited", "pass_through_unverified"]


@dataclass(frozen=True)
class FeeLine:
    kind: FeeKind
    amount_usd: Decimal
    rate_basis: dict[str, Any]
    classification: FeeClassification = "customer_debited"


@dataclass(frozen=True)
class FeeSchedules:
    sec: list[dict[str, Any]]
    taf: list[dict[str, Any]]
    cat: list[dict[str, Any]]
    cat_classification: FeeClassification
    version: str

    @classmethod
    def from_config(cls, fees: dict[str, Any], config_version: str) -> FeeSchedules:
        def rows(section: str) -> list[dict[str, Any]]:
            sched = sorted(fees[section]["schedule"], key=lambda r: _to_date(r["effective_from"]))
            out = []
            for r in sched:
                row = {**r, "effective_from": _to_date(r["effective_from"])}
                if r.get("effective_to") is not None:
                    row["effective_to"] = _to_date(r["effective_to"])
                out.append(row)
            return out

        cat_cls: FeeClassification = fees["cat_fee"].get("classification", "customer_debited")
        return cls(
            sec=rows("sec_section_31"),
            taf=rows("finra_taf"),
            cat=rows("cat_fee"),
            cat_classification=cat_cls,
            version=config_version,
        )

    @staticmethod
    def row_on(schedule: list[dict[str, Any]], on: date) -> dict[str, Any] | None:
        """The latest row whose effective_from ≤ on ≤ effective_to (open-ended without effective_to);
        None when no row covers the date — fee unknown or lapsed, never silently zero."""
        current = None
        for r in schedule:
            if r["effective_from"] <= on:
                current = r
        if current is None:
            return None
        end = current.get("effective_to")
        if end is not None and on > end:
            return None
        return current

    def for_sell(self, on: date, qty: Decimal, notional: Decimal) -> list[FeeLine]:
        lines: list[FeeLine] = []
        sec = self.row_on(self.sec, on)
        if sec is None:
            raise ValueError(f"no SEC Section 31 rate effective on {on}")
        sec_amt = (notional / Decimal(1_000_000) * Decimal(str(sec["rate"]))).quantize(CENT, ROUND_HALF_UP)
        lines.append(
            FeeLine(
                FeeKind.SEC_SECTION_31,
                sec_amt,
                {
                    "rate_per_million": sec["rate"],
                    "notional": str(notional),
                    "effective_from": sec["effective_from"].isoformat(),
                },
            )
        )
        taf = self.row_on(self.taf, on)
        if taf is None:
            raise ValueError(f"no FINRA TAF rate effective on {on}")
        raw = qty * Decimal(str(taf["rate_per_share"]))
        taf_amt = min(raw, Decimal(str(taf["max_per_trade_usd"]))).quantize(CENT, ROUND_HALF_UP)
        lines.append(
            FeeLine(
                FeeKind.FINRA_TAF,
                taf_amt,
                {
                    "rate_per_share": taf["rate_per_share"],
                    "max_per_trade_usd": taf["max_per_trade_usd"],
                    "qty": str(qty),
                    "effective_from": taf["effective_from"].isoformat(),
                },
            )
        )
        lines.extend(self._cat_lines(on, qty))
        return lines

    def for_buy(self, on: date, qty: Decimal) -> list[FeeLine]:
        return self._cat_lines(on, qty)

    def _cat_lines(self, on: date, qty: Decimal) -> list[FeeLine]:
        cat = self.row_on(self.cat, on)
        if cat is None:
            return []
        amt = (qty * Decimal(str(cat["rate_per_share"]))).quantize(CENT, ROUND_HALF_UP)
        basis = {
            "rate_per_share": cat["rate_per_share"],
            "qty": str(qty),
            "effective_from": cat["effective_from"].isoformat(),
            "effective_to": cat["effective_to"].isoformat() if cat.get("effective_to") else None,
        }
        return [FeeLine(FeeKind.CAT, amt, basis, self.cat_classification)]
