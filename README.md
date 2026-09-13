# trading-agent

Experimental short-term trading agent — Specification v2.3 (`docs/spec/SPEC-v2.3.md`, authoritative).

Status: **Phase 0 engineering package** (design, schema, models, interfaces, config, tests). No feature
implementation yet. Modes buildable: DRY_RUN, PAPER. LIVE is architected but locked out at every layer (ADR-0020).
No credentials live in this repository; secrets are Railway environment variables only.

Start at `docs/phase0/00-phase0-index.md`. Decisions: `docs/adr/`. Accepted spec amendments: `docs/spec/AMENDMENTS.md`.

## Running the checks
```
uv pip install -e ".[dev]"              # or: pip install -e ".[dev]"
export TRADEAGENT_TEST_ADMIN_URL='postgresql://<admin-user>:<password>@localhost:5432/postgres'   # PostgreSQL 16, createdb rights
python -m pytest -q                      # 97 tests; db tests create and drop a throw-away database
ruff check src tests && ruff format --check src tests
python -m mypy src                       # strict, pydantic plugin
detect-secrets scan --all-files --exclude-files '^\.git/'
```
