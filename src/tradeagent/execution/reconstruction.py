"""§8.9 / §12 / ADR-0017 delayed fill reconstruction for simulated portfolios. The fill is computed from the first
eligible consolidated quote or trade after `order_eligible_at` once the SIP embargo has passed; never estimated at
decision time. BUY at the ask / SELL at the bid with no added spread; trade fallback ± the median half-spread of usable
quotes within ±5 minutes; SIM_ADDITIONAL_SLIPPAGE_BPS applied afterwards and stored separately."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any, Protocol
from uuid import UUID

from tradeagent.config.loader import SimulationConfig
from tradeagent.domain.enums import OrderSide, OrderStatus, OrderType, ReconstructionBasis
from tradeagent.domain.models import Quote, Trade
from tradeagent.execution.ledger_apply import AppliedFill, apply_fill
from tradeagent.fees import FeeSchedules
from tradeagent.persistence.db import Database

log = logging.getLogger("tradeagent.reconstruction")
QUOTE_WINDOW = timedelta(seconds=60)
HALF_SPREAD_WINDOW = timedelta(minutes=5)
TRADE_SEARCH_WINDOW = timedelta(hours=8)
TICK = Decimal("0.01")
PRICE = Decimal("0.000001")
SHARE = Decimal("0.000000001")


class SipHistory(Protocol):
    """The slice of the market-data adapter the reconstructor needs (AlpacaBars satisfies it)."""

    @property
    def embargo(self) -> timedelta: ...

    def quotes(self, symbol: str, start: datetime, end: datetime) -> list[Quote]: ...
    def trades(self, symbol: str, start: datetime, end: datetime) -> list[Trade]: ...


def usable_quote(q: Quote) -> bool:
    """ADR-0017: two-sided, not crossed, sizes ≥ 1, spread ≤ max(1% of mid, 5 ticks)."""
    if not q.usable:
        return False
    mid = (q.ask + q.bid) / 2
    return (q.ask - q.bid) <= max(mid * Decimal("0.01"), 5 * TICK)


@dataclass(frozen=True)
class Reconstructed:
    price: Decimal
    basis: ReconstructionBasis
    half_spread: Decimal | None
    fill_at: datetime
    slippage_bps: Decimal
    raw_price: Decimal


class FillReconstructor:
    def __init__(self, sip: SipHistory, cfg: SimulationConfig, clock: Callable[[], datetime] | None = None):
        self.sip, self.cfg = sip, cfg
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.delay = timedelta(minutes=cfg.sim_fill_delay_min + cfg.sim_fill_delay_margin_min)
        self.slippage_bps = Decimal(str(cfg.sim_additional_slippage_bps))

    def ready_at(self, order: dict[str, Any]) -> datetime:
        eligible: datetime = order["order_eligible_at"]
        return eligible + self.delay

    def _median_half_spread(self, symbol: str, around: datetime, end_cap: datetime) -> Decimal | None:
        qs = self.sip.quotes(symbol, around - HALF_SPREAD_WINDOW, min(around + HALF_SPREAD_WINDOW, end_cap))
        hs = [q.half_spread for q in qs if usable_quote(q)]
        return Decimal(str(median(hs))).quantize(PRICE) if hs else None

    def reconstruct(self, order: dict[str, Any], now: datetime | None = None) -> Reconstructed | None:
        """None: no eligible print yet (order stays pending; the job expires it after expires_at)."""
        now = now or self.clock()
        eligible: datetime = order["order_eligible_at"]
        if now < self.ready_at(order):
            return None
        end_cap = now - self.sip.embargo
        if order.get("expires_at"):
            end_cap = min(end_cap, order["expires_at"])
        if end_cap <= eligible:
            return None
        symbol, side = str(order["symbol"]), OrderSide(str(order["side"]))
        otype = OrderType(str(order["order_type"]))
        buy = side == OrderSide.BUY
        result: tuple[Decimal, ReconstructionBasis, Decimal | None, datetime] | None = None

        if otype == OrderType.MARKET:
            for q in self.sip.quotes(symbol, eligible, min(eligible + QUOTE_WINDOW, end_cap)):
                if q.at >= eligible and usable_quote(q):
                    result = (
                        q.ask if buy else q.bid,
                        ReconstructionBasis.ASK if buy else ReconstructionBasis.BID,
                        None,
                        q.at,
                    )
                    break
        if result is None:
            trades = self.sip.trades(symbol, eligible, min(eligible + TRADE_SEARCH_WINDOW, end_cap))
            for t in trades:
                if t.at < eligible:
                    continue
                if otype == OrderType.STOP and order.get("stop_price") is not None:
                    stop = Decimal(str(order["stop_price"]))
                    if (buy and t.price < stop) or (not buy and t.price > stop):
                        continue
                if otype in (OrderType.LIMIT, OrderType.STOP_LIMIT) and order.get("limit_price") is not None:
                    limit = Decimal(str(order["limit_price"]))
                    if (buy and t.price > limit) or (not buy and t.price < limit):
                        continue
                hs = self._median_half_spread(symbol, t.at, end_cap)
                if hs is None:
                    return None  # ADR-0017: no usable quote in the window either → stays pending
                price = t.price + hs if buy else t.price - hs
                if otype in (OrderType.LIMIT, OrderType.STOP_LIMIT) and order.get("limit_price") is not None:
                    limit = Decimal(str(order["limit_price"]))
                    price = min(price, limit) if buy else max(price, limit)
                result = (
                    price,
                    ReconstructionBasis.TRADE_PLUS_HALF_SPREAD if buy else ReconstructionBasis.TRADE_MINUS_HALF_SPREAD,
                    hs,
                    t.at,
                )
                break
        if result is None:
            return None
        raw, basis, hs, at = result
        factor = 1 + self.slippage_bps / 10_000 if buy else 1 - self.slippage_bps / 10_000
        price = (raw * factor).quantize(PRICE, rounding=ROUND_HALF_UP)
        return Reconstructed(price, basis, hs, at, self.slippage_bps, raw)


class ReconstructionJob:
    """Runs after every scan tick and after the close: fills or expires simulated orders (§15 crash safety: it is
    deterministic given order_eligible_at, so a restart simply resumes)."""

    def __init__(
        self,
        db: Database,
        reconstructor: FillReconstructor,
        fees: FeeSchedules | None,
        settles_on: Callable[[datetime], Any] | None = None,
        on_applied: Callable[[dict[str, Any], AppliedFill, Reconstructed], None] | None = None,
    ):
        self.db, self.rc, self.fees = db, reconstructor, fees
        self.settles_on = settles_on
        self.on_applied = on_applied

    def run(self, experiment_id: UUID, now: datetime) -> list[tuple[UUID, str]]:
        outcomes: list[tuple[UUID, str]] = []
        for order in self.db.pending_reconstruction_orders(experiment_id):
            oid = UUID(str(order["id"]))
            if now < self.rc.ready_at(order):
                continue
            rec = self.rc.reconstruct(order, now)
            if rec is None:
                exp = order.get("expires_at")
                if exp is not None and now >= exp + self.rc.delay:
                    with self.db.transaction():
                        self.db.expire_order(oid, "no eligible SIP print before expiry (§14 rule 11)")
                    outcomes.append((oid, "expired"))
                continue
            qty = (
                Decimal(str(order["qty"]))
                if order.get("qty") is not None
                else (Decimal(str(order["notional"])) / rec.price).quantize(SHARE, rounding=ROUND_DOWN)
            )
            if qty <= 0:
                continue
            with self.db.transaction():
                applied = apply_fill(
                    self.db,
                    order=order,
                    qty=qty,
                    price=rec.price,
                    fill_at=rec.fill_at,
                    fill_source="reconstructed",
                    broker_fill_id=None,
                    fees=self.fees,
                    settles_on=self.settles_on(rec.fill_at) if self.settles_on else None,
                    reconstruction_basis=rec.basis.value,
                    half_spread_estimate=rec.half_spread,
                    additional_slippage_bps=rec.slippage_bps,
                )
                assert applied is not None
                self.db.transition_order(oid, OrderStatus.FILLED, f"reconstructed {rec.basis.value} @ {rec.price}")
                self.db.set_decision_filled(UUID(str(order["decision_id"])), rec.fill_at, applied.position_id)
                if self.on_applied is not None:
                    self.on_applied(order, applied, rec)
            outcomes.append((oid, "filled"))
        return outcomes
