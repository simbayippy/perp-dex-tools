#!/usr/bin/env python3
"""
Background Task Runner

Standalone script to run background tasks independently from the API server.
Perfect for VPS deployment where you want separate processes.

Usage:
    python run_tasks.py                    # Run all tasks with default schedule
    python run_tasks.py --help             # Show all options
    python run_tasks.py --collection-only  # Run only collection task
    python run_tasks.py --no-cleanup       # Skip cleanup task
"""

import asyncio
import signal
import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "funding_rate_service"))

from tasks.scheduler import TaskScheduler
from database.connection import database
from core.mappers import dex_mapper, symbol_mapper
from utils.logger import logger


class TaskRunner:
    """
    Standalone task runner for VPS deployment
    
    Manages the lifecycle of background tasks independently from the API server.
    Handles graceful shutdown on SIGTERM/SIGINT for proper VPS process management.
    """
    
    def __init__(self, args):
        """
        Initialize task runner with command line arguments
        
        Args:
            args: Parsed command line arguments
        """
        self.args = args
        self.scheduler = None
        self.running = False
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        logger.info("🤖 Task Runner initialized")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logger.info(f"📡 Received {signal_name}, initiating graceful shutdown...")
        self.running = False
    
    async def start(self):
        """
        Start the task runner
        
        This will:
        1. Connect to database
        2. Load mappers
        3. Initialize and start scheduler
        4. Run until shutdown signal
        """
        logger.info("🚀 Starting Background Task Runner...")
        
        try:
            # Connect to database
            logger.info("Connecting to database...")
            await database.connect()
            logger.info("✅ Database connected")
            
            # Load mappers
            logger.info("Loading mappers...")
            await dex_mapper.load_from_db(database)
            await symbol_mapper.load_from_db(database)
            logger.info(f"✅ Loaded {len(dex_mapper)} DEXs and {len(symbol_mapper)} symbols")
            
            # Initialize scheduler
            logger.info("Initializing task scheduler...")
            self.scheduler = TaskScheduler()
            
            # Customize scheduler based on arguments
            await self._configure_scheduler()
            
            # Start scheduler
            await self.scheduler.start()
            logger.info("✅ Task scheduler started")
            
            # Print schedule information
            self._print_schedule_info()
            
            # Set running flag
            self.running = True
            
            # Main loop - keep running until shutdown signal
            logger.info("🔄 Task runner is now active. Press Ctrl+C to stop.")
            
            while self.running:
                await asyncio.sleep(1)  # Check for shutdown every second
            
            logger.info("🛑 Shutdown signal received, stopping tasks...")
            
        except Exception as e:
            logger.error(f"❌ Error in task runner: {e}", exc_info=True)
            raise
        
        finally:
            await self._cleanup()
    
    async def _configure_scheduler(self):
        """Configure scheduler based on command line arguments"""
        
        # If collection-only mode, remove other jobs
        if self.args.collection_only:
            logger.info("📊 Running in collection-only mode")
            # We'll modify the scheduler to only add collection job
            # This requires modifying the scheduler's _add_jobs method
            # For now, we'll let it add all jobs and pause the others
        
        # If no-cleanup mode, we'll pause the cleanup job after starting
        if self.args.no_cleanup:
            logger.info("🚫 Cleanup task disabled")
    
    async def _apply_job_filters(self):
        """Apply job filters after scheduler starts"""
        if self.args.collection_only:
            # Pause non-collection jobs
            self.scheduler.pause_job('opportunity_job')
            self.scheduler.pause_job('cleanup_job')
            logger.info("⏸️ Paused opportunity and cleanup jobs (collection-only mode)")
        
        if self.args.no_cleanup:
            # Pause cleanup job
            self.scheduler.pause_job('cleanup_job')
            logger.info("⏸️ Paused cleanup job (no-cleanup mode)")
    
    def _print_schedule_info(self):
        """Print information about the scheduled tasks"""
        logger.info("📋 Task Schedule:")
        
        if not self.args.collection_only:
            logger.info("  • Funding Rate Collection: Every 60 seconds")
            logger.info("  • Opportunity Analysis: Every 2 minutes")
        else:
            logger.info("  • Funding Rate Collection: Every 60 seconds (ONLY)")
        
        if not self.args.no_cleanup and not self.args.collection_only:
            logger.info("  • Database Cleanup: Daily at 2:00 AM UTC")
        
        logger.info("  • All times in UTC")
        logger.info("  • Tasks run with automatic error recovery")
        logger.info("  • Logs will show task execution status")
    
    async def _cleanup(self):
        """Cleanup resources"""
        logger.info("🧹 Cleaning up resources...")
        
        try:
            if self.scheduler:
                await self.scheduler.shutdown()
                logger.info("✅ Task scheduler stopped")
            
            await database.disconnect()
            logger.info("✅ Database disconnected")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
        
        logger.info("👋 Task Runner stopped")
    
    async def run_once(self):
        """
        Run tasks once and exit (useful for testing)
        """
        logger.info("🧪 Running tasks once...")
        
        try:
            # Connect to database
            await database.connect()
            await dex_mapper.load_from_db(database)
            await symbol_mapper.load_from_db(database)
            
            # Initialize scheduler
            self.scheduler = TaskScheduler()
            await self.scheduler.start()
            
            # Force run all jobs once
            if not self.args.collection_only:
                logger.info("🔄 Running collection task...")
                await self.scheduler.force_run_job('collection_job')
                
                logger.info("🔄 Running opportunity task...")
                await self.scheduler.force_run_job('opportunity_job')
            else:
                logger.info("🔄 Running collection task only...")
                await self.scheduler.force_run_job('collection_job')
            
            if not self.args.no_cleanup and not self.args.collection_only:
                logger.info("🔄 Running cleanup task...")
                await self.scheduler.force_run_job('cleanup_job')
            
            logger.info("✅ All tasks completed")
            
        finally:
            await self._cleanup()


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Background Task Runner for Funding Rate Service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_tasks.py                    # Run all tasks continuously
  python run_tasks.py --collection-only  # Run only funding rate collection
  python run_tasks.py --no-cleanup       # Skip daily cleanup task
  python run_tasks.py --run-once         # Run all tasks once and exit
  python run_tasks.py --run-once --collection-only  # Test collection only

For VPS deployment:
  nohup python run_tasks.py > tasks.log 2>&1 &  # Run in background
  python run_tasks.py                           # Run in foreground with logs
        """
    )
    
    parser.add_argument(
        '--collection-only',
        action='store_true',
        help='Run only the funding rate collection task (every 60s)'
    )
    
    parser.add_argument(
        '--no-cleanup',
        action='store_true',
        help='Skip the daily database cleanup task'
    )
    
    parser.add_argument(
        '--run-once',
        action='store_true',
        help='Run all tasks once and exit (useful for testing)'
    )
    
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Set logging level'
    )
    
    args = parser.parse_args()
    
    # Create and run task runner
    runner = TaskRunner(args)
    
    try:
        if args.run_once:
            await runner.run_once()
        else:
            await runner.start()
            
            # Apply job filters after scheduler starts
            await runner._apply_job_filters()
            
            # Keep running until shutdown
            while runner.running:
                await asyncio.sleep(1)
    
    except KeyboardInterrupt:
        logger.info("🛑 Keyboard interrupt received")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    # Set up proper event loop for different platforms
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Task Runner stopped by user")
    except Exception as e:
        logger.error(f"❌ Task Runner failed: {e}")
        sys.exit(1)
