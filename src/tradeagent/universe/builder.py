"""Universe construction (§6.1, ADR-0003, ADR-0005, ADR-0006). Pure over its inputs so it is testable without the network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from tradeagent.adapters.alpaca.assets import Asset
from tradeagent.domain.enums import UniverseStatus
from tradeagent.domain.models import Instrument
from tradeagent.exclusions import ExclusionScreen

TEN_K_MAX_AGE = timedelta(days=456)  # 15 months (ADR-0005 step 3)


@dataclass(frozen=True)
class EdgarFacts:
    cik: int
    sic: int | None
    last_10k_filed_on: date | None
    name: str


@dataclass(frozen=True)
class DailyStats:
    """From SIP daily bars: last close, 20-session mean dollar volume, sessions of history."""

    last_close: Decimal
    avg_dollar_volume_20: Decimal
    history_days: int


@dataclass(frozen=True)
class Floors:
    min_price: Decimal
    min_avg_dollar_volume: Decimal
    min_history_days: int
    require_fractionable: bool


def classify(
    asset: Asset,
    edgar: EdgarFacts | None,
    stats: DailyStats | None,
    floors: Floors,
    screen: ExclusionScreen,
    as_of: date,
) -> Instrument:
    inst = Instrument(
        symbol=asset.symbol,
        name=asset.name,
        exchange=asset.exchange,
        tradable=asset.tradable,
        fractionable=asset.fractionable,
    )
    if not asset.tradable:
        return inst.model_copy(
            update={"universe_status": UniverseStatus.EXCLUDED_UNIVERSE, "status_reason": "not_tradable"}
        )
    if not asset.common_stock_shape:
        return inst.model_copy(
            update={"universe_status": UniverseStatus.EXCLUDED_UNIVERSE, "status_reason": "symbol_shape_or_ptp_or_ipo"}
        )
    if edgar is None:
        return inst.model_copy(
            update={"universe_status": UniverseStatus.EXCLUDED_UNIVERSE, "status_reason": "no_edgar_match"}
        )
    inst = inst.model_copy(
        update={"cik": str(edgar.cik), "sic": edgar.sic, "last_10k_filed_on": edgar.last_10k_filed_on}
    )
    if edgar.last_10k_filed_on is None or as_of - edgar.last_10k_filed_on > TEN_K_MAX_AGE:
        return inst.model_copy(
            update={
                "universe_status": UniverseStatus.EXCLUDED_UNIVERSE,
                "status_reason": "no_recent_10k (foreign issuer, fund, or trust)",
            }
        )
    if edgar.sic is None:
        return inst.model_copy(update={"universe_status": UniverseStatus.EXCLUDED_UNIVERSE, "status_reason": "no_sic"})
    status, why = screen.screen(asset.symbol, edgar.sic)
    if status != UniverseStatus.ELIGIBLE:
        return inst.model_copy(update={"universe_status": status, "status_reason": why})
    if floors.require_fractionable and not asset.fractionable:
        return inst.model_copy(
            update={"universe_status": UniverseStatus.EXCLUDED_FLOOR, "status_reason": "not_fractionable"}
        )
    if stats is None or stats.history_days < floors.min_history_days:
        return inst.model_copy(
            update={
                "universe_status": UniverseStatus.EXCLUDED_FLOOR,
                "status_reason": f"history<{floors.min_history_days}",
            }
        )
    if stats.last_close < floors.min_price:
        return inst.model_copy(
            update={"universe_status": UniverseStatus.EXCLUDED_FLOOR, "status_reason": f"price<{floors.min_price}"}
        )
    if stats.avg_dollar_volume_20 < floors.min_avg_dollar_volume:
        return inst.model_copy(
            update={
                "universe_status": UniverseStatus.EXCLUDED_FLOOR,
                "status_reason": f"adv20<{floors.min_avg_dollar_volume}",
            }
        )
    return inst.model_copy(update={"universe_status": UniverseStatus.ELIGIBLE, "status_reason": None})


def build_universe(
    assets: list[Asset],
    edgar: dict[str, EdgarFacts],
    stats: dict[str, DailyStats],
    floors: Floors,
    screen: ExclusionScreen,
    as_of: date,
) -> list[Instrument]:
    return [classify(a, edgar.get(a.symbol), stats.get(a.symbol), floors, screen, as_of) for a in assets]
