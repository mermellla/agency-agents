"""Typed domain models — Spec v2.3 §5–§9, §12. Pydantic v2, frozen where the spec says immutable.

These models are the contract between modules (docs/phase0/03-architecture.md). They carry validation the
database cannot express (durations, derived fields) and mirror the constraints the database does express so that
a violation is caught before a round-trip.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradeagent.domain.enums import (
    BudgetBucket,
    CashEventKind,
    CritiqueRecommendation,
    DecisionKind,
    DecisionOrigin,
    DecisionStatus,
    DecisionType,
    EarningsPlan,
    FeedTier,
    FeeKind,
    FillSource,
    ForecastOutcome,
    HoldingBucket,
    LLMStage,
    MarketRegime,
    OrderPurpose,
    OrderSide,
    OrderStatus,
    OrderType,
    ReconstructionBasis,
    RejectCode,
    SourceHealth,
    TimeInForce,
    TrustGrade,
    UniverseStatus,
)

FORECAST_EVENT = "TARGET_BEFORE_INVALIDATION_OR_TIME_STOP"
"""§7.4: the one binary event every entry decision forecasts."""

CLIENT_ORDER_ID_MAX = 128
"""Alpaca client_order_id maximum length (verified 2026-09-13)."""


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, validate_assignment=True)


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------------------------------------------
# Provenance (§5.2, §5.3)
# ---------------------------------------------------------------------------------------------------------------


class SourceStamp(Frozen):
    """Provenance attached to every data point: which adapter, which grade, how old."""

    domain: str
    source: str
    grade: TrustGrade
    observed_at: datetime
    health: SourceHealth = SourceHealth.OK

    def age(self, now: datetime | None = None) -> timedelta:
        return (now or utcnow()) - self.observed_at


class Bar(Frozen):
    symbol: str
    timeframe: Literal["1Min", "15Min", "1Day"]
    start: datetime
    end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    feed: FeedTier
    retrievable_at: datetime = Field(description="§5.1: the moment the bar became retrievable (end + embargo + fetch)")
    stamp: SourceStamp

    @model_validator(mode="after")
    def _availability_semantics(self) -> Bar:
        if self.retrievable_at < self.end:
            raise ValueError("retrievable_at must not precede the bar end (§5.1 availability semantics)")
        if self.low > self.high or not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError("bar OHLC out of range")
        return self


class Quote(Frozen):
    symbol: str
    at: datetime
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    feed: FeedTier
    stamp: SourceStamp

    @model_validator(mode="after")
    def _crossed(self) -> Quote:
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError("non-positive quote")
        return self

    @property
    def usable(self) -> bool:
        """ADR-0017 'usable quote': two-sided, not crossed, non-zero sizes."""
        return self.ask >= self.bid and self.bid_size > 0 and self.ask_size > 0

    @property
    def half_spread(self) -> Decimal:
        return (self.ask - self.bid) / 2


class Trade(Frozen):
    symbol: str
    at: datetime
    price: Decimal
    size: int
    conditions: tuple[str, ...] = ()
    feed: FeedTier
    stamp: SourceStamp


class Headline(Frozen):
    """A news item as served by the news domain (§5.2); judged by Jev in the catalyst signal (ADR-0023)."""

    id: str
    symbols: tuple[str, ...]
    headline: str
    summary: str
    source: str
    created_at: datetime
    url: str


# ---------------------------------------------------------------------------------------------------------------
# Universe and scanner (§4, §6)
# ---------------------------------------------------------------------------------------------------------------


class Instrument(Strict):
    symbol: str
    name: str | None = None
    exchange: str | None = None
    cik: str | None = None
    sic: int | None = None
    tradable: bool = False
    fractionable: bool = False
    last_10k_filed_on: date | None = None
    universe_status: UniverseStatus = UniverseStatus.EXCLUDED_UNIVERSE
    status_reason: str | None = None


class SignalValue(Frozen):
    name: str
    value: Decimal | None
    unavailable_reason: str | None = None  # §6.3 signal_unavailable_reason
    observed_at: datetime | None = None


class Candidate(Strict):
    """§6.4 scanner output row; drift fields filled at each pipeline stage (§7.6, D-50)."""

    candidate_id: UUID = Field(default_factory=uuid4)
    scan_id: UUID
    symbol: str
    rank: int = Field(ge=1)
    composite_score: Decimal
    signals: list[SignalValue]
    strategy_tags: list[str] = Field(default_factory=list)
    signal_bar_time: datetime
    signal_observed_at: datetime
    sip_signal_price: Decimal
    sip_signal_timestamp: datetime
    iex_price_at_scan: Decimal | None = None
    iex_quote_age_sec_at_scan: int | None = None
    iex_price_at_triage: Decimal | None = None
    iex_quote_age_sec_at_triage: int | None = None
    iex_price_at_decision: Decimal | None = None
    iex_quote_age_sec_at_decision: int | None = None
    iex_price_at_order: Decimal | None = None
    iex_quote_age_sec_at_order: int | None = None

    @model_validator(mode="after")
    def _observed_after_bar(self) -> Candidate:
        if self.signal_observed_at < self.signal_bar_time:
            raise ValueError("signal_observed_at precedes the bar time (§5.1)")
        return self

    def move_pct(self, current: Decimal | None) -> Decimal | None:
        if current is None:
            return None
        return (current - self.sip_signal_price) / self.sip_signal_price * 100


class ScanResult(Strict):
    scan_id: UUID = Field(default_factory=uuid4)
    kind: Literal["preopen", "intraday"]
    feed_tier: FeedTier = FeedTier.SIP_DELAYED
    started_at: datetime
    scanner_completed_at: datetime
    bars_end_at: datetime
    regime: MarketRegime
    scanner_version: str
    exclusion_list_version: str
    universe_size: int
    candidates: list[Candidate]
    source_status: list[SourceStamp]
    signals_unavailable: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _embargo(self) -> ScanResult:
        if self.bars_end_at > self.started_at:
            raise ValueError("bars_end_at must be ≤ scan start (delayed SIP embargo, §5.1)")
        if self.scanner_completed_at < self.started_at:
            raise ValueError("scanner_completed_at precedes started_at")
        return self


# ---------------------------------------------------------------------------------------------------------------
# Agent I/O (§7)
# ---------------------------------------------------------------------------------------------------------------


class Evidence(Frozen):
    claim: str
    source: str
    grade: TrustGrade
    url: str | None = None
    observed_at: datetime | None = None


class TriageItem(Frozen):
    symbol: str
    rank: int = Field(ge=1)
    reason: str = Field(max_length=200)


class TriageResult(Frozen):
    """§7.1 step 2: short structured ranking from the cheap tier."""

    ranking: tuple[TriageItem, ...]
    model: str
    completed_at: datetime


class CritiqueResult(Frozen):
    """§7.3 output of the adversarial self-critique call."""

    contradicting_evidence: tuple[Evidence, ...]
    probability_post_critique: Decimal = Field(ge=0, le=1)
    recommendation: CritiqueRecommendation
    model: str
    completed_at: datetime


class Proposal(Frozen):
    """The decision model's proposal (pre- or post-critique). Kept whole so CRITIQUE_SHADOW can replay it (D-56)."""

    decision: DecisionType
    ticker: str | None
    strategy: str | None
    direction: Literal["long", "short"] | None
    proposed_notional: Decimal | None = Field(default=None, gt=0)
    entry_type: str | None
    entry_price_or_range: str | None
    target_price: Decimal | None
    invalidation_price: Decimal | None
    time_stop_at: datetime | None
    expected_holding_period: timedelta | None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    probability: Decimal | None = Field(default=None, ge=0, le=1)
    expected_upside_percent: Decimal | None = None
    expected_downside_percent: Decimal | None = None
    expected_value: Decimal | None = None
    reward_risk_ratio: Decimal | None = None
    catalyst: str | None = None
    thesis: str | None = None
    supporting_evidence: tuple[Evidence, ...] = ()
    contradicting_evidence: tuple[Evidence, ...] = ()
    earnings_in_window: bool | None = None
    earnings_plan: EarningsPlan | None = None
    earnings_plan_reason: str | None = None
    reason_for_entry: str | None = None
    reason_for_exit_if_existing_position: str | None = None
    conditions_to_exit_early: tuple[str, ...] = ()
    sources: tuple[Evidence, ...] = ()
    no_action_reason: str | None = None

    @model_validator(mode="after")
    def _schema_rules(self) -> Proposal:
        if self.decision == DecisionType.NO_ACTION and not self.no_action_reason:
            raise ValueError("no_action_reason is required when decision = NO_ACTION (§7.6)")
        if self.earnings_in_window and (
            self.earnings_plan in (None, EarningsPlan.N_A) or not self.earnings_plan_reason
        ):
            raise ValueError("earnings_plan with reason is required when earnings_in_window (§7.6)")
        if self.decision in (DecisionType.BUY, DecisionType.ADD):
            missing = [
                n
                for n in (
                    "ticker",
                    "proposed_notional",
                    "target_price",
                    "invalidation_price",
                    "time_stop_at",
                    "expected_holding_period",
                    "probability",
                )
                if getattr(self, n) is None
            ]
            if missing:
                raise ValueError(f"entry proposal missing {missing}")
            assert self.target_price is not None and self.invalidation_price is not None
            if not (self.invalidation_price < self.target_price):
                raise ValueError("long entry requires invalidation_price < target_price")
        return self


class Timeline(Strict):
    """§7.6 decision timeline. All UTC. Drives the anti-look-ahead invariant (§8.9)."""

    signal_observed_at: datetime | None = None
    scanner_completed_at: datetime | None = None
    triage_completed_at: datetime | None = None
    decision_completed_at: datetime | None = None
    critique_completed_at: datetime | None = None
    risk_validation_completed_at: datetime | None = None
    order_submitted_at: datetime | None = None
    fill_at: datetime | None = None

    @property
    def order_eligible_at(self) -> datetime | None:
        """= risk_validation_completed_at (§7.6): the earliest legal fill time."""
        return self.risk_validation_completed_at

    @model_validator(mode="after")
    def _monotone(self) -> Timeline:
        if self.order_eligible_at and self.signal_observed_at and self.order_eligible_at < self.signal_observed_at:
            raise ValueError("order_eligible_at < signal_observed_at violates §8.9")
        if self.fill_at and self.order_eligible_at and self.fill_at < self.order_eligible_at:
            raise ValueError("fill_at < order_eligible_at violates §8.9")
        return self


class ForecastContract(Frozen):
    """§7.4: frozen at entry; resolution always uses these, never the working levels."""

    forecast_event: Literal["TARGET_BEFORE_INVALIDATION_OR_TIME_STOP"] = "TARGET_BEFORE_INVALIDATION_OR_TIME_STOP"
    target_price_at_entry: Decimal
    invalidation_price_at_entry: Decimal
    time_stop_at_entry: datetime
    probability_pre_critique: Decimal = Field(ge=0, le=1)
    probability_post_critique: Decimal | None = Field(default=None, ge=0, le=1)


class Decision(Strict):
    """§7.6 decision record as persisted in `decisions`."""

    decision_id: UUID = Field(default_factory=uuid4)
    experiment_id: UUID
    experiment_phase_id: UUID
    portfolio_id: UUID
    scan_id: UUID | None = None
    candidate_id: UUID | None = None
    data_snapshot_id: UUID | None = None
    position_id: UUID | None = None
    origin: DecisionOrigin
    kind: DecisionKind
    trigger_reason: str | None = None
    status: DecisionStatus = DecisionStatus.PROPOSED
    reject_code: RejectCode | None = None
    timeline: Timeline = Field(default_factory=Timeline)
    sip_signal_price: Decimal | None = None
    sip_signal_timestamp: datetime | None = None
    iex_price_at_triage: Decimal | None = None
    iex_price_at_decision: Decimal | None = None
    iex_price_at_order: Decimal | None = None
    signal_to_decision_move_pct: Decimal | None = None
    signal_to_order_move_pct: Decimal | None = None
    blind_interval_move_pct: Decimal | None = None
    proposal: Proposal
    pre_critique_proposal: Proposal | None = None
    critique: CritiqueResult | None = None
    critique_changed_proposal: bool = False
    forecast: ForecastContract | None = None
    forecast_outcome: ForecastOutcome | None = None
    market_regime: MarketRegime | None = None
    prompt_version: str
    exclusion_list_version: str
    config_version: str
    scanner_version: str
    qb_rules_version: str
    model_triage: str | None = None
    model_decision: str | None = None
    model_critique: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    cost_usd: Decimal = Decimal("0")

    @model_validator(mode="after")
    def _rules(self) -> Decision:
        if self.proposal.decision in (DecisionType.SHORT, DecisionType.COVER) and not (
            self.status == DecisionStatus.REJECTED and self.reject_code == RejectCode.REJECTED_ACCOUNT_INELIGIBLE
        ):
            raise ValueError("SHORT/COVER must be recorded as REJECTED_ACCOUNT_INELIGIBLE (§3.1)")
        if self.status == DecisionStatus.REJECTED and self.reject_code is None:
            raise ValueError("rejected decisions carry a reject_code")
        if (
            self.proposal.decision in (DecisionType.BUY, DecisionType.ADD)
            and self.status in (DecisionStatus.VALIDATED, DecisionStatus.EXECUTED)
            and self.forecast is None
        ):
            raise ValueError("validated entries must carry the frozen forecast contract (§7.4)")
        for v in (
            self.prompt_version,
            self.exclusion_list_version,
            self.config_version,
            self.scanner_version,
            self.qb_rules_version,
        ):
            if not v:
                raise ValueError("version fields are non-null on every decision (§10.2)")
        return self


def client_order_id(decision_id: UUID, leg_seq: int = 1) -> str:
    """§8.3: client_order_id derived from decision_id; one decision may spawn several legs (cancel/replace, stops).

    Deterministic, so a resubmission after a crash produces the same id and is a no-op at the broker (§8.8).
    """
    if leg_seq < 1:
        raise ValueError("leg_seq starts at 1")
    cid = f"{decision_id}:{leg_seq}"
    assert len(cid) <= CLIENT_ORDER_ID_MAX
    return cid


# ---------------------------------------------------------------------------------------------------------------
# Execution (§8, §12)
# ---------------------------------------------------------------------------------------------------------------


class OrderRequest(Strict):
    """What the risk desk hands the broker adapter after validation. Never edited afterwards (§2, §8.1)."""

    decision_id: UUID
    portfolio_id: UUID
    leg_seq: int = Field(default=1, ge=1)
    purpose: OrderPurpose
    symbol: str
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    qty: Decimal | None = Field(default=None, gt=0)
    notional: Decimal | None = Field(default=None, gt=0)
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    extended_hours: bool = False
    is_simulated: bool
    order_eligible_at: datetime
    expires_at: datetime | None = None

    @property
    def client_order_id(self) -> str:
        return client_order_id(self.decision_id, self.leg_seq)

    @model_validator(mode="after")
    def _rules(self) -> OrderRequest:
        if (self.qty is None) == (self.notional is None):
            raise ValueError("exactly one of qty / notional (Alpaca rule)")
        if self.purpose == OrderPurpose.ENTRY and (self.side != OrderSide.BUY or self.extended_hours):
            raise ValueError("entries are regular-session buys only (§4.3)")
        if self.extended_hours and not (self.order_type == OrderType.LIMIT and self.time_in_force == TimeInForce.DAY):
            raise ValueError("extended-hours exits are Day limit orders (§4.3)")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError("stop orders need stop_price")
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError("limit orders need limit_price")
        if self.qty is not None and self.qty != self.qty.to_integral_value() and self.time_in_force != TimeInForce.DAY:
            raise ValueError("fractional quantities are Day-only at Alpaca (§8.3)")
        return self


class OrderState(Strict):
    order_id: UUID
    client_order_id: str
    status: OrderStatus
    status_reason: str | None = None
    broker_order_id: str | None = None
    submitted_at: datetime | None = None
    filled_qty: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None


class Fill(Frozen):
    fill_id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    portfolio_id: UUID
    symbol: str
    side: OrderSide
    qty: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fill_at: datetime
    source: FillSource
    broker_fill_id: str | None = None
    reconstruction_basis: ReconstructionBasis | None = None
    half_spread_estimate: Decimal | None = None
    additional_slippage_bps: Decimal = Decimal("0")
    shadow_estimate_price: Decimal | None = None

    @property
    def notional(self) -> Decimal:
        return self.qty * self.price

    @model_validator(mode="after")
    def _rules(self) -> Fill:
        if self.source == FillSource.RECONSTRUCTED and self.reconstruction_basis is None:
            raise ValueError("reconstructed fills state their basis (§12)")
        if (
            self.reconstruction_basis in (ReconstructionBasis.ASK, ReconstructionBasis.BID)
            and self.half_spread_estimate
        ):
            raise ValueError("never add a half-spread to an ask/bid fill (§12, D-54)")
        if self.source == FillSource.BROKER and not self.broker_fill_id:
            raise ValueError("broker fills carry the broker activity id (§15 idempotency)")
        return self

    @property
    def paper_fill_minus_shadow_estimate_bps(self) -> Decimal | None:
        if self.shadow_estimate_price is None:
            return None
        return (self.price - self.shadow_estimate_price) / self.shadow_estimate_price * 10_000


class Position(Strict):
    position_id: UUID = Field(default_factory=uuid4)
    portfolio_id: UUID
    symbol: str
    entry_decision_id: UUID
    qty: Decimal = Field(ge=0)
    avg_cost: Decimal | None = None
    is_fractional: bool = False
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    working_target_price: Decimal | None = None
    working_invalidation_price: Decimal | None = None
    working_time_stop_at: datetime | None = None
    ethical_hold: bool = False
    needs_ethical_review: bool = False


class CashEvent(Frozen):
    portfolio_id: UUID
    at: datetime
    kind: CashEventKind
    amount_usd: Decimal
    balance_after_usd: Decimal = Field(ge=0)  # §2: never negative
    settles_on: date | None = None
    fill_id: UUID | None = None
    reason: str | None = None
    idempotency_key: str


class Fee(Frozen):
    fill_id: UUID
    kind: FeeKind
    amount_usd: Decimal = Field(ge=0)
    rate_basis: dict[str, Any]
    fee_schedule_version: str


class ForecastResolution(Frozen):
    decision_id: UUID
    resolution_start_at: datetime
    contract: ForecastContract
    method: Literal["bars_1m", "ticks", "time_stop"]
    outcome: ForecastOutcome
    touched_at: datetime | None = None
    touch_price: Decimal | None = None
    bars_examined: int = 0
    ticks_fetched: bool = False
    ambiguous_reason: str | None = None

    @model_validator(mode="after")
    def _rules(self) -> ForecastResolution:
        if self.outcome == ForecastOutcome.AMBIGUOUS and not self.ambiguous_reason:
            raise ValueError("AMBIGUOUS needs a reason")
        if self.outcome in (ForecastOutcome.TARGET, ForecastOutcome.INVALIDATION) and self.touched_at is None:
            raise ValueError("a touch outcome needs touched_at")
        if self.touched_at and self.touched_at < self.resolution_start_at:
            raise ValueError("touch before fill_at (§8.9)")
        return self


class ClosedTrade(Frozen):
    position_id: UUID
    portfolio_id: UUID
    experiment_phase_id: UUID
    symbol: str
    strategy: str | None
    market_regime: MarketRegime | None
    entry_fill_at: datetime
    exit_fill_at: datetime
    holding_bucket: HoldingBucket
    qty: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl_usd: Decimal
    gross_return_pct: Decimal
    net_return_pct: Decimal
    net_return_after_computational_costs_pct: Decimal
    mfe_pct: Decimal | None
    mae_pct: Decimal | None
    forecast_outcome: ForecastOutcome | None
    discretionary_exit: bool
    exit_reason: str
    overnight_gap_exposure: bool
    benchmark_spy_return_pct: Decimal | None
    benchmark_vti_return_pct: Decimal | None
    llm_cost_usd: Decimal
    regulatory_fees_usd: Decimal
    prompt_version: str

    @property
    def holding_seconds(self) -> int:
        return int((self.exit_fill_at - self.entry_fill_at).total_seconds())


# ---------------------------------------------------------------------------------------------------------------
# Broker policy, budget, LLM accounting (§8.10, §9)
# ---------------------------------------------------------------------------------------------------------------


class BrokerPolicy(Frozen):
    """§8.10 as amended (OI-02/OI-03 accepted): five fields written to Alpaca's account configuration and verified on
    every boot. Literal types make it impossible to construct a looser policy in code."""

    max_margin_multiplier: Literal["1"] = "1"
    no_shorting: Literal[True] = True
    max_options_trading_level: Literal[0] = 0
    fractional_trading: Literal[True] = True
    disable_overnight_trading: Literal[True] = True

    def matches(self, observed: AccountConfiguration) -> bool:
        return (
            observed.max_margin_multiplier == "1"
            and observed.no_shorting is True
            and observed.max_options_trading_level == 0
            and observed.fractional_trading is True
            and observed.disable_overnight_trading is True
        )

    def as_patch(self) -> dict[str, object]:
        """Body for PATCH /v2/account/configurations."""
        return {
            "max_margin_multiplier": "1",
            "no_shorting": True,
            "max_options_trading_level": 0,
            "fractional_trading": True,
            "disable_overnight_trading": True,
        }


class AccountConfiguration(Frozen):
    """Alpaca GET/PATCH /v2/account/configurations (fields verified 2026-09-13)."""

    max_margin_multiplier: str
    no_shorting: bool
    max_options_trading_level: int
    fractional_trading: bool | None = None
    disable_overnight_trading: bool | None = None
    suspend_trade: bool | None = None
    trade_confirm_email: str | None = None
    ptp_no_exception_entry: bool | None = None


class LLMCall(Frozen):
    stage: LLMStage
    bucket: BudgetBucket
    model: str
    prompt_version: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    tokens_cached_read: int = Field(default=0, ge=0)
    tokens_cached_write: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(ge=0)
    latency_ms: int | None = None
    request_hash: str | None = None
    response_hash: str | None = None
    status: Literal["ok", "invalid_output", "error", "refused"] = "ok"


class BudgetState(Frozen):
    trade_date: date
    bucket: BudgetBucket
    allowance_usd: Decimal
    carried_in_usd: Decimal
    spent_usd: Decimal
    overage_usd: Decimal

    @property
    def remaining_usd(self) -> Decimal:
        return self.allowance_usd + self.carried_in_usd - self.spent_usd

    @property
    def exhausted(self) -> bool:
        return self.remaining_usd <= 0


def context_hash(payload: bytes) -> str:
    """§10.4 permanent hash of the full LLM input context."""
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    n
    for n in dir()
    if n[:1].isupper() or n in ("client_order_id", "context_hash", "utcnow", "FORECAST_EVENT", "CLIENT_ORDER_ID_MAX")
]
