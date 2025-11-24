# Live Position Viewer (WebSocket Edition)

View real-time funding arbitrage positions directly from the Control API using the new streaming WebSocket feed.

## Highlights

- ✅ **WebSocket marks:** prices update the moment BBO streams tick (no REST polling loops)
- ✅ **Auto discovery:** `--username` resolves the correct Control API port + stored API key from the database
- ✅ **Account aware:** automatically filters to the account tied to the running strategy (override with `--account`)
- ✅ **Split cadence:** static metrics (entry, funding, leverage) refresh slowly while marks refresh instantly
- ✅ **Rich TUI:** colorful, compact table rendered with [`rich`](https://github.com/Textualize/rich)

## Prerequisites

- Strategy must run with the Control API enabled (via Telegram bot or `CONTROL_API_ENABLED=1`)
- Database access (`DATABASE_URL`) and `CREDENTIAL_ENCRYPTION_KEY` are required for auto-discovery
- `pip install -r requirements.txt` (already includes `aiohttp`, `rich`, `databases`, `python-dotenv`)

## Usage

### Auto-detect everything (recommended)

```bash
# Resolve API key + control port for alice, filter to her only running account
python scripts/strategies/view_live_positions.py --username alice

# Specify account when the user has multiple simultaneous runs
python scripts/strategies/view_live_positions.py --username alice --account acc1
```

What happens under the hood:

1. Uses `DATABASE_URL` to find the user and running `strategy_runs`
2. Pulls the Control API port tied to the selected run
3. Reads the stored Telegram API key (`telegram_api_key_encrypted`) if available
4. Subscribes to `/api/v1/live/bbo` for tick-level price updates

### Manual overrides

```bash
# Explicit host/port/API key (legacy behavior)
python scripts/strategies/view_live_positions.py \
    --host 127.0.0.1 --port 8768 --api-key YOUR_API_KEY

# Change UI refresh cadence and slow-static refresh cycle
python scripts/strategies/view_live_positions.py \
    --username alice --refresh 0.5 --static-refresh 60
```

CLI flags overview:

| Flag | Description |
|------|-------------|
| `--username` | Fetch API key + port from DB (requires `DATABASE_URL`) |
| `--account` | Prefer a specific account when user has multiple runs |
| `--host/--port` | Manual Control API host/port (fallback if no username) |
| `--api-key` | Override API key resolution |
| `--refresh` | Table redraw interval (default 1s) |
| `--static-refresh` | Slow REST refresh interval for funding/entry data (default 30s) |

## How It Works

1. **REST fetch (slow lane):** periodically calls `/api/v1/positions` to refresh entry, funding, leverage, etc.
2. **WebSocket stream (fast lane):** subscribes to `/api/v1/live/bbo` which mirrors the strategy's WebSocket BBO events.
3. **Local synthesis:** viewer recalculates mark price + uPnL per leg using live bids/asks while preserving the slow metrics.

The Control API server now exposes a FastAPI websocket endpoint:

```
GET  /api/v1/positions      # unchanged (slow/static data)
WS   /api/v1/live/bbo       # new! push BBO events to any authorized client
```

## Example Output

```
╭────────────────────────── Live Positions (via Control API) ──────────────────────────╮
│ Account │ Symbol │ Exchange │ Side  │   Qty │    Entry │     Mark │    uPnL │ APY │ Age │
├─────────┼────────┼──────────┼───────┼───────┼──────────┼──────────┼─────────┼─────┼─────┤
│ acc1    │ ZEC    │ ASTER    │ long  │ 4.539 │ 549.9061 │ 592.3210 │ -$192.5 │ 7.6%│ 02:41│
│         │        │ LIGHTER  │ short │ 4.539 │ 549.6910 │ 591.3950 │ +$189.3 │-53% │     │
╰──────────────────────────────────────────────────────────────────────────────────────╯
```

Marks update instantly whenever the websocket emits a new bid/ask, so uPnL flickers in real time without hammering REST endpoints.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Missing API key` | Provide `--api-key` or set `CONTROL_API_KEY`. Auto-mode requires Telegram auth to have stored a key. |
| `No strategy runs` | Ensure the user has an active `strategy_runs` row with a non-null `control_api_port`. |
| `WebSocket error` | Control API might be offline. Check `trading_bot` logs for the control server status. |
| `Stale funding values` | Increase `--static-refresh` cadence if you need faster funding updates. |

## Related Tools

- [`scripts/strategies/check_strategy_logs.py`](./check_strategy_logs.py) – Stream stdout/stderr logs
- [`scripts/strategies/view_all_strategies.py`](./view_all_strategies.py) – Inspect running strategy runs & ports
- [`strategies/control/server.py`](../../strategies/control/server.py) – Control API server implementation

Enjoy the near tick-for-tick monitoring! If you need to expose the websocket to external consumers (e.g., dashboards), you already have a Control API endpoint ready to serve them. 💹
