#!/usr/bin/env python3
"""
Quick test to check if a symbol is tradeable on Aster.
"""
import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

async def check_aster_symbols():
    """Check what symbols are available on Aster."""
    url = "https://fapi.asterdex.com/fapi/v1/exchangeInfo"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                print(f"❌ Failed to fetch exchange info: {response.status}")
                return
            
            data = await response.json()
            symbols = data.get('symbols', [])
            
            print(f"\n📊 Total symbols on Aster: {len(symbols)}")
            
            # Filter trading symbols
            trading_symbols = [s for s in symbols if s.get('status') == 'TRADING']
            print(f"✅ Trading symbols: {len(trading_symbols)}")
            
            # Check for MON
            mon_symbols = [s for s in symbols if 'MON' in s.get('symbol', '')]
            if mon_symbols:
                print(f"\n🔍 Found symbols containing 'MON':")
                for s in mon_symbols:
                    print(f"  - {s.get('symbol')}: status={s.get('status')}, "
                          f"baseAsset={s.get('baseAsset')}, quoteAsset={s.get('quoteAsset')}")
            else:
                print(f"\n❌ No symbols containing 'MON' found on Aster")
            
            # Show first 20 trading symbols
            print(f"\n📋 First 20 TRADING symbols:")
            for s in trading_symbols[:20]:
                print(f"  - {s.get('symbol')} ({s.get('baseAsset')}/{s.get('quoteAsset')})")

if __name__ == "__main__":
    asyncio.run(check_aster_symbols())

