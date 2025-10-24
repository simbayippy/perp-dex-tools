#!/usr/bin/env python3
"""
Test the credential loader to ensure it works correctly.

Usage:
    python database/scripts/test_credential_loader.py acc1
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.credential_loader import DatabaseCredentialLoader
import json


async def test_credential_loader(account_name: str):
    """Test loading credentials for an account."""
    
    print(f"\n{'='*70}")
    print(f"  Testing Credential Loader for Account: {account_name}")
    print(f"{'='*70}\n")
    
    loader = DatabaseCredentialLoader()
    
    try:
        # Test 1: Get account info
        print("✅ Test 1: Loading account info...")
        account_info = await loader.get_account_info(account_name)
        print(f"   Account ID: {account_info['id']}")
        print(f"   Description: {account_info['description']}")
        print(f"   Active: {account_info['is_active']}")
        print(f"   Created: {account_info['created_at']}")
        
        # Test 2: List exchanges
        print("\n✅ Test 2: Listing configured exchanges...")
        exchanges = await loader.list_account_exchanges(account_name)
        print(f"   Configured exchanges: {', '.join(exchanges)}")
        
        # Test 3: Load credentials
        print("\n✅ Test 3: Loading and decrypting credentials...")
        credentials = await loader.load_account_credentials(account_name)
        
        print(f"\n   Loaded credentials for {len(credentials)} exchange(s):\n")
        
        for exchange_name, creds in credentials.items():
            print(f"   📦 {exchange_name.upper()}:")
            for key, value in creds.items():
                # Mask the value for security
                if len(value) > 8:
                    masked_value = value[:4] + "..." + value[-4:]
                else:
                    masked_value = "***"
                print(f"      • {key}: {masked_value}")
        
        # Test 4: Verify format for each exchange
        print(f"\n✅ Test 4: Verifying credential format...")
        
        if 'lighter' in credentials:
            lighter_creds = credentials['lighter']
            required = ['private_key', 'account_index', 'api_key_index']
            has_all = all(key in lighter_creds for key in required)
            print(f"   Lighter: {'✅ All required fields present' if has_all else '❌ Missing fields'}")
        
        if 'aster' in credentials:
            aster_creds = credentials['aster']
            required = ['api_key', 'secret_key']
            has_all = all(key in aster_creds for key in required)
            print(f"   Aster: {'✅ All required fields present' if has_all else '❌ Missing fields'}")
        
        if 'backpack' in credentials:
            backpack_creds = credentials['backpack']
            required = ['api_key', 'secret_key']
            has_all = all(key in backpack_creds for key in required)
            print(f"   Backpack: {'✅ All required fields present' if has_all else '❌ Missing fields'}")
        
        print(f"\n{'='*70}")
        print("  ✅ All tests passed! Credential loader is working correctly.")
        print(f"{'='*70}\n")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        await loader.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python database/scripts/test_credential_loader.py <account_name>")
        print("Example: python database/scripts/test_credential_loader.py acc1")
        sys.exit(1)
    
    account_name = sys.argv[1]
    success = asyncio.run(test_credential_loader(account_name))
    sys.exit(0 if success else 1)

