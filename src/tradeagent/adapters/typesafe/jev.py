"""Jev (TypeSafe System One) judgment engine — ADR-0023. Every call is accounted as an `LLMCall` with model, tokens
and cost (0 during the alpha, priced by `budget.jev_cost_per_1k_tokens_usd` afterwards)."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from typesafe_sdk import (
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    RetryPolicy,
    Score,
    ScoreAnswer,
    TypeSafeClient,
    TypeSafeError,
)

from tradeagent.domain.enums import BudgetBucket, LLMStage, SourceHealth, TrustGrade
from tradeagent.domain.models import LLMCall, SourceStamp

JEV_DOMAIN = "judgments"
JEV_SOURCE = "typesafe-jev"


class JevUnavailable(RuntimeError):
    """No key, or the service failed after retries. Consumers fall back to their deterministic rule."""


@dataclass(frozen=True)
class Judgment:
    """Answers keyed by question id: Noul → probability, Choice → (choice, confidence, probabilities), Score → (score, confidence, probabilities)."""

    answers: dict[str, dict[str, Any]]
    model: str
    request_id: str | None
    call: LLMCall

    def noul(self, key: str) -> float:
        return float(self.answers[key]["noul"])

    def choice(self, key: str) -> str:
        return str(self.answers[key]["choice"])

    def score(self, key: str) -> float:
        return float(self.answers[key]["score"])

    def confidence(self, key: str) -> float | None:
        v = self.answers[key].get("confidence")
        return None if v is None else float(v)


def _serialise_answer(a: NoulAnswer | ChoiceAnswer | ScoreAnswer) -> dict[str, Any]:
    if isinstance(a, NoulAnswer):
        return {"type": "noul", "noul": a.noul}
    if isinstance(a, ChoiceAnswer):
        return {
            "type": "choice",
            "choice": a.choice,
            "confidence": a.confidence,
            "probabilities": dict(a.probabilities),
        }
    return {
        "type": "score",
        "score": a.score,
        "confidence": a.confidence,
        "probabilities": {str(k): v for k, v in a.probabilities.items()},
    }


class JevClient:
    domain = JEV_DOMAIN
    source = JEV_SOURCE

    def __init__(
        self,
        api_key: str | None,
        model: str = "jev-latest",
        prompt_version: str = "0.0.0",
        cost_per_1k_tokens_usd: Decimal = Decimal("0"),
        transport: Any | None = None,
        timeout: float = 10.0,
    ):
        self.model = model
        self.prompt_version = prompt_version
        self.cost_per_1k = cost_per_1k_tokens_usd
        self._client: TypeSafeClient | None = None
        if api_key:
            self._client = TypeSafeClient(
                api_key=api_key,
                model=model,
                transport=transport,
                timeout=timeout,
                retry=RetryPolicy(max_retries=2, timeout=timeout),
            )
        self.last_health: SourceStamp | None = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def judge(
        self, state: dict[str, Any], questions: dict[str, Noul | Choice | Score], stage: LLMStage, question_set: str
    ) -> Judgment:
        if self._client is None:
            raise JevUnavailable("TYPESAFE_API_KEY not configured")
        started = time.perf_counter()
        request_hash = hashlib.sha256(
            json.dumps({"set": question_set, "state": state}, sort_keys=True, default=str).encode()
        ).hexdigest()
        try:
            resp = self._client.system_one(state, questions)
        except (
            TypeSafeError
        ) as exc:  # rate limit, overload, auth, connection: all become "unavailable" for the consumer
            self.last_health = SourceStamp(
                domain=JEV_DOMAIN,
                source=JEV_SOURCE,
                grade=TrustGrade.RESEARCH,
                observed_at=datetime.now(tz=UTC),
                health=SourceHealth.DOWN,
            )
            raise JevUnavailable(f"{type(exc).__name__}: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        answers = {k: _serialise_answer(v) for k, v in resp.answers.items()}
        tokens_in = int(resp.usage.input_tokens or 0)
        tokens_out = int(resp.usage.output_tokens or 0)
        cost = (Decimal(tokens_in + tokens_out) / Decimal(1000) * self.cost_per_1k).quantize(Decimal("0.000001"))
        call = LLMCall(
            stage=stage,
            bucket=BudgetBucket.ENTRIES
            if stage in (LLMStage.TRIAGE, LLMStage.DECISION, LLMStage.CRITIQUE)
            else BudgetBucket.EXITS_REVIEWS,
            model=resp.model or self.model,
            prompt_version=self.prompt_version,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            latency_ms=latency_ms,
            request_hash=request_hash,
            response_hash=hashlib.sha256(json.dumps(answers, sort_keys=True).encode()).hexdigest(),
        )
        self.last_health = SourceStamp(
            domain=JEV_DOMAIN,
            source=JEV_SOURCE,
            grade=TrustGrade.RESEARCH,
            observed_at=datetime.now(tz=UTC),
            health=SourceHealth.OK,
        )
        request_id = getattr(resp, "request_id", None)
        return Judgment(answers, resp.model or self.model, str(request_id) if request_id else None, call)

    def health(self) -> SourceStamp:
        now = datetime.now(tz=UTC)
        if self._client is None:
            return SourceStamp(
                domain=JEV_DOMAIN,
                source=JEV_SOURCE,
                grade=TrustGrade.RESEARCH,
                observed_at=now,
                health=SourceHealth.DOWN,
            )
        return self.last_health or SourceStamp(
            domain=JEV_DOMAIN, source=JEV_SOURCE, grade=TrustGrade.RESEARCH, observed_at=now, health=SourceHealth.OK
        )
