"""Scanner (§6): pure over its inputs. `ScanInputs` is assembled by `ScanRunner` (I/O) or by tests (synthetic)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from tradeagent.domain.enums import FeedTier, MarketRegime
from tradeagent.domain.models import (
    Bar,
    Candidate,
    Headline,
    Instrument,
    LLMCall,
    Quote,
    ScanResult,
    SignalValue,
    SourceStamp,
    Trade,
)
from tradeagent.scanner.composite import composite_scores, strategy_tags
from tradeagent.scanner.regime import classify_regime
from tradeagent.scanner.signals.catalyst import CatalystClassifier
from tradeagent.scanner.signals.technical import (
    available_on,
    breakout_daily,
    gap_continuation,
    gap_vs_prior_close,
    momentum,
    opening_range,
    orb_iex_assisted,
    orb_sip_confirmed,
    relative_strength,
    rsi,
    sma,
    sma_relation,
    volatility_expansion,
)


@dataclass
class ScanInputs:
    kind: str  # preopen | intraday
    now: datetime
    session_date: date
    universe: list[Instrument]  # already classified (§6.1); only ELIGIBLE symbols are scored
    daily: dict[str, list[Bar]]  # ≥ 60 sessions where available; must include "SPY"
    bars15: dict[str, list[Bar]] = field(default_factory=dict)  # today's 15-minute bars (intraday only)
    iex_quotes: dict[str, Quote] = field(default_factory=dict)
    iex_trades: dict[str, Trade] = field(default_factory=dict)
    headlines: dict[str, list[Headline]] = field(default_factory=dict)
    earnings_dates: dict[str, date] = field(default_factory=dict)
    source_status: list[SourceStamp] = field(default_factory=list)
    bars_end_at: datetime | None = None


@dataclass
class ScanOutput:
    result: ScanResult
    regime_inputs: dict[str, Any]
    signals: dict[str, dict[str, Any]]  # per symbol raw signals (persisted on candidates)
    calls: list[LLMCall]
    memberships: list[Instrument]


class Scanner:
    version: str

    def __init__(
        self,
        scanner_cfg: dict[str, Any],
        plan_tier: FeedTier,
        top_n: int,
        exclusion_list_version: str,
        catalyst: CatalystClassifier | None,
        clock: Callable[[], datetime],
    ):
        self.cfg = scanner_cfg
        self.version = str(scanner_cfg["scanner_version"])
        self.plan_tier = plan_tier
        self.top_n = top_n
        self.exclusion_list_version = exclusion_list_version
        self.catalyst = catalyst
        self.clock = clock

    # ---- helpers
    @staticmethod
    def _breadth(daily: dict[str, list[Bar]], symbols: list[str]) -> float | None:
        above = total = 0
        for s in symbols:
            bars = daily.get(s) or []
            c = [float(b.close) for b in bars]
            s50 = sma(c, 50)
            if s50 is None:
                continue
            total += 1
            above += 1 if c[-1] > s50 else 0
        return None if total == 0 else above / total * 100

    def _signals_for(self, inst: Instrument, inp: ScanInputs, spy: list[Bar]) -> dict[str, Any] | None:
        bars = inp.daily.get(inst.symbol) or []
        if len(bars) < 21:
            return None
        prior_close = bars[-1].close
        b15 = inp.bars15.get(inst.symbol) or []
        sig: dict[str, Any] = {
            "momentum_5d": momentum(bars, 5),
            "momentum_10d": momentum(bars, 10),
            "momentum_20d": momentum(bars, 20),
            "relative_strength_vs_spy_20d": relative_strength(bars, spy, 20),
            "rsi_14": rsi(bars),
            "sma_20_over_50": sma_relation(bars),
            "breakout_daily": breakout_daily(bars),
            "volatility_expansion": volatility_expansion(bars),
            "gap_vs_prior_close": gap_vs_prior_close(opening_range(b15), prior_close) if b15 else None,
            "intraday_setup": None,
            "intraday_setup_strength": None,
            "intraday_invalidation": None,
            "intraday_target": None,
        }
        q = inp.iex_quotes.get(inst.symbol)
        t = inp.iex_trades.get(inst.symbol)
        iex_price = t.price if t else None
        iex_age = int((inp.now - t.at).total_seconds()) if t else None
        spread_pct = float((q.ask - q.bid) / q.ask * 100) if q and q.ask > 0 else None
        setup = (
            orb_sip_confirmed(b15, bars)
            or gap_continuation(b15, prior_close)
            or orb_iex_assisted(b15, bars, iex_price, iex_age, spread_pct)
        )
        if setup:
            sig.update(
                intraday_setup=setup.strategy,
                intraday_setup_strength=setup.strength,
                intraday_invalidation=float(setup.invalidation),
                intraday_target=float(setup.target),
                intraday_detail=setup.detail,
            )
        ed = inp.earnings_dates.get(inst.symbol)
        sig["earnings_date"] = ed.isoformat() if ed else None
        sig["earnings_within_2_sessions"] = bool(ed and 0 <= (ed - inp.session_date).days <= 2)
        sig["iex_price"] = float(iex_price) if iex_price else None
        sig["iex_age_sec"] = iex_age
        sig["iex_spread_pct"] = spread_pct
        return sig

    def run(self, inp: ScanInputs) -> ScanOutput:
        started = inp.now
        spy = inp.daily.get("SPY") or []
        eligible = [i for i in inp.universe if i.universe_status.value == "eligible"]
        symbols = [i.symbol for i in eligible]
        breadth = self._breadth(inp.daily, symbols)
        regime, regime_inputs = classify_regime(spy, breadth, self.cfg)
        computable, unavailable = available_on(self.plan_tier)
        signals: dict[str, dict[str, Any]] = {}
        for inst in eligible:
            s = self._signals_for(inst, inp, spy)
            if s is not None:
                signals[inst.symbol] = s
        # catalysts (Jev or keyword) only for symbols with headlines: cost-bounded by max_headlines_per_symbol
        calls: list[LLMCall] = []
        names = {i.symbol: i.name for i in eligible}
        for sym, sig in signals.items():
            hs = inp.headlines.get(sym) or []
            if self.catalyst is not None and hs:
                cs = self.catalyst.classify(sym, names.get(sym), hs)
                sig.update(catalyst_strength=cs.strength, catalyst_direction=cs.direction, catalyst=cs.as_signal())
                calls.extend(cs.calls)
                if cs.unavailable_reason:
                    unavailable = {**unavailable, "catalyst": cs.unavailable_reason}
            else:
                sig.update(catalyst_strength=None, catalyst_direction="none")
        scores = composite_scores(signals, self.cfg)
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[: self.top_n]
        candidates: list[Candidate] = []
        completed = self.clock()
        scan_id = uuid4()
        for rank, (sym, score) in enumerate(ranked, start=1):
            bars = inp.daily[sym]
            newest = (inp.bars15.get(sym) or [bars[-1]])[-1]
            sig = signals[sym]
            q, t = inp.iex_quotes.get(sym), inp.iex_trades.get(sym)
            candidates.append(
                Candidate(
                    scan_id=scan_id,
                    symbol=sym,
                    rank=rank,
                    composite_score=Decimal(str(round(score, 6))),
                    signals=[
                        SignalValue(
                            name=k,
                            value=(
                                Decimal(str(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
                            ),
                            unavailable_reason=unavailable.get(k),
                        )
                        for k, v in sig.items()
                        if k in computable or k in ("catalyst_strength", "intraday_setup_strength")
                    ],
                    strategy_tags=strategy_tags(sig),
                    signal_bar_time=newest.end,
                    signal_observed_at=max(newest.retrievable_at, started),
                    sip_signal_price=newest.close,
                    sip_signal_timestamp=newest.end,
                    iex_price_at_scan=(t.price if t else None),
                    iex_quote_age_sec_at_scan=(int((inp.now - q.at).total_seconds()) if q else None),
                )
            )
        result = ScanResult(
            scan_id=scan_id,
            kind=inp.kind,
            feed_tier=self.plan_tier,
            started_at=started,
            scanner_completed_at=max(completed, started),
            bars_end_at=inp.bars_end_at or (started - timedelta(minutes=15)),
            regime=regime,
            scanner_version=self.version,
            exclusion_list_version=self.exclusion_list_version,
            universe_size=len(eligible),
            candidates=candidates,
            source_status=inp.source_status,
            signals_unavailable=[f"{k}: {v}" for k, v in unavailable.items()],
        )
        return ScanOutput(result, regime_inputs, signals, calls, inp.universe)


def regime_name(r: MarketRegime) -> str:
    return r.value
