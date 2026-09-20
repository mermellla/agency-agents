"""§12 benchmarks: cash ($500 flat), SPY and VTI ($500 bought at the experiment start close, marked daily from SIP daily
bars into `benchmark_prices`). The buy is a simulated order whose eligibility is the moment the start-day close became
retrievable (§8.9), filled at that official close with half-spread 0 and reason recorded."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.domain.enums import OrderPurpose, OrderSide, OrderStatus, OrderType, TimeInForce
from tradeagent.domain.models import Bar, OrderRequest
from tradeagent.execution.ledger_apply import apply_fill
from tradeagent.persistence.db import Database

log = logging.getLogger("tradeagent.benchmarks")
BENCHMARKS = (("SPY", "benchmark_spy"), ("VTI", "benchmark_vti"))
SHARE = Decimal("0.000000001")


class Benchmarks:
    def __init__(
        self, db: Database, sip: Any, experiment_id: UUID, phase_id: UUID, clock: Callable[[], datetime] | None = None
    ):
        self.db, self.sip = db, sip
        self.experiment_id, self.phase_id = experiment_id, phase_id
        self.clock = clock or (lambda: datetime.now(tz=UTC))

    def _daily(self, symbols: list[str], start: date, end_dt: datetime) -> dict[str, list[Bar]]:
        out: dict[str, list[Bar]] = {}
        for b in self.sip.bars(symbols, "1Day", datetime.combine(start, datetime.min.time(), tzinfo=UTC), end_dt):
            out.setdefault(b.symbol, []).append(b)
        return out

    def mark(self, through: date) -> dict[str, tuple[date, Decimal]]:
        """Upsert SPY/VTI closes for every retrievable session up to `through`. Returns the latest close per symbol."""
        end = min(
            self.clock() - self.sip.embargo,
            datetime.combine(through + timedelta(days=1), datetime.min.time(), tzinfo=UTC),
        )
        bars = self._daily([s for s, _ in BENCHMARKS], through - timedelta(days=10), end)
        latest: dict[str, tuple[date, Decimal]] = {}
        with self.db.transaction():
            for sym, bs in bars.items():
                for b in sorted(bs, key=lambda x: x.start):
                    d = b.start.astimezone(UTC).date()
                    self.db.upsert_benchmark_price(sym, d, b.close, str(b.stamp.source))
                    latest[sym] = (d, b.close)
        return latest

    def ensure_started(self, started_on: date) -> list[str]:
        """Buy SPY/VTI at the start-day close once each; returns the symbols bought this call."""
        bought: list[str] = []
        end = self.clock() - self.sip.embargo
        bars = self._daily([s for s, _ in BENCHMARKS], started_on, end)
        for sym, kind in BENCHMARKS:
            # decisions.ticker references instruments; the ETFs are universe-excluded but must exist as rows
            self.db.conn.execute(
                "insert into instruments (symbol, name, tradable, fractionable, universe_status, status_reason) values (%s, %s, true, true, 'excluded_universe', 'benchmark ETF (§12)') on conflict do nothing",
                (sym, sym),
            )
            portfolio = self.db.portfolio_by_kind(self.experiment_id, kind)
            pid = UUID(str(portfolio["id"]))
            if self.db.open_positions(pid) or self.db.open_orders_for_symbol(pid, sym, ("entry",)):
                continue
            start_bar = next((b for b in bars.get(sym, []) if b.start.astimezone(UTC).date() == started_on), None)
            if start_bar is None:
                continue  # the start-day close is not retrievable yet
            cash = self.db.available_cash(pid)
            if cash <= 0:
                continue
            at = start_bar.retrievable_at
            with self.db.transaction():
                did = self.db.create_benchmark_decision(
                    self.experiment_id, self.phase_id, pid, sym, cash, self.db.phase_versions(self.phase_id), at
                )
                req = OrderRequest(
                    decision_id=did,
                    portfolio_id=pid,
                    purpose=OrderPurpose.ENTRY,
                    symbol=sym,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    time_in_force=TimeInForce.DAY,
                    notional=cash,
                    is_simulated=True,
                    order_eligible_at=at,
                )
                oid = self.db.record_order(req, self.experiment_id, self.phase_id, None, cash)
                self.db.transition_order(oid, OrderStatus.VALIDATED, "benchmark start (§12)")
                self.db.transition_order(oid, OrderStatus.FILL_PENDING_RECONSTRUCTION, "benchmark start (§12)")
                order = self.db.order(oid)
                assert order is not None
                qty = (cash / start_bar.close).quantize(SHARE)
                applied = apply_fill(
                    self.db,
                    order=order,
                    qty=qty,
                    price=start_bar.close,
                    fill_at=at,
                    fill_source="reconstructed",
                    broker_fill_id=None,
                    fees=None,
                    settles_on=None,
                    reconstruction_basis="trade_plus_half_spread",
                    half_spread_estimate=Decimal(0),
                )
                assert applied is not None
                self.db.transition_order(oid, OrderStatus.FILLED, f"official close {start_bar.close} on {started_on}")
                self.db.set_decision_filled(did, at, applied.position_id)
                self.db.upsert_benchmark_price(sym, started_on, start_bar.close, str(start_bar.stamp.source))
            bought.append(sym)
            log.info("benchmark %s bought: %s @ %s", sym, qty, start_bar.close)
        return bought

    def equity(self, kind: str, marks: dict[str, Decimal]) -> Decimal:
        pid = UUID(str(self.db.portfolio_by_kind(self.experiment_id, kind)["id"]))
        cash = self.db.cash_balance(pid)
        for p in self.db.open_positions(pid):
            price = marks.get(str(p["symbol"]))
            if price is None:
                latest = self.db.benchmark_close_on_or_before(str(p["symbol"]), self.clock().date())
                price = latest[1] if latest else Decimal(str(p["avg_cost"]))
            cash += Decimal(str(p["qty"])) * price
        return cash.quantize(Decimal("0.01"))
