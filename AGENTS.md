# Repository Guidelines

## Project Structure & Module Organization
- `runbot.py` and `trading_bot.py` orchestrate live strategies; YAML configs live in `configs/` and are generated via `trading_config/`.
- `strategies/` holds the layered strategy stack—`base_strategy`, shared `components/`, and live code under `implementations/` such as `funding_arbitrage/`.
- `exchange_clients/` packages typed DEX connectors (`py.typed` ships with it); CLI/UI helpers live in `helpers/`, `scripts/`, `dashboard/`, and `tui/`.
- `funding_rate_service/` provides the FastAPI backend and shares PostgreSQL state with strategy operations (`strategies/implementations/funding_arbitrage/operations/position_opener.py` and `strategies/implementations/funding_arbitrage/position_manager.py`).
- Tests are routed through `tests/` with discovery/warnings managed by `pytest.ini`; public docs live in `docs/`, internal work notes in `docs-internal/`.

## Build, Test, and Development Commands
- `make install` (or `python -m venv venv && pip install -r requirements.txt && pip install -e './exchange_clients[all]'`) sets up the virtualenv.
- `python runbot.py --config configs/example_grid.yml` executes a saved strategy; swap in your own config to replay.
- `cd funding_rate_service && uvicorn main:app --reload` launches the API service against your local Postgres.
- `pytest` exercises the suite; add `-m integration` for cross-DEX paths or `tests/path/test_module.py -k scenario` to target cases.
- `black funding_rate_service` and `flake8` enforce formatting; run both before opening a PR.

## Coding Style & Naming Conventions
- Target Python 3.10+, four-space indentation, and PEP 8 defaults; `.flake8` relaxes line length to 129 chars.
- Modules/files stay lowercase with underscores; classes are PascalCase; configs and environment keys remain uppercase snake_case.
- Preserve type hints—connectors expose `py.typed`, so prefer explicit typing for new public APIs.

## Testing Guidelines
- Place tests in `tests/` using the `test_*.py` pattern; mirror package structure when practical.
- Mark async cases with `@pytest.mark.asyncio`; tag `@pytest.mark.unit` or `@pytest.mark.integration` so CI filtering remains accurate.
- When touching strategies or connectors, add regression coverage for order lifecycle, funding calculations, and error paths.

## Commit & Pull Request Guidelines
- Follow the existing history: concise, imperative summaries (`strategies: tighten funding arb rebalance`) with focused scope.
- Reference related configs/docs in the commit body when they change, and keep secrets out of diffs.
- PRs should outline motivation, testing performed (`pytest`, manual run commands), and include screenshots for TUI/dashboard updates; link issues/tasks when available.

## Deployment & Environment
- Develop and validate changes locally, but mirror settings to the production VPS where bots run; sync configs and virtualenv packages before redeploying.
- Share PostgreSQL credentials and API keys via secure channels, populate `.env` from `env_example.txt`, and avoid committing secrets.
- Strategy YAMLs in `configs/` must stay anonymized; keep per-server overrides in `docs-internal/` or private vaults, not in git.
