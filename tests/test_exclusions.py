from tradeagent.config import load_settings
from tradeagent.domain.enums import UniverseStatus
from tradeagent.exclusions import ExclusionScreen


def screen() -> ExclusionScreen:
    s = load_settings(env={})
    return ExclusionScreen.from_config(s.exclusions, s.sic_backstop)


def test_denylist_symbol_excluded_regardless_of_sic():
    st, why = screen().screen("xom", None)
    assert st == UniverseStatus.EXCLUDED_ETHICAL and why.startswith("denylist:fossil_fuels")


def test_sic_backstop_catches_unknown_producer():
    st, why = screen().screen("ZZZZ", 1311)
    assert st == UniverseStatus.EXCLUDED_ETHICAL and why == "sic:1311:fossil_fuels"


def test_default_deny_needs_review_unless_allowlisted():
    sc = screen()
    assert sc.screen("ZZZZ", 3721)[0] == UniverseStatus.NEEDS_ETHICAL_REVIEW
    sc2 = ExclusionScreen(**{**sc.__dict__, "allowlist": {"ZZZZ"}})
    assert sc2.screen("ZZZZ", 3721)[0] == UniverseStatus.ELIGIBLE


def test_spac_is_universe_exclusion_not_ethical():
    assert screen().screen("SPAC", 6770)[0] == UniverseStatus.EXCLUDED_UNIVERSE


def test_clean_name_eligible():
    assert screen().screen("AAPL", 3571) == (UniverseStatus.ELIGIBLE, None)
