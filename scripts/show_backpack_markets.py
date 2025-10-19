#!/usr/bin/env python3
"""
Fetch and print Backpack markets showing symbol/type info.

Requires the `bpx` SDK to be installed in the active environment
(`pip install bpx` or equivalent). Useful for discovering the exact
`symbol` strings needed when normalizing markets (e.g., `BTC_USDC_PERP`).
"""

import json
from pprint import pprint

from bpx.public import Public


def main() -> None:
    public = Public()
    markets = public.get_markets()

    # Some SDK methods return JSON text; normalize to Python objects.
    if isinstance(markets, str):
        markets = json.loads(markets)

    print(f"Total markets: {len(markets)}\n")
    for market in markets:
        symbol = market.get("symbol")
        market_type = market.get("marketType") or market.get("type")
        base = market.get("baseSymbol")
        quote = market.get("quoteSymbol")
        print(f"symbol={symbol}, type={market_type}, base={base}, quote={quote}")

    if markets:
        print("\nFirst market entry (raw):")
        pprint(markets[0])


if __name__ == "__main__":
    main()
