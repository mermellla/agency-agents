"""T-20 / ADR-0020: no live broker URL, no live key names, no secrets, no funding endpoints anywhere in code or config."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCAN_DIRS = [REPO / "src", REPO / "config", REPO / "supabase", REPO / "tests"]
FORBIDDEN = [
    re.compile(r"(?<!paper-)api\.alpaca\.markets"),  # live trading base URL
    re.compile(r"ALPACA_LIVE_(KEY|SECRET)"),  # live key pair is never read in this phase
    re.compile(r"/v1/(funding|transfers|ach_relationships)"),  # funding endpoints (§2 no deposits/withdrawals)
    re.compile(r"(sk-ant-|APCA-API-SECRET-KEY:\s*\S{10,}|re_[A-Za-z0-9]{20,})"),  # secret-looking literals
]


def test_no_live_or_secret_strings():
    hits = []
    for d in SCAN_DIRS:
        for p in d.rglob("*"):
            if (
                p.is_file()
                and p.suffix in {".py", ".yaml", ".yml", ".sql", ".toml"}
                and p.name != "test_lockout_grep.py"
            ):
                text = p.read_text(encoding="utf-8", errors="ignore")
                for rx in FORBIDDEN:
                    for m in rx.finditer(text):
                        hits.append(f"{p.relative_to(REPO)}: {m.group(0)}")
    assert hits == [], hits
