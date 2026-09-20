"""Historical SIP quotes/trades (ADR-0017 inputs) and cash-dividend parsing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from tradeagent.adapters.alpaca.client import MARKET_DATA_URL, AlpacaClient, AlpacaCredentials
from tradeagent.adapters.alpaca.corporate_actions import parse_dividends
from tradeagent.adapters.alpaca.market_data import EmbargoViolation, make_market_data

NOW = datetime(2026, 9, 17, 14, 30, tzinfo=UTC)


@respx.mock
def test_quotes_and_trades_history_paged_and_embargoed():
    q1 = {
        "quotes": [{"t": "2026-09-17T13:30:00.1Z", "bp": 100, "ap": 100.2, "bs": 2, "as": 3}],
        "next_page_token": "p2",
    }
    q2 = {
        "quotes": [
            {"t": "2026-09-17T13:30:01Z", "bp": 0, "ap": 100.2, "bs": 2, "as": 3},
            {"t": "2026-09-17T13:30:02Z", "bp": 100.1, "ap": 100.3, "bs": 1, "as": 1},
        ],
        "next_page_token": None,
    }
    qroute = respx.get(f"{MARKET_DATA_URL}/v2/stocks/AAPL/quotes").mock(
        side_effect=[httpx.Response(200, json=q1), httpx.Response(200, json=q2)]
    )
    troute = respx.get(f"{MARKET_DATA_URL}/v2/stocks/AAPL/trades").mock(
        return_value=httpx.Response(
            200,
            json={
                "trades": [{"t": "2026-09-17T13:30:00.5Z", "p": 100.1, "s": 50, "c": ["@"]}],
                "next_page_token": None,
            },
        )
    )
    sip, _ = make_market_data(AlpacaClient(AlpacaCredentials("k", "s")), "basic", clock=lambda: NOW)
    start, end = NOW - timedelta(hours=1), NOW - timedelta(minutes=30)
    qs = sip.quotes("AAPL", start, end)
    assert [str(q.bid) for q in qs] == ["100", "100.1"] and qs[0].usable and qs[0].half_spread == Decimal("0.1")
    assert (
        qroute.calls[1].request.url.params["page_token"] == "p2" and qroute.calls[0].request.url.params["feed"] == "sip"
    )
    ts = sip.trades("AAPL", start, end)
    assert ts[0].price == Decimal("100.1") and ts[0].conditions == ("@",) and troute.called
    with pytest.raises(EmbargoViolation):
        sip.quotes("AAPL", start, NOW - timedelta(minutes=5))


def test_parse_dividends():
    data = {
        "corporate_actions": {
            "cash_dividends": [
                {
                    "symbol": "AAPL",
                    "rate": 0.26,
                    "ex_date": "2026-09-16",
                    "payable_date": "2026-09-30",
                    "record_date": "2026-09-16",
                },
                {"symbol": "X", "rate": None, "ex_date": "2026-09-16"},
            ]
        }
    }
    ds = parse_dividends(data)
    assert len(ds) == 1 and ds[0].rate == Decimal("0.26") and ds[0].payable_date.isoformat() == "2026-09-30"
