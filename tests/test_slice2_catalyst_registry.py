"""ADR-0023 catalyst signal (Jev + keyword fallback); T-11 registry failover; T-19 import-graph rule."""

from __future__ import annotations

import importlib
import json
import pkgutil
import sys
from datetime import UTC, datetime

import httpx2

from tradeagent.adapters.typesafe.jev import JevClient
from tradeagent.config import load_settings
from tradeagent.data.registry import SourceRegistry
from tradeagent.domain.enums import SourceHealth, TrustGrade
from tradeagent.domain.models import Headline, SourceStamp
from tradeagent.scanner.signals.catalyst import CatalystClassifier, KeywordRule

CFG = load_settings(env={}).scanner["catalyst"]
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def kw() -> KeywordRule:
    return KeywordRule(CFG["keyword_fallback"]["positive"], CFG["keyword_fallback"]["negative"])


def h(i: str, text: str) -> Headline:
    return Headline(id=i, symbols=("ACME",), headline=text, summary="", source="benzinga", created_at=NOW, url="")


def jev_answers(material: float, direction: str, kind: str) -> dict:
    return {
        "model": "jev-latest",
        "usage": {"input_tokens": 90, "output_tokens": 4},
        "answers": {
            "is_material": {"type": "noul", "noul": material},
            "direction": {
                "type": "choice",
                "choice": direction,
                "confidence": 0.8,
                "probabilities": {direction: 0.8, "none": 0.2},
            },
            "kind": {"type": "choice", "choice": kind, "confidence": 0.7, "probabilities": {kind: 0.7, "none": 0.3}},
            "time_horizon": {
                "type": "score",
                "score": 2.0,
                "confidence": 0.5,
                "legend": {"0": "m", "1": "h", "2": "d", "3": "w"},
                "probabilities": {"2": 1.0},
            },
        },
    }


def test_keyword_fallback_rule():
    c = CatalystClassifier(None, kw(), "keyword", 0.6, 6)
    s = c.classify(
        "ACME",
        "Acme",
        [
            h("1", "Acme beats estimates and raises guidance"),
            h("2", "Acme faces lawsuit over recall"),
            h("3", "Top 10 stocks to watch"),
        ],
    )
    assert s.strength == 0.6 and s.direction == "mixed" and [v.judged_by for v in s.verdicts] == ["keyword"] * 3
    assert s.verdicts[2].is_material == 0.0 and s.calls == [] and s.unavailable_reason is None


def test_jev_path_records_calls_and_answers():
    responses = iter([jev_answers(0.92, "bullish", "guidance"), jev_answers(0.10, "none", "none")])

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert body["state"]["symbol"] == "ACME" and body["model"] == "jev-latest"
        return httpx2.Response(200, json=next(responses), headers={"x-typesafe-request-id": "req"})

    jev = JevClient("key", prompt_version="0.1.0", transport=httpx2.MockTransport(handler))
    c = CatalystClassifier(jev, kw(), "jev", 0.6, 6)
    s = c.classify("ACME", "Acme", [h("1", "Acme raises guidance"), h("2", "Top 10 stocks to watch")])
    assert (
        s.strength == 0.92
        and s.direction == "bullish"
        and len(s.calls) == 2
        and all(call.cost_usd == 0 for call in s.calls)
    )
    assert (
        s.verdicts[0].judged_by == "jev"
        and s.verdicts[0].kind == "guidance"
        and s.verdicts[0].answers["direction"]["probabilities"]["bullish"] == 0.8
    )
    sig = s.as_signal()
    assert sig["catalyst_judged_by"] == ["jev"] and sig["catalyst_verdicts"][1]["is_material"] == 0.10


def test_jev_down_falls_back_to_keywords_and_marks_unavailable():
    jev = JevClient(
        "key",
        transport=httpx2.MockTransport(
            lambda r: httpx2.Response(529, json={"error": "overloaded"}, headers={"x-typesafe-request-id": "req"})
        ),
    )
    c = CatalystClassifier(jev, kw(), "jev", 0.6, 6)
    s = c.classify("ACME", "Acme", [h("1", "Acme raises guidance"), h("2", "Acme misses")])
    assert (
        s.unavailable_reason == "jev_down"
        and {v.judged_by for v in s.verdicts} == {"keyword"}
        and s.direction == "mixed"
    )
    no_key = CatalystClassifier(JevClient(None), kw(), "jev", 0.6, 6)
    assert no_key.classify("ACME", None, [h("1", "Acme upgraded")]).unavailable_reason == "jev_down"


def stamp(domain: str, source: str, health: SourceHealth) -> SourceStamp:
    return SourceStamp(domain=domain, source=source, grade=TrustGrade.EXECUTION, observed_at=NOW, health=health)


def test_registry_failover_and_entry_halt():
    reg = SourceRegistry()
    reg.register(
        "bars_quotes",
        lambda: stamp("bars_quotes", "alpaca-sip", SourceHealth.DOWN),
        lambda: stamp("bars_quotes", "alpaca-iex", SourceHealth.OK),
        required_for_entries=True,
    )
    reg.register("news", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    reg.refresh()
    assert reg.healthy_source("bars_quotes").source == "alpaca-iex" and reg.entries_halted() == []
    assert reg.domain_down("news") and reg.healthy_source("news") is None
    reg2 = SourceRegistry()
    reg2.register(
        "bars_quotes", lambda: stamp("bars_quotes", "alpaca-sip", SourceHealth.DOWN), required_for_entries=True
    )
    reg2.refresh()
    assert reg2.entries_halted() == ["bars_quotes"]
    assert reg2.as_json()[0]["health"] == "down"


def test_scanner_modules_import_no_market_data_adapter():
    """T-19: swapping the market-data adapter (plan flip) touches no scanner module."""
    import tradeagent.scanner as pkg

    forbidden = {
        "tradeagent.adapters.alpaca.market_data",
        "tradeagent.adapters.alpaca.client",
        "tradeagent.adapters.alpaca.news",
        "tradeagent.adapters.alpaca.assets",
    }
    for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        if m.name.endswith(".runner"):
            continue  # the I/O runner is the composition seam, not scanner logic
        before = set(sys.modules)
        importlib.import_module(m.name)
        mod = sys.modules[m.name]
        imported = {
            v.__name__
            for v in vars(mod).values()
            if hasattr(v, "__module__") is False and hasattr(v, "__name__") and v.__name__ in forbidden
        }
        src_imports = {n for n in forbidden if n in open(mod.__file__).read()}
        assert not imported and not src_imports, (m.name, src_imports)
        del before
