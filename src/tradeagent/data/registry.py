"""Source registry (§5.2): one interface per domain with primary and fallback, health checks, and the `source_status`
stamp recorded on every scan. Bars/quotes down halts new entries; any other domain degrades its signals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tradeagent.domain.enums import SourceHealth, TrustGrade
from tradeagent.domain.models import SourceStamp

HealthFn = Callable[[], SourceStamp]


@dataclass
class DomainEntry:
    domain: str
    primary: HealthFn
    fallback: HealthFn | None = None
    required_for_entries: bool = False
    last: list[SourceStamp] = field(default_factory=list)


class SourceRegistry:
    def __init__(self) -> None:
        self.domains: dict[str, DomainEntry] = {}

    def register(
        self, domain: str, primary: HealthFn, fallback: HealthFn | None = None, required_for_entries: bool = False
    ) -> None:
        self.domains[domain] = DomainEntry(domain, primary, fallback, required_for_entries)

    def refresh(self) -> list[SourceStamp]:
        stamps: list[SourceStamp] = []
        for entry in self.domains.values():
            entry.last = []
            for fn in (entry.primary, entry.fallback):
                if fn is None:
                    continue
                try:
                    s = fn()
                except Exception:
                    s = SourceStamp(
                        domain=entry.domain,
                        source="unknown",
                        grade=TrustGrade.RESEARCH,
                        observed_at=datetime.now(tz=UTC),
                        health=SourceHealth.DOWN,
                    )
                entry.last.append(s)
                stamps.append(s)
        return stamps

    def healthy_source(self, domain: str) -> SourceStamp | None:
        """First OK (or degraded) source in primary→fallback order; None when the domain is down."""
        for s in self.domains[domain].last:
            if s.health != SourceHealth.DOWN:
                return s
        return None

    def domain_down(self, domain: str) -> bool:
        return domain in self.domains and self.healthy_source(domain) is None

    def entries_halted(self) -> list[str]:
        return [d for d, e in self.domains.items() if e.required_for_entries and self.domain_down(d)]

    def as_json(self) -> list[dict[str, object]]:
        return [
            {
                "domain": s.domain,
                "source": s.source,
                "grade": s.grade.value,
                "health": s.health.value,
                "observed_at": s.observed_at.isoformat(),
            }
            for e in self.domains.values()
            for s in e.last
        ]
