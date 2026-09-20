"""§8.7 runaway guards: MAX_ORDERS_PER_DAY, MAX_LLM_CALLS_PER_HOUR, HALT_AFTER_CONSECUTIVE_REJECTS, optional
KILL_SWITCH_DAILY_LOSS_PCT (bug containment, default off). A tripped guard halts new entries and emails."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from tradeagent.adapters.alpaca.calendar import ET
from tradeagent.config.loader import GuardsConfig
from tradeagent.domain.enums import HaltScope
from tradeagent.ops.halts import Halter
from tradeagent.persistence.db import Database

JEV_MODEL_PREFIX = "jev"  # zero-cost System One judgments are not the runaway the LLM-call guard watches


class Guards:
    def __init__(
        self, db: Database, cfg: GuardsConfig, halter: Halter, experiment_id: UUID, primary_portfolio_id: UUID
    ):
        self.db, self.cfg, self.halter = db, cfg, halter
        self.experiment_id, self.primary = experiment_id, primary_portfolio_id

    @staticmethod
    def _session_bounds(now: datetime) -> tuple[datetime, datetime]:
        d = now.astimezone(ET).date()
        start = datetime.combine(d, datetime.min.time(), tzinfo=ET)
        return start, start + timedelta(days=1)

    def orders_today_ok(self, now: datetime) -> bool:
        start, end = self._session_bounds(now)
        n = self.db.orders_created_between(self.primary, start, end)
        if n >= self.cfg.max_orders_per_day:
            if "MAX_ORDERS_PER_DAY" not in self.halter.state().codes:
                self.halter.halt(
                    "MAX_ORDERS_PER_DAY", HaltScope.ENTRIES, {"orders_today": n, "max": self.cfg.max_orders_per_day}
                )
            return False
        return True

    def llm_rate_ok(self, now: datetime) -> bool:
        n = self.db.llm_calls_between(self.experiment_id, now - timedelta(hours=1), now, JEV_MODEL_PREFIX)
        if n >= self.cfg.max_llm_calls_per_hour:
            if "MAX_LLM_CALLS_PER_HOUR" not in self.halter.state().codes:
                self.halter.halt(
                    "MAX_LLM_CALLS_PER_HOUR",
                    HaltScope.ENTRIES,
                    {"calls_last_hour": n, "max": self.cfg.max_llm_calls_per_hour},
                )
            return False
        return True

    def consecutive_rejects_ok(self, portfolio_id: UUID) -> bool:
        n = self.cfg.halt_after_consecutive_rejects
        recent = self.db.recent_decisions(portfolio_id, n)
        if len(recent) >= n and all(str(r["status"]) == "rejected" for r in recent):
            if "CONSECUTIVE_REJECTS" not in self.halter.state().codes:
                self.halter.halt(
                    "CONSECUTIVE_REJECTS",
                    HaltScope.ENTRIES,
                    {"count": n, "codes": [str(r["reject_code"]) for r in recent]},
                )
            return False
        return True

    def kill_switch_ok(self, equity_at_open: Decimal, equity_now: Decimal) -> bool:
        pct = self.cfg.kill_switch_daily_loss_pct
        if pct is None or equity_at_open <= 0:
            return True
        loss = (equity_at_open - equity_now) / equity_at_open * 100
        if loss >= Decimal(str(pct)):
            if "KILL_SWITCH_DAILY_LOSS" not in self.halter.state().codes:
                self.halter.halt("KILL_SWITCH_DAILY_LOSS", HaltScope.ENTRIES, {"loss_pct": str(loss), "threshold": pct})
            return False
        return True
