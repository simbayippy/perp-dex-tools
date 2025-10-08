#!/usr/bin/env python3
"""
Test script to verify funding arbitrage strategy imports work correctly.
"""

import sys
import os

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Test all critical imports for funding arbitrage strategy."""
    
    print("Testing funding arbitrage strategy imports...")
    
    try:
        print("1. Testing strategy factory...")
        from strategies import StrategyFactory
        print("   ✅ StrategyFactory imported successfully")
        
        print("2. Testing funding arbitrage strategy...")
        from strategies.implementations.funding_arbitrage import FundingArbitrageStrategy
        print("   ✅ FundingArbitrageStrategy imported successfully")
        
        print("3. Testing funding rate service components...")
        from funding_rate_service.core.opportunity_finder import OpportunityFinder
        print("   ✅ OpportunityFinder imported successfully")
        
        from funding_rate_service.database.repositories import FundingRateRepository
        print("   ✅ FundingRateRepository imported successfully")
        
        from funding_rate_service.database.connection import database
        print("   ✅ Database connection imported successfully")
        
        print("\n🎉 All imports successful! The funding arbitrage strategy should work.")
        return True
        
    except Exception as e:
        print(f"\n❌ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_imports()
    sys.exit(0 if success else 1)
