#!/usr/bin/env python3
"""
Test script for unified logging system

Demonstrates the new logging capabilities across different component types.
Run this to see the new log formatting in action.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers.unified_logger import (
    get_exchange_logger,
    get_strategy_logger, 
    get_service_logger,
    get_core_logger
)


def test_exchange_logging():
    """Test exchange client logging."""
    print("\n🔄 Testing Exchange Client Logging")
    print("=" * 50)
    
    # Basic exchange logger
    logger = get_exchange_logger("aster", "BTC")
    
    logger.info("🔗 Connected to Aster WebSocket with listen key")
    logger.debug("🔍 Symbol normalization: 'BTC' → 'BTCUSDT'")
    logger.warning("⚠️ Failed to keepalive listen key: 500")
    logger.error("❌ Error keeping alive listen key: Connection timeout")
    
    # Logger with additional context
    order_logger = logger.with_context(order_id="12345", side="buy")
    order_logger.info("📤 Placing market order with data: {'symbol': 'BTCUSDT', 'side': 'BUY'}")
    order_logger.info("✅ Order placed successfully")
    
    # Transaction logging
    logger.log_transaction("12345", "buy", "10.5", "50000.00", "filled")


def test_strategy_logging():
    """Test strategy logging."""
    print("\n🎯 Testing Strategy Logging")
    print("=" * 50)
    
    # Strategy logger
    logger = get_strategy_logger("funding_arbitrage")
    
    logger.info("🔍 Scanning for funding arbitrage opportunities...")
    logger.info("📊 Found 3 opportunities: BTC, ETH, SOL")
    logger.warning("⚠️ Position BTC-LONG needs rebalancing")
    logger.error("❌ Failed to close position: Insufficient balance")
    
    # Position-specific logging
    position_logger = logger.with_context(position_id="pos_123", symbol="BTC")
    position_logger.info("💰 Opening position: LONG on Lighter, SHORT on GRVT")
    position_logger.info("✅ Position opened successfully")
    
    # Grid strategy logger
    grid_logger = get_strategy_logger("grid", ticker="ETH", direction="long")
    grid_logger.info("📈 Grid level triggered at $3,200")
    grid_logger.info("🎯 Placing take profit order at $3,220")


def test_service_logging():
    """Test service logging."""
    print("\n🔧 Testing Service Logging")
    print("=" * 50)
    
    # Service logger
    logger = get_service_logger("funding_rate_service")
    
    logger.info("🚀 Starting funding rate collection...")
    logger.info("📡 Fetching rates from 5 exchanges")
    logger.debug("🔍 Processing Aster response: 15 symbols")
    logger.warning("⚠️ Lighter API rate limit reached, backing off")
    logger.error("❌ Failed to fetch from EdgeX: HTTP 500")
    
    # Database operations
    db_logger = logger.with_context(table="funding_rates", operation="insert")
    db_logger.info("💾 Inserted 75 new funding rate records")


def test_core_logging():
    """Test core utility logging."""
    print("\n⚙️ Testing Core Utility Logging")
    print("=" * 50)
    
    # Core logger
    logger = get_core_logger("order_executor")
    
    logger.info("⚡ Executing atomic multi-order batch")
    logger.debug("🔍 Pre-flight checks passed for 2 orders")
    logger.warning("⚠️ Partial fill detected, initiating rollback")
    logger.error("❌ Rollback failed, manual intervention required")
    
    # Risk management
    risk_logger = get_core_logger("risk_manager", strategy="funding_arb")
    risk_logger.info("🛡️ Risk check passed: exposure within limits")
    risk_logger.warning("⚠️ High correlation detected between positions")


def test_backward_compatibility():
    """Test backward compatibility with old .log() method."""
    print("\n🔄 Testing Backward Compatibility")
    print("=" * 50)
    
    logger = get_exchange_logger("backpack", "SOL")
    
    # Old style calls should still work
    logger.log("Connected to Backpack WebSocket", "INFO")
    logger.log("Debug message", "DEBUG") 
    logger.log("Warning message", "WARNING")
    logger.log("Error occurred", "ERROR")
    
    print("✅ Backward compatibility confirmed!")


def main():
    """Run all logging tests."""
    print("🧪 Unified Logging System Test")
    print("=" * 60)
    print("This script demonstrates the new logging capabilities.")
    print("Check both console output and log files in logs/ directory.")
    print("=" * 60)
    
    try:
        test_exchange_logging()
        test_strategy_logging()
        test_service_logging()
        test_core_logging()
        test_backward_compatibility()
        
        print("\n" + "=" * 60)
        print("✅ All logging tests completed successfully!")
        print("📁 Check the logs/ directory for generated log files:")
        print("   - exchange_aster_activity.log")
        print("   - exchange_aster_errors.log")
        print("   - strategy_funding_arbitrage_activity.log")
        print("   - service_funding_rate_service_activity.log")
        print("   - core_order_executor_activity.log")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
