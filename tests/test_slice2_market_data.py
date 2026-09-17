"""T-08: SIP embargo, availability semantics, request budget, plan flip (T-19 adapter half)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from tradeagent.adapters.alpaca.client import MARKET_DATA_URL, AlpacaClient, AlpacaCredentials
from tradeagent.adapters.alpaca.market_data import EmbargoViolation, RequestBudget, make_market_data
from tradeagent.domain.enums import FeedTier

NOW = datetime(2026, 9, 17, 14, 30, tzinfo=UTC)


def client() -> AlpacaClient:
    return AlpacaClient(AlpacaCredentials("k", "s"))


def test_sip_delayed_refuses_end_inside_embargo():
    sip, _ = make_market_data(client(), "basic", clock=lambda: NOW)
    with pytest.raises(EmbargoViolation):
        sip.bars(["AAPL"], "15Min", NOW - timedelta(hours=2), NOW - timedelta(minutes=14))


@respx.mock
def test_bars_parse_and_retrievable_at_never_before_embargo():
    payload = {
        "bars": {
            "AAPL": [
                {
                    "t": "2026-09-17T13:30:00Z",
                    "o": 100,
                    "h": 101,
                    "l": 99.5,
                    "c": 100.5,
                    "v": 12345,
                    "n": 10,
                    "vw": 100.2,
                }
            ]
        },
        "next_page_token": None,
    }
    route = respx.get(f"{MARKET_DATA_URL}/v2/stocks/bars").mock(return_value=httpx.Response(200, json=payload))
    sip, _ = make_market_data(client(), "basic", clock=lambda: NOW)
    bars = sip.bars(["AAPL"], "15Min", NOW - timedelta(hours=2), NOW - timedelta(minutes=16))
    assert len(bars) == 1 and bars[0].feed == FeedTier.SIP_DELAYED
    b = bars[0]
    assert b.end == datetime(2026, 9, 17, 13, 45, tzinfo=UTC)
    assert b.retrievable_at >= b.end + timedelta(minutes=15) and b.retrievable_at >= NOW  # §5.1 availability semantics
    assert route.calls[0].request.url.params["feed"] == "sip"


@respx.mock
def test_pagination_and_request_budget():
    page1 = {
        "bars": {"AAPL": [{"t": "2026-09-16T04:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]},
        "next_page_token": "tok",
    }
    page2 = {
        "bars": {"MSFT": [{"t": "2026-09-16T04:00:00Z", "o": 2, "h": 2, "l": 2, "c": 2, "v": 2}]},
        "next_page_token": None,
    }
    respx.get(f"{MARKET_DATA_URL}/v2/stocks/bars").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    budget = RequestBudget(limit_per_min=200)
    sip, _ = make_market_data(client(), "basic", budget=budget, clock=lambda: NOW)
    bars = sip.bars(["AAPL", "MSFT"], "1Day", NOW - timedelta(days=5), NOW - timedelta(days=1))
    assert {b.symbol for b in bars} == {"AAPL", "MSFT"} and budget.used_last_minute == 2 and not budget.over_limit


def test_scan_request_budget_under_200_per_min():
    """~2,000 symbols: daily history (60 sessions) + one 15-minute pull + IEX quotes/trades ≈ 10+10+10+10 requests."""
    sip, iex = make_market_data(client(), "basic", clock=lambda: NOW)
    from tradeagent.adapters.alpaca.market_data import SYMBOLS_PER_REQUEST

    symbols = 2000
    requests_per_scan = 4 * -(-symbols // SYMBOLS_PER_REQUEST)
    assert requests_per_scan < 200


def test_plan_flip_selects_tiers():
    sip, iex = make_market_data(client(), "basic")
    assert (sip.tier, iex.tier, sip.feed, iex.feed) == (FeedTier.SIP_DELAYED, FeedTier.IEX_REALTIME, "sip", "iex")
    assert sip.embargo == timedelta(minutes=15) and iex.embargo == timedelta(0)
    rt, ref = make_market_data(client(), "algo_trader_plus")
    assert rt is ref and rt.tier == FeedTier.SIP_REALTIME and rt.embargo == timedelta(0)


@respx.mock
def test_latest_quotes_and_trades_iex():
    respx.get(f"{MARKET_DATA_URL}/v2/stocks/quotes/latest").mock(
        return_value=httpx.Response(
            200, json={"quotes": {"AAPL": {"t": "2026-09-17T14:29:50Z", "bp": 100.0, "bs": 3, "ap": 100.05, "as": 2}}}
        )
    )
    respx.get(f"{MARKET_DATA_URL}/v2/stocks/trades/latest").mock(
        return_value=httpx.Response(
            200, json={"trades": {"AAPL": {"t": "2026-09-17T14:29:55Z", "p": 100.02, "s": 100, "c": ["@"]}}}
        )
    )
    _, iex = make_market_data(client(), "basic", clock=lambda: NOW)
    q = iex.latest_quotes(["AAPL"])["AAPL"]
    t = iex.latest_trades(["AAPL"])["AAPL"]
    assert q.usable and float(q.half_spread) == pytest.approx(0.025) and q.feed == FeedTier.IEX_REALTIME
    assert float(t.price) == 100.02 and t.conditions == ("@",)
