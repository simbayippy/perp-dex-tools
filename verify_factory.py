#!/usr/bin/env python3
"""
Verification script to test that all exchanges can be instantiated via the factory.
This is a temporary script for testing the refactoring.
"""

import sys
from decimal import Decimal
from exchange_clients.factory import ExchangeFactory

def test_factory():
    """Test that the factory can list and create all exchanges."""
    
    print("=" * 70)
    print("TESTING EXCHANGE FACTORY")
    print("=" * 70)
    
    # Test 1: Get supported exchanges
    print("\n✅ Test 1: Get supported exchanges")
    exchanges = ExchangeFactory.get_supported_exchanges()
    print(f"   Supported exchanges: {', '.join(exchanges)}")
    print(f"   Total: {len(exchanges)} exchanges")
    
    # Test 2: Verify registry
    print("\n✅ Test 2: Verify factory registry")
    registry = ExchangeFactory._registered_exchanges
    for name, path in registry.items():
        print(f"   - {name}: {path}")
    
    # Test 3: Test factory instantiation (without connecting)
    print("\n✅ Test 3: Test factory can resolve all exchange classes")
    test_config = {
        'ticker': 'BTC',
        'quantity': Decimal('0.01'),
        'take_profit': Decimal('0.5'),
        'direction': 'buy',
        'max_orders': 5,
        'wait_time': 10,
        'grid_step': Decimal('0.1'),
        'stop_price': Decimal('-1'),
        'pause_price': Decimal('-1')
    }
    
    results = []
    for exchange_name in exchanges:
        try:
            # Just test that the class can be imported and instantiated
            # We won't call connect() as that requires credentials
            client = ExchangeFactory.create_exchange(exchange_name, test_config)
            exchange_display_name = client.get_exchange_name()
            results.append((exchange_name, "✅ SUCCESS", exchange_display_name))
            print(f"   ✅ {exchange_name}: Successfully created {exchange_display_name} client")
        except Exception as e:
            results.append((exchange_name, "❌ FAILED", str(e)))
            print(f"   ❌ {exchange_name}: FAILED - {e}")
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    success_count = sum(1 for _, status, _ in results if status == "✅ SUCCESS")
    fail_count = len(results) - success_count
    
    print(f"\nTotal exchanges: {len(results)}")
    print(f"✅ Successful: {success_count}")
    print(f"❌ Failed: {fail_count}")
    
    if fail_count == 0:
        print("\n🎉 ALL EXCHANGES CAN BE INSTANTIATED VIA FACTORY!")
        print("✅ Refactoring verification: PASSED")
        return 0
    else:
        print("\n⚠️  Some exchanges failed to instantiate")
        print("❌ Refactoring verification: FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(test_factory())

