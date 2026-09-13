"""Config seeds load, validate, and carry the spec defaults (Appendix B). LIVE is refused (ADR-0020)."""

from __future__ import annotations

import pytest

from tradeagent.config import LiveLockedOut, Settings, load_settings
from tradeagent.domain.enums import ExecutionMode


@pytest.fixture(scope="module")
def settings() -> Settings:
    return load_settings(env={})


def test_spec_defaults(settings):
    r = settings.risk
    assert r.execution.execution_mode == ExecutionMode.DRY_RUN and r.execution.live_enabled is False
    assert r.capital.experiment_equity_start == 500.00
    assert r.position_limits.max_single_position_pct == 40 and r.position_limits.max_positions == 5
    assert tuple(r.position_limits.normal_position_range_pct) == (10, 30)
    assert r.horizon.latency_floor_min == 30 and r.horizon.min_expected_hold == "1h"
    assert r.scanner.scan_interval_min == 15 and r.scanner.scan_top_n == 25 and r.scanner.triage_top_k == 3
    assert r.staleness.max_sip_bar_age_min == 20 and r.staleness.max_iex_trade_age_min == 5
    assert r.simulation.sim_fill_delay_min == 15 and r.simulation.sim_additional_slippage_bps == 0
    assert r.market_data.market_data_plan == "basic" and r.market_data.iex_stream_max_symbols == 30
    assert r.market_data.candidate_outcome_horizons == ["15m", "1h", "1d", "5d"]
    assert r.market_data.data_upgrade_benefit_factor == 2.0
    assert r.budget.llm_monthly_cap_usd == 10.00
    assert r.retention.context_retention_days == 90
    assert r.guards.kill_switch_daily_loss_pct is None
    bp = r.execution.broker_policy
    assert (
        bp.max_margin_multiplier,
        bp.no_shorting,
        bp.max_options_trading_level,
        bp.fractional_trading,
        bp.disable_overnight_trading,
    ) == ("1", True, 0, True, True)


def test_model_ids_are_current_and_tiered(settings):
    b = settings.risk.budget
    assert b.model_triage == "claude-haiku-4-5"
    assert b.model_decision == "claude-sonnet-5" and b.model_critique == "claude-sonnet-5"


def test_live_refused_by_env():
    with pytest.raises(LiveLockedOut):
        load_settings(env={"EXECUTION_MODE": "LIVE"})
    with pytest.raises(LiveLockedOut):
        load_settings(env={"LIVE_ENABLED": "true"})


def test_config_version_is_content_hash(settings):
    again = load_settings(env={})
    assert settings.versions.config_version == again.versions.config_version
    assert len(settings.versions.config_version) == 64


def test_versions_present(settings):
    v = settings.versions
    assert v.scanner_version == "1.0.0" and v.qb_rules_version == "1.0.0"
    assert v.exclusion_list_version == "2026.09.13-r1+sic:2026.09.13-r1"


def test_fee_schedule_shape(settings):
    f = settings.fees
    assert f["sec_section_31"]["applies_to"] == "sells" and f["finra_taf"]["applies_to"] == "sells"
    assert [r["rate"] for r in f["sec_section_31"]["schedule"]] == [0.00, 20.60]
    assert [(r["rate_per_share"], r["max_per_trade_usd"]) for r in f["finra_taf"]["schedule"]] == [
        (0.000166, 8.30),
        (0.000195, 9.79),
        (0.000232, 11.61),
        (0.000240, 12.05),
    ]


def test_exclusions_schema(settings):
    ex = settings.exclusions
    assert ex["version"] and ex["updated_on"]
    for e in ex["deny"]:
        assert set(e) >= {"symbol", "category", "reason", "source"}
        assert e["category"] in ex["categories"]
    symbols = {e["symbol"] for e in ex["deny"]}
    assert {
        "XOM",
        "LMT",
        "GEO",
        "GE",
        "PLTR",
        "LDOS",
        "CACI",
        "SAIC",
        "BAH",
        "OSK",
    } <= symbols  # owner-retained edge cases + OSK
    assert not ({"HWM", "HON", "POWW", "HES"} & symbols)  # owner-approved cleanup
    assert not any(e.get("needs_owner_review") for e in ex["deny"])


EDGAR_SIC_SNAPSHOT_2026_09_13 = {
    1220,
    1221,
    1311,
    1381,
    1382,
    1389,
    2911,
    3480,
    3720,
    3721,
    3724,
    3728,
    3760,
    3812,
    4610,
    4922,
    4923,
    4924,
    5171,
    5172,
    3730,
    3533,
    6792,
    2990,
    6770,
    6221,
    6189,
}
NOT_IN_EDGAR = {1241, 1321, 3483, 3489, 3795, 4612, 4613}


def test_sic_backstop_only_uses_real_edgar_codes(settings):
    sic = settings.sic_backstop
    used = set()
    for rows in sic["deny"].values():
        used |= {int(r["sic"]) for r in rows}
    used |= (
        {int(r["sic"]) for r in sic["default_deny"]}
        | {int(r["sic"]) for r in sic["universe_exclude"]}
        | {int(r["sic"]) for r in sic["review_before_enabling"]}
    )
    assert used - {3795} <= EDGAR_SIC_SNAPSHOT_2026_09_13
    assert not ((used - {3795}) & NOT_IN_EDGAR)
    assert {4923, 4924} <= {int(r["sic"]) for r in sic["default_deny"]}  # owner decision 2026-09-13
    assert {int(r["sic"]) for r in sic["spec_codes_not_in_edgar"]} == NOT_IN_EDGAR
    assert 3795 in {
        int(r["sic"]) for r in sic["deny"]["weapons_defense"]
    }  # owner decision: retained despite absence from EDGAR
