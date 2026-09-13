"""Shared fixtures. Database tests need a reachable PostgreSQL 16 with createdb rights, passed as
TRADEAGENT_TEST_ADMIN_URL (a libpq URL to the maintenance database). Unset → the db tests are skipped."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((REPO / "supabase" / "migrations").glob("*.sql"))
ADMIN_URL = os.environ.get("TRADEAGENT_TEST_ADMIN_URL", "")


def _psycopg():
    return pytest.importorskip("psycopg")


@pytest.fixture(scope="session")
def migrated_db_url():
    psycopg = _psycopg()
    if not ADMIN_URL:
        pytest.skip("TRADEAGENT_TEST_ADMIN_URL not set")
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL not reachable at {ADMIN_URL}: {exc}")
    name = f"tradeagent_test_{uuid.uuid4().hex[:8]}"
    admin.execute(f'create database "{name}"')
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"
    with psycopg.connect(url, autocommit=True) as conn:
        for f in MIGRATIONS:
            conn.execute(f.read_text())
    yield url
    admin.execute(f'drop database "{name}"')
    admin.close()


@pytest.fixture
def db(migrated_db_url):
    """One transaction per test, rolled back at the end, so tests never see each other's rows."""
    psycopg = _psycopg()
    with psycopg.connect(migrated_db_url) as conn:
        conn.execute("set search_path = trading, public")
        yield conn
        conn.rollback()


@pytest.fixture
def seed(db):
    """Experiment, versions, open phase, primary + shadow portfolios, one instrument, one scan/candidate."""
    from tests.seed import Seed

    return Seed.create(db)
