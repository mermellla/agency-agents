"""§8.1 risk desk. Every decision passes, in order: schema validity → mode → exclusion layer → instrument eligibility →
horizon bounds and latency floor → virtual cash (broker buying power ignored) → position limits → staleness → budget →
duplicate → drift fields (advisory) → order construction. The first failure rejects with a coded reason; the desk never
edits a decision (no resizing, no repricing)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tradeagent.adapters.alpaca.calendar import ET
from tradeagent.config import Settings
from tradeagent.domain.enums import (
    DecisionOrigin,
    DecisionType,
    ExecutionMode,
    OrderPurpose,
    OrderSide,
    OrderType,
    RejectCode,
    TimeInForce,
    TrustGrade,
    UniverseStatus,
)
from tradeagent.domain.models import Decision, OrderRequest
from tradeagent.exclusions import ExclusionScreen
from tradeagent.risk.sizing import position_limit_breach, qty_for_notional, whole_share_cap

ENTRY_TYPES = (DecisionType.BUY, DecisionType.ADD)
EXIT_TYPES = (DecisionType.SELL, DecisionType.REDUCE)
LAST_INTRADAY_ENTRY_ET = (15, 0)  # ADR-0011: a same-day time stop after 15:00 ET cannot satisfy MIN_EXPECTED_HOLD


def parse_hold(text: str) -> timedelta:
    """'1h', '30m', '2d', '10 trading days' (≈ 1.4 calendar days each)."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(m|min|h|hour|hours|d|day|days|trading days)\s*", text)
    if not m:
        raise ValueError(f"unparseable holding period {text!r}")
    n, unit = Decimal(m.group(1)), m.group(2)
    if unit in ("m", "min"):
        return timedelta(minutes=float(n))
    if unit.startswith("h"):
        return timedelta(hours=float(n))
    if unit == "trading days":
        return timedelta(days=float(n) * 7 / 5)
    return timedelta(days=float(n))


@dataclass(frozen=True)
class ReferencePrice:
    """An execution-grade price the desk may size against (§5.3)."""

    price: Decimal
    at: datetime
    grade: TrustGrade
    source: str
    is_fallback: bool = False


@dataclass
class ValidationContext:
    now: datetime
    mode: ExecutionMode
    instrument: dict[str, Any] | None
    available_cash: Decimal
    equity: Decimal
    open_positions: list[dict[str, Any]]
    marks: dict[str, Decimal] = field(default_factory=dict)
    reference: ReferencePrice | None = None
    sip_bar_end: datetime | None = None
    last_known_price: Decimal | None = None
    all_halted: bool = False
    entries_halted: bool = False
    entry_budget_exhausted: bool = False
    duplicate: bool = False


@dataclass
class Verdict:
    reject_code: RejectCode | None
    detail: dict[str, Any] = field(default_factory=dict)
    drift: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.reject_code is None


class RiskDesk:
    def __init__(self, settings: Settings, screen: ExclusionScreen, clock: Callable[[], datetime] | None = None):
        self.settings = settings
        self.risk = settings.risk
        self.screen = screen
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.min_hold = parse_hold(self.risk.horizon.min_expected_hold)
        self.max_hold = parse_hold(self.risk.horizon.max_expected_hold)
        self.latency_floor = timedelta(minutes=self.risk.horizon.latency_floor_min)

    # ---- §8.1 pipeline
    def validate(self, d: Decision, ctx: ValidationContext) -> Verdict:
        pr = d.proposal
        kind = pr.decision
        if kind in (DecisionType.SHORT, DecisionType.COVER):
            return Verdict(RejectCode.REJECTED_ACCOUNT_INELIGIBLE, {"reason": "1x long-only account (§3.1)"})
        if kind in (DecisionType.HOLD, DecisionType.NO_ACTION):
            return Verdict(None, {"note": "no order"})
        symbol = pr.ticker or ""
        entry = kind in ENTRY_TYPES
        held = next((p for p in ctx.open_positions if p["symbol"] == symbol), None)

        # 2. mode and halts (§8.7)
        if ctx.mode == ExecutionMode.LIVE:
            return Verdict(RejectCode.REJECTED_MODE, {"mode": ctx.mode.value})
        if ctx.all_halted or (entry and ctx.entries_halted):
            return Verdict(RejectCode.REJECTED_HALTED, {"scope": "all" if ctx.all_halted else "entries"})

        # 3. exclusion layer (§4.4, §14 rule 8, §15 default-deny after the fact)
        sic = ctx.instrument.get("sic") if ctx.instrument else None
        status, reason = self.screen.screen(symbol, sic)
        if status == UniverseStatus.EXCLUDED_ETHICAL and (entry or held is None):
            return Verdict(RejectCode.REJECTED_ETHICAL_SCREEN, {"reason": reason})
        if status == UniverseStatus.NEEDS_ETHICAL_REVIEW and entry:
            return Verdict(RejectCode.NEEDS_ETHICAL_REVIEW, {"reason": reason})
        if held is not None and held.get("ethical_hold") and kind == DecisionType.ADD:
            return Verdict(RejectCode.REJECTED_ETHICAL_HOLD_ADD, {"reason": "ETHICAL_HOLD: no ADD, exits allowed"})
        if held is not None and held.get("needs_ethical_review") and kind == DecisionType.ADD:
            return Verdict(RejectCode.NEEDS_ETHICAL_REVIEW, {"reason": "held name entered a default-deny SIC"})

        if not entry:
            if held is None:
                return Verdict(RejectCode.REJECTED_INSTRUMENT_INELIGIBLE, {"reason": "no open position to exit"})
            stale = self._staleness(ctx, entry=False)
            if stale:
                return Verdict(RejectCode.REJECTED_STALE_DATA, stale)
            if ctx.duplicate:
                return Verdict(RejectCode.REJECTED_DUPLICATE, {"reason": "an exit order is already open"})
            return Verdict(None, {}, self._drift(d, ctx))

        # 4. instrument eligibility (§4.1, §4.2)
        inst = ctx.instrument
        if inst is None or not inst.get("tradable"):
            return Verdict(RejectCode.REJECTED_INSTRUMENT_INELIGIBLE, {"reason": "not a tradable instrument"})
        if self.risk.universe.require_fractionable and not inst.get("fractionable"):
            return Verdict(RejectCode.REJECTED_INSTRUMENT_INELIGIBLE, {"reason": "not fractionable (ADR-0003)"})
        if str(inst.get("universe_status")) != UniverseStatus.ELIGIBLE.value:
            return Verdict(
                RejectCode.REJECTED_INSTRUMENT_INELIGIBLE,
                {"reason": f"universe_status={inst.get('universe_status')}: {inst.get('status_reason')}"},
            )

        # 5. horizon bounds and latency floor (§3.5, ADR-0011)
        ehp = pr.expected_holding_period
        assert ehp is not None and pr.time_stop_at is not None and pr.proposed_notional is not None
        if ehp < self.min_hold or ehp > self.max_hold:
            return Verdict(
                RejectCode.REJECTED_HORIZON,
                {"expected_holding_period": str(ehp), "bounds": [str(self.min_hold), str(self.max_hold)]},
            )
        if ehp < self.latency_floor or pr.time_stop_at - ctx.now < self.latency_floor:
            return Verdict(
                RejectCode.REJECTED_LATENCY_FLOOR,
                {"expected_holding_period": str(ehp), "time_stop_in": str(pr.time_stop_at - ctx.now)},
            )
        now_et = ctx.now.astimezone(ET)
        if (
            pr.time_stop_at.astimezone(ET).date() == now_et.date()
            and (now_et.hour, now_et.minute) >= LAST_INTRADAY_ENTRY_ET
        ):
            return Verdict(RejectCode.REJECTED_LATENCY_FLOOR, {"reason": "intraday entry after 15:00 ET (ADR-0011)"})

        # 6. virtual cash — broker buying power is never consulted (§2, §3.2, ADR-0021)
        notional = Decimal(pr.proposed_notional)
        if notional > ctx.available_cash:
            return Verdict(
                RejectCode.REJECTED_VIRTUAL_CASH,
                {"notional": str(notional), "available_virtual_cash": str(ctx.available_cash)},
            )

        # 7. position limits (§8.2)
        breach = position_limit_breach(
            notional, ctx.equity, ctx.open_positions, symbol, self.risk.position_limits, ctx.marks
        )
        if breach:
            return Verdict(RejectCode.REJECTED_POSITION_LIMIT, {"reason": breach})

        # 8. staleness (§5.5, §14 rule 5)
        stale = self._staleness(ctx, entry=True)
        if stale:
            return Verdict(RejectCode.REJECTED_STALE_DATA, stale)

        # 9. budget (§8.5, §9): only LLM-originated entries spend the entry bucket
        if d.origin == DecisionOrigin.LLM and ctx.entry_budget_exhausted:
            return Verdict(RejectCode.REJECTED_BUDGET, {"bucket": "entries"})

        # 10. duplicate (§8.3)
        if ctx.duplicate:
            return Verdict(
                RejectCode.REJECTED_DUPLICATE, {"reason": "open entry order or same decision already placed"}
            )

        # 11. drift fields — advisory only (D-48)
        return Verdict(None, {}, self._drift(d, ctx))

    def _staleness(self, ctx: ValidationContext, entry: bool) -> dict[str, Any] | None:
        st = self.risk.staleness
        ref = ctx.reference
        if ref is None:
            return {"reason": "no execution-grade reference price"}
        if ref.grade != TrustGrade.EXECUTION:
            return {"reason": f"reference price is {ref.grade.value}-grade ({ref.source}); never used for execution"}
        age = ctx.now - ref.at
        # an IEX reference must be fresh (MAX_IEX_TRADE_AGE_MIN); a delayed-SIP reference (quant baseline, no drift
        # judgment) is bounded by the scan-bar rule instead (MAX_SIP_BAR_AGE_MIN)
        limit = st.max_sip_bar_age_min if "sip" in ref.source else st.max_iex_trade_age_min
        if age > timedelta(minutes=limit):
            return {"reason": "reference price too old", "age_sec": int(age.total_seconds()), "source": ref.source}
        if (
            entry
            and ctx.sip_bar_end is not None
            and ctx.now - ctx.sip_bar_end > timedelta(minutes=st.max_sip_bar_age_min)
        ):
            return {"reason": "SIP scan bar too old", "bar_age_sec": int((ctx.now - ctx.sip_bar_end).total_seconds())}
        if ref.is_fallback and ctx.last_known_price:
            move = abs(ref.price - ctx.last_known_price) / ctx.last_known_price * 100
            if move > Decimal(str(st.fallback_price_tolerance_pct)):
                return {"reason": "fallback price outside FALLBACK_PRICE_TOLERANCE_PCT", "move_pct": str(move)}
        return None

    def _drift(self, d: Decision, ctx: ValidationContext) -> dict[str, Any]:
        if ctx.reference is None or d.sip_signal_price in (None, 0):
            return {}
        assert d.sip_signal_price is not None
        move = (ctx.reference.price - d.sip_signal_price) / d.sip_signal_price * 100
        return {
            "iex_price_at_order": ctx.reference.price,
            "signal_to_order_move_pct": move.quantize(Decimal("0.000001")),
        }

    # ---- §8.3 order construction (never edits the decision)
    def construct_orders(
        self,
        d: Decision,
        ctx: ValidationContext,
        *,
        is_simulated: bool,
        expires_at: datetime | None,
    ) -> list[OrderRequest]:
        pr = d.proposal
        kind = pr.decision
        assert d.timeline.order_eligible_at is not None and pr.ticker
        held = next((p for p in ctx.open_positions if p["symbol"] == pr.ticker), None)
        common = dict(
            decision_id=d.decision_id,
            portfolio_id=d.portfolio_id,
            symbol=pr.ticker,
            is_simulated=is_simulated,
            order_eligible_at=d.timeline.order_eligible_at,
            expires_at=expires_at,
        )
        if kind in ENTRY_TYPES:
            assert pr.proposed_notional is not None
            notional = Decimal(pr.proposed_notional).quantize(Decimal("0.01"))
            fractionable = bool(ctx.instrument and ctx.instrument.get("fractionable"))
            if fractionable:
                return [
                    OrderRequest(
                        purpose=OrderPurpose.ENTRY,
                        side=OrderSide.BUY,
                        order_type=OrderType.MARKET,
                        time_in_force=TimeInForce.DAY,
                        notional=notional,
                        **common,
                    )
                ]
            # whole shares: cap at cash × (1 − OVERFILL_BUFFER_PCT) (§3.4)
            assert ctx.reference is not None
            cap = min(notional, whole_share_cap(ctx.available_cash, self.risk.capital.overfill_buffer_pct))
            qty = (cap / ctx.reference.price).to_integral_value(rounding="ROUND_DOWN")
            if qty < 1:
                raise ValueError("whole-share order below one share after the overfill cap")
            return [
                OrderRequest(
                    purpose=OrderPurpose.ENTRY,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    time_in_force=TimeInForce.DAY,
                    qty=qty,
                    **common,
                )
            ]
        assert held is not None
        pos_qty = Decimal(str(held["qty"]))
        if kind == DecisionType.REDUCE:
            assert pr.proposed_notional is not None and ctx.reference is not None
            qty = min(pos_qty, qty_for_notional(Decimal(pr.proposed_notional), ctx.reference.price))
            purpose = OrderPurpose.REDUCE
        else:
            qty, purpose = pos_qty, OrderPurpose.EXIT
        return [
            OrderRequest(
                purpose=purpose,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                time_in_force=TimeInForce.DAY,
                qty=qty,
                **common,
            )
        ]
