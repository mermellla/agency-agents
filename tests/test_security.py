"""ADR-0022: trading state is inaccessible to Supabase client roles; only the worker role can read or write it."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.db
psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def roles_db_url(migrated_db_url):
    """Create the Supabase client roles locally (they pre-exist on Supabase) and re-apply the grants migration so the
    revokes take effect for them, exactly as they would on the hosted project."""
    from tests.conftest import MIGRATIONS

    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        for r in ("anon", "authenticated"):
            conn.execute(
                f"do $$ begin if not exists (select 1 from pg_roles where rolname = '{r}') then create role {r} nologin; end if; end $$"
            )
        conn.execute([m for m in MIGRATIONS if m.name.endswith("_security.sql")][0].read_text())
        conn.execute("alter role trading_worker login")
        conn.execute("alter role anon login")
        conn.execute("alter role authenticated login")
    return migrated_db_url


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_client_roles_cannot_touch_trading_state(roles_db_url, role):
    with psycopg.connect(roles_db_url, autocommit=True) as conn:
        conn.execute(f"set role {role}")
        for stmt in (
            "select count(*) from trading.decisions",
            "select count(*) from trading.cash_ledger",
            "insert into trading.halts (code, scope) values ('x', 'all')",
            "select trading.portfolio_available_cash(gen_random_uuid())",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)


def test_worker_role_has_no_delete(roles_db_url):
    with psycopg.connect(roles_db_url, autocommit=True) as conn:
        conn.execute("set role trading_worker")
        conn.execute("select count(*) from trading.decisions")  # allowed
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from trading.decisions")


def test_nothing_in_public_schema(roles_db_url):
    with psycopg.connect(roles_db_url) as conn:
        assert conn.execute("select count(*) from pg_tables where schemaname = 'public'").fetchone()[0] == 0
        assert (
            conn.execute(
                "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname like '%order%'"
            ).fetchone()[0]
            == 0
        )
