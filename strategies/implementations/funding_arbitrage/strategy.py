"""
Funding Arbitrage Strategy - Main Orchestrator

3-Phase Execution Loop:
1. Monitor existing positions
2. Check exit conditions & close
3. Scan for new opportunities

Pattern: Stateful strategy with multi-DEX support
"""

import asyncio

from strategies.base_strategy import BaseStrategy
from .config import FundingArbConfig
from .models import FundingArbPosition

# Direct imports from funding_rate_service (internal calls, no HTTP)
# Make imports conditional to avoid config loading issues during import
try:
    from funding_rate_service.core.opportunity_finder import OpportunityFinder
    from funding_rate_service.database.repositories import FundingRateRepository
    from funding_rate_service.database.connection import database
    FUNDING_SERVICE_AVAILABLE = True
except ImportError as e:
    # For testing or when funding service config is not available
    OpportunityFinder = None
    FundingRateRepository = None
    database = None
    FUNDING_SERVICE_AVAILABLE = False

from typing import Dict, Any, List, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timezone
from uuid import uuid4

# Execution layer imports
from strategies.execution.patterns.atomic_multi_order import (
    AtomicMultiOrderExecutor,
    OrderSpec,
    AtomicExecutionResult
)
from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer
from exchange_clients.events import LiquidationEvent
from .position_monitor import PositionMonitor
# Funding_arb operation helpers
from .operations import PositionOpener, OpportunityScanner, PositionCloser


class FundingArbitrageStrategy(BaseStrategy):
    """
    Delta-neutral funding rate arbitrage strategy.
    
    Strategy:
    ---------
    1. Long on DEX with low funding rate (pay funding)
    2. Short on DEX with high funding rate (receive funding)
    3. Collect funding rate divergence (paid periodically)
    4. Close when divergence shrinks or better opportunity exists
    
    Complexity: Multi-DEX, requires careful monitoring
    """
    
    def __init__(self, config, exchange_client):
        """
        Initialize funding arbitrage strategy.
        
        Args:
            config: Trading configuration (will be converted to FundingArbConfig)
            exchange_client: Single exchange client or dict of exchange clients
        """
        # Convert TradingConfig to FundingArbConfig if needed
        if not isinstance(config, FundingArbConfig):
            funding_config = self._convert_trading_config(config)
        else:
            funding_config = config
        
        # Convert single exchange client to dict format if needed
        if isinstance(exchange_client, dict):
            exchange_clients = exchange_client
        else:
            # Single exchange client - create dict using the primary exchange name
            # Try to get exchange name from client, fallback to config
            if hasattr(exchange_client, 'get_exchange_name'):
                try:
                    primary_exchange = exchange_client.get_exchange_name()
                except:
                    primary_exchange = funding_config.exchange
            else:
                primary_exchange = funding_config.exchange
            exchange_clients = {primary_exchange: exchange_client}
        
        # Initialize BaseStrategy (note: no exchange_client for multi-DEX)
        super().__init__(funding_config, exchange_client=None)
        self.config = funding_config  # Store the converted config
        
        # Store exchange clients dict (multi-DEX support)
        self.exchange_clients = exchange_clients
        
        available_exchanges = list(exchange_clients.keys())
        required_exchanges = funding_config.exchanges  # These are from scan_exchanges
        
        missing_exchanges = [dex for dex in required_exchanges if dex not in exchange_clients]
        if missing_exchanges:
            self.logger.log(f"ℹ️  Exchanges configured for scanning: {required_exchanges}")
            self.logger.log(f"ℹ️  Exchanges with trading clients: {available_exchanges}")
            if missing_exchanges:
                self.logger.log(f"⚠️  Trading not available on: {missing_exchanges} (funding data only)")
            self.logger.log(f"✅ Will scan ALL configured exchanges but only trade on {available_exchanges}")
            
        if not available_exchanges:
            raise ValueError(f"No trading-capable exchange clients available. At least one exchange with full trading support is required.")
        
        # ⭐ Direct internal services (no HTTP, shared database)
        if not FUNDING_SERVICE_AVAILABLE:
            raise RuntimeError(
                "Funding rate service is not available. "
                "Please ensure the funding_rate_service is properly configured and the database is accessible."
            )
        
        # Import the correct FeeCalculator from funding_rate_service
        from funding_rate_service.core.fee_calculator import FundingArbFeeCalculator
        from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
        
        self.fee_calculator = FundingArbFeeCalculator()
        self.opportunity_finder = OpportunityFinder(
            database=database,
            fee_calculator=self.fee_calculator,
            dex_mapper=dex_mapper,
            symbol_mapper=symbol_mapper
        )
        self.funding_rate_repo = FundingRateRepository(database)
        
        # ⭐ Price Provider (shared cache for all execution components)
        from strategies.execution.core.price_provider import PriceProvider
        self.price_provider = PriceProvider(
            cache_ttl_seconds=5.0,  # Cache prices for 5 seconds
            prefer_websocket=False  # Prefer cache over WebSocket for stability
        )
        
        # ⭐ Execution Common layer (atomic delta-neutral execution)
        self.atomic_executor = AtomicMultiOrderExecutor(price_provider=self.price_provider)
        self.liquidity_analyzer = LiquidityAnalyzer(
            max_slippage_pct=Decimal("0.005"),  # 0.5% max slippage
            max_spread_bps=50,  # 50 basis points
            min_liquidity_score=0.6,
            price_provider=self.price_provider  # Share the cache
        )
        
        # ⭐ Position and state management (database-backed)
        # Compose what we need directly - no factory methods
        from .position_manager import FundingArbPositionManager
        
        self.position_manager = FundingArbPositionManager()

        # Tracking
        self.failed_symbols = set()  # Track symbols that failed validation (avoid retrying same cycle)
        
        self.position_opened_this_session = False
        
        # Keep track of warnings, to prevent spam
        self._session_limit_warning_logged = False
        self._max_position_warning_logged = False

        # Liquidation event consumption
        self._liquidation_consumers_started = False
        self._liquidation_tasks: List[asyncio.Task] = []
        self._liquidation_queues: Dict[str, asyncio.Queue[LiquidationEvent]] = {}

        # Position monitoring helper
        self.position_monitor = PositionMonitor(
            position_manager=self.position_manager,
            funding_rate_repo=self.funding_rate_repo,
            exchange_clients=self.exchange_clients,
            logger=self.logger,
        )
        self.position_opener = PositionOpener(self)
        self.opportunity_scanner = OpportunityScanner(self)
        self.position_closer = PositionCloser(self)

        # Async orchestration helpers
        self._monitor_task = None
        self._monitor_stop_event = None
        self._last_opportunity_scan_ts = 0.0

        self._control_server_started = False

 
    # ========================================================================
    # Main Execution Loop.
    # ========================================================================
       
    async def execute_strategy(self):
        """
        Execute the funding arbitrage strategy.
        
        This is the main entry point called by the trading bot.
        """
        self.failed_symbols.clear()
        loop = asyncio.get_running_loop()
        self._last_opportunity_scan_ts = loop.time()

        await self._ensure_liquidation_consumers_started()

        try:
            if not await self.opportunity_scanner.has_capacity():
                self.logger.log("No capacity for new positions; skipping opportunity scan", "DEBUG")
                return

            self.logger.log("Scanning for new funding arbitrage opportunities", "INFO")
            opportunities = await self.opportunity_scanner.scan()
            for opportunity in opportunities:
                if not await self.opportunity_scanner.has_capacity():
                    break
                if not await self.opportunity_scanner.should_take(opportunity):
                    continue
                await self.position_opener.open(opportunity)

        except Exception as exc:
            self.logger.log(f"Strategy execution failed: {exc}", "ERROR")

    # ========================================================================
    # Abstract Method Implementations (Required by BaseStrategy)
    # ========================================================================

    def get_strategy_name(self) -> str:
        return "Funding Rate Arbitrage"

    async def _initialize_strategy(self):
        """Strategy-specific initialization logic."""
        # Initialize position and state managers
        await self.position_manager.initialize()
        self.logger.log("FundingArbitrageStrategy initialized successfully")
        if self._monitor_task is None:
            self._monitor_stop_event = asyncio.Event()
            self._monitor_task = asyncio.create_task(self._monitor_positions_loop(), name="funding-arb-monitor")
            self.logger.log("Started background monitor loop", "DEBUG")
    
    async def should_execute(self) -> bool:
        """
        Determine if strategy should execute based on market conditions.

        For funding arbitrage, we always check for opportunities.
        """
        interval = max(self.config.risk_config.check_interval_seconds, 1)
        now = asyncio.get_running_loop().time()
        return (now - self._last_opportunity_scan_ts) >= interval

    
    def get_required_parameters(self) -> List[str]:
        """Get list of required strategy parameters."""
        return [
            "target_exposure",
            "min_profit_rate", 
            "exchanges"
        ]
    
    # ========================================================================
    # Helpers
    # ========================================================================
    
    def _convert_trading_config(self, trading_config) -> FundingArbConfig:
        """
        Convert TradingConfig from runbot.py to FundingArbConfig.
        
        Args:
            trading_config: TradingConfig object from runbot.py
            
        Returns:
            FundingArbConfig object for the strategy
        """
        # Extract strategy-specific parameters from strategy_params
        strategy_params = getattr(trading_config, 'strategy_params', {})
        
        # Parse exchanges from strategy_params or use single exchange
        # Check both 'scan_exchanges' (from YAML) and 'exchanges' (from CLI) for backward compat
        exchanges_str = strategy_params.get('scan_exchanges') or strategy_params.get('exchanges', trading_config.exchange)
        if isinstance(exchanges_str, str):
            exchanges = [ex.strip() for ex in exchanges_str.split(',')]
        elif isinstance(exchanges_str, list):
            exchanges = exchanges_str
        else:
            exchanges = [trading_config.exchange]
        
        from .config import RiskManagementConfig
        from funding_rate_service.config import settings
        
        # Get target exposure from strategy_params
        target_exposure = Decimal(str(strategy_params.get('target_exposure', 100.0)))

        config_path = strategy_params.get("_config_path")

        
        funding_config = FundingArbConfig(
            exchange=trading_config.exchange,  # Primary exchange
            exchanges=exchanges,  # All exchanges for arbitrage
            symbols=[trading_config.ticker],
            max_positions=strategy_params.get('max_positions', 5),
            default_position_size_usd=target_exposure,  # Use target_exposure as default position size
            max_position_size_usd=target_exposure * Decimal('10'),  # Max is 10x the default
            max_total_exposure_usd=Decimal(str(strategy_params.get('max_total_exposure_usd', float(target_exposure) * 5))),
            min_profit=Decimal(str(strategy_params.get('min_profit_rate', 0.0001))),
            max_oi_usd=Decimal(str(strategy_params.get('max_oi_usd', 10000000.0))),  # 10M default
            max_new_positions_per_cycle=strategy_params.get('max_new_positions_per_cycle', 2),
            # Required database URL from funding_rate_service settings
            database_url=settings.database_url,
            # Risk management defaults
            risk_config=RiskManagementConfig(),
            # Ticker for logging
            ticker=trading_config.ticker,
            config_path=config_path
            # Note: bridge_settings not implemented yet
        )
        return funding_config

    # ========================================================================
    # Internal Loops
    # ========================================================================

    async def _monitor_positions_loop(self):
        """Background loop to refresh and close existing positions."""
        interval = max(self.config.risk_config.check_interval_seconds, 1)
        stop_event = self._monitor_stop_event

        try:
            while stop_event and not stop_event.is_set():
                try:
                    await self.position_monitor.monitor()
                    await self.position_closer.evaluateAndClosePositions()
                except Exception as exc:
                    self.logger.log(f"Monitor loop error: {exc}", "ERROR")

                if stop_event.is_set():
                    break

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            self.logger.log("Background monitor loop stopped", "DEBUG")

    # ========================================================================
    # Cleanup
    # ========================================================================
    
    async def cleanup(self):
        """Cleanup strategy resources."""
        if self._monitor_stop_event:
            self._monitor_stop_event.set()
        if self._monitor_task:
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        self._monitor_stop_event = None
        self._last_opportunity_scan_ts = 0.0

        # Close position and state managers
        if hasattr(self, 'position_manager'):
            await self.position_manager.close()
        if getattr(self, "_control_server_started", False) and self.control_server:
            await self.control_server.stop()
            self._control_server_started = False

        # Stop liquidation consumers
        for task in self._liquidation_tasks:
            task.cancel()
        for task in self._liquidation_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._liquidation_tasks.clear()

        for exchange, queue in list(self._liquidation_queues.items()):
            client = self.exchange_clients.get(exchange)
            if client:
                try:
                    client.unregister_liquidation_queue(queue)
                except Exception:
                    pass
        self._liquidation_queues.clear()
        self._liquidation_consumers_started = False

        await super().cleanup()

    async def _ensure_liquidation_consumers_started(self) -> None:
        """Start background consumers for exchange liquidation streams."""
        if self._liquidation_consumers_started:
            return

        for name, client in self.exchange_clients.items():
            supports = getattr(client, "supports_liquidation_stream", None)
            try:
                if not callable(supports) or not client.supports_liquidation_stream():
                    continue
            except Exception:
                continue

            try:
                queue = client.liquidation_events_queue()
            except Exception as exc:
                self.logger.log(
                    f"⚠️ Unable to subscribe to liquidation stream for {name}: {exc}",
                    "WARNING",
                )
                continue

            self._liquidation_queues[name] = queue
            task = asyncio.create_task(self._consume_liquidation_events(name, queue))
            self._liquidation_tasks.append(task)

        self._liquidation_consumers_started = True

    async def _consume_liquidation_events(
        self,
        exchange: str,
        queue: asyncio.Queue[LiquidationEvent],
    ) -> None:
        """Background task to forward liquidation events to the position closer."""
        while True:
            try:
                event = await queue.get()
                await self.position_closer.handle_liquidation_event(event)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.log(
                    f"Error processing liquidation event from {exchange}: {exc}",
                    "ERROR",
                )
    
