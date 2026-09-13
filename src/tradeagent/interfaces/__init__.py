"""Major domain interfaces (Protocols) — docs/phase0/04-interfaces.md.

Every external dependency is behind one of these so that: (a) DRY_RUN needs no broker (NullBroker), (b) the
MARKET_DATA_PLAN flip swaps an adapter and changes no scanner code (§5.6, ADR-0015), (c) every adapter is mockable
in tests. Implementations arrive in the vertical slices (docs/phase0/07-implementation-sequence.md).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import AsyncIterator, Protocol, Sequence, runtime_checkable
from uuid import UUID

from tradeagent.domain.enums import (
    BrokerPolicyResult,
    BudgetBucket,
    ExecutionMode,
    FeedTier,
    MarketRegime,
    OrderStatus,
    ReconcileResult,
    RejectCode,
    SourceHealth,
    UniverseStatus,
)
from tradeagent.domain.models import (
    AccountConfiguration,
    Bar,
    BrokerPolicy,
    BudgetState,
    Candidate,
    CashEvent,
    ClosedTrade,
    CritiqueResult,
    Decision,
    Evidence,
    Fill,
    ForecastContract,
    ForecastResolution,
    Instrument,
    LLMCall,
    OrderRequest,
    OrderState,
    Position,
    Proposal,
    Quote,
    ScanResult,
    SourceStamp,
    Trade,
    TriageResult,
)

# ------------------------------------------------------------------ data adapters (§5.2 source registry)


@runtime_checkable
class HealthCheckable(Protocol):
    domain: str
    source: str

    async def health(self) -> SourceStamp: ...


class MarketDataAdapter(HealthCheckable, Protocol):
    """Bars/quotes/trades for one feed tier. The scanner is written against this interface only (ADR-0015)."""

    tier: FeedTier

    async def bars(self, symbols: Sequence[str], timeframe: str, start: datetime, end: datetime) -> list[Bar]:
        """`end` must respect the tier's embargo (SIP_DELAYED: ≤ now − 15 min). Adapter raises on violation."""
        ...

    async def latest_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]: ...
    async def latest_trades(self, symbols: Sequence[str]) -> dict[str, Trade]: ...
    async def quotes(self, symbol: str, start: datetime, end: datetime) -> list[Quote]: ...
    async def trades(self, symbol: str, start: datetime, end: datetime) -> list[Trade]: ...


class MarketStream(Protocol):
    """§5.6 early-warning stream on the focus set (IEX on basic, SIP on algo_trader_plus)."""

    tier: FeedTier
    max_symbols: int

    async def subscribe(self, symbols: Sequence[str]) -> None: ...
    async def unsubscribe(self, symbols: Sequence[str]) -> None: ...
    def events(self) -> AsyncIterator[Quote | Trade | Bar]: ...
    async def close(self) -> None: ...


class NewsAdapter(HealthCheckable, Protocol):
    async def headlines(self, symbols: Sequence[str], since: datetime, limit: int) -> list[Evidence]: ...


class FilingsAdapter(HealthCheckable, Protocol):
    async def company_metadata(self, symbol: str) -> Instrument: ...
    async def recent_8k_items(self, cik: str, since: datetime) -> list[Evidence]: ...
    async def latest_10q_10k_highlights(self, cik: str) -> list[Evidence]: ...


class FundamentalsAdapter(HealthCheckable, Protocol):
    async def facts(self, cik: str, concepts: Sequence[str]) -> list[Evidence]: ...


class EarningsCalendarAdapter(HealthCheckable, Protocol):
    async def next_earnings_date(self, symbol: str) -> date | None: ...
    async def last_surprise(self, symbol: str) -> Evidence | None: ...


class TradingCalendar(Protocol):
    """§11 scheduler input; §3.3 settlement dates; §3.5 trading-day arithmetic."""

    async def sessions(self, start: date, end: date) -> list[tuple[date, datetime, datetime]]: ...
    async def is_session(self, d: date) -> bool: ...
    async def add_trading_days(self, from_dt: datetime, n: int) -> datetime: ...
    async def settlement_date(self, trade_date: date) -> date: ...


class CorporateActionsAdapter(HealthCheckable, Protocol):
    async def actions(self, symbols: Sequence[str], start: date, end: date) -> list[dict]: ...


# ------------------------------------------------------------------ broker (§8, ADR-0020)


class Broker(Protocol):
    """Order and account gateway. Exactly one implementation per mode: NullBroker (DRY_RUN), AlpacaPaperBroker (PAPER).

    ADR-0020: there is no LIVE implementation in this codebase; the factory raises LiveLockedOutError for
    ExecutionMode.LIVE. `observe_only` is the DRY_RUN posture for the pre-open stop job (§16): read, never submit.
    """

    mode: ExecutionMode
    observe_only: bool

    async def submit(self, request: OrderRequest) -> OrderState:
        """Idempotent on client_order_id: resubmission returns the existing order (§8.3, §8.8)."""
        ...

    async def cancel(self, order_id: UUID) -> OrderState: ...
    async def replace(self, order_id: UUID, request: OrderRequest) -> OrderState: ...
    async def order_status(self, client_order_id: str) -> OrderState | None: ...
    async def open_orders(self) -> list[OrderState]: ...
    async def positions(self) -> list[Position]: ...
    async def activities_since(self, since: datetime | None) -> list[dict]: ...
    async def account_configuration(self) -> AccountConfiguration: ...
    async def write_account_configuration(self, policy: BrokerPolicy) -> AccountConfiguration: ...


class LiveLockedOutError(RuntimeError):
    """Raised anywhere a LIVE code path would otherwise be reachable (ADR-0020)."""


# ------------------------------------------------------------------ pipeline (§6, §7)


class RegimeClassifier(Protocol):
    async def classify(self, trade_date: date) -> tuple[MarketRegime, dict]: ...


class UniverseBuilder(Protocol):
    async def build(self, as_of: date) -> list[Instrument]: ...


class ExclusionLayer(Protocol):
    """§4.4 — enforced at the scanner prefilter and at the risk desk; never by the LLM."""

    version: str

    def screen(self, symbol: str, sic: int | None) -> tuple[UniverseStatus, str | None]: ...


class Scanner(Protocol):
    """Deterministic scanner (§6). Polling mode on basic; `EventDrivenScanner` is the SIP_REALTIME mode (ADR-0015)."""

    version: str

    async def scan(self, kind: str, now: datetime) -> ScanResult: ...


class EventDrivenScanner(Scanner, Protocol):
    async def on_event(self, event: Quote | Trade | Bar) -> ScanResult | None: ...


class DossierBuilder(Protocol):
    async def slim(self, candidate: Candidate) -> dict: ...
    async def full(self, candidate: Candidate, portfolio_id: UUID) -> dict: ...


class LLMClient(Protocol):
    """§7.1 model tiers. Every call returns its LLMCall accounting row (§9)."""

    async def triage(self, dossiers: Sequence[dict], prompt_version: str) -> tuple[TriageResult, LLMCall]: ...
    async def decide(self, dossier: dict, prompt_version: str) -> tuple[Proposal, LLMCall]: ...
    async def critique(self, proposal: Proposal, prompt_version: str) -> tuple[CritiqueResult, LLMCall]: ...
    async def finalize(self, proposal: Proposal, critique: CritiqueResult, prompt_version: str) -> tuple[Proposal, LLMCall]: ...
    async def daily_review(self, position_dossier: dict, prompt_version: str) -> tuple[Proposal, LLMCall]: ...


# ------------------------------------------------------------------ risk, budget, execution (§8, §9)


class RiskDesk(Protocol):
    """§8.1 validation pipeline in order. Never edits a decision; rejects with the first failing code."""

    async def validate(self, decision: Decision, virtual_cash: Decimal, open_positions: Sequence[Position]) -> RejectCode | None: ...
    async def construct_orders(self, decision: Decision) -> list[OrderRequest]: ...


class BudgetDesk(Protocol):
    async def state(self, trade_date: date, bucket: BudgetBucket) -> BudgetState: ...
    async def charge(self, call: LLMCall, trade_date: date) -> BudgetState: ...


class FillReconstructor(Protocol):
    """§8.9 / §12 delayed fill reconstruction for simulated portfolios."""

    async def reconstruct(self, request: OrderRequest, sim_fill_delay: timedelta, additional_slippage_bps: Decimal) -> Fill | None: ...


class StopArmer(Protocol):
    """§8.3 pre-open re-arm and post-open verification (ADR-0013)."""

    async def rearm(self, session_date: date) -> list[dict]: ...
    async def verify_post_open(self, session_date: date) -> list[dict]: ...


class Reconciler(Protocol):
    """§8.6 replay-or-halt."""

    async def reconcile(self) -> tuple[ReconcileResult, dict]: ...


class BrokerPolicyEnforcer(Protocol):
    """§8.10."""

    async def enforce(self, policy: BrokerPolicy, enforce_writes: bool) -> tuple[BrokerPolicyResult, AccountConfiguration | None]: ...


class FocusSetManager(Protocol):
    """§5.6 focus set: positions first, then candidates by score, capped (ADR-0014)."""

    def select(self, positions: Sequence[Position], candidates: Sequence[Candidate], cap: int) -> list[str]: ...


# ------------------------------------------------------------------ persistence (ADR-0002)


class Ledger(Protocol):
    """Repository over the Supabase schema. All writes are idempotent by key; deletes do not exist."""

    async def record_decision(self, decision: Decision) -> None: ...
    async def update_decision_status(self, decision_id: UUID, status: str, reject_code: RejectCode | None) -> None: ...
    async def record_order(self, request: OrderRequest) -> UUID: ...
    async def transition_order(self, order_id: UUID, to_status: OrderStatus, reason: str, broker_event_id: str | None) -> None: ...
    async def record_fill(self, fill: Fill) -> None: ...
    async def append_cash(self, event: CashEvent) -> None: ...
    async def virtual_cash(self, portfolio_id: UUID) -> Decimal: ...
    async def open_positions(self, portfolio_id: UUID) -> list[Position]: ...
    async def freeze_forecast(self, decision_id: UUID, contract: ForecastContract) -> None: ...
    async def record_forecast_resolution(self, resolution: ForecastResolution) -> None: ...
    async def record_closed_trade(self, trade: ClosedTrade) -> None: ...


# ------------------------------------------------------------------ analytics and ops (§13, §11)


class ForecastResolver(Protocol):
    async def resolve(self, decision: Decision, fill_at: datetime) -> ForecastResolution: ...


class CandidateOutcomeJob(Protocol):
    async def compute(self, scan_id: UUID) -> int: ...


class AnalysisJob(Protocol):
    async def report(self, experiment_id: UUID) -> dict: ...


class Notifier(Protocol):
    async def send(self, kind: str, subject: str, body_html: str, body_text: str) -> str: ...


class Scheduler(Protocol):
    async def run_forever(self) -> None: ...


__all__ = [n for n in dir() if n[:1].isupper()]
