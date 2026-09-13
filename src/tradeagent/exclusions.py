"""Ethical exclusion layer — §4.4. Pure, deterministic screening over the versioned denylist and SIC backstop.

Used at both enforcement points (scanner prefilter and risk desk). The LLM is never the enforcement layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tradeagent.domain.enums import UniverseStatus


@dataclass(frozen=True)
class ExclusionScreen:
    version: str
    denylist: dict[str, dict[str, Any]]
    allowlist: set[str]
    deny_sics: dict[int, str]
    default_deny_sics: dict[int, str]
    universe_exclude_sics: dict[int, str]

    @classmethod
    def from_config(cls, exclusions: dict[str, Any], sic_backstop: dict[str, Any]) -> "ExclusionScreen":
        deny_sics: dict[int, str] = {}
        for category, rows in sic_backstop.get("deny", {}).items():
            for row in rows:
                deny_sics[int(row["sic"])] = category
        return cls(
            version=f"{exclusions['version']}+sic:{sic_backstop['version']}",
            denylist={e["symbol"].upper(): e for e in exclusions.get("deny", [])},
            allowlist={e["symbol"].upper() if isinstance(e, dict) else str(e).upper() for e in exclusions.get("allow", [])},
            deny_sics=deny_sics,
            default_deny_sics={int(r["sic"]): r["title"] for r in sic_backstop.get("default_deny", [])},
            universe_exclude_sics={int(r["sic"]): r.get("reason", r["title"]) for r in sic_backstop.get("universe_exclude", [])},
        )

    def screen(self, symbol: str, sic: int | None) -> tuple[UniverseStatus, str | None]:
        """Order matters: explicit denylist > SIC deny > universe-exclude SIC > default-deny SIC (unless allowlisted)."""
        s = symbol.upper()
        if s in self.denylist:
            e = self.denylist[s]
            return UniverseStatus.EXCLUDED_ETHICAL, f"denylist:{e['category']}:{e['reason']}"
        if sic is not None and sic in self.deny_sics:
            return UniverseStatus.EXCLUDED_ETHICAL, f"sic:{sic}:{self.deny_sics[sic]}"
        if sic is not None and sic in self.universe_exclude_sics:
            return UniverseStatus.EXCLUDED_UNIVERSE, f"sic:{sic}:{self.universe_exclude_sics[sic]}"
        if sic is not None and sic in self.default_deny_sics and s not in self.allowlist:
            return UniverseStatus.NEEDS_ETHICAL_REVIEW, f"default_deny_sic:{sic}:{self.default_deny_sics[sic]}"
        return UniverseStatus.ELIGIBLE, None
