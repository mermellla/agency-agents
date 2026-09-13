"""Phase drift detection and opening (§13.2, ADR-0019)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

from tradeagent.persistence.db import Database
from tradeagent.versioning.registry import RegisteredVersions

PHASE_CHANGE_PATH = Path(__file__).resolve().parents[3] / "config" / "phase_change.yaml"
TRACKED = (
    "prompt_version",
    "config_version",
    "scanner_version",
    "qb_rules_version",
    "exclusion_list_version",
    "model_triage",
    "model_decision",
    "model_critique",
    "migration_version",
    "code_version",
)


class PhaseReasonMissing(RuntimeError):
    """Versions drifted from the open phase and config/phase_change.yaml does not justify it → halt PHASE_REASON_MISSING."""


@dataclass(frozen=True)
class VersionsInForce:
    prompt_version: str
    config_version: str
    scanner_version: str
    qb_rules_version: str
    exclusion_list_version: str
    model_triage: str
    model_decision: str
    model_critique: str
    migration_version: str
    code_version: str

    @classmethod
    def build(
        cls, rv: RegisteredVersions, models: tuple[str, str, str], migration_version: str, code_version: str
    ) -> VersionsInForce:
        return cls(
            rv.prompt_version,
            rv.config_version,
            rv.scanner_version,
            rv.qb_rules_version,
            rv.exclusion_list_version,
            models[0],
            models[1],
            models[2],
            migration_version,
            code_version,
        )


def load_phase_change(path: Path | None = None) -> dict[str, Any]:
    with (path or PHASE_CHANGE_PATH).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


def drift(open_phase: dict[str, Any], now: VersionsInForce) -> dict[str, tuple[Any, Any]]:
    d = asdict(now)
    return {k: (open_phase.get(k), d[k]) for k in TRACKED if open_phase.get(k) != d[k]}


def ensure_phase(
    db: Database, experiment_id: UUID, now: VersionsInForce, phase_change: dict[str, Any], at: datetime
) -> tuple[dict[str, Any], bool]:
    """Return (open phase, opened_new). Opens the first phase unconditionally; on drift requires a justified
    phase_change.yaml (non-empty reason, valid category, for_versions naming every drifted version key)."""
    current = db.open_phase(experiment_id)
    fields: dict[str, Any] = asdict(now)
    if current is None:
        fields.update(
            category=str(phase_change.get("category") or "initial"),
            reason=str(phase_change.get("reason") or "initial phase"),
            started_at=at,
            what_changed={"initial": True},
        )
        if fields["category"] not in ("initial", "strategy", "correctness", "safety"):
            fields["category"] = "initial"
        return db.insert_phase(experiment_id, fields), True
    changed = drift(current, now)
    if not changed:
        return current, False
    reason = str(phase_change.get("reason") or "").strip()
    category = str(phase_change.get("category") or "").strip()
    sequence = phase_change.get("sequence")
    declared = {k: v for k, v in (phase_change.get("for_versions") or {}).items() if v is not None}
    version_keys = [
        k
        for k in changed
        if k in ("prompt_version", "config_version", "scanner_version", "qb_rules_version", "exclusion_list_version")
    ]
    missing = [k for k in version_keys if str(declared.get(k)) != str(changed[k][1])]
    next_seq = int(current["seq"]) + 1
    fresh = sequence == next_seq  # a stale record (already used for an earlier phase) cannot justify this one
    if not fresh or not reason or category not in ("strategy", "correctness", "safety") or missing:
        raise PhaseReasonMissing(
            f"versions drifted {sorted(changed)} but config/phase_change.yaml does not justify phase {next_seq} "
            f"(sequence={sequence}, reason={'ok' if reason else 'EMPTY'}, category={category or 'EMPTY'}, "
            f"for_versions missing/mismatched={missing})"
        )
    db.close_phase(UUID(str(current["id"])), at)
    fields.update(
        category=category,
        reason=reason,
        started_at=at,
        what_changed={k: {"from": v[0], "to": v[1]} for k, v in changed.items()},
    )
    return db.insert_phase(experiment_id, fields), True
