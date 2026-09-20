"""QB-1.0 quant shadow (§12, config/qb_rules.yaml): same universe, exclusions, scanner and risk limits as the primary,
fixed rules, no LLM. Candidate = highest composite score not held; size 20% of equity; stop 2×ATR(14) below entry;
target 2R; time stop 5 trading days; max 5 positions; exits before entries; no drift judgment.
`order_eligible_at` = scan completion + this validation, so the baseline acts earlier than the LLM on the same signal."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.adapters.alpaca.calendar import StaticCalendar
from tradeagent.config import Settings
from tradeagent.domain.enums import DecisionKind, DecisionOrigin, DecisionStatus, DecisionType, TrustGrade
from tradeagent.domain.models import Decision, ForecastContract, Proposal, Timeline
from tradeagent.execution.executor import Executor
from tradeagent.persistence.db import Database
from tradeagent.portfolios.sim_exits import ExitAction, SimulatedExitEngine
from tradeagent.risk.desk import ReferencePrice, RiskDesk, ValidationContext
from tradeagent.risk.sizing import equity as equity_of
from tradeagent.scanner.scanner import ScanInputs, ScanOutput
from tradeagent.scanner.signals.technical import atr

log = logging.getLogger("tradeagent.qb")
CENT = Decimal("0.01")


@dataclass
class QBReport:
    exits: list[ExitAction] = field(default_factory=list)
    candidate: str | None = None
    decision_id: UUID | None = None
    status: str = "no_candidate"  # no_candidate | full | no_atr | validated | rejected
    reject_code: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class QuantBaseline:
    def __init__(
        self,
        db: Database,
        desk: RiskDesk,
        executor: Executor,
        exits: SimulatedExitEngine,
        settings: Settings,
        cal: StaticCalendar,
        experiment_id: UUID,
        phase_id: UUID,
        clock: Callable[[], datetime] | None = None,
        halted: Callable[[], tuple[bool, bool]] | None = None,
    ):
        self.db, self.desk, self.executor, self.exits = db, desk, executor, exits
        self.settings, self.cal = settings, cal
        self.rules = settings.qb_rules
        self.experiment_id, self.phase_id = experiment_id, phase_id
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.halted = halted or (lambda: (False, False))
        self.portfolio_id = UUID(str(db.portfolio_by_kind(experiment_id, "quant_shadow")["id"]))

    async def on_scan(self, out: ScanOutput, inputs: ScanInputs, session_close: datetime | None) -> QBReport:
        report = QBReport()
        # 1. exits before entries (§14 rule 1)
        report.exits = await self.exits.check(self.portfolio_id, inputs.bars15, session_close)
        now = self.clock()
        positions = self.db.open_positions(self.portfolio_id)
        held = {str(p["symbol"]) for p in positions}
        if len(positions) >= int(self.rules["max_positions"]):
            report.status = "full"
            return report
        cand = next((c for c in sorted(out.result.candidates, key=lambda c: c.rank) if c.symbol not in held), None)
        if cand is None:
            return report
        report.candidate = cand.symbol
        daily = inputs.daily.get(cand.symbol) or []
        a = atr(daily, int(self.rules["atr_period"]))
        if a is None or a <= 0:
            report.status, report.detail = "no_atr", {"bars": len(daily)}
            return report
        atr_d = Decimal(str(round(a, 6)))
        ref = cand.sip_signal_price
        marks = {s: b[-1].close for s, b in inputs.daily.items() if b}
        cash = self.db.available_cash(self.portfolio_id)
        eq = equity_of(self.db.cash_balance(self.portfolio_id), positions, marks)
        notional = min((eq * Decimal(str(self.rules["size_pct_of_equity"])) / 100).quantize(CENT), cash.quantize(CENT))
        stop_mult, r_mult = Decimal(str(self.rules["stop_atr_multiple"])), Decimal(str(self.rules["target_r_multiple"]))
        inv = (ref - stop_mult * atr_d).quantize(CENT)
        target = (ref + r_mult * stop_mult * atr_d).quantize(CENT)
        time_stop = self.cal.add_trading_days(now, int(self.rules["time_stop_trading_days"]))
        if notional <= 0 or inv <= 0:
            report.status, report.detail = "rejected", {"reason": "no capital or degenerate levels"}
            return report
        proposal = Proposal(
            decision=DecisionType.BUY,
            ticker=cand.symbol,
            strategy="QB-1.0",
            direction="long",
            proposed_notional=notional,
            entry_type="market",
            entry_price_or_range=f"first eligible SIP print after {now.isoformat()} (signal {ref})",
            target_price=target,
            invalidation_price=inv,
            time_stop_at=time_stop,
            expected_holding_period=time_stop - now,
            probability=Decimal("0.5"),
            reason_for_entry=f"highest composite score {cand.composite_score} not held (rank {cand.rank}); ATR14={atr_d}",
        )
        versions = self.db.phase_versions(self.phase_id)
        decision = Decision(
            experiment_id=self.experiment_id,
            experiment_phase_id=self.phase_id,
            portfolio_id=self.portfolio_id,
            scan_id=cand.scan_id,
            candidate_id=cand.candidate_id,
            origin=DecisionOrigin.QUANT,
            kind=DecisionKind.ENTRY,
            timeline=Timeline(
                signal_observed_at=cand.signal_observed_at,
                scanner_completed_at=out.result.scanner_completed_at,
                risk_validation_completed_at=now,
            ),
            sip_signal_price=ref,
            sip_signal_timestamp=cand.sip_signal_timestamp,
            proposal=proposal,
            forecast=ForecastContract(
                target_price_at_entry=target,
                invalidation_price_at_entry=inv,
                time_stop_at_entry=time_stop,
                probability_pre_critique=Decimal("0.5"),
            ),
            market_regime=out.result.regime,
            **versions,
        )
        all_halted, entries_halted = self.halted()
        ctx = ValidationContext(
            now=now,
            mode=self.settings.risk.execution.execution_mode,
            instrument=self.db.instrument(cand.symbol),
            available_cash=cash,
            equity=eq,
            open_positions=positions,
            marks=marks,
            reference=ReferencePrice(ref, cand.signal_observed_at, TrustGrade.EXECUTION, "alpaca-sip"),
            sip_bar_end=cand.signal_bar_time,
            all_halted=all_halted,
            entries_halted=entries_halted,
            duplicate=bool(self.db.open_orders_for_symbol(self.portfolio_id, cand.symbol, ("entry",))),
        )
        verdict = self.desk.validate(decision, ctx)
        report.decision_id = decision.decision_id
        with self.db.transaction():
            if not verdict.ok:
                assert verdict.reject_code is not None
                decision.reject_code = verdict.reject_code
                decision.status = DecisionStatus.REJECTED
                self.db.insert_decision(decision)
                report.status, report.reject_code, report.detail = "rejected", verdict.reject_code.value, verdict.detail
                log.info("QB rejected %s: %s %s", cand.symbol, verdict.reject_code.value, verdict.detail)
                return report
            decision.status = DecisionStatus.VALIDATED
            decision.iex_price_at_order = verdict.drift.get("iex_price_at_order")
            decision.signal_to_order_move_pct = verdict.drift.get("signal_to_order_move_pct")
            self.db.insert_decision(decision)
            orders = self.desk.construct_orders(decision, ctx, is_simulated=True, expires_at=session_close)
            placement = await self.executor.place(decision, orders, ref)
        report.status, report.detail = (
            "validated",
            {"placement": placement.status, "orders": [str(o) for o in placement.order_ids]},
        )
        return report
