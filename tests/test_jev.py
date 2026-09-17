"""ADR-0023: Jev adapter against a mock transport; unavailable without a key; accounting rows."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx2
import pytest

from tradeagent.adapters.typesafe.jev import JevClient, JevUnavailable
from tradeagent.adapters.typesafe.questions import catalyst_questions, catalyst_state
from tradeagent.domain.enums import LLMStage

ANSWER = {
    "model": "jev-latest",
    "usage": {"input_tokens": 120, "output_tokens": 4},
    "answers": {
        "is_material": {"type": "noul", "noul": 0.91},
        "direction": {
            "type": "choice",
            "choice": "bullish",
            "confidence": 0.8,
            "probabilities": {"bullish": 0.85, "bearish": 0.05, "mixed": 0.05, "none": 0.05},
        },
        "kind": {
            "type": "choice",
            "choice": "guidance",
            "confidence": 0.7,
            "probabilities": {"guidance": 0.7, "earnings": 0.2, "other": 0.1},
        },
        "time_horizon": {
            "type": "score",
            "score": 2.3,
            "confidence": 0.6,
            "legend": {"0": "Minutes", "1": "Hours", "2": "Days", "3": "Weeks"},
            "probabilities": {"0": 0.05, "1": 0.1, "2": 0.35, "3": 0.5},
        },
    },
}


def mock_transport(payload: dict, status: int = 200, seen: list | None = None) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx2.Response(status, json=payload, headers={"x-typesafe-request-id": "req-1"})

    return httpx2.MockTransport(handler)


def test_judge_returns_typed_answers_and_accounting():
    seen: list = []
    jev = JevClient("key", prompt_version="0.1.0", transport=mock_transport(ANSWER, seen=seen))
    state = catalyst_state(
        "ACME", "Acme Corp", "Acme raises full-year guidance", None, "benzinga", "2026-09-17T12:00:00Z"
    )
    j = jev.judge(state, catalyst_questions(), LLMStage.TRIAGE, "catalyst.v1")
    assert j.noul("is_material") == pytest.approx(0.91)
    assert j.choice("direction") == "bullish" and j.choice("kind") == "guidance"
    assert j.score("time_horizon") == pytest.approx(2.3) and j.confidence("time_horizon") == pytest.approx(0.6)
    assert (
        j.call.model == "jev-latest"
        and j.call.tokens_in == 120
        and j.call.tokens_out == 4
        and j.call.cost_usd == Decimal("0")
    )
    assert j.call.prompt_version == "0.1.0" and j.call.request_hash and j.call.response_hash
    body = seen[0]
    assert body["state"]["symbol"] == "ACME" and set(body["questions"]) == {
        "is_material",
        "direction",
        "kind",
        "time_horizon",
    }
    assert "none" in body["questions"]["direction"]["criteria"]  # every Choice has a no-match option


def test_cost_applies_when_priced():
    jev = JevClient("key", cost_per_1k_tokens_usd=Decimal("0.5"), transport=mock_transport(ANSWER))
    j = jev.judge({"x": "y"}, catalyst_questions(), LLMStage.TRIAGE, "catalyst.v1")
    assert j.call.cost_usd == Decimal("0.062")  # 124 tokens × $0.5 / 1k


def test_unavailable_without_key_and_on_error():
    jev = JevClient(None)
    assert not jev.available and jev.health().health.value == "down"
    with pytest.raises(JevUnavailable):
        jev.judge({"x": "y"}, catalyst_questions(), LLMStage.TRIAGE, "catalyst.v1")
    bad = JevClient("key", transport=mock_transport({"error": "overloaded"}, status=529))
    with pytest.raises(JevUnavailable):
        bad.judge({"x": "y"}, catalyst_questions(), LLMStage.TRIAGE, "catalyst.v1")
    assert bad.health().health.value == "down"
