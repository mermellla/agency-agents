"""Version computation and registration (ADR-0019)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tradeagent.config import Settings, canonical_hash
from tradeagent.persistence.db import Database

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
_SEMVER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)\.md$")


@dataclass(frozen=True)
class PromptVersion:
    version: str
    content_hash: str
    changelog: str
    files: tuple[str, ...]


def load_prompt_version(prompts_dir: Path | None = None) -> PromptVersion:
    prompts_dir = prompts_dir or PROMPTS_DIR
    """Highest semver across roles; hash covers every file at that version so a silent edit changes the hash."""
    found: dict[tuple[int, int, int], list[Path]] = {}
    for f in sorted(prompts_dir.glob("*/v*.md")):
        m = _SEMVER.match(f.name)
        if m:
            found.setdefault((int(m[1]), int(m[2]), int(m[3])), []).append(f)
    if not found:
        raise RuntimeError("PROMPT_VERSION_MISSING: no prompts/<role>/vX.Y.Z.md files")
    key = max(found)
    files = found[key]
    h = hashlib.sha256()
    changelog = ""
    for f in files:
        text = f.read_text(encoding="utf-8")
        h.update(f.name.encode())
        h.update(text.encode())
        fm = yaml.safe_load(text.split("---")[1]) if text.startswith("---") else {}
        changelog = changelog or str(fm.get("changelog", ""))
    return PromptVersion(
        ".".join(map(str, key)), h.hexdigest(), changelog, tuple(str(f.relative_to(prompts_dir)) for f in files)
    )


@dataclass(frozen=True)
class RegisteredVersions:
    prompt_version: str
    config_version: str
    scanner_version: str
    qb_rules_version: str
    exclusion_list_version: str


def collect_versions(settings: Settings, prompts_dir: Path | None = None) -> RegisteredVersions:
    pv = load_prompt_version(prompts_dir)
    v = settings.versions
    return RegisteredVersions(
        pv.version, v.config_version, v.scanner_version, v.qb_rules_version, v.exclusion_list_version
    )


def register_all(db: Database, settings: Settings, prompts_dir: Path | None = None) -> RegisteredVersions:
    """Upsert every registry row; a reused version string with different content raises VERSION_REUSED."""
    pv = load_prompt_version(prompts_dir)
    db.register_version("prompt_versions", pv.version, {}, pv.content_hash, {"changelog": pv.changelog})
    risk_raw: dict[str, Any] = settings.risk.model_dump(mode="json")
    db.register_version(
        "config_versions", settings.versions.config_version, {"risk_policy": risk_raw, "fees": settings.fees}, ""
    )
    db.register_version("scanner_versions", settings.versions.scanner_version, settings.scanner, "")
    db.register_version("qb_rules_versions", settings.versions.qb_rules_version, settings.qb_rules, "")
    excl_content = {"exclusions": settings.exclusions, "sic_backstop": settings.sic_backstop}
    db.register_version(
        "exclusion_list_versions",
        settings.versions.exclusion_list_version,
        excl_content,
        canonical_hash(excl_content),
        {"entry_count": len(settings.exclusions.get("deny", []))},
    )
    return RegisteredVersions(
        pv.version,
        settings.versions.config_version,
        settings.versions.scanner_version,
        settings.versions.qb_rules_version,
        settings.versions.exclusion_list_version,
    )
