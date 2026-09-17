"""Catalyst signal (§6.2 'significant news / material filings') — Jev-judged with a deterministic keyword fallback (ADR-0023)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from tradeagent.adapters.typesafe.jev import JevClient, JevUnavailable
from tradeagent.adapters.typesafe.questions import CATALYST_SET, catalyst_questions, catalyst_state
from tradeagent.domain.enums import LLMStage
from tradeagent.domain.models import Headline, LLMCall


@dataclass(frozen=True)
class CatalystVerdict:
    headline_id: str
    is_material: float
    direction: str
    kind: str
    time_horizon: float | None
    judged_by: str  # jev | keyword
    answers: dict[str, Any] = field(default_factory=dict)


@dataclass
class CatalystSignal:
    symbol: str
    strength: float  # 0..1: max material probability among catalysts
    direction: str  # bullish | bearish | mixed | none
    verdicts: list[CatalystVerdict]
    calls: list[LLMCall]
    unavailable_reason: str | None = None

    def as_signal(self) -> dict[str, Any]:
        return {
            "catalyst_strength": self.strength,
            "catalyst_direction": self.direction,
            "catalyst_judged_by": sorted({v.judged_by for v in self.verdicts}),
            "catalyst_verdicts": [
                {
                    "headline_id": v.headline_id,
                    "is_material": v.is_material,
                    "direction": v.direction,
                    "kind": v.kind,
                    "time_horizon": v.time_horizon,
                    "judged_by": v.judged_by,
                    "answers": v.answers,
                }
                for v in self.verdicts
            ],
            "unavailable_reason": self.unavailable_reason,
        }


class KeywordRule:
    def __init__(self, positive: Sequence[str], negative: Sequence[str]):
        self.pos = [re.compile(r"\b" + re.escape(p) + r"\b", re.I) for p in positive]
        self.neg = [re.compile(r"\b" + re.escape(n) + r"\b", re.I) for n in negative]

    def judge(self, h: Headline) -> CatalystVerdict:
        text = f"{h.headline} {h.summary}"
        p = any(r.search(text) for r in self.pos)
        n = any(r.search(text) for r in self.neg)
        if p and n:
            return CatalystVerdict(h.id, 0.6, "mixed", "other", None, "keyword")
        if p:
            return CatalystVerdict(h.id, 0.6, "bullish", "other", None, "keyword")
        if n:
            return CatalystVerdict(h.id, 0.6, "bearish", "other", None, "keyword")
        return CatalystVerdict(h.id, 0.0, "none", "none", None, "keyword")


class CatalystClassifier:
    def __init__(
        self,
        jev: JevClient | None,
        keyword: KeywordRule,
        classifier: str,
        material_threshold: float,
        max_headlines: int,
    ):
        self.jev = jev
        self.keyword = keyword
        self.classifier = classifier
        self.threshold = material_threshold
        self.max_headlines = max_headlines

    def classify(self, symbol: str, company_name: str | None, headlines: Sequence[Headline]) -> CatalystSignal:
        verdicts: list[CatalystVerdict] = []
        calls: list[LLMCall] = []
        unavailable: str | None = None
        use_jev = self.classifier == "jev" and self.jev is not None and self.jev.available
        if self.classifier == "jev" and not use_jev:
            unavailable = "jev_down"
        for h in list(headlines)[: self.max_headlines]:
            if use_jev:
                try:
                    assert self.jev is not None
                    j = self.jev.judge(
                        catalyst_state(symbol, company_name, h.headline, h.summary, h.source, h.created_at.isoformat()),
                        catalyst_questions(),
                        LLMStage.TRIAGE,
                        CATALYST_SET,
                    )
                    calls.append(j.call)
                    verdicts.append(
                        CatalystVerdict(
                            h.id,
                            j.noul("is_material"),
                            j.choice("direction"),
                            j.choice("kind"),
                            j.score("time_horizon"),
                            "jev",
                            j.answers,
                        )
                    )
                    continue
                except JevUnavailable:
                    use_jev = False
                    unavailable = "jev_down"
            verdicts.append(self.keyword.judge(h))
        material = [v for v in verdicts if v.is_material >= self.threshold]
        strength = max((v.is_material for v in material), default=0.0)
        if not material:
            direction = "none"
        else:
            dirs = {v.direction for v in material}
            direction = dirs.pop() if len(dirs) == 1 else "mixed"
        return CatalystSignal(symbol, strength, direction, verdicts, calls, unavailable)
