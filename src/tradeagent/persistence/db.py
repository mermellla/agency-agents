"""Database access for the worker (ADR-0002, ADR-0022). One psycopg connection, `search_path = trading, public`,
explicit transactions. No DELETE statements exist anywhere in this package; the worker role could not run them anyway."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import psycopg.types.json
from psycopg.rows import dict_row

from tradeagent.domain.enums import (
    BrokerPolicyResult,
    ExecutionMode,
    HaltScope,
    OrderStatus,
    PortfolioKind,
    ReconcileResult,
)
from tradeagent.domain.models import Decision, Instrument, LLMCall, OrderRequest

# YAML config carries dates; jsonb payloads serialise them as ISO strings.
psycopg.types.json.set_json_dumps(lambda obj: json.dumps(obj, default=str, sort_keys=True))


def canonical_json(content: dict[str, Any]) -> dict[str, Any]:
    """The form a jsonb payload has after a database round-trip (dates → ISO strings)."""
    result: dict[str, Any] = json.loads(json.dumps(content, default=str, sort_keys=True))
    return result


REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"

# The newest migration's sentinel object: boot refuses to run against a schema that lacks it (§10.5, ADR-0019).
SCHEMA_SENTINEL: tuple[str, str, str] = ("20260913000011", "fees", "classification")

PORTFOLIO_SET: tuple[tuple[PortfolioKind, str, bool], ...] = (
    (PortfolioKind.LLM_PRIMARY, "primary", True),
    (PortfolioKind.QUANT_SHADOW, "qb-1.0", False),
    (PortfolioKind.BENCHMARK_CASH, "benchmark-cash", False),
    (PortfolioKind.BENCHMARK_SPY, "benchmark-spy", False),
    (PortfolioKind.BENCHMARK_VTI, "benchmark-vti", False),
    (PortfolioKind.CRITIQUE_SHADOW_PAIRED, "critique-paired", False),
    (PortfolioKind.CRITIQUE_SHADOW_PARALLEL, "critique-parallel", False),
    (PortfolioKind.DETERMINISTIC_EXIT_SHADOW, "deterministic-exit", False),
)
"""§12: every portfolio that runs from day one. Only the primary touches the broker (schema-enforced)."""


def connect(database_url: str) -> psycopg.Connection[dict[str, Any]]:
    conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=False)
    conn.execute("set search_path = trading, public")
    return conn


def latest_migration_version() -> str:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise RuntimeError("no migrations found")
    return files[-1].name.split("_", 1)[0]


class Database:
    """Slice 1 repository surface: bootstrap, versions, phases, halts, cash. Grows by slice (see `Ledger` Protocol)."""

    def __init__(self, conn: psycopg.Connection[dict[str, Any]]):
        self.conn = conn

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.conn.transaction():
            yield

    # ---- schema
    def schema_is_current(self) -> bool:
        _, table, column = SCHEMA_SENTINEL
        row = self.conn.execute(
            "select 1 from information_schema.columns where table_schema = 'trading' and table_name = %s and column_name = %s",
            (table, column),
        ).fetchone()
        return row is not None

    # ---- experiment and portfolios (§3.2, §12)
    def get_experiment(self, name: str) -> dict[str, Any] | None:
        return self.conn.execute("select * from experiments where name = %s", (name,)).fetchone()

    def create_experiment(
        self, name: str, mode: ExecutionMode, equity_start: Decimal, started_on: date
    ) -> dict[str, Any]:
        if mode == ExecutionMode.LIVE:  # belt and braces: the schema refuses it too (ADR-0020)
            raise RuntimeError("LIVE_LOCKED_OUT")
        row = self.conn.execute(
            "insert into experiments (name, execution_mode, equity_start_usd, started_on) values (%s, %s, %s, %s) returning *",
            (name, mode.value, equity_start, started_on),
        ).fetchone()
        assert row is not None
        return row

    def portfolios(self, experiment_id: UUID) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from portfolios where experiment_id = %s order by name", (experiment_id,)
        ).fetchall()

    def ensure_portfolios(self, experiment_id: UUID, equity_start: Decimal) -> list[dict[str, Any]]:
        existing = {p["name"] for p in self.portfolios(experiment_id)}
        for kind, name, touches_broker in PORTFOLIO_SET:
            if name not in existing:
                self.conn.execute(
                    "insert into portfolios (experiment_id, kind, name, touches_broker, equity_start_usd) values (%s, %s, %s, %s, %s)",
                    (experiment_id, kind.value, name, touches_broker, equity_start),
                )
        return self.portfolios(experiment_id)

    def ensure_initial_equity(
        self, experiment_id: UUID, phase_id: UUID, portfolio: dict[str, Any], at: datetime
    ) -> bool:
        """Book the §3.2 opening balance once per portfolio; idempotent by key. Returns True when a row was written."""
        key = f"initial_equity:{portfolio['id']}"
        exists = self.conn.execute("select 1 from cash_ledger where idempotency_key = %s", (key,)).fetchone()
        if exists:
            return False
        self.conn.execute(
            """insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, at, kind, amount_usd, balance_after_usd, idempotency_key, reason)
               values (%s, %s, %s, %s, 'initial_equity', %s, %s, %s, 'EXPERIMENT_EQUITY_START')""",
            (
                portfolio["id"],
                experiment_id,
                phase_id,
                at,
                portfolio["equity_start_usd"],
                portfolio["equity_start_usd"],
                key,
            ),
        )
        return True

    def cash_balance(self, portfolio_id: UUID) -> Decimal:
        row = self.conn.execute("select portfolio_cash_balance(%s) as b", (portfolio_id,)).fetchone()
        assert row is not None
        return Decimal(row["b"])

    def available_cash(self, portfolio_id: UUID) -> Decimal:
        row = self.conn.execute("select portfolio_available_cash(%s) as b", (portfolio_id,)).fetchone()
        assert row is not None
        return Decimal(row["b"])

    def verify_cash_chain(self, portfolio_id: UUID) -> bool:
        """ADR-0002 projection check: every row's balance equals the previous balance plus its amount."""
        rows = self.conn.execute(
            "select amount_usd, balance_after_usd from cash_ledger where portfolio_id = %s order by id", (portfolio_id,)
        ).fetchall()
        prev = Decimal(0)
        for r in rows:
            if Decimal(r["balance_after_usd"]) != prev + Decimal(r["amount_usd"]):
                return False
            prev = Decimal(r["balance_after_usd"])
        return True

    # ---- version registries (ADR-0019)
    def register_version(
        self, table: str, version: str, content: dict[str, Any], content_hash: str, extra: dict[str, Any] | None = None
    ) -> None:
        """Insert if absent; refuse a reused version string with different content (VERSION_REUSED)."""
        hash_col = "content_hash" if table in ("prompt_versions", "exclusion_list_versions") else None
        row = self.conn.execute(f"select * from {table} where version = %s", (version,)).fetchone()  # noqa: S608 (table from a fixed set)
        if row is not None:
            stored = row[hash_col] if hash_col else row["content"]
            current = content_hash if hash_col else canonical_json(content)
            if stored != current:
                raise RuntimeError(f"VERSION_REUSED: {table} {version} already registered with different content")
            return
        extra = extra or {}
        if table == "prompt_versions":
            self.conn.execute(
                "insert into prompt_versions (version, content_hash, changelog) values (%s, %s, %s)",
                (version, content_hash, extra.get("changelog", "")),
            )
        elif table == "exclusion_list_versions":
            self.conn.execute(
                "insert into exclusion_list_versions (version, content_hash, content, entry_count) values (%s, %s, %s, %s)",
                (version, content_hash, psycopg.types.json.Jsonb(content), extra.get("entry_count", 0)),
            )
        else:
            self.conn.execute(
                f"insert into {table} (version, content) values (%s, %s)",  # noqa: S608
                (version, psycopg.types.json.Jsonb(content)),
            )

    # ---- phases (§13.2)
    def open_phase(self, experiment_id: UUID) -> dict[str, Any] | None:
        return self.conn.execute(
            "select * from experiment_phases where experiment_id = %s and ended_at is null", (experiment_id,)
        ).fetchone()

    def close_phase(self, phase_id: UUID, at: datetime) -> None:
        self.conn.execute("update experiment_phases set ended_at = %s where id = %s", (at, phase_id))

    def insert_phase(self, experiment_id: UUID, fields: dict[str, Any]) -> dict[str, Any]:
        seq_row = self.conn.execute(
            "select coalesce(max(seq), 0) + 1 as seq from experiment_phases where experiment_id = %s", (experiment_id,)
        ).fetchone()
        assert seq_row is not None
        cols = {"experiment_id": experiment_id, "seq": seq_row["seq"], **fields}
        cols["what_changed"] = psycopg.types.json.Jsonb(cols.get("what_changed", {}))
        keys = list(cols)
        row = self.conn.execute(
            f"insert into experiment_phases ({', '.join(keys)}) values ({', '.join(['%s'] * len(keys))}) returning *",  # noqa: S608
            [cols[k] for k in keys],
        ).fetchone()
        assert row is not None
        return row

    # ---- halts (§8.7)
    def record_halt(self, experiment_id: UUID | None, code: str, scope: HaltScope, detail: dict[str, Any]) -> UUID:
        row = self.conn.execute(
            "insert into halts (experiment_id, code, scope, detail) values (%s, %s, %s, %s) returning id",
            (experiment_id, code, scope.value, psycopg.types.json.Jsonb(detail)),
        ).fetchone()
        assert row is not None
        return UUID(str(row["id"]))

    def open_halts(self, experiment_id: UUID | None) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from halts where cleared_at is null and (experiment_id = %s or experiment_id is null) order by at",
            (experiment_id,),
        ).fetchall()

    # ---- reconciliation and notifications
    def record_reconciliation(
        self, experiment_id: UUID, mode: ExecutionMode, result: ReconcileResult, diff: dict[str, Any], notes: str
    ) -> None:
        self.conn.execute(
            "insert into reconciliations (experiment_id, execution_mode, result, diff, notes) values (%s, %s, %s, %s, %s)",
            (experiment_id, mode.value, result.value, psycopg.types.json.Jsonb(diff), notes),
        )

    def record_notification(
        self, kind: str, recipient: str, subject: str, provider_message_id: str, status: str, payload: dict[str, Any]
    ) -> None:
        self.conn.execute(
            "insert into notifications (kind, recipient, subject, provider_message_id, status, payload) values (%s, %s, %s, %s, %s, %s)",
            (kind, recipient, subject, provider_message_id, status, psycopg.types.json.Jsonb(payload)),
        )

    # ---- market group (§6.4, §10.1) — Slice 2
    def upsert_instruments(self, instruments: list[Instrument], exclusion_list_version: str) -> None:
        for i in instruments:
            self.conn.execute(
                """insert into instruments (symbol, name, exchange, cik, sic, tradable, fractionable, universe_status, status_reason, exclusion_list_version, last_10k_filed_on, as_of)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                   on conflict (symbol) do update set name = excluded.name, exchange = excluded.exchange, cik = excluded.cik, sic = excluded.sic,
                     tradable = excluded.tradable, fractionable = excluded.fractionable, universe_status = excluded.universe_status,
                     status_reason = excluded.status_reason, exclusion_list_version = excluded.exclusion_list_version,
                     last_10k_filed_on = excluded.last_10k_filed_on, as_of = now()""",
                (
                    i.symbol,
                    i.name,
                    i.exchange,
                    i.cik,
                    i.sic,
                    i.tradable,
                    i.fractionable,
                    i.universe_status.value,
                    i.status_reason,
                    exclusion_list_version,
                    i.last_10k_filed_on,
                ),
            )

    def upsert_regime(self, trade_date: date, regime: str, scanner_version: str, inputs: dict[str, Any]) -> UUID:
        row = self.conn.execute(
            """insert into regimes (trade_date, regime, scanner_version, inputs) values (%s, %s, %s, %s)
               on conflict (trade_date, scanner_version) do update set regime = excluded.regime, inputs = excluded.inputs, computed_at = now() returning id""",
            (trade_date, regime, scanner_version, psycopg.types.json.Jsonb(inputs)),
        ).fetchone()
        assert row is not None
        return UUID(str(row["id"]))

    def insert_source_status(self, stamps: list[dict[str, Any]]) -> None:
        for s in stamps:
            self.conn.execute(
                "insert into source_status (domain, source, grade, health, detail) values (%s, %s, %s, %s, %s)",
                (s["domain"], s["source"], s["grade"], s["health"], psycopg.types.json.Jsonb(s)),
            )

    def persist_scan(
        self,
        out: Any,
        experiment_id: UUID,
        phase_id: UUID,
        source_status: list[dict[str, Any]],
        entries_halted: list[str],
    ) -> UUID:
        r = out.result
        regime_id = self.upsert_regime(r.started_at.date(), r.regime.value, r.scanner_version, out.regime_inputs)
        self.insert_source_status(source_status)
        self.conn.execute(
            """insert into scans (id, experiment_id, experiment_phase_id, kind, feed_tier, started_at, scanner_completed_at, bars_end_at, regime_id, scanner_version,
               exclusion_list_version, universe_size, candidate_count, source_status, signals_unavailable)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                r.scan_id,
                experiment_id,
                phase_id,
                r.kind,
                r.feed_tier.value,
                r.started_at,
                r.scanner_completed_at,
                r.bars_end_at,
                regime_id,
                r.scanner_version,
                r.exclusion_list_version,
                r.universe_size,
                len(r.candidates),
                psycopg.types.json.Jsonb({"sources": source_status, "entries_halted": entries_halted}),
                psycopg.types.json.Jsonb(r.signals_unavailable),
            ),
        )
        for inst in out.memberships:
            self.conn.execute(
                "insert into universe_memberships (scan_id, symbol, status, reason) values (%s, %s, %s, %s) on conflict do nothing",
                (r.scan_id, inst.symbol, inst.universe_status.value, inst.status_reason),
            )
        for c in r.candidates:
            sig = dict(out.signals.get(c.symbol, {}))
            self.conn.execute(
                """insert into candidates (id, scan_id, symbol, rank, composite_score, signals, strategy_tags, signal_bar_time, signal_observed_at, sip_signal_price,
                   sip_signal_timestamp, iex_price_at_scan, iex_quote_age_sec_at_scan) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    c.candidate_id,
                    c.scan_id,
                    c.symbol,
                    c.rank,
                    c.composite_score,
                    psycopg.types.json.Jsonb(sig),
                    c.strategy_tags,
                    c.signal_bar_time,
                    c.signal_observed_at,
                    c.sip_signal_price,
                    c.sip_signal_timestamp,
                    c.iex_price_at_scan,
                    c.iex_quote_age_sec_at_scan,
                ),
            )
        return UUID(str(r.scan_id))

    def insert_llm_call(
        self, call: LLMCall, experiment_id: UUID, phase_id: UUID, prompt_version: str, decision_id: UUID | None = None
    ) -> None:
        self.conn.execute(
            """insert into llm_calls (experiment_id, experiment_phase_id, decision_id, stage, bucket, model, prompt_version, tokens_in, tokens_out,
               tokens_cached_read, tokens_cached_write, cost_usd, latency_ms, request_hash, response_hash, status)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                experiment_id,
                phase_id,
                decision_id,
                call.stage.value,
                call.bucket.value,
                call.model,
                prompt_version,
                call.tokens_in,
                call.tokens_out,
                call.tokens_cached_read,
                call.tokens_cached_write,
                call.cost_usd,
                call.latency_ms,
                call.request_hash,
                call.response_hash,
                call.status,
            ),
        )

    # ---- execution group — Slice 3
    def primary_portfolio(self, experiment_id: UUID) -> dict[str, Any]:
        row = self.conn.execute(
            "select * from portfolios where experiment_id = %s and kind = 'llm_primary'", (experiment_id,)
        ).fetchone()
        assert row is not None
        return row

    def open_positions(self, portfolio_id: UUID) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from positions where portfolio_id = %s and status = 'open' order by symbol", (portfolio_id,)
        ).fetchall()

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        return self.conn.execute("select * from orders where client_order_id = %s", (client_order_id,)).fetchone()

    def order_by_broker_id(self, broker_order_id: str) -> dict[str, Any] | None:
        return self.conn.execute("select * from orders where broker_order_id = %s", (broker_order_id,)).fetchone()

    def open_broker_orders(self, portfolio_id: UUID) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from orders where portfolio_id = %s and not is_simulated and status in ('SUBMITTED', 'PARTIALLY_FILLED') order by created_at",
            (portfolio_id,),
        ).fetchall()

    def protective_orders(self, position_id: UUID) -> list[dict[str, Any]]:
        return self.conn.execute(
            """select * from orders where position_id = %s and purpose in ('protective_stop', 'stop_rearm', 'software_stop') and order_is_open(status) order by created_at""",
            (position_id,),
        ).fetchall()

    def record_order(
        self,
        req: OrderRequest,
        experiment_id: UUID,
        phase_id: UUID,
        position_id: UUID | None,
        reserved_notional: Decimal,
    ) -> UUID:
        row = self.conn.execute(
            """insert into orders (decision_id, portfolio_id, experiment_id, experiment_phase_id, position_id, client_order_id, leg_seq, purpose, symbol, side, order_type,
               time_in_force, qty, notional, limit_price, stop_price, take_profit_price, extended_hours, is_simulated, order_eligible_at, expires_at, reserved_notional_usd)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (
                req.decision_id,
                req.portfolio_id,
                experiment_id,
                phase_id,
                position_id,
                req.client_order_id,
                req.leg_seq,
                req.purpose.value,
                req.symbol,
                req.side.value,
                req.order_type.value,
                req.time_in_force.value,
                req.qty,
                req.notional,
                req.limit_price,
                req.stop_price,
                req.take_profit_price,
                req.extended_hours,
                req.is_simulated,
                req.order_eligible_at,
                req.expires_at,
                reserved_notional,
            ),
        ).fetchone()
        assert row is not None
        return UUID(str(row["id"]))

    def transition_order(
        self,
        order_id: UUID,
        to_status: OrderStatus,
        reason: str,
        broker_order_id: str | None = None,
        submitted_at: datetime | None = None,
    ) -> None:
        current = self.conn.execute("select status from orders where id = %s", (order_id,)).fetchone()
        assert current is not None
        if current["status"] == to_status.value:
            if broker_order_id:
                self.conn.execute(
                    "update orders set broker_order_id = coalesce(broker_order_id, %s) where id = %s",
                    (broker_order_id, order_id),
                )
            return
        self.conn.execute(
            "update orders set status = %s, status_reason = %s, broker_order_id = coalesce(%s, broker_order_id), submitted_at = coalesce(%s, submitted_at) where id = %s",
            (to_status.value, reason, broker_order_id, submitted_at, order_id),
        )

    def create_system_decision(
        self,
        experiment_id: UUID,
        phase_id: UUID,
        portfolio_id: UUID,
        symbol: str,
        decision: str,
        position_id: UUID | None,
        reason: str,
        versions: dict[str, str],
        at: datetime,
    ) -> UUID:
        row = self.conn.execute(
            """insert into decisions (experiment_id, experiment_phase_id, portfolio_id, position_id, origin, kind, status, decision, ticker, direction, trigger_reason,
               reason_for_exit_if_existing_position, signal_observed_at, risk_validation_completed_at, order_eligible_at,
               prompt_version, exclusion_list_version, config_version, scanner_version, qb_rules_version)
               values (%s, %s, %s, %s, 'system', 'system_exit', 'validated', %s, %s, 'long', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning decision_id""",
            (
                experiment_id,
                phase_id,
                portfolio_id,
                position_id,
                decision,
                symbol,
                reason,
                reason,
                at,
                at,
                at,
                versions["prompt_version"],
                versions["exclusion_list_version"],
                versions["config_version"],
                versions["scanner_version"],
                versions["qb_rules_version"],
            ),
        ).fetchone()
        assert row is not None
        return UUID(str(row["decision_id"]))

    def phase_versions(self, phase_id: UUID) -> dict[str, str]:
        row = self.conn.execute(
            "select prompt_version, exclusion_list_version, config_version, scanner_version, qb_rules_version from experiment_phases where id = %s",
            (phase_id,),
        ).fetchone()
        assert row is not None
        return {k: str(v) for k, v in row.items()}

    def record_stop_coverage(
        self, session_date: date, position_id: UUID, lot_id: UUID | None, order_id: UUID | None, **fields: Any
    ) -> None:
        cols = {
            "session_date": session_date,
            "position_id": position_id,
            "lot_id": lot_id,
            "order_id": order_id,
            **fields,
        }
        keys = list(cols)
        updates = ", ".join(f"{k} = excluded.{k}" for k in keys if k not in ("session_date", "position_id"))
        self.conn.execute(
            f"insert into stop_coverage ({', '.join(keys)}) values ({', '.join(['%s'] * len(keys))}) on conflict (session_date, position_id) do update set {updates}",  # noqa: S608
            [cols[k] for k in keys],
        )

    def record_broker_policy_check(
        self,
        experiment_id: UUID | None,
        mode: ExecutionMode,
        expected: dict[str, Any],
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        result: BrokerPolicyResult,
    ) -> None:
        self.conn.execute(
            "insert into broker_policy_checks (experiment_id, execution_mode, expected, observed_before, observed_after, result) values (%s, %s, %s, %s, %s, %s)",
            (
                experiment_id,
                mode.value,
                psycopg.types.json.Jsonb(expected),
                psycopg.types.json.Jsonb(before) if before is not None else None,
                psycopg.types.json.Jsonb(after) if after is not None else None,
                result.value,
            ),
        )

    def last_reconciled_event_at(self, experiment_id: UUID) -> datetime | None:
        row = self.conn.execute(
            "select max(last_reconciled_event_at) as t from reconciliations where experiment_id = %s and result in ('agree', 'repaired')",
            (experiment_id,),
        ).fetchone()
        return row["t"] if row else None

    def record_reconciliation_full(
        self,
        experiment_id: UUID,
        mode: ExecutionMode,
        result: ReconcileResult,
        diff: dict[str, Any],
        notes: str,
        replayed: int,
        last_event_at: datetime | None,
    ) -> None:
        self.conn.execute(
            "insert into reconciliations (experiment_id, execution_mode, result, events_replayed, last_reconciled_event_at, diff, notes) values (%s, %s, %s, %s, %s, %s, %s)",
            (experiment_id, mode.value, result.value, replayed, last_event_at, psycopg.types.json.Jsonb(diff), notes),
        )

    def apply_split(self, position_id: UUID, ratio: Decimal, at: datetime, reason: str) -> None:
        pos = self.conn.execute("select * from positions where id = %s", (position_id,)).fetchone()
        assert pos is not None
        inv = Decimal(1) / ratio
        self.conn.execute(
            """update positions set qty = qty * %s, avg_cost = avg_cost * %s, working_target_price = working_target_price * %s,
               working_invalidation_price = working_invalidation_price * %s, is_fractional = ((qty * %s) <> trunc(qty * %s)) where id = %s""",
            (ratio, inv, inv, inv, ratio, ratio, position_id),
        )
        for lot in self.conn.execute(
            "select * from lots where position_id = %s and qty_remaining > 0", (position_id,)
        ).fetchall():
            new_rem = Decimal(lot["qty_remaining"]) * ratio
            self.conn.execute(
                "update lots set qty_remaining = %s, cost_basis = cost_basis * %s, is_fractional = %s where id = %s",
                (new_rem, inv, new_rem != new_rem.to_integral_value(), lot["id"]),
            )
            self.conn.execute(
                "insert into lot_events (lot_id, at, kind, qty_delta, reason) values (%s, %s, 'split_adjust', %s, %s)",
                (lot["id"], at, new_rem - Decimal(lot["qty_remaining"]), reason),
            )

    def change_symbol(self, position_id: UUID, new_symbol: str, at: datetime) -> None:
        self.conn.execute(
            "insert into instruments (symbol, tradable, fractionable, universe_status, status_reason) values (%s, true, true, 'excluded_universe', 'symbol change; re-evaluated next universe build') on conflict do nothing",
            (new_symbol,),
        )
        self.conn.execute("update positions set symbol = %s where id = %s", (new_symbol, position_id))
        for lot in self.conn.execute("select id from lots where position_id = %s", (position_id,)).fetchall():
            self.conn.execute(
                "insert into lot_events (lot_id, at, kind, qty_delta, reason) values (%s, %s, 'symbol_change', 0, %s)",
                (lot["id"], at, f"to {new_symbol}"),
            )

    # ---- decisions, budget, analytics — Slice 4
    def insert_decision(self, d: Decision) -> UUID:
        """Persist a §7.6 decision after validation (status validated / rejected / no_action); the timeline is
        complete at insert so the write-once trigger never has to be revisited."""
        pr, t, f = d.proposal, d.timeline, d.forecast
        cols: dict[str, Any] = dict(
            decision_id=d.decision_id,
            experiment_id=d.experiment_id,
            experiment_phase_id=d.experiment_phase_id,
            portfolio_id=d.portfolio_id,
            scan_id=d.scan_id,
            candidate_id=d.candidate_id,
            data_snapshot_id=d.data_snapshot_id,
            position_id=d.position_id,
            origin=d.origin.value,
            kind=d.kind.value,
            trigger_reason=d.trigger_reason,
            status=d.status.value,
            reject_code=d.reject_code.value if d.reject_code else None,
            signal_observed_at=t.signal_observed_at,
            scanner_completed_at=t.scanner_completed_at,
            triage_completed_at=t.triage_completed_at,
            decision_completed_at=t.decision_completed_at,
            critique_completed_at=t.critique_completed_at,
            risk_validation_completed_at=t.risk_validation_completed_at,
            order_eligible_at=t.order_eligible_at,
            order_submitted_at=t.order_submitted_at,
            fill_at=t.fill_at,
            sip_signal_price=d.sip_signal_price,
            sip_signal_timestamp=d.sip_signal_timestamp,
            iex_price_at_triage=d.iex_price_at_triage,
            iex_price_at_decision=d.iex_price_at_decision,
            iex_price_at_order=d.iex_price_at_order,
            signal_to_decision_move_pct=d.signal_to_decision_move_pct,
            signal_to_order_move_pct=d.signal_to_order_move_pct,
            blind_interval_move_pct=d.blind_interval_move_pct,
            no_action_reason=pr.no_action_reason,
            decision=pr.decision.value,
            ticker=pr.ticker,
            strategy=pr.strategy,
            direction=pr.direction,
            proposed_notional=pr.proposed_notional,
            entry_type=pr.entry_type,
            entry_price_or_range=pr.entry_price_or_range,
            target_price=pr.target_price,
            invalidation_price=pr.invalidation_price,
            time_stop_at=pr.time_stop_at,
            expected_holding_period=pr.expected_holding_period,
            confidence=pr.confidence,
            forecast_event=f.forecast_event if f else None,
            forecast_target_price_at_entry=f.target_price_at_entry if f else None,
            forecast_invalidation_price_at_entry=f.invalidation_price_at_entry if f else None,
            forecast_time_stop_at_entry=f.time_stop_at_entry if f else None,
            probability_pre_critique=f.probability_pre_critique if f else pr.probability,
            probability_post_critique=f.probability_post_critique if f else None,
            critique_recommendation=d.critique.recommendation.value if d.critique else None,
            critique_changed_proposal=d.critique_changed_proposal,
            pre_critique_proposal=(
                psycopg.types.json.Jsonb(d.pre_critique_proposal.model_dump(mode="json"))
                if d.pre_critique_proposal
                else None
            ),
            expected_upside_percent=pr.expected_upside_percent,
            expected_downside_percent=pr.expected_downside_percent,
            expected_value=pr.expected_value,
            reward_risk_ratio=pr.reward_risk_ratio,
            catalyst=pr.catalyst,
            thesis=pr.thesis,
            supporting_evidence=psycopg.types.json.Jsonb([e.model_dump(mode="json") for e in pr.supporting_evidence]),
            contradicting_evidence=psycopg.types.json.Jsonb(
                [e.model_dump(mode="json") for e in pr.contradicting_evidence]
            ),
            earnings_in_window=pr.earnings_in_window,
            earnings_plan=pr.earnings_plan.value if pr.earnings_plan else None,
            earnings_plan_reason=pr.earnings_plan_reason,
            market_regime=d.market_regime.value if d.market_regime else None,
            reason_for_entry=pr.reason_for_entry,
            reason_for_exit_if_existing_position=pr.reason_for_exit_if_existing_position,
            conditions_to_exit_early=psycopg.types.json.Jsonb(list(pr.conditions_to_exit_early)),
            sources=psycopg.types.json.Jsonb([e.model_dump(mode="json") for e in pr.sources]),
            prompt_version=d.prompt_version,
            exclusion_list_version=d.exclusion_list_version,
            config_version=d.config_version,
            scanner_version=d.scanner_version,
            qb_rules_version=d.qb_rules_version,
            model_triage=d.model_triage,
            model_decision=d.model_decision,
            model_critique=d.model_critique,
            tokens_in=d.tokens_in,
            tokens_out=d.tokens_out,
            tokens_cached=d.tokens_cached,
            cost_usd=d.cost_usd,
        )
        keys = list(cols)
        self.conn.execute(
            f"insert into decisions ({', '.join(keys)}) values ({', '.join(['%s'] * len(keys))})",
            [cols[k] for k in keys],
        )
        return d.decision_id

    def decision(self, decision_id: UUID) -> dict[str, Any] | None:
        return self.conn.execute("select * from decisions where decision_id = %s", (decision_id,)).fetchone()

    def set_decision_status(self, decision_id: UUID, status: str, reject_code: str | None = None) -> None:
        self.conn.execute(
            "update decisions set status = %s, reject_code = coalesce(%s, reject_code) where decision_id = %s",
            (status, reject_code, decision_id),
        )

    def set_decision_submitted(self, decision_id: UUID, at: datetime) -> None:
        self.conn.execute(
            "update decisions set order_submitted_at = coalesce(order_submitted_at, %s) where decision_id = %s",
            (at, decision_id),
        )

    def set_decision_filled(self, decision_id: UUID, fill_at: datetime, position_id: UUID) -> None:
        self.conn.execute(
            "update decisions set fill_at = coalesce(fill_at, %s), status = 'executed', position_id = coalesce(position_id, %s) where decision_id = %s",
            (fill_at, position_id, decision_id),
        )

    def decisions_on(self, experiment_id: UUID, day_start: datetime, day_end: datetime) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from decisions where experiment_id = %s and created_at >= %s and created_at < %s order by created_at",
            (experiment_id, day_start, day_end),
        ).fetchall()

    def recent_decisions(self, portfolio_id: UUID, n: int) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select status, reject_code, origin from decisions where portfolio_id = %s and origin in ('llm', 'quant') and kind = 'entry' order by created_at desc limit %s",
            (portfolio_id, n),
        ).fetchall()

    # ---- orders and positions helpers
    def order(self, order_id: UUID) -> dict[str, Any] | None:
        return self.conn.execute("select * from orders where id = %s", (order_id,)).fetchone()

    def pending_reconstruction_orders(self, experiment_id: UUID) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from orders where experiment_id = %s and status = 'FILL_PENDING_RECONSTRUCTION' and is_simulated order by order_eligible_at, created_at",
            (experiment_id,),
        ).fetchall()

    def open_orders_for_symbol(
        self, portfolio_id: UUID, symbol: str, purposes: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from orders where portfolio_id = %s and symbol = %s and purpose = any(%s) and order_is_open(status) order by created_at",
            (portfolio_id, symbol, list(purposes)),
        ).fetchall()

    def open_orders_for_position(self, position_id: UUID) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from orders where position_id = %s and order_is_open(status) order by created_at", (position_id,)
        ).fetchall()

    def orders_for_decision(self, decision_id: UUID) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from orders where decision_id = %s order by leg_seq", (decision_id,)
        ).fetchall()

    def orders_created_between(self, portfolio_id: UUID, start: datetime, end: datetime) -> int:
        row = self.conn.execute(
            "select count(*) as n from orders where portfolio_id = %s and created_at >= %s and created_at < %s and purpose in ('entry', 'exit', 'reduce')",
            (portfolio_id, start, end),
        ).fetchone()
        assert row is not None
        return int(row["n"])

    def position(self, position_id: UUID) -> dict[str, Any] | None:
        return self.conn.execute("select * from positions where id = %s", (position_id,)).fetchone()

    def set_working_levels(
        self, position_id: UUID, target: Decimal | None, invalidation: Decimal | None, time_stop_at: datetime | None
    ) -> None:
        self.conn.execute(
            "update positions set working_target_price = %s, working_invalidation_price = %s, working_time_stop_at = %s where id = %s",
            (target, invalidation, time_stop_at, position_id),
        )

    def fills_for_position(self, position_id: UUID) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from fills where position_id = %s order by fill_at, created_at", (position_id,)
        ).fetchall()

    def fees_for_position(self, position_id: UUID) -> dict[str, Decimal]:
        rows = self.conn_rows(
            "select coalesce(f.classification, 'customer_debited') as c, coalesce(sum(f.amount_usd), 0) as s from fees f join fills x on x.id = f.fill_id where x.position_id = %s group by 1",
            (position_id,),
        )
        return {str(r["c"]): Decimal(r["s"]) for r in rows}

    def conn_rows(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        return self.conn.execute(sql, params).fetchall()

    def llm_cost_for_decisions(self, decision_ids: list[UUID]) -> Decimal:
        if not decision_ids:
            return Decimal(0)
        row = self.conn.execute(
            "select coalesce(sum(cost_usd), 0) as s from llm_calls where decision_id = any(%s)", (decision_ids,)
        ).fetchone()
        assert row is not None
        return Decimal(row["s"])

    def llm_calls_between(self, experiment_id: UUID, start: datetime, end: datetime, exclude_prefix: str) -> int:
        row = self.conn.execute(
            "select count(*) as n from llm_calls where experiment_id = %s and created_at >= %s and created_at < %s and model not like %s",
            (experiment_id, start, end, exclude_prefix + "%"),
        ).fetchone()
        assert row is not None
        return int(row["n"])

    def portfolio_by_kind(self, experiment_id: UUID, kind: str) -> dict[str, Any]:
        row = self.conn.execute(
            "select * from portfolios where experiment_id = %s and kind = %s", (experiment_id, kind)
        ).fetchone()
        assert row is not None, f"portfolio kind {kind} missing"
        return row

    def instrument(self, symbol: str) -> dict[str, Any] | None:
        return self.conn.execute("select * from instruments where symbol = %s", (symbol,)).fetchone()

    def append_cash_event(
        self,
        portfolio_id: UUID,
        experiment_id: UUID,
        phase_id: UUID,
        at: datetime,
        kind: str,
        amount: Decimal,
        idempotency_key: str,
        reason: str,
        settles_on: date | None = None,
        fill_id: UUID | None = None,
    ) -> bool:
        """Idempotent cash movement under the portfolio lock (dividends, adjustments). Returns True when written."""
        if self.conn.execute("select 1 from cash_ledger where idempotency_key = %s", (idempotency_key,)).fetchone():
            return False
        self.conn.execute("select lock_portfolio(%s)", (portfolio_id,))
        balance = self.cash_balance(portfolio_id) + amount
        self.conn.execute(
            """insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, at, kind, amount_usd, balance_after_usd, settles_on, fill_id, reason, idempotency_key)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                portfolio_id,
                experiment_id,
                phase_id,
                at,
                kind,
                amount,
                balance,
                settles_on,
                fill_id,
                reason,
                idempotency_key,
            ),
        )
        return True

    # ---- budget ledger (§9, ADR-0004)
    def budget_row(self, experiment_id: UUID, trade_date: date, bucket: str) -> dict[str, Any] | None:
        return self.conn.execute(
            "select * from budget_ledger where experiment_id = %s and trade_date = %s and bucket = %s",
            (experiment_id, trade_date, bucket),
        ).fetchone()

    def latest_budget_row_before(
        self, experiment_id: UUID, trade_date: date, bucket: str, month_start: date
    ) -> dict[str, Any] | None:
        return self.conn.execute(
            "select * from budget_ledger where experiment_id = %s and bucket = %s and trade_date < %s and trade_date >= %s order by trade_date desc limit 1",
            (experiment_id, bucket, trade_date, month_start),
        ).fetchone()

    def insert_budget_row(
        self, experiment_id: UUID, trade_date: date, bucket: str, allowance: Decimal, carried_in: Decimal
    ) -> dict[str, Any]:
        row = self.conn.execute(
            "insert into budget_ledger (experiment_id, trade_date, bucket, allowance_usd, carried_in_usd) values (%s, %s, %s, %s, %s) on conflict (experiment_id, trade_date, bucket) do update set allowance_usd = budget_ledger.allowance_usd returning *",
            (experiment_id, trade_date, bucket, allowance, carried_in),
        ).fetchone()
        assert row is not None
        return row

    def charge_budget(
        self,
        experiment_id: UUID,
        trade_date: date,
        bucket: str,
        cost: Decimal,
        overage: Decimal,
        exhausted_at: datetime | None,
    ) -> dict[str, Any]:
        row = self.conn.execute(
            "update budget_ledger set spent_usd = spent_usd + %s, overage_usd = %s, exhausted_at = coalesce(exhausted_at, %s) where experiment_id = %s and trade_date = %s and bucket = %s returning *",
            (cost, overage, exhausted_at, experiment_id, trade_date, bucket),
        ).fetchone()
        assert row is not None
        return row

    # ---- benchmarks and closed trades (§12, §13)
    def upsert_benchmark_price(self, symbol: str, trade_date: date, close: Decimal, source: str) -> None:
        self.conn.execute(
            "insert into benchmark_prices (symbol, trade_date, close, adjusted_close, source) values (%s, %s, %s, %s, %s) on conflict (symbol, trade_date) do update set close = excluded.close, source = excluded.source",
            (symbol, trade_date, close, close, source),
        )

    def benchmark_close_on_or_before(self, symbol: str, on: date) -> tuple[date, Decimal] | None:
        row = self.conn.execute(
            "select trade_date, close from benchmark_prices where symbol = %s and trade_date <= %s order by trade_date desc limit 1",
            (symbol, on),
        ).fetchone()
        return (row["trade_date"], Decimal(row["close"])) if row else None

    def insert_closed_trade(self, cols: dict[str, Any]) -> UUID:
        keys = list(cols)
        row = self.conn.execute(
            f"insert into closed_trades ({', '.join(keys)}) values ({', '.join(['%s'] * len(keys))}) on conflict (position_id) do nothing returning id",
            [cols[k] for k in keys],
        ).fetchone()
        if row is None:
            existing = self.conn.execute(
                "select id from closed_trades where position_id = %s", (cols["position_id"],)
            ).fetchone()
            assert existing is not None
            return UUID(str(existing["id"]))
        return UUID(str(row["id"]))

    def closed_trades_between(self, experiment_id: UUID, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from closed_trades where experiment_id = %s and exit_fill_at >= %s and exit_fill_at < %s order by exit_fill_at",
            (experiment_id, start, end),
        ).fetchall()

    def halts_between(self, experiment_id: UUID, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select * from halts where (experiment_id = %s or experiment_id is null) and at >= %s and at < %s order by at",
            (experiment_id, start, end),
        ).fetchall()

    def fills_between(self, experiment_id: UUID, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.conn.execute(
            "select f.*, p.name as portfolio_name from fills f join portfolios p on p.id = f.portfolio_id where f.experiment_id = %s and f.fill_at >= %s and f.fill_at < %s order by f.fill_at",
            (experiment_id, start, end),
        ).fetchall()

    def expire_order(self, order_id: UUID, reason: str) -> None:
        self.transition_order(order_id, OrderStatus.EXPIRED, reason)

    def create_benchmark_decision(
        self,
        experiment_id: UUID,
        phase_id: UUID,
        portfolio_id: UUID,
        symbol: str,
        notional: Decimal,
        versions: dict[str, str],
        at: datetime,
    ) -> UUID:
        """§12 buy-and-hold benchmark entry. The schema requires a forecast contract on every validated BUY; a
        benchmark has none, so the contract carries labelled placeholders (unreachable target, floor invalidation,
        far time stop) and origin=system / strategy=BENCHMARK keeps it out of calibration."""
        row = self.conn.execute(
            """insert into decisions (experiment_id, experiment_phase_id, portfolio_id, origin, kind, status, decision, ticker, direction, strategy,
               proposed_notional, entry_type, reason_for_entry, trigger_reason, signal_observed_at, risk_validation_completed_at, order_eligible_at,
               forecast_target_price_at_entry, forecast_invalidation_price_at_entry, forecast_time_stop_at_entry, probability_pre_critique,
               prompt_version, exclusion_list_version, config_version, scanner_version, qb_rules_version)
               values (%s, %s, %s, 'system', 'entry', 'validated', 'BUY', %s, 'long', 'BENCHMARK', %s, 'market',
               'benchmark buy at experiment start close (§12); forecast contract is a schema placeholder, excluded from calibration',
               'benchmark_start', %s, %s, %s, 1000000, 0.01, '2099-12-31T00:00:00Z', 0.5, %s, %s, %s, %s, %s) returning decision_id""",
            (
                experiment_id,
                phase_id,
                portfolio_id,
                symbol,
                notional,
                at,
                at,
                at,
                versions["prompt_version"],
                versions["exclusion_list_version"],
                versions["config_version"],
                versions["scanner_version"],
                versions["qb_rules_version"],
            ),
        ).fetchone()
        assert row is not None
        return UUID(str(row["decision_id"]))
