"""
Funding Arbitrage Strategy - Main Orchestrator

3-Phase Execution Loop:
1. Monitor existing positions
2. Check exit conditions & close
3. Scan for new opportunities

Pattern: Stateful strategy with multi-DEX support
"""

import asyncio
import traceback

from strategies.base_strategy import BaseStrategy
from .config import FundingArbConfig
from .models import FundingArbPosition

# Direct imports from funding_rate_service (internal calls, no HTTP)
# Make imports conditional to avoid config loading issues during import
try:
    from funding_rate_service.core.opportunity_finder import OpportunityFinder
    from database.repositories import FundingRateRepository
    from database.connection import database
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
    AtomicExecutionResult,
)
from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer
from exchange_clients.events import LiquidationEvent
from .position_monitor import PositionMonitor
# Funding_arb operation helpers
from .operations import PositionOpener, OpportunityScanner, PositionCloser
from .operations.cooldown_manager import CooldownManager


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
            # Single exchange client - create dict using the connector name
            if hasattr(exchange_client, 'get_exchange_name'):
                try:
                    exchange_name = exchange_client.get_exchange_name()
                except Exception:
                    exchange_name = funding_config.exchange
            else:
                exchange_name = funding_config.exchange
            exchange_clients = {exchange_name: exchange_client}
        
        # Initialize BaseStrategy (note: no exchange_client for multi-DEX)
        super().__init__(funding_config, exchange_client=None)
        self.config = funding_config  # Store the converted config
        
        # Store original config path for hot-reloading
        self._config_path = funding_config.config_path if hasattr(funding_config, 'config_path') else None
        
        # Store exchange clients dict (multi-DEX support)
        self.exchange_clients = exchange_clients
        
        available_exchanges = list(exchange_clients.keys())
        required_exchanges = funding_config.exchanges  # These are from scan_exchanges
        
        missing_exchanges = [dex for dex in required_exchanges if dex not in exchange_clients]
        if missing_exchanges:
            self.logger.info(f"ℹ️  Exchanges configured for scanning: {required_exchanges}")
            self.logger.info(f"ℹ️  Exchanges with trading clients: {available_exchanges}")
            if missing_exchanges:
                self.logger.warning(f"⚠️  Trading not available on: {missing_exchanges} (funding data only)")
            self.logger.info(f"✅ Will scan ALL configured exchanges but only trade on {available_exchanges}")
            
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
        
        # ⭐ Price Provider (shared data source for all execution components)
        from strategies.execution.core.price_provider import PriceProvider
        self.price_provider = PriceProvider()
        
        # Pass account_name for multi-account support (needed for notification service and executor)
        account_name = getattr(funding_config, 'account_name', None)
        
        # Notification service for Telegram notifications (needed by executor)
        from .utils.notification_service import StrategyNotificationService
        self.notification_service = StrategyNotificationService(account_name=account_name)
        
        # ⭐ Leverage Validator (shared instance for caching across all operations)
        # Create this BEFORE the executor so we can pass it to benefit from caching
        from strategies.execution.core.leverage_validator import LeverageValidator
        self.leverage_validator = LeverageValidator()
        
        # ⭐ Execution Common layer (atomic delta-neutral execution)
        # Pass shared leverage_validator to executor so it can use cached leverage info
        self.atomic_executor = AtomicMultiOrderExecutor(
            price_provider=self.price_provider,
            account_name=account_name,
            notification_service=self.notification_service,
            leverage_validator=self.leverage_validator  # ⭐ Share leverage validator for caching
        )
        self.liquidity_analyzer = LiquidityAnalyzer(
            max_slippage_pct=Decimal("0.005"),  # 0.5% max slippage
            max_spread_bps=50,  # 50 basis points
            min_liquidity_score=0.6,
            price_provider=self.price_provider  # Share the price source
        )
        
        # ⭐ Position and state management (database-backed)
        # Compose what we need directly - no factory methods
        from .position_manager import FundingArbPositionManager
        
        # Pass strategy logger to position manager for unified logging
        self.position_manager = FundingArbPositionManager(account_name=account_name, logger=self.logger)

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
            strategy_config=self.config,
        )
        
        # Cooldown manager for wide spread tracking
        self.cooldown_manager = CooldownManager()
        
        self.position_opener = PositionOpener(self)
        self.opportunity_scanner = OpportunityScanner(self)
        self.position_closer = PositionCloser(self)

        # Async orchestration helpers
        self._monitor_task = None
        self._monitor_stop_event = None
        self._last_opportunity_scan_ts = 0.0
        self._shutdown_requested = False  # Track if strategy is shutting down

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
                self.logger.debug("No capacity for new positions; skipping opportunity scan")
                return

            self.logger.info("Scanning for new funding arbitrage opportunities")
            opportunities = await self.opportunity_scanner.scan()
            for opportunity in opportunities:
                if not await self.opportunity_scanner.has_capacity():
                    break
                if not await self.opportunity_scanner.should_take(opportunity):
                    continue
                await self.position_opener.open(opportunity)

        except Exception as exc:
            self.logger.error(f"Strategy execution failed: {exc}")

    # ========================================================================
    # Abstract Method Implementations (Required by BaseStrategy)
    # ========================================================================

    def get_strategy_name(self) -> str:
        return "Funding Rate Arbitrage"

    async def _initialize_strategy(self):
        """Strategy-specific initialization logic."""
        # Initialize position and state managers
        await self.position_manager.initialize()
        self.logger.info("FundingArbitrageStrategy initialized successfully")
        if self._monitor_task is None:
            self._monitor_stop_event = asyncio.Event()
            self.logger.info("🔄 Creating background monitor task...")
            self._monitor_task = asyncio.create_task(self._monitor_positions_loop(), name="funding-arb-monitor")
            self.logger.info(f"✅ Background monitor task created: {self._monitor_task.get_name()} (task_id={id(self._monitor_task)})")
        else:
            self.logger.warning(f"⚠️ Monitor task already exists: {self._monitor_task.get_name()}")
    
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
            exchanges = [ex.strip().lower() for ex in exchanges_str.split(',') if ex.strip()]
        elif isinstance(exchanges_str, list):
            exchanges = [str(ex).strip().lower() for ex in exchanges_str if str(ex).strip()]
        else:
            exchanges = [trading_config.exchange]

        mandatory_exchange = strategy_params.get('mandatory_exchange')
        if not mandatory_exchange:
            mandatory_exchange = strategy_params.get('primary_exchange')
        if isinstance(mandatory_exchange, str):
            mandatory_exchange = mandatory_exchange.strip().lower() or None
        else:
            mandatory_exchange = None

        if mandatory_exchange and mandatory_exchange not in exchanges:
            exchanges.append(mandatory_exchange)
        
        from .config import RiskManagementConfig
        from funding_rate_service.config import settings
        
        # Get target_margin from strategy_params
        # For backward compatibility: if target_exposure is set but target_margin is not,
        # convert target_exposure to target_margin using a conservative leverage assumption
        target_margin = strategy_params.get('target_margin')
        target_exposure = strategy_params.get('target_exposure')
        
        if target_margin is not None:
            target_margin_decimal = Decimal(str(target_margin))
        elif target_exposure is not None:
            # Backward compatibility: convert target_exposure to target_margin
            # Assume 10x leverage as a conservative estimate
            # margin = exposure / leverage
            target_exposure_decimal = Decimal(str(target_exposure))
            target_margin_decimal = target_exposure_decimal / Decimal('10')
            self.logger.info(
                f"⚠️ Config uses deprecated 'target_exposure'=${target_exposure_decimal:.2f}. "
                f"Converted to target_margin=${target_margin_decimal:.2f} (assuming 10x leverage). "
                f"Please update your config to use 'target_margin' instead."
            )
        else:
            # Default fallback
            target_margin_decimal = Decimal("40")
            self.logger.warning(
                "Neither target_margin nor target_exposure specified, using default target_margin=$40"
            )
        
        # Calculate default exposure for max_position_size_usd calculation (conservative estimate)
        # This is just for the max size limit, actual exposure will be calculated dynamically
        target_exposure_decimal = target_margin_decimal * Decimal('10')  # Assume 10x leverage for max size calc

        config_path = strategy_params.get("_config_path")

        
        limit_order_offset_param = strategy_params.get('limit_order_offset_pct')
        if limit_order_offset_param is not None:
            limit_order_offset_pct = Decimal(str(limit_order_offset_param))
        else:
            limit_order_offset_pct = Decimal("0.0001")

        # Build risk configuration using overrides (fallback to defaults)
        risk_defaults = RiskManagementConfig()
        risk_config = RiskManagementConfig(
            strategy=strategy_params.get('risk_strategy', risk_defaults.strategy),
            min_erosion_threshold=Decimal(
                str(
                    strategy_params.get(
                        'profit_erosion_threshold',
                        risk_defaults.min_erosion_threshold,
                    )
                )
            ),
            max_position_age_hours=strategy_params.get(
                'max_position_age_hours',
                risk_defaults.max_position_age_hours,
            ),
            check_interval_seconds=strategy_params.get(
                'check_interval_seconds',
                risk_defaults.check_interval_seconds,
            ),
            min_hold_hours=float(
                strategy_params.get(
                    'min_hold_hours',
                    risk_defaults.min_hold_hours,
                ) or 0.0
            ),
            enable_liquidation_prevention=strategy_params.get(
                'enable_liquidation_prevention',
                risk_defaults.enable_liquidation_prevention,
            ),
            min_liquidation_distance_pct=Decimal(
                str(
                    strategy_params.get(
                        'min_liquidation_distance_pct',
                        risk_defaults.min_liquidation_distance_pct,
                    )
                )
            ),
        )

        max_oi_value = strategy_params.get('max_oi_usd')
        if mandatory_exchange and max_oi_value is not None:
            max_oi_usd = Decimal(str(max_oi_value))
        else:
            max_oi_usd = None

        # Extract volume/OI filter params
        min_volume_24h_value = strategy_params.get('min_volume_24h')
        min_volume_24h = Decimal(str(min_volume_24h_value)) if min_volume_24h_value is not None else None

        min_oi_usd_value = strategy_params.get('min_oi_usd')
        min_oi_usd = Decimal(str(min_oi_usd_value)) if min_oi_usd_value is not None else None

        funding_config = FundingArbConfig(
            exchange=trading_config.exchange,
            exchanges=exchanges,
            mandatory_exchange=mandatory_exchange,
            symbols=[trading_config.ticker],
            max_positions=strategy_params.get('max_positions', 5),
            target_margin=target_margin_decimal,
            default_position_size_usd=target_exposure_decimal,  # Will be adjusted dynamically if target_margin is set
            max_position_size_usd=target_exposure_decimal * Decimal('10'),  # Max is 10x the default
            max_total_exposure_usd=Decimal(str(strategy_params.get('max_total_exposure_usd', float(target_exposure_decimal) * 5))),
            min_profit=Decimal(str(strategy_params.get('min_profit_rate', DEFAULT_MIN_PROFIT_RATE_PER_INTERVAL))),
            limit_order_offset_pct=limit_order_offset_pct,
            max_oi_usd=max_oi_usd,
            min_volume_24h=min_volume_24h,
            min_oi_usd=min_oi_usd,
            max_new_positions_per_cycle=strategy_params.get('max_new_positions_per_cycle', 2),
            # Required database URL from funding_rate_service settings
            database_url=settings.database_url,
            # Risk management defaults
            risk_config=risk_config,
            # Ticker for logging
            ticker=trading_config.ticker,
            config_path=config_path,
            # Multi-account support
            account_name=strategy_params.get('_account_name')
            # Note: bridge_settings not implemented yet
        )
        
        return funding_config
    
    async def reload_config(self) -> bool:
        """
        Reload configuration from the config file without restarting.
        
        This allows hot-reloading of config changes. The new config will be
        used on the next execution cycle.
        
        Returns:
            True if reloaded successfully, False otherwise
        """
        if not self._config_path:
            self.logger.warning("Cannot reload config: no config path stored")
            return False
        
        try:
            from pathlib import Path
            from trading_config.config_yaml import load_config_from_yaml
            
            config_path = Path(self._config_path)
            if not config_path.exists():
                self.logger.error(f"Config file not found: {config_path}")
                return False
            
            # Load new config from file
            loaded = load_config_from_yaml(config_path)
            strategy_config = loaded["config"]
            
            # Convert to FundingArbConfig
            # We need to reconstruct the TradingConfig-like object first
            class TempTradingConfig:
                def __init__(self, config_dict):
                    self.exchange = config_dict.get("mandatory_exchange") or "multi"
                    self.ticker = "ALL"
                    self.strategy = "funding_arbitrage"
                    self.strategy_params = config_dict
                    self._config_path = str(config_path)
            
            temp_config = TempTradingConfig(strategy_config)
            new_funding_config = self._convert_trading_config(temp_config)
            
            # Update the config
            self.config = new_funding_config
            self._config_path = str(config_path)  # Update path in case it changed
            
            self.logger.info(f"✅ Config reloaded successfully from {config_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to reload config: {e}", exc_info=True)
            return False

    # ========================================================================
    # Internal Loops
    # ========================================================================

    async def _monitor_positions_loop(self):
        """Background loop to refresh and close existing positions."""
        interval = max(self.config.risk_config.check_interval_seconds, 1)
        stop_event = self._monitor_stop_event

        try:
            while stop_event and not stop_event.is_set():
                # Check shutdown flag from strategy
                if self._shutdown_requested:
                    break
                
                # Check for cancellation before starting operations
                if stop_event.is_set():
                    break
                
                try:
                    await self.position_monitor.monitor()
                    if stop_event.is_set() or self._shutdown_requested:
                        break
                    await self.position_closer.evaluateAndClosePositions()
                    
                except asyncio.CancelledError:
                    # Task was cancelled, exit immediately
                    break
                except Exception as exc:
                    # If shutdown was requested, exit on any error (database might be closed)
                    if (stop_event and stop_event.is_set()) or self._shutdown_requested:
                        break
                    self.logger.error(f"Monitor loop error: {exc}")

                if stop_event.is_set() or self._shutdown_requested:
                    break

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                    break  # Event was set, exit
                except asyncio.TimeoutError:
                    continue  # Timeout expected, continue loop
                except asyncio.CancelledError:
                    break  # Task cancelled, exit
        except asyncio.CancelledError:
            # Task cancellation - exit cleanly
            pass
        except Exception as e:
            self.logger.error(f"Monitor loop error: {e}")
        finally:
            pass  # Silent exit

    # ========================================================================
    # Cleanup
    # ========================================================================
    
    async def cleanup(self):
        """Cleanup strategy resources."""
        self._shutdown_requested = True  # Set shutdown flag immediately
        
        # Stop monitor task
        if self._monitor_task and not self._monitor_task.done():
            if self._monitor_stop_event:
                self._monitor_stop_event.set()
            self._monitor_task.cancel()
            try:
                await asyncio.wait_for(self._monitor_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass  # Task stopped or cancelled
            finally:
                self._monitor_task = None
        
        if self._monitor_stop_event:
            self._monitor_stop_event = None
        self._last_opportunity_scan_ts = 0.0

        # Close position and state managers with timeout
        if hasattr(self, 'position_manager'):
            shutdown = getattr(self.position_manager, "shutdown", None)
            if callable(shutdown):
                try:
                    await asyncio.wait_for(shutdown(), timeout=10.0)
                except asyncio.TimeoutError:
                    self.logger.warning("Position manager shutdown timed out")
                except Exception as e:
                    self.logger.error(f"Error shutting down position manager: {e}")
        if getattr(self, "_control_server_started", False) and self.control_server:
            try:
                await asyncio.wait_for(self.control_server.stop(), timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("Control server stop timed out")
            except Exception as e:
                self.logger.error(f"Error stopping control server: {e}")
            self._control_server_started = False

        # Stop liquidation consumers with timeout
        for task in self._liquidation_tasks:
            task.cancel()
        for task in self._liquidation_tasks:
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.TimeoutError:
                self.logger.warning(f"Liquidation task cancellation timed out")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.logger.error(f"Error waiting for liquidation task: {e}")
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
                self.logger.warning(
                    f"⚠️ Unable to subscribe to liquidation stream for {name}: {exc}"
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
                self.logger.error(
                    f"Error processing liquidation event from {exchange}: {exc}"
                )
    
DEFAULT_MIN_PROFIT_RATE_PER_INTERVAL = Decimal("0.0002283105")
