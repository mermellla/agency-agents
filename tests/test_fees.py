from datetime import date
from decimal import Decimal

import pytest

from tradeagent.config import load_settings
from tradeagent.domain.enums import FeeKind
from tradeagent.fees import FeeSchedules


def schedules() -> FeeSchedules:
    s = load_settings(env={})
    return FeeSchedules.from_config(s.fees, s.versions.config_version)


def test_schedule_is_effective_dated():
    f = schedules()
    assert f.row_on(f.taf, date(2025, 12, 31))["rate_per_share"] == 0.000166
    assert f.row_on(f.taf, date(2026, 1, 1))["rate_per_share"] == 0.000195
    assert f.row_on(f.taf, date(2027, 6, 1))["max_per_trade_usd"] == 11.61
    assert f.row_on(f.taf, date(2028, 1, 1))["rate_per_share"] == 0.000240
    assert f.row_on(f.sec, date(2026, 4, 3))["rate"] == 0.00
    assert f.row_on(f.sec, date(2026, 4, 4))["rate"] == 20.60
    assert f.row_on(f.sec, date(2020, 1, 1)) is None  # unknown, never silently zero


def test_sell_fees_2026():
    lines = {
        line.kind: line for line in schedules().for_sell(date(2026, 9, 14), qty=Decimal("1.5"), notional=Decimal("150"))
    }
    assert lines[FeeKind.SEC_SECTION_31].amount_usd == Decimal("0.00")  # 150 × 20.60 / 1e6 = $0.00309 → $0.00
    assert lines[FeeKind.FINRA_TAF].amount_usd == Decimal("0.00")  # 1.5 × 0.000195 → $0.00
    big = {
        line.kind: line
        for line in schedules().for_sell(date(2026, 9, 14), qty=Decimal("60000"), notional=Decimal("1000000"))
    }
    assert big[FeeKind.SEC_SECTION_31].amount_usd == Decimal("20.60")
    assert big[FeeKind.FINRA_TAF].amount_usd == Decimal("9.79")  # capped per trade
    assert (
        lines[FeeKind.CAT].classification == "pass_through_unverified"
    )  # owner: kept apart from customer-debited fees
    assert lines[FeeKind.CAT].amount_usd == Decimal("0.00")  # 1.5 × $0.000001
    cat_big = {
        line.kind: line
        for line in schedules().for_sell(date(2026, 9, 14), qty=Decimal("6000000"), notional=Decimal("1"))
    }
    assert cat_big[FeeKind.CAT].amount_usd == Decimal("6.00")
    assert {line.kind for line in schedules().for_buy(date(2026, 9, 14), qty=Decimal("10"))} == {FeeKind.CAT}
    assert schedules().for_buy(date(2027, 1, 1), qty=Decimal("10")) == []  # CAT window ends 2026-12-31
    april = schedules().for_sell(date(2026, 4, 30), qty=Decimal("1"), notional=Decimal("1"))
    assert {line.kind for line in april} == {FeeKind.SEC_SECTION_31, FeeKind.FINRA_TAF}  # CAT starts 2026-05-01


def test_unknown_date_raises():
    with pytest.raises(ValueError):
        schedules().for_sell(date(2019, 1, 1), qty=Decimal("1"), notional=Decimal("10"))
