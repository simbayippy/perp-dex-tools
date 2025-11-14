#!/usr/bin/env python3
"""
Standalone Control API Server

Starts the control API server independently of strategy execution.
This allows querying balances and positions even when no strategies are running.

The server serves ALL users with valid API keys. Each user can only access
their own accounts (unless they are admin). Authentication is required via
X-API-Key header or Authorization: Bearer <key>.

Usage:
    # Run in foreground (for testing)
    python scripts/start_control_api.py
    
    # Run in screen session (recommended for production)
    screen -S control_api python scripts/start_control_api.py
    
    # Run with custom host/port
    python scripts/start_control_api.py --host 0.0.0.0 --port 8766
    
    # Detach from screen: Ctrl+A then D
    # Reattach: screen -r control_api
"""

import asyncio
import logging
import os
import sys
import signal
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn
from strategies.control.server import app
from strategies.control.funding_arb_controller import FundingArbStrategyController
from database.connection import database

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global server reference for signal handling
_server: uvicorn.Server = None
_shutdown_event: asyncio.Event = None


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    if _shutdown_event:
        _shutdown_event.set()
    if _server:
        _server.should_exit = True


async def main():
    """Main entry point."""
    global _server, _shutdown_event
    
    # Create shutdown event
    _shutdown_event = asyncio.Event()
    
    # Parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="Standalone Control API Server")
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("CONTROL_API_HOST", "127.0.0.1"),
        help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CONTROL_API_PORT", "8766")),
        help="Port to bind to (default: 8766)"
    )
    args = parser.parse_args()
    
    try:
        # Ensure database is connected
        if not database.is_connected:
            await database.connect()
            logger.info("Database connected")
        
        # Create read-only controller (no strategy instance)
        controller = FundingArbStrategyController(strategy=None)
        from strategies.control.server import set_strategy_controller
        set_strategy_controller(controller)
        logger.info("Control API initialized in read-only mode")
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Create uvicorn config
        config = uvicorn.Config(
            app=app,
            host=args.host,
            port=args.port,
            log_level="info",
            access_log=True,
            loop="asyncio"
        )
        _server = uvicorn.Server(config)
        
        logger.info("="*70)
        logger.info("Control API Server Starting")
        logger.info("="*70)
        logger.info(f"Host: {args.host}")
        logger.info(f"Port: {args.port}")
        logger.info("Mode: Read-only (no strategy required)")
        logger.info("Access: All users with valid API keys")
        logger.info("="*70)
        logger.info("")
        logger.info("Available endpoints:")
        logger.info("  GET  /api/v1/status     - Get account status")
        logger.info("  GET  /api/v1/accounts   - List accounts")
        logger.info("  GET  /api/v1/positions  - Get positions (read-only)")
        logger.info("  GET  /api/v1/balances   - Get balances")
        logger.info("  POST /api/v1/positions/{id}/close - Close position (requires running strategy)")
        logger.info("  POST /api/v1/config/reload - Reload config (requires running strategy)")
        logger.info("")
        logger.info("Authentication: X-API-Key header or Authorization: Bearer <key>")
        logger.info("")
        logger.info("Press Ctrl+C to stop")
        logger.info("="*70)
        
        # Start server
        await _server.serve()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup
        if database.is_connected:
            try:
                await database.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting database: {e}")
        logger.info("Control API server stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown complete")

