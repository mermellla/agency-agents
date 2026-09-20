"""T-36 daily digest content, Jev tagging, and the notifications audit row; Resend transport."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import httpx
import respx
from psycopg.rows import dict_row

from tests.seed import NOW
from tests.test_jev import mock_transport
from tradeagent.adapters.email.resend import RESEND_URL, NullSender, ResendSender
from tradeagent.adapters.typesafe.jev import JevClient
from tradeagent.domain.enums import ExecutionMode, HaltScope
from tradeagent.ops.digest import build_digest
from tradeagent.ops.notifications import Notifier
from tradeagent.persistence.db import Database

TAGS = {
    "model": "jev-latest",
    "usage": {"input_tokens": 60, "output_tokens": 2},
    "answers": {
        "day_character": {
            "type": "choice",
            "choice": "eventful",
            "confidence": 0.8,
            "probabilities": {"eventful": 0.8, "routine": 0.2},
        },
        "owner_attention_needed": {"type": "noul", "noul": 0.9},
    },
}


def test_daily_digest(db, seed):
    db.row_factory = dict_row
    dbx = Database(db)
    d = seed.decision(created_at=NOW)
    seed.decision(status="rejected", reject_code="REJECTED_VIRTUAL_CASH", created_at=NOW + timedelta(minutes=1))
    o = seed.order(d)
    seed.transition(o, "VALIDATED")
    seed.transition(o, "FILL_PENDING_RECONSTRUCTION")
    seed.fill(o, fill_at=NOW + timedelta(minutes=5))
    dbx.conn.execute(
        "insert into positions (portfolio_id, experiment_id, opened_in_phase_id, symbol, entry_decision_id, opened_at, qty, avg_cost, working_invalidation_price, working_target_price, working_time_stop_at) values (%s, %s, %s, 'AAPL', %s, %s, 1, 100, 95, 110, %s)",
        (seed.primary_id, seed.experiment_id, seed.phase_id, d, NOW, NOW + timedelta(days=5)),
    )
    dbx.record_halt(seed.experiment_id, "MAX_ORDERS_PER_DAY", HaltScope.ENTRIES, {"n": 12})
    seen: list = []
    jev = JevClient("k", transport=mock_transport(TAGS, seen=seen))
    digest = build_digest(dbx, seed.experiment_id, date(2026, 9, 14), {"AAPL": Decimal("104")}, jev, Decimal("500"))
    assert digest.subject == "Daily digest 2026-09-14: eventful — attention needed"
    assert seen[0]["state"]["open_halts"] == 1 and seen[0]["state"]["decisions"] == 2
    p = {x["name"]: x for x in digest.payload["portfolios"]}
    assert (
        p["primary"]["cash"] == "500.000000" and p["primary"]["equity"] == "604.000000"
    )  # seed fill books no cash; 1 share at 104
    assert p["primary"]["positions"][0]["stop"] == "95.0000" and p["qb-1.0"]["equity"] == "500.000000"
    assert digest.payload["rejections_by_code"] == {"REJECTED_VIRTUAL_CASH": 1} and digest.payload["fills"] == 1
    assert digest.payload["open_halts"] == ["MAX_ORDERS_PER_DAY"] and digest.payload["tags"]["source"] == "jev"
    assert "AAPL qty 1" in digest.text and "REJECTED_VIRTUAL_CASH" in digest.text
    assert len(digest.calls) == 1 and digest.calls[0].cost_usd == 0
    # rules fallback without Jev
    plain = build_digest(dbx, seed.experiment_id, date(2026, 9, 14), {}, None, Decimal("500"))
    assert plain.payload["tags"] == {"day_character": "degraded", "owner_attention_needed": True, "source": "rules"}
    # the notification audit row
    sender = NullSender()
    notifier = Notifier(dbx, sender, "owner@example.test", ExecutionMode.DRY_RUN, 1)
    assert notifier.send("daily_digest", digest.subject, digest.text, digest.html, digest.payload)
    row = dbx.conn.execute("select * from notifications order by id desc limit 1").fetchone()
    assert (
        row["kind"] == "daily_digest"
        and row["status"] == "sent"
        and row["subject"].startswith("[TA:DRY_RUN:1] Daily digest")
    )
    assert row["payload"]["tags"]["day_character"] == "eventful"


@respx.mock
def test_resend_sender_posts_and_failures_are_recorded(db, seed):
    db.row_factory = dict_row
    dbx = Database(db)
    route = respx.post(RESEND_URL).mock(return_value=httpx.Response(200, json={"id": "msg_1"}))
    sender = ResendSender("test-key", "Trade Agent <agent@example.test>")
    notifier = Notifier(dbx, sender, "owner@example.test", ExecutionMode.PAPER, 2)
    assert notifier.send("halt", "HALT X", "body")
    req = route.calls[0].request
    body = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer test-key" and body["subject"] == "[TA:PAPER:2] HALT X"
    assert body["to"] == ["owner@example.test"] and body["from"].startswith("Trade Agent")
    route.mock(return_value=httpx.Response(500, text="boom"))
    assert not notifier.send("halt", "HALT Y", "body")
    rows = dbx.conn.execute("select provider_message_id, status from notifications order by id").fetchall()
    assert [(r["provider_message_id"], r["status"]) for r in rows] == [
        ("msg_1", "sent"),
        ("error:RuntimeError", "failed"),
    ]
