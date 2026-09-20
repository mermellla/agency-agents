"""§17 / ADR-0010 daily digest: equity and cash per portfolio, open positions with stops and time stops, decisions and
rejections by code, fills, closed trades, budget spent, halts and stop incidents. Jev tags the day (ADR-0023 analytics
tagging) for the subject line; the statistics are the record."""

from __future__ import annotations

import html as _html
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from tradeagent.adapters.alpaca.calendar import ET
from tradeagent.adapters.typesafe.jev import JevClient, JevUnavailable
from tradeagent.adapters.typesafe.questions import DIGEST_SET, digest_questions, digest_state
from tradeagent.domain.enums import LLMStage
from tradeagent.domain.models import LLMCall
from tradeagent.persistence.db import Database
from tradeagent.risk.sizing import equity as equity_of

log = logging.getLogger("tradeagent.digest")


@dataclass
class Digest:
    subject: str
    text: str
    html: str
    payload: dict[str, Any]
    calls: list[LLMCall] = field(default_factory=list)


def day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time(0, 0), tzinfo=ET).astimezone(UTC)
    return start, start + timedelta(days=1)


def build_digest(
    db: Database,
    experiment_id: UUID,
    trade_date: date,
    marks: dict[str, Decimal],
    jev: JevClient | None = None,
    equity_start: Decimal | None = None,
) -> Digest:
    start, end = day_bounds(trade_date)
    portfolios: list[dict[str, Any]] = []
    for p in db.portfolios(experiment_id):
        pid = UUID(str(p["id"]))
        cash = db.cash_balance(pid)
        positions = db.open_positions(pid)
        portfolios.append(
            {
                "name": str(p["name"]),
                "cash": str(cash),
                "equity": str(equity_of(cash, positions, marks)),
                "positions": [
                    {
                        "symbol": str(x["symbol"]),
                        "qty": str(x["qty"]),
                        "avg_cost": str(x["avg_cost"]),
                        "stop": str(x["working_invalidation_price"]),
                        "target": str(x["working_target_price"]),
                        "time_stop": x["working_time_stop_at"].isoformat() if x["working_time_stop_at"] else None,
                    }
                    for x in positions
                ],
            }
        )
    decisions = db.decisions_on(experiment_id, start, end)
    by_status: dict[str, int] = {}
    by_reject: dict[str, int] = {}
    for d in decisions:
        by_status[str(d["status"])] = by_status.get(str(d["status"]), 0) + 1
        if d["reject_code"]:
            by_reject[str(d["reject_code"])] = by_reject.get(str(d["reject_code"]), 0) + 1
    fills = db.fills_between(experiment_id, start, end)
    closed = db.closed_trades_between(experiment_id, start, end)
    halts = db.halts_between(experiment_id, start, end)
    open_halts = db.open_halts(experiment_id)
    incidents = db.conn_rows(
        "select position_id, incident_code, verification_result from stop_coverage where session_date = %s and incident_code is not null",
        (trade_date,),
    )
    budget = {
        str(r["bucket"]): {k: str(r[k]) for k in ("allowance_usd", "carried_in_usd", "spent_usd", "overage_usd")}
        for r in db.conn_rows(
            "select * from budget_ledger where experiment_id = %s and trade_date = %s", (experiment_id, trade_date)
        )
    }
    scans = db.conn_rows(
        "select count(*) as n from scans where experiment_id = %s and started_at >= %s and started_at < %s",
        (experiment_id, start, end),
    )[0]["n"]
    primary = next((p for p in portfolios if p["name"] == "primary"), None)
    eq_primary = Decimal(primary["equity"]) if primary else Decimal(0)
    change_pct = ((eq_primary - equity_start) / equity_start * 100).quantize(Decimal("0.01")) if equity_start else None
    entries = budget.get("entries")
    spent_pct = None
    if entries:
        allow = Decimal(entries["allowance_usd"]) + Decimal(entries["carried_in_usd"])
        spent_pct = float((Decimal(entries["spent_usd"]) / allow * 100).quantize(Decimal("0.1"))) if allow else None
    stats = {
        "scans": int(scans),
        "decisions": len(decisions),
        "rejections": sum(by_reject.values()),
        "fills": len(fills),
        "closed_trades": len(closed),
        "halts": len(halts),
        "open_halts": len(open_halts),
        "budget_entries_spent_pct": spent_pct,
        "equity_change_pct": float(change_pct) if change_pct is not None else None,
    }
    tags: dict[str, Any] = {"day_character": None, "owner_attention_needed": None, "source": "none"}
    calls: list[LLMCall] = []
    if jev is not None and jev.available:
        try:
            j = jev.judge(digest_state(stats), digest_questions(), LLMStage.DAILY_REVIEW, DIGEST_SET)
            tags = {
                "day_character": j.choice("day_character"),
                "owner_attention_needed": j.noul("owner_attention_needed") >= 0.5,
                "source": "jev",
            }
            calls.append(j.call)
        except JevUnavailable as exc:
            log.warning("digest tagging unavailable: %s", exc)
    if tags["day_character"] is None:  # deterministic fallback
        tags["day_character"] = "degraded" if open_halts else ("eventful" if halts or incidents else "routine")
        tags["owner_attention_needed"] = bool(open_halts or incidents)
        tags["source"] = "rules"
    subject = f"Daily digest {trade_date.isoformat()}: {tags['day_character']}"
    if tags["owner_attention_needed"]:
        subject += " — attention needed"

    lines = [f"Trading day {trade_date.isoformat()} ({tags['day_character']}, tagged by {tags['source']})", ""]
    for p in portfolios:
        lines.append(f"{p['name']}: equity {p['equity']} cash {p['cash']}")
        for x in p["positions"]:
            lines.append(
                f"  {x['symbol']} qty {x['qty']} @ {x['avg_cost']} stop {x['stop']} target {x['target']} time stop {x['time_stop']}"
            )
    lines += [
        "",
        f"Scans: {scans}. Decisions: {len(decisions)} {by_status}",
        f"Rejections by code: {by_reject or 'none'}",
    ]
    lines.append(
        f"Fills: {len(fills)}"
        + "".join(
            f"\n  {f['portfolio_name']} {f['side']} {f['qty']} {f['symbol']} @ {f['price']} ({f['fill_source']})"
            for f in fills
        )
    )
    lines.append(
        f"Closed trades: {len(closed)}"
        + "".join(
            f"\n  {c['symbol']} net {c['net_return_pct']}% after costs {c['net_return_after_computational_costs_pct']}% ({c['exit_reason']})"
            for c in closed
        )
    )
    lines.append(f"Budget: {budget or 'no rows'}")
    lines.append(
        f"Halts today: {[h['code'] for h in halts] or 'none'}; open halts: {[h['code'] for h in open_halts] or 'none'}"
    )
    lines.append(f"Stop incidents: {[i['incident_code'] for i in incidents] or 'none'}")
    text = "\n".join(lines)
    html_body = '<pre style="font-family:menlo,monospace;font-size:13px">' + _html.escape(text) + "</pre>"
    payload = {
        "trade_date": trade_date.isoformat(),
        "portfolios": portfolios,
        "decisions_by_status": by_status,
        "rejections_by_code": by_reject,
        "fills": len(fills),
        "closed_trades": len(closed),
        "budget": budget,
        "halts": [str(h["code"]) for h in halts],
        "open_halts": [str(h["code"]) for h in open_halts],
        "stop_incidents": [str(i["incident_code"]) for i in incidents],
        "stats": stats,
        "tags": tags,
    }
    return Digest(subject, text, html_body, payload, calls)
