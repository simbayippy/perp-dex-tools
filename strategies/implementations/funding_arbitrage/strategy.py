"""
Funding Arbitrage Strategy - Main Orchestrator

3-Phase Execution Loop:
1. Monitor existing positions
2. Check exit conditions & close
3. Scan for new opportunities

Pattern: Stateful strategy with multi-DEX support
"""

from strategies.categories.stateful_strategy import StatefulStrategy
from strategies.base_strategy import StrategyAction
from .config import FundingArbConfig
from .models import FundingArbPosition
from .funding_analyzer import FundingRateAnalyzer

from dashboard.config import DashboardSettings
from dashboard.models import (
    LifecycleStage,
    SessionHealth,
    SessionState,
    TimelineCategory,
)
from dashboard.service import DashboardService
from dashboard import control_server

# Direct imports from funding_rate_service (internal calls, no HTTP)
# Make imports conditional to avoid config loading issues during import
try:
    from funding_rate_service.core.opportunity_finder import OpportunityFinder
    from funding_rate_service.database.repositories import FundingRateRepository, DashboardRepository
    from funding_rate_service.database.connection import database
    FUNDING_SERVICE_AVAILABLE = True
except ImportError as e:
    # For testing or when funding service config is not available
    OpportunityFinder = None
    FundingRateRepository = None
    DashboardRepository = None
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
from .monitoring import PositionMonitor
# Funding_arb operation helpers
from .operations import DashboardReporter, PositionOpener, OpportunityScanner, PositionCloser


class FundingArbitrageStrategy(StatefulStrategy):
    """
    Delta-neutral funding rate arbitrage strategy.
    
    Strategy:
    ---------
    1. Long on DEX with low funding rate (pay funding)
    2. Short on DEX with high funding rate (receive funding)
    3. Collect funding rate divergence (paid periodically)
    4. Close when divergence shrinks or better opportunity exists
    
    Complexity: Multi-DEX, stateful, requires careful monitoring
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
        
        # Pass exchange_clients dict to StatefulStrategy
        super().__init__(funding_config, exchange_clients)
        self.config = funding_config  # Store the converted config
        
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
        
        # ⭐ Core components from Hummingbot pattern
        self.analyzer = FundingRateAnalyzer()
        
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
        from .position_manager import FundingArbPositionManager
        from .state_manager import FundingArbStateManager
        
        self.position_manager = FundingArbPositionManager()
        self.state_manager = FundingArbStateManager()

        # Tracking
        self.cumulative_funding = {}  # {position_id: Decimal}
        self.failed_symbols = set()  # Track symbols that failed validation (avoid retrying same cycle)
        
        # 🔒 Session-level position limit (Issue #4 fix)
        # Set to True to limit strategy to 1 position per bot session
        # Set to False to allow multiple positions based on max_positions config
        self.one_position_per_session = True  # Keep it simple for now
        self.position_opened_this_session = False
        self._session_limit_warning_logged = False
        self._max_position_warning_logged = False

        # Position monitoring helper
        self.position_monitor = PositionMonitor(
            position_manager=self.position_manager,
            funding_rate_repo=self.funding_rate_repo,
            exchange_clients=self.exchange_clients,
            logger=self.logger,
        )
        self.dashboard = DashboardReporter(self)
        self.position_opener = PositionOpener(self)
        self.opportunity_scanner = OpportunityScanner(self)
        self.position_closer = PositionCloser(self)

        # Dashboard service (optional)
        self.dashboard_settings = self._resolve_dashboard_settings()
        self.dashboard_enabled = bool(self.dashboard_settings.enabled and FUNDING_SERVICE_AVAILABLE)
        self._current_dashboard_stage: LifecycleStage = LifecycleStage.INITIALIZING
        self.dashboard_service, self.control_server = self._create_dashboard_resources(
            available_exchanges
        )
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

        try:
            # Phase 1: Monitor existing positions
            self.logger.log("Phase 1: Monitoring positions", "INFO")
            await self.dashboard.set_stage(
                LifecycleStage.MONITORING,
                "Monitoring active positions",
            )
            await self.position_monitor.monitor()
            await self.dashboard.publish_snapshot()

            # Phase 2: Check exit conditions & close
            closed = await self.position_closer.evaluate()
            if closed:
                await self.dashboard.set_stage(
                    LifecycleStage.CLOSING,
                    "Evaluating exit conditions",
                )
                self.logger.log("Phase 2: NEED TO EXIT!", "INFO")

            # Phase 3: Scan for new opportunities
            if self.opportunity_scanner.has_capacity():
                self.logger.log("Phase 3: Scanning new opportunities", "INFO")
                await self.dashboard.set_stage(
                    LifecycleStage.SCANNING,
                    "Scanning for new opportunities",
                )
                opportunities = await self.opportunity_scanner.scan()
                for opportunity in opportunities:
                    if not self.opportunity_scanner.has_capacity():
                        break
                    if not self.opportunity_scanner.should_take(opportunity):
                        continue
                    new_position = await self.position_opener.open(opportunity)

                    if new_position:
                        self.logger.log(f"New position opened: {new_position}", "INFO")
                    else:
                        self.logger.log(f"No new position opened", "INFO")

                await self.dashboard.set_stage(
                    LifecycleStage.IDLE,
                    "Funding arbitrage cycle completed",
                    category=TimelineCategory.INFO,
                )

            await self.dashboard.publish_snapshot()

            if self.config.risk_config.check_interval_seconds > 0:
                self.logger.log(f"Sleeping for {self.config.risk_config.check_interval_seconds} seconds", "INFO")
                await asyncio.sleep(self.config.risk_config.check_interval_seconds)


        except Exception as exc:
            self.logger.log(f"Strategy execution failed: {exc}", "ERROR")
            await self.dashboard.set_stage(
                LifecycleStage.ERROR,
                f"Funding arbitrage cycle error: {exc}",
                category=TimelineCategory.ERROR,
            )

    # ========================================================================
    # Abstract Method Implementations (Required by BaseStrategy)
    # ========================================================================

    def get_strategy_name(self) -> str:
        return "Funding Rate Arbitrage"

    # async def _initialize_strategy(self):
    #     """Strategy-specific initialization logic."""
    #     # Initialize position and state managers
    #     await self.position_manager.initialize()
    #     await self.state_manager.initialize()
    #     if self.dashboard_service.enabled:
    #         await self.dashboard_service.start()
    #         await self.dashboard.set_stage(
    #             LifecycleStage.IDLE,
    #             "Strategy initialized",
    #             category=TimelineCategory.INFO,
    #         )
    #         await self.dashboard.publish_snapshot("Startup state captured")
    #         if self.control_server:
    #             await self.control_server.start()
    #             self._control_server_started = True

    #     self.logger.log("FundingArbitrageStrategy initialized successfully")
    
    async def should_execute(self) -> bool:
        """
        Determine if strategy should execute based on market conditions.

        For funding arbitrage, we always check for opportunities.
        """
        return True

    # ========================================================================
    # Helpers & Dashboard Helpers
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

        dashboard_config = strategy_params.get('dashboard') or {}
        if isinstance(dashboard_config, DashboardSettings):
            dashboard_settings = dashboard_config
        elif isinstance(dashboard_config, dict):
            dashboard_settings = DashboardSettings(**dashboard_config)
        else:
            dashboard_settings = DashboardSettings()
        
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
            dashboard=dashboard_settings,
            # Ticker for logging
            ticker=trading_config.ticker,
            config_path=config_path
            # Note: bridge_settings not implemented yet
        )
        return funding_config

    def _resolve_dashboard_settings(self) -> DashboardSettings:
        """
        Derive dashboard settings from config if supplied, otherwise fallback to defaults.
        """
        candidate = getattr(self.config, "dashboard", None)

        if isinstance(candidate, DashboardSettings):
            return candidate

        if isinstance(candidate, dict):
            try:
                return DashboardSettings(**candidate)
            except Exception as exc:  # pylint: disable=broad-except
                self.logger.log(
                    f"⚠️  Invalid dashboard config ({exc}); falling back to defaults.",
                    "WARNING",
                )

        defaults = DashboardSettings()
        defaults.enabled = True
        return defaults

    def _create_dashboard_resources(
        self, available_exchanges: List[str]
    ) -> Tuple[DashboardService, Optional[Any]]:
        dashboard_metadata = {
            "available_exchanges": available_exchanges,
            "scan_exchanges": self.config.exchanges,
        }

        session_state = SessionState(
            session_id=uuid4(),
            strategy="funding_arbitrage",
            config_path=getattr(self.config, "config_path", None),
            started_at=datetime.now(timezone.utc),
            last_heartbeat=datetime.now(timezone.utc),
            health=SessionHealth.STARTING,
            lifecycle_stage=LifecycleStage.INITIALIZING,
            max_positions=self.config.max_positions,
            max_total_exposure_usd=getattr(self.config, "max_total_exposure_usd", None),
            dry_run=getattr(self.config, "dry_run", False),
            metadata=dashboard_metadata,
        )

        repository = None
        if self.dashboard_enabled and DashboardRepository is not None:
            repository = DashboardRepository(database)
        elif self.dashboard_settings.enabled and not FUNDING_SERVICE_AVAILABLE:
            self.logger.log(
                "Dashboard persistence disabled – funding rate service database unavailable",
                "WARNING",
            )

        renderer_factory = None
        if self.dashboard_enabled:
            renderer_name = (self.dashboard_settings.renderer or "rich").lower()
            if renderer_name == "rich":
                try:
                    from dashboard.renderers import RichDashboardRenderer

                    refresh = self.dashboard_settings.refresh_interval_seconds
                    max_events = min(self.dashboard_settings.event_retention, 20)
                    renderer_factory = lambda: RichDashboardRenderer(
                        refresh_interval_seconds=refresh,
                        max_events=max_events,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    self.logger.log(
                        f"⚠️  Dashboard renderer unavailable: {exc}. Falling back to log output.",
                        "WARNING",
                    )
                    renderer_factory = None
            elif renderer_name == "plain":
                try:
                    from dashboard.renderers import PlainTextDashboardRenderer

                    renderer_factory = lambda: PlainTextDashboardRenderer()
                except Exception as exc:  # pylint: disable=broad-except
                    self.logger.log(
                        f"⚠️  Plain dashboard renderer unavailable: {exc}. Falling back to log output.",
                        "WARNING",
                    )
                    renderer_factory = None
            else:
                self.logger.log(
                    f"ℹ️  Dashboard renderer '{renderer_name}' not supported yet. Using log output.",
                    "INFO",
                )

        service = DashboardService(
            session_state=session_state,
            settings=self.dashboard_settings,
            repository=repository,
            renderer_factory=renderer_factory,
        )
        control = control_server if self.dashboard_enabled else None
        return service, control

    # ========================================================================
    # Cleanup
    # ========================================================================
    
    async def cleanup(self):
        """Cleanup strategy resources."""
        # Close position and state managers
        if hasattr(self, 'position_manager'):
            await self.position_manager.close()
        if hasattr(self, 'state_manager'):
            await self.state_manager.close()
        if getattr(self, "dashboard_service", None) and self.dashboard_service.enabled:
            await self.dashboard_service.stop(health=SessionHealth.STOPPED)
        if getattr(self, "_control_server_started", False) and self.control_server:
            await self.control_server.stop()
            self._control_server_started = False

        await super().cleanup()
    
