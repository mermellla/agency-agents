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

from tradeagent.domain.enums import ExecutionMode, HaltScope, PortfolioKind, ReconcileResult

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
