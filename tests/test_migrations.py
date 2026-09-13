import pytest

pytestmark = pytest.mark.db

SPEC_TABLES = {
    # §10.1
    "experiments",
    "experiment_phases",
    "portfolios",
    "prompt_versions",
    "config_versions",
    "scanner_versions",
    "qb_rules_versions",
    "exclusion_list_versions",
    "scans",
    "candidates",
    "candidate_outcomes",
    "regimes",
    "source_status",
    "benchmark_prices",
    "decisions",
    "llm_calls",
    "data_snapshots",
    "orders",
    "order_events",
    "fills",
    "lots",
    "cash_ledger",
    "fees",
    "closed_trades",
}


def test_all_spec_tables_exist(db):
    rows = db.execute("select tablename from pg_tables where schemaname = 'trading'").fetchall()
    have = {r[0] for r in rows}
    assert SPEC_TABLES <= have, SPEC_TABLES - have
    assert (
        db.execute("select count(*) from pg_tables where schemaname = 'public'").fetchone()[0] == 0
    )  # nothing in the exposed schema


def test_rls_enabled_everywhere(db):
    rows = db.execute(
        "select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'trading' and relkind = 'r' and not relrowsecurity"
    ).fetchall()
    assert rows == []


def test_enums_match_python(db):
    from tradeagent.domain.enums import ENUM_TABLE

    for pg_name, py_enum in ENUM_TABLE.items():
        rows = db.execute(
            "select e.enumlabel from pg_enum e join pg_type t on t.oid = e.enumtypid where t.typname = %s order by e.enumsortorder",
            (pg_name,),
        ).fetchall()
        assert {r[0] for r in rows} == {m.value for m in py_enum}, pg_name
