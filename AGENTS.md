# Repository Guidelines

## Project Structure & Module Organization
Core execution lives in `runbot.py` and `trading_bot.py`, orchestrating YAML strategy configs from `configs/` (generated via `trading_config/`). Strategies are layered under `strategies/`, with shared `components/`, base abstractions in `base_strategy/`, and live implementations in `implementations/` such as `funding_arbitrage/`. Exchange connectors reside in `exchange_clients/` (ships `py.typed`), while operational helpers and UI shells are in `helpers/`, `scripts/`, `dashboard/`, and `tui/`. The FastAPI backend sits in `funding_rate_service/`, sharing PostgreSQL state with funding arb operations. Tests live under `tests/`; public docs are in `docs/`, internal notes in `docs-internal/`.

## Build, Test, and Development Commands
Set up dependencies with `make install` (or `python -m venv venv && pip install -r requirements.txt && pip install -e './exchange_clients[all]'`). Replay a saved strategy via `python runbot.py --config configs/example_grid.yml`. Launch the funding API from `funding_rate_service/` using `uvicorn main:app --reload`. Run the full suite with `pytest`, add `-m integration` for cross-DEX coverage, or target specific cases via `pytest tests/path/test_module.py -k scenario`. Apply formatters before PRs: `black funding_rate_service` and `flake8`.

## Coding Style & Naming Conventions
Target Python 3.10+, four-space indentation, and PEP 8 defaults; `.flake8` allows 129-character lines. Keep modules lowercase with underscores, classes in PascalCase, configs and environment keys in uppercase snake_case. Preserve type hints for any public APIs, especially when touching `exchange_clients/`.

## Testing Guidelines
Write tests under `tests/` using the `test_*.py` pattern and mirror package layout. Use `pytest.mark.asyncio` for async paths and tag scope with `@pytest.mark.unit` or `@pytest.mark.integration`. Cover order lifecycle, funding calculations, and error handling whenever you adjust strategies or connectors.

## Commit & Pull Request Guidelines
Follow the repo history: concise, imperative summaries such as `strategies: tighten funding arb rebalance`. Reference changed configs or docs in the body and never leak secrets. PRs should explain motivation, list validation (`pytest`, manual runs), and include screenshots for TUI/dashboard tweaks. Link issues or tasks where applicable.

## Security & Configuration Tips
Populate `.env` from `env_example.txt` and keep API keys out of git. Sync local configs with production VPS settings before deployment, and store per-server overrides in `docs-internal/` or secure vaults. Strategy YAMLs in `configs/` must remain anonymized.
