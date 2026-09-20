"""§9 budget desk (ADR-0004, OI-09 as approved): monthly cap prorated over the trading days of the calendar month;
60% entries (hard) / 40% exits+reviews (soft, overage logged as BUDGET_OVERAGE_EXIT); unspent daily allowance carries
within the month and resets on the 1st. Jev calls cost 0 during the alpha and pass through unchanged."""

from __future__ import annotations

import calendar as _cal
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal
from uuid import UUID

from tradeagent.adapters.alpaca.calendar import StaticCalendar
from tradeagent.config.loader import BudgetConfig
from tradeagent.domain.enums import BudgetBucket
from tradeagent.domain.models import BudgetState, LLMCall
from tradeagent.persistence.db import Database

log = logging.getLogger("tradeagent.budget")
MICRO = Decimal("0.000001")
ALERT_FRACTION = Decimal("0.8")


def month_bounds(d: date) -> tuple[date, date]:
    return d.replace(day=1), d.replace(day=_cal.monthrange(d.year, d.month)[1])


class BudgetDesk:
    def __init__(
        self,
        db: Database,
        cfg: BudgetConfig,
        cal: StaticCalendar,
        experiment_id: UUID,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db, self.cfg, self.cal, self.experiment_id = db, cfg, cal, experiment_id
        self.clock = clock or (lambda: datetime.now(tz=UTC))

    def trading_days_in_month(self, d: date) -> int:
        first, last = month_bounds(d)
        n = len(self.cal.sessions_between(first, last))
        return n or 21  # a calendar that does not cover the month yet: ADR-0004's ~21-day assumption

    def allowance(self, d: date, bucket: BudgetBucket) -> Decimal:
        cap = Decimal(str(self.cfg.llm_monthly_cap_usd))
        pct = Decimal(str(self.cfg.llm_entry_bucket_pct))
        share = pct / 100 if bucket == BudgetBucket.ENTRIES else (100 - pct) / 100
        return (cap * share / self.trading_days_in_month(d)).quantize(MICRO, rounding=ROUND_DOWN)

    def _as_state(self, row: dict[str, object]) -> BudgetState:
        return BudgetState(
            trade_date=row["trade_date"],
            bucket=BudgetBucket(str(row["bucket"])),
            allowance_usd=Decimal(str(row["allowance_usd"])),
            carried_in_usd=Decimal(str(row["carried_in_usd"])),
            spent_usd=Decimal(str(row["spent_usd"])),
            overage_usd=Decimal(str(row["overage_usd"])),
        )

    def state(self, d: date, bucket: BudgetBucket) -> BudgetState:
        row = self.db.budget_row(self.experiment_id, d, bucket.value)
        if row is None:
            first, _ = month_bounds(d)
            prev = self.db.latest_budget_row_before(self.experiment_id, d, bucket.value, first)
            carried = Decimal(0)
            if prev is not None:
                remaining = (
                    Decimal(str(prev["allowance_usd"]))
                    + Decimal(str(prev["carried_in_usd"]))
                    - Decimal(str(prev["spent_usd"]))
                )
                carried = max(Decimal(0), remaining)
            row = self.db.insert_budget_row(self.experiment_id, d, bucket.value, self.allowance(d, bucket), carried)
        return self._as_state(row)

    def entries_exhausted(self, d: date) -> bool:
        return self.state(d, BudgetBucket.ENTRIES).exhausted

    def charge(self, call: LLMCall, d: date) -> tuple[BudgetState, list[str]]:
        """Record a call's cost against its bucket. Returns the new state and the alert events it triggered
        (budget_80pct, budget_exhausted, BUDGET_OVERAGE_EXIT)."""
        before = self.state(d, call.bucket)
        cost = Decimal(call.cost_usd)
        after_spent = before.spent_usd + cost
        budget = before.allowance_usd + before.carried_in_usd
        events: list[str] = []
        overage = max(Decimal(0), after_spent - budget) if call.bucket == BudgetBucket.EXITS_REVIEWS else Decimal(0)
        exhausted_at = self.clock() if after_spent >= budget and cost > 0 else None
        if budget > 0 and before.spent_usd < budget * ALERT_FRACTION <= after_spent:
            events.append("budget_80pct")
        if before.spent_usd < budget <= after_spent:
            events.append("budget_exhausted")
        if call.bucket == BudgetBucket.EXITS_REVIEWS and after_spent > budget and cost > 0:
            events.append("BUDGET_OVERAGE_EXIT")
        row = self.db.charge_budget(self.experiment_id, d, call.bucket.value, cost, overage, exhausted_at)
        return self._as_state(row), events
