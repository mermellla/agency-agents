"""T-06 (universe half): ADR-0005 membership test and ADR-0003 floors."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tradeagent.adapters.alpaca.assets import Asset, parse_assets
from tradeagent.config import load_settings
from tradeagent.domain.enums import UniverseStatus
from tradeagent.exclusions import ExclusionScreen
from tradeagent.universe.builder import DailyStats, EdgarFacts, Floors, classify

AS_OF = date(2026, 9, 17)
FLOORS = Floors(Decimal("5"), Decimal("10000000"), 60, True)


def screen() -> ExclusionScreen:
    s = load_settings(env={})
    return ExclusionScreen.from_config(s.exclusions, s.sic_backstop)


def asset(sym="ACME", **kw) -> Asset:
    base = dict(symbol=sym, name="Acme Corp", exchange="NASDAQ", tradable=True, fractionable=True, attributes=())
    base.update(kw)
    return Asset(**base)


def edgar(sic=3571, tenk=date(2026, 2, 20)) -> EdgarFacts:
    return EdgarFacts(1234, sic, tenk, "ACME CORP")


def stats(close="50", adv="20000000", days=80) -> DailyStats:
    return DailyStats(Decimal(close), Decimal(adv), days)


def test_eligible_domestic_operating_company():
    i = classify(asset(), edgar(), stats(), FLOORS, screen(), AS_OF)
    assert i.universe_status == UniverseStatus.ELIGIBLE and i.sic == 3571 and i.cik == "1234"


def test_adr_and_funds_excluded_by_missing_10k():
    assert classify(asset("TSM"), edgar(tenk=None), stats(), FLOORS, screen(), AS_OF).status_reason.startswith(
        "no_recent_10k"
    )
    assert (
        classify(asset("OLD"), edgar(tenk=date(2024, 1, 1)), stats(), FLOORS, screen(), AS_OF).universe_status
        == UniverseStatus.EXCLUDED_UNIVERSE
    )


def test_no_edgar_match_and_symbol_shape():
    assert classify(asset(), None, stats(), FLOORS, screen(), AS_OF).status_reason == "no_edgar_match"
    assert (
        classify(asset("BRK.B"), edgar(), stats(), FLOORS, screen(), AS_OF).status_reason
        == "symbol_shape_or_ptp_or_ipo"
    )
    assert (
        classify(
            asset("ET", attributes=("ptp_with_exception",)), edgar(), stats(), FLOORS, screen(), AS_OF
        ).status_reason
        == "symbol_shape_or_ptp_or_ipo"
    )


def test_spac_and_ethical_screen():
    assert (
        classify(asset("SPAC"), edgar(sic=6770), stats(), FLOORS, screen(), AS_OF).universe_status
        == UniverseStatus.EXCLUDED_UNIVERSE
    )
    assert (
        classify(asset("XOM"), edgar(sic=2911), stats(), FLOORS, screen(), AS_OF).universe_status
        == UniverseStatus.EXCLUDED_ETHICAL
    )
    assert (
        classify(asset("ZZZZ"), edgar(sic=3721), stats(), FLOORS, screen(), AS_OF).universe_status
        == UniverseStatus.NEEDS_ETHICAL_REVIEW
    )


def test_floors():
    assert classify(asset(), edgar(), stats(close="4.99"), FLOORS, screen(), AS_OF).status_reason.startswith("price<")
    assert classify(asset(), edgar(), stats(adv="9999999"), FLOORS, screen(), AS_OF).status_reason.startswith("adv20<")
    assert classify(asset(), edgar(), stats(days=59), FLOORS, screen(), AS_OF).status_reason.startswith("history<")
    assert (
        classify(asset(fractionable=False), edgar(), stats(), FLOORS, screen(), AS_OF).status_reason
        == "not_fractionable"
    )
    assert classify(asset(), edgar(), None, FLOORS, screen(), AS_OF).universe_status == UniverseStatus.EXCLUDED_FLOOR


def test_parse_assets_keeps_active_us_equities_only():
    rows = [
        {
            "symbol": "AAPL",
            "class": "us_equity",
            "status": "active",
            "tradable": True,
            "fractionable": True,
            "name": "Apple",
            "exchange": "NASDAQ",
            "attributes": ["has_options"],
        },
        {"symbol": "BTCUSD", "class": "crypto", "status": "active", "tradable": True},
        {"symbol": "DEAD", "class": "us_equity", "status": "inactive", "tradable": False},
    ]
    assets = parse_assets(rows)
    assert [a.symbol for a in assets] == ["AAPL"] and assets[0].common_stock_shape
