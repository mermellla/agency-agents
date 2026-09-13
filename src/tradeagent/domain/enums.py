"""Enumerations mirroring the Postgres enums in supabase/migrations/20260913000001_extensions_enums.sql.

Keep the two in lock-step: tests/test_db_invariants.py::test_enums_match_database compares them.
"""

from __future__ import annotations

from enum import StrEnum


class ExecutionMode(StrEnum):
    DRY_RUN = "DRY_RUN"
    PAPER = "PAPER"
    LIVE = "LIVE"  # architected, locked out (ADR-0020)


class PortfolioKind(StrEnum):
    LLM_PRIMARY = "llm_primary"
    LLM_VARIANT = "llm_variant"
    QUANT_SHADOW = "quant_shadow"
    BENCHMARK_CASH = "benchmark_cash"
    BENCHMARK_SPY = "benchmark_spy"
    BENCHMARK_VTI = "benchmark_vti"
    CRITIQUE_SHADOW_PAIRED = "critique_shadow_paired"
    CRITIQUE_SHADOW_PARALLEL = "critique_shadow_parallel"
    DETERMINISTIC_EXIT_SHADOW = "deterministic_exit_shadow"


class PhaseCategory(StrEnum):
    INITIAL = "initial"
    STRATEGY = "strategy"
    CORRECTNESS = "correctness"
    SAFETY = "safety"


class TrustGrade(StrEnum):
    EXECUTION = "execution"
    RESEARCH = "research"


class SourceHealth(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class FeedTier(StrEnum):
    SIP_DELAYED = "SIP_DELAYED"
    IEX_REALTIME = "IEX_REALTIME"
    SIP_REALTIME = "SIP_REALTIME"


class MarketRegime(StrEnum):
    TRENDING_BULL = "TRENDING_BULL"
    TRENDING_BEAR = "TRENDING_BEAR"
    RANGE_BOUND = "RANGE_BOUND"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    RISK_OFF = "RISK_OFF"
    UNKNOWN = "UNKNOWN"


class DecisionType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    ADD = "ADD"
    REDUCE = "REDUCE"
    HOLD = "HOLD"
    NO_ACTION = "NO_ACTION"
    SHORT = "SHORT"  # always rejected in V1 (§3.1)
    COVER = "COVER"  # always rejected in V1 (§3.1)


class DecisionOrigin(StrEnum):
    LLM = "llm"
    QUANT = "quant"
    SYSTEM = "system"
    OWNER = "owner"


class DecisionKind(StrEnum):
    ENTRY = "entry"
    DAILY_REVIEW = "daily_review"
    TRIGGERED_REVIEW = "triggered_review"
    SYSTEM_EXIT = "system_exit"
    OWNER_RESOLUTION = "owner_resolution"


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    REJECTED = "rejected"
    EXECUTED = "executed"
    NO_ACTION = "no_action"


class CritiqueRecommendation(StrEnum):
    PROCEED = "proceed"
    REDUCE = "reduce"
    ABANDON = "abandon"


class EarningsPlan(StrEnum):
    HOLD_THROUGH = "hold_through"
    EXIT_BEFORE = "exit_before"
    N_A = "n_a"


class ForecastOutcome(StrEnum):
    TARGET = "TARGET"
    INVALIDATION = "INVALIDATION"
    TIME_STOP = "TIME_STOP"
    AMBIGUOUS = "AMBIGUOUS"


class OrderStatus(StrEnum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"  # LIVE day-one only; unreachable while LIVE is locked out
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FILL_PENDING_RECONSTRUCTION = "FILL_PENDING_RECONSTRUCTION"


class OrderPurpose(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    REDUCE = "reduce"
    PROTECTIVE_STOP = "protective_stop"
    STOP_REARM = "stop_rearm"
    TARGET = "target"
    BRACKET_ENTRY = "bracket_entry"
    EXT_HOURS_EXIT = "ext_hours_exit"
    SOFTWARE_STOP = "software_stop"
    FORCE_CLOSE = "force_close"
    CANCEL_REPLACE = "cancel_replace"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"


class FillSource(StrEnum):
    BROKER = "broker"
    RECONSTRUCTED = "reconstructed"


class ReconstructionBasis(StrEnum):
    ASK = "ask"
    BID = "bid"
    TRADE_PLUS_HALF_SPREAD = "trade_plus_half_spread"
    TRADE_MINUS_HALF_SPREAD = "trade_minus_half_spread"


class CashEventKind(StrEnum):
    INITIAL_EQUITY = "initial_equity"
    BUY = "buy"
    SELL = "sell"
    FEE = "fee"
    DIVIDEND = "dividend"
    BENCHMARK_MARK = "benchmark_mark"
    RECONCILE_REPAIR = "reconcile_repair"
    OWNER_RESOLUTION = "owner_resolution"
    ADJUSTMENT = "adjustment"


class FeeKind(StrEnum):
    SEC_SECTION_31 = "sec_section_31"
    FINRA_TAF = "finra_taf"
    CAT = "cat"
    BROKER_COMMISSION = "broker_commission"
    OTHER = "other"


class LLMStage(StrEnum):
    TRIAGE = "triage"
    DECISION = "decision"
    CRITIQUE = "critique"
    DAILY_REVIEW = "daily_review"
    TRIGGERED_REVIEW = "triggered_review"


class BudgetBucket(StrEnum):
    ENTRIES = "entries"
    EXITS_REVIEWS = "exits_reviews"


class HaltScope(StrEnum):
    ALL = "all"
    ENTRIES = "entries"


class ReconcileResult(StrEnum):
    AGREE = "agree"
    REPAIRED = "repaired"
    HALT = "halt"


class BrokerPolicyResult(StrEnum):
    MATCH = "match"
    APPLIED = "applied"
    HALT = "halt"


class ShadowLinkKind(StrEnum):
    CRITIQUE_PAIRED = "critique_paired"
    CRITIQUE_PARALLEL = "critique_parallel"
    DETERMINISTIC_EXIT_TWIN = "deterministic_exit_twin"


class HoldingBucket(StrEnum):
    INTRADAY = "intraday"
    OVERNIGHT = "overnight"
    MULTI_DAY = "multi_day"


class PositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class UniverseStatus(StrEnum):
    ELIGIBLE = "eligible"
    EXCLUDED_UNIVERSE = "excluded_universe"
    EXCLUDED_ETHICAL = "excluded_ethical"
    NEEDS_ETHICAL_REVIEW = "needs_ethical_review"
    EXCLUDED_FLOOR = "excluded_floor"


class RejectCode(StrEnum):
    """Coded rejection reasons (§4.4, §5.5, §7.6, §8.1, §15). Extensible; text column in the database."""

    REJECTED_INVALID_OUTPUT = "REJECTED_INVALID_OUTPUT"
    REJECTED_MODE = "REJECTED_MODE"
    REJECTED_ETHICAL_SCREEN = "REJECTED_ETHICAL_SCREEN"
    NEEDS_ETHICAL_REVIEW = "NEEDS_ETHICAL_REVIEW"
    REJECTED_INSTRUMENT_INELIGIBLE = "REJECTED_INSTRUMENT_INELIGIBLE"
    REJECTED_ACCOUNT_INELIGIBLE = "REJECTED_ACCOUNT_INELIGIBLE"
    REJECTED_HORIZON = "REJECTED_HORIZON"
    REJECTED_LATENCY_FLOOR = "REJECTED_LATENCY_FLOOR"
    REJECTED_VIRTUAL_CASH = "REJECTED_VIRTUAL_CASH"
    REJECTED_POSITION_LIMIT = "REJECTED_POSITION_LIMIT"
    REJECTED_STALE_DATA = "REJECTED_STALE_DATA"
    REJECTED_BUDGET = "REJECTED_BUDGET"
    REJECTED_DUPLICATE = "REJECTED_DUPLICATE"
    REJECTED_BROKER = "REJECTED_BROKER"
    REJECTED_HALTED = "REJECTED_HALTED"
    REJECTED_ETHICAL_HOLD_ADD = "REJECTED_ETHICAL_HOLD_ADD"


ENUM_TABLE: dict[str, type[StrEnum]] = {
    "execution_mode": ExecutionMode,
    "portfolio_kind": PortfolioKind,
    "phase_category": PhaseCategory,
    "trust_grade": TrustGrade,
    "source_health": SourceHealth,
    "feed_tier": FeedTier,
    "market_regime": MarketRegime,
    "decision_type": DecisionType,
    "decision_origin": DecisionOrigin,
    "decision_kind": DecisionKind,
    "decision_status": DecisionStatus,
    "critique_recommendation": CritiqueRecommendation,
    "earnings_plan": EarningsPlan,
    "forecast_outcome": ForecastOutcome,
    "order_status": OrderStatus,
    "order_purpose": OrderPurpose,
    "order_side": OrderSide,
    "order_type": OrderType,
    "time_in_force": TimeInForce,
    "fill_source": FillSource,
    "reconstruction_basis": ReconstructionBasis,
    "cash_event_kind": CashEventKind,
    "fee_kind": FeeKind,
    "llm_stage": LLMStage,
    "budget_bucket": BudgetBucket,
    "halt_scope": HaltScope,
    "reconcile_result": ReconcileResult,
    "broker_policy_result": BrokerPolicyResult,
    "shadow_link_kind": ShadowLinkKind,
    "holding_bucket": HoldingBucket,
    "position_status": PositionStatus,
    "universe_status": UniverseStatus,
}
"""Postgres enum name → Python enum, for the schema/code parity test."""
