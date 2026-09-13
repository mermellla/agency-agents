"""Configuration loading and versioning (Appendix B, ADR-0019, ADR-0020).

- Reads the versioned YAML files in config/ and the secrets-free environment.
- Computes `config_version` as the sha256 of the canonical serialization of risk_policy.yaml + fees.yaml.
- Refuses to construct settings for EXECUTION_MODE=LIVE (ADR-0020). This is the first of several lockouts; the
  broker factory and the database (`experiments.live_locked_out`) are the others.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradeagent.domain.enums import ExecutionMode

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"


class LiveLockedOut(RuntimeError):
    pass


def canonical_hash(*objs: Any) -> str:
    payload = json.dumps(list(objs), sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


class BrokerPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_margin_multiplier: str = "1"
    no_shorting: bool = True
    max_options_trading_level: int = 0
    fractional_trading: bool = True
    disable_overnight_trading: bool = True


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_mode: ExecutionMode
    live_enabled: bool = False
    live_approval_until: str | None = None
    live_approval_first_n_orders: int | None = None
    broker_policy_enforce: bool = True
    broker_policy: BrokerPolicyConfig

    @model_validator(mode="after")
    def _lockout(self) -> ExecutionConfig:
        if self.execution_mode == ExecutionMode.LIVE or self.live_enabled:
            raise LiveLockedOut("LIVE execution is not enabled in this codebase (Spec v2.3 §16, ADR-0020)")
        bp = self.broker_policy
        if (
            bp.max_margin_multiplier != "1"
            or not bp.no_shorting
            or bp.max_options_trading_level != 0
            or not bp.fractional_trading
            or not bp.disable_overnight_trading
        ):
            raise ValueError(
                "BROKER_POLICY must be 1x / no shorting / options level 0 / fractional on / overnight off (§8.10 as amended)"
            )
        return self


class CapitalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_equity_start: float = Field(gt=0)
    overfill_buffer_pct: float = Field(ge=0, lt=100)


class PositionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_single_position_pct: float = Field(gt=0, lt=100)  # §8.2 "No trade may use the entire balance"
    normal_position_range_pct: tuple[float, float]
    max_positions: int = Field(ge=1)

    @model_validator(mode="after")
    def _range(self) -> PositionLimits:
        lo, hi = self.normal_position_range_pct
        if not (0 < lo <= hi <= self.max_single_position_pct):
            raise ValueError("normal_position_range_pct must sit inside (0, max_single_position_pct]")
        return self


class HorizonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_expected_hold: str
    max_expected_hold: str
    latency_floor_min: int = Field(ge=15)  # can never be below the feed delay itself (§3.5)


class UniverseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_price_usd: float = Field(gt=0)
    min_avg_dollar_volume_usd: float = Field(gt=0)
    min_history_days: int = Field(ge=1)
    require_fractionable: bool
    include_adr_foreign: bool


class ScannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scan_interval_min: int = Field(ge=15)  # §5.1: 5-minute cadence does not fit the free rate limit
    scan_top_n: int = Field(ge=1)
    triage_top_k: int = Field(ge=1)
    triage_score_threshold: float = Field(ge=0, le=1)
    triage_min_interval_min: int = Field(ge=0)


class StalenessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_sip_bar_age_min: int = Field(ge=15)
    max_iex_trade_age_min: int = Field(ge=1)
    max_news_age_hours: int = Field(ge=1)
    fallback_price_tolerance_pct: float = Field(gt=0)


class ExtendedHoursConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ext_hours_max_quote_age_sec: int = Field(ge=1)
    ext_hours_max_cross_pct: float = Field(ge=0)
    sessions_allowed_for_exits: list[str]

    @model_validator(mode="after")
    def _no_overnight(self) -> ExtendedHoursConfig:
        if "overnight" in self.sessions_allowed_for_exits:
            raise ValueError("overnight session is never allowed (OI-02)")
        return self


class ReviewsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_invalidation_proximity_pct: float = Field(gt=0)


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sim_fill_delay_min: int = Field(ge=15)  # must cover the SIP embargo (§8.9)
    sim_fill_delay_margin_min: int = Field(ge=0)
    sim_additional_slippage_bps: float = Field(ge=0)
    forecast_resolution: str


class MarketDataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market_data_plan: str
    iex_stream_max_symbols: int = Field(ge=1)
    candidate_outcome_horizons: list[str]
    data_upgrade_review_equity_usd: float
    data_upgrade_benefit_factor: float = Field(ge=1)

    @model_validator(mode="after")
    def _plan(self) -> MarketDataConfig:
        if self.market_data_plan not in ("basic", "algo_trader_plus"):
            raise ValueError("market_data_plan must be basic | algo_trader_plus")
        if self.market_data_plan == "basic" and self.iex_stream_max_symbols > 30:
            raise ValueError("basic plan caps WebSocket subscriptions at 30 symbols (verified 2026-09-13)")
        return self


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    llm_monthly_cap_usd: float = Field(gt=0)
    llm_entry_bucket_pct: float = Field(gt=0, lt=100)
    proration: str
    model_triage: str
    model_decision: str
    model_critique: str
    dossier_max_headlines: int = Field(ge=1)
    dossier_token_cap: int = Field(ge=500)


class GuardsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_orders_per_day: int = Field(ge=1)
    max_llm_calls_per_hour: int = Field(ge=1)
    halt_after_consecutive_rejects: int = Field(ge=1)
    kill_switch_daily_loss_pct: float | None = None


class RetentionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_retention_days: int = Field(ge=1)


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exclusion_list_path: str


class RiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    execution: ExecutionConfig
    capital: CapitalConfig
    position_limits: PositionLimits
    horizon: HorizonConfig
    universe: UniverseConfig
    scanner: ScannerConfig
    staleness: StalenessConfig
    extended_hours: ExtendedHoursConfig
    reviews: ReviewsConfig
    simulation: SimulationConfig
    market_data: MarketDataConfig
    budget: BudgetConfig
    guards: GuardsConfig
    retention: RetentionConfig
    paths: PathsConfig


@dataclass(frozen=True)
class ConfigVersions:
    config_version: str
    scanner_version: str
    qb_rules_version: str
    exclusion_list_version: str
    prompt_version: str | None


@dataclass(frozen=True)
class Settings:
    risk: RiskPolicy
    fees: dict[str, Any]
    scanner: dict[str, Any]
    qb_rules: dict[str, Any]
    exclusions: dict[str, Any]
    sic_backstop: dict[str, Any]
    versions: ConfigVersions


def load_settings(config_dir: Path = CONFIG_DIR, env: dict[str, str] | None = None) -> Settings:
    """Load and validate all versioned config. `EXECUTION_MODE` in the environment overrides the YAML value,
    but a LIVE value in either place raises LiveLockedOut."""
    env = dict(os.environ if env is None else env)
    risk_raw = _load_yaml(config_dir / "risk_policy.yaml")
    if "EXECUTION_MODE" in env:
        risk_raw.setdefault("execution", {})["execution_mode"] = env["EXECUTION_MODE"]
    if env.get("LIVE_ENABLED", "").lower() == "true":
        raise LiveLockedOut("LIVE_ENABLED=true is refused in this codebase (ADR-0020)")
    risk = RiskPolicy.model_validate(risk_raw)
    fees = _load_yaml(config_dir / "fees.yaml")
    scanner = _load_yaml(config_dir / "scanner.yaml")
    qb = _load_yaml(config_dir / "qb_rules.yaml")
    exclusions = _load_yaml(config_dir / risk.paths.exclusion_list_path.split("/")[-1])
    sic = _load_yaml(config_dir / "sic_backstop.yaml")
    versions = ConfigVersions(
        config_version=canonical_hash(risk_raw, fees),
        scanner_version=str(scanner["scanner_version"]),
        qb_rules_version=str(qb["qb_rules_version"]),
        exclusion_list_version=f"{exclusions['version']}+sic:{sic['version']}",
        prompt_version=env.get("PROMPT_VERSION"),
    )
    return Settings(
        risk=risk, fees=fees, scanner=scanner, qb_rules=qb, exclusions=exclusions, sic_backstop=sic, versions=versions
    )
