#!/usr/bin/env python3
"""
Script to verify that different strategies are using different Paradex credentials.

This helps diagnose websocket concurrency issues by checking if multiple
strategies share the same Paradex account credentials.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, Set, Tuple
import dotenv

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables
env_file = project_root / ".env"
if env_file.exists():
    dotenv.load_dotenv(env_file)

from databases import Database
from helpers.unified_logger import get_logger

logger = get_logger("admin", "verify_paradex_creds")


async def get_strategy_accounts(database: Database) -> Dict[str, Dict[str, any]]:
    """Get all running strategies with their account information."""
    query = """
        SELECT 
            sr.id as run_id,
            sr.status,
            u.username,
            a.account_name,
            sc.config_name,
            sc.strategy_type
        FROM strategy_runs sr
        JOIN users u ON sr.user_id = u.id
        JOIN accounts a ON sr.account_id = a.id
        JOIN strategy_configs sc ON sr.config_id = sc.id
        WHERE sr.status = 'running'
        ORDER BY sr.started_at DESC
    """
    rows = await database.fetch_all(query)
    return [dict(row) for row in rows]


async def get_paradex_credentials_for_account(database: Database, account_name: str) -> Dict[str, str]:
    """Get Paradex credentials for a specific account."""
    from database.credential_loader import DatabaseCredentialLoader
    
    loader = DatabaseCredentialLoader(database)
    try:
        credentials = await loader.load_account_credentials(account_name)
        paradex_creds = credentials.get('paradex', {})
        
        # Return only the identifying fields (not the full keys for security)
        return {
            'l1_address': paradex_creds.get('l1_address', 'NOT_SET'),
            'l2_address': paradex_creds.get('l2_address', 'NOT_SET'),
            'environment': paradex_creds.get('environment', 'NOT_SET'),
            # Show first/last 4 chars of private key for verification
            'l2_private_key_preview': _preview_key(paradex_creds.get('l2_private_key_hex', 'NOT_SET')),
        }
    except Exception as e:
        return {'error': str(e)}


def _preview_key(key: str, length: int = 8) -> str:
    """Show preview of a key (first and last N chars)."""
    if not key or key == 'NOT_SET' or len(key) <= length * 2:
        return key
    return f"{key[:length]}...{key[-length:]}"


async def check_env_credentials() -> Dict[str, str]:
    """Check if Paradex credentials are set in environment variables."""
    return {
        'l1_address': os.getenv('PARADEX_L1_ADDRESS', 'NOT_SET'),
        'l2_address': os.getenv('PARADEX_L2_ADDRESS', 'NOT_SET'),
        'environment': os.getenv('PARADEX_ENVIRONMENT', 'NOT_SET'),
        'l2_private_key_preview': _preview_key(os.getenv('PARADEX_L2_PRIVATE_KEY', 'NOT_SET')),
    }


async def main():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable not set")
        sys.exit(1)
    
    database = Database(database_url)
    
    try:
        await database.connect()
        
        # Get all running strategies
        strategies = await get_strategy_accounts(database)
        
        if not strategies:
            print("\n⚠️  No running strategies found in database")
            return
        
        print("\n" + "="*100)
        print("PARADEX CREDENTIAL VERIFICATION")
        print("="*100)
        print(f"\nFound {len(strategies)} running strategy(ies)\n")
        
        # Collect credentials for each strategy
        credential_map: Dict[str, Dict[str, any]] = {}
        env_creds = await check_env_credentials()
        
        for strategy in strategies:
            account_name = strategy['account_name']
            run_id = str(strategy['run_id'])[:8]
            
            print(f"Strategy {run_id}:")
            print(f"  Account: {account_name}")
            print(f"  User: {strategy['username']}")
            print(f"  Config: {strategy['config_name']}")
            
            # Get credentials from database
            db_creds = await get_paradex_credentials_for_account(database, account_name)
            
            if 'error' in db_creds:
                print(f"  ⚠️  Error loading credentials: {db_creds['error']}")
                credential_map[run_id] = {'source': 'error', 'creds': db_creds}
            elif db_creds.get('l1_address') != 'NOT_SET':
                print(f"  ✅ Database credentials found")
                print(f"     L1 Address: {db_creds['l1_address']}")
                print(f"     L2 Address: {db_creds.get('l2_address', 'N/A')}")
                print(f"     Environment: {db_creds.get('environment', 'N/A')}")
                print(f"     L2 Key Preview: {db_creds['l2_private_key_preview']}")
                credential_map[run_id] = {'source': 'database', 'creds': db_creds, 'account': account_name}
            else:
                print(f"  ⚠️  No Paradex credentials in database for account '{account_name}'")
                print(f"     Will fall back to environment variables")
                credential_map[run_id] = {'source': 'env_fallback', 'creds': env_creds, 'account': account_name}
            
            print()
        
        # Check for conflicts
        print("\n" + "="*100)
        print("CONFLICT ANALYSIS")
        print("="*100 + "\n")
        
        # Group by L1 address (the unique identifier)
        l1_to_strategies: Dict[str, Set[Tuple[str, str]]] = {}
        
        for run_id, info in credential_map.items():
            if info['source'] == 'error':
                continue
            
            creds = info['creds']
            l1_addr = creds.get('l1_address', 'UNKNOWN')
            
            if l1_addr not in l1_to_strategies:
                l1_to_strategies[l1_addr] = set()
            
            l1_to_strategies[l1_addr].add((run_id, info.get('account', 'unknown')))
        
        # Report conflicts
        conflicts_found = False
        for l1_addr, strategies_using_it in l1_to_strategies.items():
            if len(strategies_using_it) > 1:
                conflicts_found = True
                print(f"⚠️  CONFLICT: L1 Address {l1_addr} is used by {len(strategies_using_it)} strategy(ies):")
                for run_id, account in strategies_using_it:
                    print(f"   - Strategy {run_id} (Account: {account})")
                print()
        
        if not conflicts_found:
            print("✅ No credential conflicts detected - each strategy uses different Paradex credentials")
        else:
            print("\n❌ CONFLICT DETECTED!")
            print("\nThis could cause websocket concurrency issues because:")
            print("1. Multiple strategies are using the same Paradex account")
            print("2. Paradex may limit websocket connections per account/IP")
            print("3. The SDK's internal _read_messages() tasks may conflict")
            print("\nRecommendation: Use different Paradex accounts for each strategy")
        
        # Check environment variable fallback
        if env_creds.get('l1_address') != 'NOT_SET':
            print("\n" + "="*100)
            print("ENVIRONMENT VARIABLE FALLBACK")
            print("="*100)
            print("\n⚠️  WARNING: PARADEX_L1_ADDRESS is set in environment variables")
            print("   If database credentials are missing, strategies will fall back to env vars")
            print("   This could cause all strategies to use the same credentials!")
            print(f"\n   Env L1 Address: {env_creds['l1_address']}")
            print(f"   Env L2 Address: {env_creds.get('l2_address', 'N/A')}")
            print(f"   Env L2 Key Preview: {env_creds['l2_private_key_preview']}")
        
        print("\n" + "="*100)
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

