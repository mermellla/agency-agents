"""End-to-end: synthetic inputs → Scanner.run → persist_scan → rows in scans/candidates/universe_memberships/regimes/llm_calls."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tests.seed import Seed
from tests.synthetic import bar15, daily
from tradeagent.config import load_settings
from tradeagent.domain.enums import BudgetBucket, FeedTier, LLMStage
from tradeagent.domain.models import Instrument, LLMCall, UniverseStatus
from tradeagent.persistence.db import Database, connect
from tradeagent.scanner.scanner import ScanInputs, Scanner

pytestmark = pytest.mark.db
NOW = datetime(2026, 9, 17, 14, 30, tzinfo=UTC)
DAY = date(2026, 9, 17)


def ok(sym: str) -> Instrument:
    return Instrument(symbol=sym, name=sym, tradable=True, fractionable=True, universe_status=UniverseStatus.ELIGIBLE)


def instruments() -> list[Instrument]:
    excluded = Instrument(
        symbol="XOM",
        tradable=True,
        fractionable=True,
        universe_status=UniverseStatus.EXCLUDED_ETHICAL,
        status_reason="denylist",
    )
    return [ok("UP"), ok("DN"), ok("FLAT"), excluded]


def test_scan_persists_all_rows(migrated_db_url):
    conn = connect(migrated_db_url)
    db = Database(conn)
    seed = Seed.create(conn)
    for sym in ("UP", "DN", "FLAT", "SPY"):
        conn.execute(
            "insert into instruments (symbol, tradable, fractionable, universe_status) values (%s, true, true, 'eligible') on conflict do nothing",
            (sym,),
        )
    conn.execute("insert into scanner_versions (version, content) values ('1.0.0', '{}') on conflict do nothing")
    settings = load_settings(env={})
    scanner = Scanner(
        settings.scanner,
        FeedTier.SIP_DELAYED,
        25,
        seed.versions["exclusion_list_version"],
        None,
        lambda: NOW + timedelta(seconds=5),
    )
    inputs = ScanInputs(
        kind="intraday",
        now=NOW,
        session_date=DAY,
        universe=instruments(),
        daily={
            "UP": daily("UP", drift=0.01, vol=0.004),
            "DN": daily("DN", drift=-0.01, vol=0.004),
            "FLAT": daily("FLAT", vol=0.003),
            "SPY": daily("SPY", n=90, drift=0.002, vol=0.004),
        },
        bars15={
            "UP": [
                bar15("UP", DAY, (9, 30), 100, 101, 99.5, 100.8),
                bar15("UP", DAY, (9, 45), 100.8, 102.5, 100.7, 102.3),
            ]
        },
        bars_end_at=NOW - timedelta(minutes=15),
    )
    out = scanner.run(inputs)
    r = out.result
    assert r.universe_size == 3 and [c.symbol for c in r.candidates][0] == "UP" and len(r.candidates) == 3
    assert "orb_sip_confirmed" in r.candidates[0].strategy_tags
    assert any("intraday_relative_volume" in s for s in r.signals_unavailable)
    for c in r.candidates:
        assert c.signal_observed_at >= c.signal_bar_time and c.signal_observed_at >= r.started_at  # §5.1
    with db.transaction():
        scan_id = db.persist_scan(
            out,
            seed.experiment_id,
            seed.phase_id,
            [{"domain": "bars_quotes", "source": "synthetic", "grade": "execution", "health": "ok"}],
            entries_halted=[],
        )
        db.insert_llm_call(
            LLMCall(
                stage=LLMStage.TRIAGE,
                bucket=BudgetBucket.ENTRIES,
                model="jev-latest",
                prompt_version=seed.versions["prompt_version"],
                tokens_in=90,
                tokens_out=4,
                cost_usd=0,
            ),
            seed.experiment_id,
            seed.phase_id,
            prompt_version=seed.versions["prompt_version"],
        )
    assert (
        conn.execute("select candidate_count, universe_size from scans where id = %s", (scan_id,)).fetchone()[
            "candidate_count"
        ]
        == 3
    )
    assert conn.execute("select count(*) from candidates where scan_id = %s", (scan_id,)).fetchone()["count"] == 3
    assert (
        conn.execute(
            "select status from universe_memberships where scan_id = %s and symbol = 'XOM'", (scan_id,)
        ).fetchone()["status"]
        == "excluded_ethical"
    )
    assert (
        conn.execute(
            "select regime from regimes where trade_date = %s and scanner_version = '1.0.0'", (DAY,)
        ).fetchone()
        is not None
    )
    assert (
        conn.execute(
            "select model, cost_usd from llm_calls where model = 'jev-latest' order by created_at desc limit 1"
        ).fetchone()["cost_usd"]
        == 0
    )
    top = conn.execute("select signals from candidates where scan_id = %s and rank = 1", (scan_id,)).fetchone()[
        "signals"
    ]
    assert top["intraday_setup"] == "ORB_SIP_CONFIRMED" and top["momentum_20d"] > 0
    conn.rollback()
    conn.close()
