# Live Position Viewer

View real-time positions via the Control API with a beautiful Rich table display.

## Overview

The `view_live_positions.py` script fetches position data from a running strategy's Control API and displays it in a live-updating Rich table in your terminal.

## Features

- ✅ Real-time position monitoring (updates every 1-2 seconds)
- ✅ Fetches data via Control API (no direct process access needed)
- ✅ Beautiful Rich table with color-coded uPnL
- ✅ Shows all metrics: qty, entry, mark, uPnL, funding, APY, age
- ✅ Works from any terminal (local or SSH)
- ✅ No impact on running strategy

## Getting Your API Key

The Control API requires authentication. First, get your API key:

```bash
# Get API key for your username
python database/scripts/users/get_api_key.py --username YOUR_USERNAME

# Or get API key by Telegram user ID
python database/scripts/users/get_api_key.py --telegram-user-id YOUR_TELEGRAM_ID

# Interactive mode
python database/scripts/users/get_api_key.py --interactive
```

This will display your API key. Copy it for use with the viewer.

## Usage

### Basic Usage

```bash
# View positions with API key
python scripts/strategies/view_live_positions.py --port 8768 --api-key YOUR_API_KEY

# Or set as environment variable (recommended)
export CONTROL_API_KEY=your_api_key_here
python scripts/strategies/view_live_positions.py --port 8768
```

### Custom Refresh Interval

```bash
# Refresh every 2 seconds instead of 1 second
python scripts/strategies/view_live_positions.py --port 8768 --api-key YOUR_API_KEY --refresh 2
```

### Remote Server

```bash
# Connect to a remote VPS
python scripts/strategies/view_live_positions.py --host 192.168.1.100 --port 8768 --api-key YOUR_API_KEY
```

## How It Works

1. Strategy runs with `--enable-control-api` flag (automatically enabled via Telegram bot)
2. Control API exposes `/api/v1/positions` endpoint
3. Viewer script fetches position data every N seconds
4. Rich table displays data with real-time updates

## Requirements

- Strategy must be running with Control API enabled
- Default port is configured in `.env` as `CONTROL_API_PORT` (e.g., 8768)
- Requires `aiohttp` and `rich` packages (already in requirements.txt)

## Finding the Port

Each strategy has its own Control API port. You can find it:

1. **In Telegram**: Use `/status` command to see the API port
2. **In logs**: Check strategy logs for "Control API server started on http://127.0.0.1:XXXX"
3. **In config**: Check your strategy YAML config file for `control_api_port`

## Example Output

```
╭─────────────────────────── Live Positions (via Control API) ───────────────────────────╮
│ Symbol │ Exchange │ Side  │    Qty │    Entry │     Mark │    uPnL │ Funding │    APY │      Age │
├────────┼──────────┼───────┼────────┼──────────┼──────────┼─────────┼─────────┼────────┼──────────┤
│    ZEC │    ASTER │  long │ 0.0500 │ 582.3400 │ 582.4500 │  +$0.55 │    0.25 │  3.12% │ 02:15:30 │
│        │  LIGHTER │ short │ 0.0500 │ 582.4000 │ 582.4500 │  -$0.25 │   -0.15 │ -2.45% │          │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

## Troubleshooting

### "HTTP 401 Unauthorized"
- You need an API key to access the Control API
- Run `python database/scripts/users/get_api_key.py --username YOUR_USERNAME` to get your key
- Pass it with `--api-key` or set `CONTROL_API_KEY` environment variable

### "Connection error: Cannot connect to host"
- Check that the strategy is running with Control API enabled
- Verify the port number is correct
- Ensure firewall allows connections to the port (if remote)

### "No open positions"
- This is normal if the strategy hasn't opened any positions yet
- The viewer will continue running and show positions when they open

### "HTTP 404"
- Control API is running but the endpoint doesn't exist
- This might indicate an older version of the strategy that doesn't support the positions endpoint
- Update your strategy code to the latest version

## Comparison with Other Monitoring Tools

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `view_live_positions.py` | Real-time position viewer | VPS admin wants to monitor positions in real-time |
| `check_strategy_logs.py` | View log files | Debug errors or review historical logs |
| Position Monitor logs | Periodic snapshots | Already in logs every 60s, no setup needed |
| Control API `/positions` | Programmatic access | Building custom tools or integrations |

## See Also

- [scripts/strategies/check_strategy_logs.py](./check_strategy_logs.py) - View strategy logs
- [strategies/control/README.md](../../strategies/control/README.md) - Control API documentation
