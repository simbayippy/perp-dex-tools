#!/usr/bin/env python3
"""
Check Lighter OI Data

View the OI data stored in the database for Lighter exchange.
Helps diagnose if the max_oi filter is working with correct data.

Usage:
    python check_lighter_oi_data.py              # Show all symbols
    python check_lighter_oi_data.py BTC          # Show specific symbol
    python check_lighter_oi_data.py --compare    # Compare DB vs API
"""

import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Add project root to path (go up one level from scripts/ to project root)
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from database.connection import database


async def check_lighter_oi_data(symbol_filter=None, compare_with_api=False):
    """
    Check OI data for Lighter exchange from the database
    
    Args:
        symbol_filter: Optional symbol to filter (e.g., "BTC")
        compare_with_api: If True, fetch current data from API and compare
    """
    await database.connect()
    
    try:
        print("\n" + "="*80)
        print("🔍 Lighter Exchange - Open Interest Data Check")
        print("="*80 + "\n")
        
        # Query to get latest OI data for Lighter
        query = """
            SELECT 
                s.symbol,
                ds.volume_24h,
                ds.open_interest_usd,
                ds.updated_at,
                NOW() - ds.updated_at as age
            FROM dex_symbols ds
            JOIN dexes d ON ds.dex_id = d.id
            JOIN symbols s ON ds.symbol_id = s.id
            WHERE d.name = 'lighter'
        """
        
        params = {}
        
        if symbol_filter:
            query += " AND s.symbol = :symbol"
            params["symbol"] = symbol_filter.upper()
        
        query += " ORDER BY ds.open_interest_usd DESC NULLS LAST"
        
        rows = await database.fetch_all(query, values=params if params else None)
        
        if not rows:
            print("❌ No data found for Lighter exchange")
            if symbol_filter:
                print(f"   (Symbol filter: {symbol_filter})")
            return
        
        # Prepare table data
        table_data = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        for row in rows:
            symbol = row['symbol']
            volume = row['volume_24h']
            oi = row['open_interest_usd']
            updated_at = row['updated_at']
            
            # Calculate age
            if updated_at:
                if updated_at.tzinfo is None:
                    updated_at_utc = updated_at
                else:
                    updated_at_utc = updated_at.astimezone(timezone.utc).replace(tzinfo=None)
                age = now - updated_at_utc
                age_str = f"{int(age.total_seconds())}s ago"
                
                # Warn if stale (> 2 minutes)
                if age.total_seconds() > 120:
                    age_str = f"⚠️  {age_str} (STALE)"
            else:
                age_str = "Unknown"
            
            # Format values
            volume_str = f"${volume:,.0f}" if volume else "N/A"
            oi_str = f"${oi:,.0f}" if oi else "N/A"
            
            # Check against max_oi cap
            max_oi_cap = Decimal("1000000")  # Your configured cap
            status = ""
            if oi:
                if oi > max_oi_cap:
                    status = "❌ EXCEEDS CAP"
                else:
                    status = "✅ Within cap"
            
            table_data.append([
                symbol,
                oi_str,
                status,
                volume_str,
                age_str
            ])
        
        # Print table
        print(f"{'Symbol':<12} {'Open Interest':>18} {'vs 1M Cap':<18} {'Volume 24h':>18} {'Data Age':<20}")
        print("-" * 90)
        for row in table_data:
            print(f"{row[0]:<12} {row[1]:>18} {row[2]:<18} {row[3]:>18} {row[4]:<20}")
        
        # Summary statistics
        print("\n" + "="*80)
        print("📊 Summary:")
        print(f"  Total symbols: {len(rows)}")
        
        symbols_with_oi = sum(1 for row in rows if row['open_interest_usd'])
        print(f"  Symbols with OI data: {symbols_with_oi}")
        
        if symbols_with_oi > 0:
            oi_values = [row['open_interest_usd'] for row in rows if row['open_interest_usd']]
            avg_oi = sum(oi_values) / len(oi_values)
            max_oi_found = max(oi_values)
            min_oi_found = min(oi_values)
            
            print(f"  Average OI: ${avg_oi:,.0f}")
            print(f"  Max OI: ${max_oi_found:,.0f}")
            print(f"  Min OI: ${min_oi_found:,.0f}")
            
            # Count how many exceed the cap
            max_oi_cap = Decimal("1000000")
            exceeds_cap = sum(1 for oi in oi_values if oi > max_oi_cap)
            within_cap = symbols_with_oi - exceeds_cap
            
            print(f"\n  🎯 vs Your max_oi_usd Cap (${max_oi_cap:,.0f}):")
            print(f"     Within cap: {within_cap} symbols")
            print(f"     Exceeds cap: {exceeds_cap} symbols")
        
        # Check data freshness
        print(f"\n  ⏰ Data Freshness:")
        stale_count = 0
        for row in rows:
            updated_at = row['updated_at']
            if updated_at:
                if updated_at.tzinfo is None:
                    updated_at_utc = updated_at
                else:
                    updated_at_utc = updated_at.astimezone(timezone.utc).replace(tzinfo=None)
                age = now - updated_at_utc
                if age.total_seconds() > 120:  # > 2 minutes
                    stale_count += 1
        
        fresh_count = len(rows) - stale_count
        print(f"     Fresh data (< 2 min): {fresh_count} symbols")
        if stale_count > 0:
            print(f"     ⚠️  Stale data (> 2 min): {stale_count} symbols")
        
        # Optional: Compare with live API data
        if compare_with_api:
            print("\n" + "="*80)
            print("🔄 Comparing with Live Lighter API...")
            await compare_with_live_api(rows)
        
        print("\n" + "="*80)
        
        # Recommendations
        if stale_count > 0:
            print("\n⚠️  WARNING: Some data is stale!")
            print("   - Check if funding_rate_service/run_tasks.py is running")
            print("   - Collection should happen every 60 seconds")
        
        if exceeds_cap > 0 and symbols_with_oi > 0:
            print(f"\n💡 TIP: {exceeds_cap}/{symbols_with_oi} symbols exceed your 1M cap")
            print("   These should be filtered out by the opportunity finder")
        
    finally:
        await database.disconnect()


async def compare_with_live_api(db_rows):
    """Compare database values with live API data"""
    try:
        from exchange_clients.lighter import LighterFundingAdapter
        
        adapter = LighterFundingAdapter()
        
        print("   Fetching live data from Lighter API...")
        live_data = await adapter.fetch_market_data()
        await adapter.close()
        
        print(f"   ✅ Fetched data for {len(live_data)} symbols from API\n")
        
        # Compare
        comparison_data = []
        
        for row in db_rows[:10]:  # Compare first 10 symbols
            symbol = row['symbol']
            db_oi = row['open_interest_usd']
            
            if symbol in live_data:
                api_oi = live_data[symbol].get('open_interest')
                
                if db_oi and api_oi:
                    diff = api_oi - db_oi
                    diff_pct = (diff / db_oi * 100) if db_oi > 0 else 0
                    
                    status = ""
                    if abs(diff_pct) < 1:
                        status = "✅ Match"
                    elif abs(diff_pct) < 10:
                        status = "⚠️  Small diff"
                    else:
                        status = "❌ Large diff"
                    
                    comparison_data.append([
                        symbol,
                        f"${db_oi:,.0f}",
                        f"${api_oi:,.0f}",
                        f"${diff:,.0f}",
                        f"{diff_pct:.1f}%",
                        status
                    ])
        
        if comparison_data:
            print(f"\n   {'Symbol':<10} {'DB OI':>15} {'API OI':>15} {'Difference':>15} {'Diff %':>10} {'Status':<15}")
            print("   " + "-" * 85)
            for row in comparison_data:
                print(f"   {row[0]:<10} {row[1]:>15} {row[2]:>15} {row[3]:>15} {row[4]:>10} {row[5]:<15}")
        else:
            print("   ⚠️  No matching symbols found for comparison")
        
    except Exception as e:
        print(f"   ❌ Failed to fetch live data: {e}")


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Check Lighter OI data stored in the database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python check_lighter_oi_data.py                # Show all symbols
  python check_lighter_oi_data.py BTC            # Show BTC only
  python check_lighter_oi_data.py --compare      # Compare with live API
  python check_lighter_oi_data.py BTC --compare  # Compare BTC with API
        """
    )
    
    parser.add_argument(
        'symbol',
        nargs='?',
        help='Filter by specific symbol (e.g., BTC, ETH)'
    )
    
    parser.add_argument(
        '--compare',
        action='store_true',
        help='Compare database values with live Lighter API'
    )
    
    args = parser.parse_args()
    
    await check_lighter_oi_data(
        symbol_filter=args.symbol,
        compare_with_api=args.compare
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

