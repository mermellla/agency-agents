"""ScanRunner: the I/O side of a scan — gathers inputs from adapters, runs the pure Scanner, persists (§6.4, §10.1)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.adapters.alpaca.assets import Asset
from tradeagent.adapters.alpaca.market_data import AlpacaBars, RequestBudget
from tradeagent.data.registry import SourceRegistry
from tradeagent.domain.models import Bar, Headline, Instrument
from tradeagent.persistence.db import Database
from tradeagent.scanner.scanner import ScanInputs, Scanner, ScanOutput
from tradeagent.universe.builder import DailyStats, EdgarFacts, Floors, build_universe

log = logging.getLogger("tradeagent.scanner")


def daily_stats(bars: list[Bar]) -> DailyStats | None:
    if not bars:
        return None
    last20 = bars[-20:]
    adv = sum(float(b.close) * b.volume for b in last20) / len(last20)
    return DailyStats(
        last_close=bars[-1].close, avg_dollar_volume_20=Decimal(str(round(adv, 2))), history_days=len(bars)
    )


class ScanRunner:
    def __init__(
        self,
        db: Database,
        scanner: Scanner,
        sip: AlpacaBars,
        iex: AlpacaBars,
        registry: SourceRegistry,
        floors: Floors,
        screen: Any,
        experiment_id: UUID,
        phase_id: UUID,
        prompt_version: str,
        headlines_fn: Callable[[list[str], datetime], list[Headline]] | None = None,
        earnings_fn: Callable[[date, date], dict[str, date]] | None = None,
        assets_fn: Callable[[], list[Asset]] | None = None,
        edgar_fn: Callable[[list[Asset]], dict[str, EdgarFacts]] | None = None,
        budget: RequestBudget | None = None,
        clock: Callable[[], datetime] | None = None,
        history_days: int = 60,
        news_age_hours: int = 36,
    ):
        self.db, self.scanner, self.sip, self.iex, self.registry = db, scanner, sip, iex, registry
        self.floors, self.screen = floors, screen
        self.experiment_id, self.phase_id, self.prompt_version = experiment_id, phase_id, prompt_version
        self.headlines_fn, self.earnings_fn, self.assets_fn, self.edgar_fn = (
            headlines_fn,
            earnings_fn,
            assets_fn,
            edgar_fn,
        )
        self.budget = budget or RequestBudget()
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.history_days, self.news_age_hours = history_days, news_age_hours
        self._universe_cache: tuple[date, list[Instrument]] | None = None

    # ---- universe (refreshed once per session, §6.1)
    def universe(self, session_date: date, daily: dict[str, list[Bar]]) -> list[Instrument]:
        if self._universe_cache and self._universe_cache[0] == session_date:
            return self._universe_cache[1]
        assets = self.assets_fn() if self.assets_fn else []
        shaped = [a for a in assets if a.tradable and a.common_stock_shape]
        edgar = self.edgar_fn(shaped) if self.edgar_fn else {}
        stats = {s: st for s, b in daily.items() if (st := daily_stats(b)) is not None}
        universe = build_universe(assets, edgar, stats, self.floors, self.screen, session_date)
        self.db.upsert_instruments(universe, self.scanner.exclusion_list_version)
        self._universe_cache = (session_date, universe)
        return universe

    def gather(self, kind: str, now: datetime, session_date: date, symbols_hint: list[str] | None = None) -> ScanInputs:
        stamps = self.registry.refresh()
        end = now - self.sip.embargo
        start_daily = now - timedelta(days=int(self.history_days * 1.6) + 10)
        assets = self.assets_fn() if self.assets_fn else []
        shaped = [a.symbol for a in assets if a.tradable and a.common_stock_shape]
        pull = symbols_hint or shaped
        daily = _group(self.sip.bars(sorted(set(pull) | {"SPY"}), "1Day", start_daily, end))
        universe = self.universe(session_date, daily)
        eligible = [i.symbol for i in universe if i.universe_status.value == "eligible"]
        bars15: dict[str, list[Bar]] = {}
        quotes: dict[str, Any] = {}
        trades: dict[str, Any] = {}
        if kind == "intraday" and eligible:
            day_start = datetime.combine(session_date, datetime.min.time(), tzinfo=UTC)
            bars15 = _group(self.sip.bars(eligible, "15Min", day_start, end))
            quotes = self.iex.latest_quotes(eligible)
            trades = self.iex.latest_trades(eligible)
        headlines: dict[str, list[Headline]] = {}
        if self.headlines_fn and eligible:
            since = now - timedelta(hours=self.news_age_hours)
            for h in self.headlines_fn(eligible, since):
                for s in h.symbols:
                    if s in daily:
                        headlines.setdefault(s, []).append(h)
        earnings: dict[str, date] = {}
        if self.earnings_fn:
            try:
                earnings = self.earnings_fn(session_date, session_date + timedelta(days=21))
            except Exception as exc:  # research-grade source: degrade, never halt (§5.2)
                log.warning("earnings calendar unavailable: %s", exc)
        return ScanInputs(
            kind=kind,
            now=now,
            session_date=session_date,
            universe=universe,
            daily=daily,
            bars15=bars15,
            iex_quotes=quotes,
            iex_trades=trades,
            headlines=headlines,
            earnings_dates=earnings,
            source_status=stamps,
            bars_end_at=end,
        )

    def run_and_persist(
        self, kind: str, now: datetime, session_date: date, symbols_hint: list[str] | None = None
    ) -> ScanOutput:
        halted = self.registry.entries_halted() if self.registry.domains else []
        inputs = self.gather(kind, now, session_date, symbols_hint)
        out = self.scanner.run(inputs)
        with self.db.transaction():
            self.db.persist_scan(out, self.experiment_id, self.phase_id, self.registry.as_json(), entries_halted=halted)
            for call in out.calls:
                self.db.insert_llm_call(call, self.experiment_id, self.phase_id, prompt_version=self.prompt_version)
        log.info(
            "scan %s: universe=%d candidates=%d regime=%s jev_calls=%d budget_used=%d/min",
            kind,
            out.result.universe_size,
            len(out.result.candidates),
            out.result.regime.value,
            len(out.calls),
            self.budget.used_last_minute,
        )
        return out


def _group(bars: list[Bar]) -> dict[str, list[Bar]]:
    out: dict[str, list[Bar]] = {}
    for b in bars:
        out.setdefault(b.symbol, []).append(b)
    for v in out.values():
        v.sort(key=lambda b: b.start)
    return out
