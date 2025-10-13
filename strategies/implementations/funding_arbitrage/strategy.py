"""
Funding Arbitrage Strategy - Main Orchestrator

⭐ STRUCTURE BASED ON Hummingbot v2_funding_rate_arb.py ⭐

3-Phase Execution Loop:
1. Monitor existing positions
2. Check exit conditions & close
3. Scan for new opportunities

Pattern: Stateful strategy with multi-DEX support
"""

from strategies.categories.stateful_strategy import StatefulStrategy
from strategies.base_strategy import StrategyResult, StrategyAction
from strategies.components import InMemoryPositionManager
from .config import FundingArbConfig
from .models import FundingArbPosition, OpportunityData
from .funding_analyzer import FundingRateAnalyzer

from dashboard.config import DashboardSettings
from dashboard.models import (
    DashboardSnapshot,
    FundingSnapshot,
    LifecycleStage,
    PortfolioSnapshot,
    PositionLegSnapshot,
    PositionSnapshot,
    SessionHealth,
    SessionState,
    TimelineCategory,
    TimelineEvent,
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

from typing import Dict, Any, List, Tuple, Optional
from decimal import Decimal
from datetime import datetime, timezone
from uuid import UUID, uuid4

# Execution layer imports
from strategies.execution.patterns.atomic_multi_order import (
    AtomicMultiOrderExecutor,
    OrderSpec,
    AtomicExecutionResult
)
from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer
from helpers.unified_logger import log_stage


class FundingArbitrageStrategy(StatefulStrategy):
    """
    Delta-neutral funding rate arbitrage strategy.
    
    ⭐ Core logic from Hummingbot v2_funding_rate_arb.py ⭐
    
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
        # self.exchange_clients is already set by StatefulStrategy.__init__
        
        # ⭐ Exchange Client Management
        # 
        # IMPORTANT DISTINCTION:
        # 1. scan_exchanges (config) - Which exchanges to consider for opportunity scanning
        #    - These are just labels for filtering database queries
        #    - The funding rate data comes from the database (collected by funding_rate_service)
        #    - No trading client needed for scanning
        #
        # 2. exchange_clients (this dict) - Which exchanges we can actually TRADE on
        #    - Only exchanges with fully implemented BaseExchangeClient can be here
        #    - Currently: Only 'lighter' is fully implemented
        #    - Others (edgex, grvt, aster, backpack) only have funding adapters
        #
        # The strategy will:
        # - Scan opportunities across ALL exchanges in scan_exchanges (from database)
        # - But only execute trades on exchanges with available trading clients
        
        available_exchanges = list(exchange_clients.keys())
        required_exchanges = funding_config.exchanges  # These are from scan_exchanges
        
        missing_exchanges = [dex for dex in required_exchanges if dex not in exchange_clients]
        if missing_exchanges:
            self.logger.log(f"ℹ️  Exchanges configured for scanning: {required_exchanges}")
            self.logger.log(f"ℹ️  Exchanges with trading clients: {available_exchanges}")
            if missing_exchanges:
                self.logger.log(f"⚠️  Trading not available on: {missing_exchanges} (funding data only)")
            self.logger.log(f"✅ Will scan ALL configured exchanges but only trade on {available_exchanges}")
            
            # Keep all exchanges in config for opportunity scanning (database queries)
            # Don't filter them out - we want to see opportunities even if we can't trade them all yet
            # funding_config.exchanges remains unchanged
            
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
        
        # ⭐ Execution layer (atomic delta-neutral execution)
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


        # Dashboard service (optional)
        self._manual_pause = False
        self.dashboard_enabled = bool(self.config.dashboard.enabled and FUNDING_SERVICE_AVAILABLE)
        self._current_dashboard_stage: LifecycleStage = LifecycleStage.INITIALIZING
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
        elif self.config.dashboard.enabled and not FUNDING_SERVICE_AVAILABLE:
            self.logger.log(
                "Dashboard persistence disabled – funding rate service database unavailable",
                "WARNING",
            )

        renderer_factory = None
        if self.dashboard_enabled:
            renderer_name = (self.config.dashboard.renderer or "rich").lower()
            if renderer_name == "rich":
                try:
                    from dashboard.renderers import RichDashboardRenderer

                    refresh = self.config.dashboard.refresh_interval_seconds
                    max_events = min(self.config.dashboard.event_retention, 20)
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

        self.dashboard_service = DashboardService(
            session_state=session_state,
            settings=self.config.dashboard,
            repository=repository,
            renderer_factory=renderer_factory,
        )
        self.control_server = control_server if self.dashboard_enabled else None
        self._control_server_started = False
    
    
    def get_strategy_name(self) -> str:
        return "Funding Rate Arbitrage"

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
 
    # ========================================================================
    # Main Execution Loop
    # ========================================================================
    
    async def execute_cycle(self) -> StrategyResult:
        """
        Main 3-phase execution loop.
        
        ⭐ Pattern from Hummingbot v2_funding_rate_arb.py ⭐
        
        Phase 1: Monitor existing positions
        Phase 2: Check exit conditions & close
        Phase 3: Scan for new opportunities
        
        Called every minute by trading_bot.py.
        
        Returns:
            StrategyResult with actions taken
        """
        actions_taken = []
        
        # Reset failed symbols at start of each cycle (allow retry on next cycle)
        self.failed_symbols.clear()
        
        try:
            # Phase 1: Monitor existing positions
            self.logger.log("Phase 1: Monitoring positions", "INFO")
            await self._monitor_positions()
            
            # Phase 2: Check exits
            self.logger.log("Phase 2: Checking exit conditions", "INFO")
            closed = await self._check_exit_conditions()
            actions_taken.extend(closed)
            
            # Phase 3: New opportunities (if capacity available)
            if self._has_capacity():
                self.logger.log("Phase 3: Scanning new opportunities", "INFO")
                opened = await self._scan_opportunities()
                actions_taken.extend(opened)
            else:
                self.logger.log("Phase 3: At max capacity, skipping new opportunities", "INFO")
            
            return StrategyResult(
                action=StrategyAction.REBALANCE if actions_taken else StrategyAction.WAIT,
                message=f"Cycle complete: {len(actions_taken)} actions taken",
                wait_time=self.config.risk_config.check_interval_seconds
            )
            
        except Exception as e:
            self.logger.log(f"Error in execute_cycle: {e}", "ERROR")
            return StrategyResult(
                action=StrategyAction.WAIT,
                message=f"Error: {e}",
                wait_time=60
            )
    
    # ========================================================================
    # Phase 1: Monitor Positions
    # ========================================================================
    
    async def _monitor_positions(self):
        """
        Update position states with current funding rates.
        
        For each position:
        1. Fetch current funding rates
        2. Update position state
        3. Calculate current profitability
        """
        positions = await self.position_manager.get_open_positions()
        
        if not positions:
            self.logger.log("No open positions to monitor", "DEBUG")
            return
        
        for position in positions:
            try:
                # ⭐ Direct repository call (no HTTP)
                # Get current rates from funding rate repository
                from funding_rate_service.core.mappers import symbol_mapper
                
                # Fetch latest rates
                rate1_data = await self.funding_rate_repo.get_latest_specific(
                    position.long_dex, position.symbol
                )
                rate2_data = await self.funding_rate_repo.get_latest_specific(
                    position.short_dex, position.symbol
                )
                
                if rate1_data and rate2_data:
                    rate1 = Decimal(str(rate1_data['funding_rate']))
                    rate2 = Decimal(str(rate2_data['funding_rate']))
                    divergence = rate2 - rate1
                    
                    # Update position
                    position.current_divergence = divergence
                    position.last_check = datetime.now()
                    await self.position_manager.update_position(position)
                else:
                    self.logger.log(
                        f"Could not fetch rates for {position.symbol}",
                        "WARNING"
                    )
                    continue
                
                # Log status
                erosion = position.get_profit_erosion()
                self.logger.log(
                    f"Position {position.symbol}: "
                    f"Entry={position.entry_divergence*100:.3f}%, "
                    f"Current={position.current_divergence*100:.3f}%, "
                    f"Erosion={erosion*100:.1f}%, "
                    f"PnL=${position.get_net_pnl():.2f}",
                    "INFO"
                )
                
            except Exception as e:
                self.logger.log(
                    f"Error monitoring position {position.id}: {e}",
                    "ERROR"
                )
    
    # ========================================================================
    # Phase 2: Exit Conditions
    # ========================================================================
    
    async def _check_exit_conditions(self) -> List[str]:
        """
        ⭐ FROM v2_funding_rate_arb.py stop_actions_proposal() ⭐
        
        Check if any positions should close.
        
        Returns:
            List of action descriptions
        """
        actions = []
        positions = await self.position_manager.get_open_positions()
        
        for position in positions:
            should_close, reason = self._should_close_position(position)
            
            if should_close:
                await self._close_position(position, reason)
                actions.append(f"Closed {position.symbol}: {reason}")
        
        return actions
    
    def _should_close_position(
        self,
        position: FundingArbPosition
    ) -> Tuple[bool, str]:
        """
        Determine if position should be closed.
        
        Exit conditions (from Hummingbot + your risk management):
        1. Funding rate flipped (critical - immediate exit)
        2. Profit erosion (divergence dropped too much)
        3. Time limit (held too long)
        4. Better opportunity exists
        
        Returns:
            (should_close, reason)
        """
        # 1. Funding rate flipped (CRITICAL - losing money!)
        if position.current_divergence and position.current_divergence < 0:
            return True, "FUNDING_FLIP"
        
        # 2. Profit erosion (divergence dropped significantly)
        erosion = position.get_profit_erosion()
        if erosion < self.config.risk_config.min_erosion_threshold:
            return True, "PROFIT_EROSION"
        
        # 3. Time limit (max position age)
        if position.get_age_hours() > self.config.risk_config.max_position_age_hours:
            return True, "TIME_LIMIT"
        
        # 4. Better opportunity exists (optional)
        if self.config.risk_config.enable_better_opportunity:
            # Check if a significantly better opportunity exists for same symbol
            # TODO: Implement better opportunity check
            pass
        
        return False, None
    
    async def _close_position(self, position: FundingArbPosition, reason: str):
        """
        Close both sides of delta-neutral position.
        
        Args:
            position: Position to close
            reason: Reason for closing
        """
        try:
            await self._set_dashboard_stage(
                LifecycleStage.CLOSING,
                f"Closing {position.symbol} ({reason})",
                category=TimelineCategory.EXECUTION,
            )
            # Close long side
            long_client = self.exchange_clients[position.long_dex]
            await long_client.close_position(position.symbol)
            
            # Close short side
            short_client = self.exchange_clients[position.short_dex]
            await short_client.close_position(position.symbol)
            
            # Calculate final PnL
            pnl = position.get_net_pnl()
            pnl_pct = position.get_net_pnl_pct()
            
            # Mark as closed
            position.status = "closed"
            position.exit_reason = reason
            position.closed_at = datetime.now()
            await self.position_manager.update_position(position)
            
            self.logger.log(
                f"✅ Closed {position.symbol} ({reason}): "
                f"PnL=${pnl:.2f} ({pnl_pct*100:.2f}%), "
                f"Age={position.get_age_hours():.1f}h",
                "INFO"
            )
            await self._publish_dashboard_snapshot(f"Closed {position.symbol} ({reason})")
            
        except Exception as e:
            self.logger.log(
                f"Error closing position {position.id}: {e}",
                "ERROR"
            )
            raise
    
    # ========================================================================
    # Phase 3: New Opportunities
    # ========================================================================
    
    async def _scan_opportunities(self) -> List[str]:
        """
        ⭐ FROM v2_funding_rate_arb.py create_actions_proposal() ⭐
        
        Find and open new positions.
        
        Returns:
            List of action descriptions
        """
        actions = []
        
        try:
            # Stop immediately if we've already hit our capacity for this session.
            if not self._has_capacity():
                return actions
            
            # ⭐ Direct internal service call (no HTTP)
            # Use opportunity finder to find opportunities
            from funding_rate_service.models.filters import OpportunityFilter
            
            # Get list of AVAILABLE exchanges (those with valid trading clients)
            # This filters out exchanges that were skipped due to missing credentials
            available_exchanges = list(self.exchange_clients.keys())
            
            filters = OpportunityFilter(
                min_profit_percent=self.config.min_profit,
                max_oi_usd=self.config.max_oi_usd,
                whitelist_dexes=available_exchanges if available_exchanges else None,
                symbol=None,  # Don't filter by symbol - look at all opportunities
                limit=10
            )
            
            # 🔍 DEBUG: Log the filters being used
            self.logger.log(
                f"Filters - min_profit: {self.config.min_profit}, max_oi_usd: {self.config.max_oi_usd}, "
                f"configured_dexes: {self.config.exchanges}, available_dexes: {available_exchanges}",
                "DEBUG"
            )
            
            opportunities = await self.opportunity_finder.find_opportunities(filters)
            
            self.logger.log(
                f"Found {len(opportunities)} opportunities",
                "INFO"
            )
            
            # Take top opportunities up to limit
            max_new = self.config.max_new_positions_per_cycle
            for opp in opportunities[:max_new]:
                # Skip symbols that failed in this cycle
                if opp.symbol in self.failed_symbols:
                    self.logger.log(
                        f"⏭️  Skipping {opp.symbol} - already failed validation this cycle",
                        "DEBUG"
                    )
                    continue
                
                if self._should_take_opportunity(opp):
                    success = await self._open_position(opp)
                    if success:
                        actions.append(f"Opened {opp.symbol} on {opp.long_dex}/{opp.short_dex}")
                    
                    # Stop if we hit capacity
                    if not self._has_capacity():
                        break
            
        except Exception as e:
            self.logger.log(f"Error scanning opportunities: {e}", "ERROR")
        
        return actions
    
    def _should_take_opportunity(self, opportunity) -> bool:
        """
        Apply client-side filters to opportunity.
        
        Args:
            opportunity: ArbitrageOpportunity object
            
        Returns:
            True if should take this opportunity
        """
        # ⭐ Safety check: Verify we have trading clients for both sides
        # NOTE: This should rarely trigger since we filter at the opportunity finder level,
        # but it's kept as a defensive safety net in case of race conditions or stale data
        long_dex = opportunity.long_dex
        short_dex = opportunity.short_dex
        
        if long_dex not in self.exchange_clients:
            self.logger.log(
                f"⚠️  SAFETY CHECK: Skipping {opportunity.symbol} opportunity - "
                f"{long_dex} (long side) not in available clients (should have been filtered earlier)",
                "WARNING"
            )
            return False
        
        if short_dex not in self.exchange_clients:
            self.logger.log(
                f"⚠️  SAFETY CHECK: Skipping {opportunity.symbol} opportunity - "
                f"{short_dex} (short side) not in available clients (should have been filtered earlier)",
                "WARNING"
            )
            return False
        
        # Check position size limits
        # (size is based on config, not from opportunity)
        size_usd = self.config.default_position_size_usd
        if size_usd > self.config.max_position_size_usd:
            return False
        
        # Check total exposure
        current_exposure = self._calculate_total_exposure()
        if current_exposure + size_usd > self.config.max_total_exposure_usd:
            return False
        
        # Add more filters as needed
        return True
    
    async def _ensure_contract_attributes(self, exchange_client: Any, symbol: str) -> bool:
        """
        Ensure exchange client has contract attributes initialized for symbol.
        
        For multi-symbol strategies, contract attributes (tick_size, multipliers, etc.)
        need to be initialized per-symbol before trading.
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading symbol
            
        Returns:
            True if successful and symbol is tradeable, False otherwise
        """
        try:
            exchange_name = exchange_client.get_exchange_name()
            
            # Check if we need to initialize (config has ticker="ALL" for multi-symbol)
            if not hasattr(exchange_client.config, 'contract_id') or exchange_client.config.ticker == "ALL":
                self.logger.log(
                    f"🔧 [{exchange_name.upper()}] Initializing contract attributes for {symbol}",
                    "INFO"
                )
                
                # Temporarily set ticker to specific symbol for initialization
                original_ticker = exchange_client.config.ticker
                exchange_client.config.ticker = symbol
                
                # Get contract attributes (initializes multipliers, tick_size, contract_id)
                try:
                    contract_id, tick_size = await exchange_client.get_contract_attributes()
                    
                    # Additional validation: contract_id should be meaningful
                    if not contract_id or contract_id == "":
                        self.logger.log(
                            f"❌ [{exchange_name.upper()}] Symbol {symbol} initialization returned empty contract_id",
                            "WARNING"
                        )
                        return False
                    
                    self.logger.log(
                        f"✅ [{exchange_name.upper()}] {symbol} initialized → "
                        f"contract_id={contract_id}, tick_size={tick_size}",
                        "INFO"
                    )
                    
                except ValueError as e:
                    # Specific handling for "symbol not found" errors
                    error_msg = str(e).lower()
                    if "not found" in error_msg or "not supported" in error_msg:
                        self.logger.log(
                            f"⚠️  [{exchange_name.upper()}] Symbol {symbol} is NOT TRADEABLE on {exchange_name} "
                            f"(not listed or not supported)",
                            "WARNING"
                        )
                    else:
                        self.logger.log(
                            f"❌ [{exchange_name.upper()}] Failed to initialize {symbol}: {e}",
                            "ERROR"
                        )
                    return False
                
                finally:
                    # Always restore original ticker
                    if original_ticker != symbol:
                        exchange_client.config.ticker = original_ticker
                
                return True
            
            return True  # Already initialized
            
        except Exception as e:
            self.logger.log(
                f"❌ [{exchange_name.upper()}] Unexpected error initializing {symbol}: {e}",
                "ERROR"
            )
            import traceback
            self.logger.log(f"Traceback: {traceback.format_exc()}", "DEBUG")
            return False
    
    async def _open_position(self, opportunity) -> bool:
        """
        Open delta-neutral position from opportunity using atomic execution.
        
        ⭐ Uses AtomicMultiOrderExecutor for safety ⭐
        
        Args:
            opportunity: ArbitrageOpportunity object
            
        Returns:
            True if position opened successfully, False otherwise
        """
        try:
            symbol = opportunity.symbol
            long_dex = opportunity.long_dex
            short_dex = opportunity.short_dex
            size_usd = self.config.default_position_size_usd
            
            # Get exchange clients
            self.logger.log(f"Available exchange clients: {list(self.exchange_clients.keys())}", "DEBUG")
            self.logger.log(f"Looking for long_dex: {long_dex}, short_dex: {short_dex}", "DEBUG")
            
            long_client = self.exchange_clients.get(long_dex)
            short_client = self.exchange_clients.get(short_dex)
            
            if long_client is None:
                self.logger.log(
                    f"❌ No exchange client found for long_dex: {long_dex}",
                    "ERROR"
                )
                self.failed_symbols.add(symbol)
                return False
            if short_client is None:
                self.logger.log(
                    f"❌ No exchange client found for short_dex: {short_dex}",
                    "ERROR"
                )
                self.failed_symbols.add(symbol)
                return False

            await self._set_dashboard_stage(
                LifecycleStage.OPENING,
                f"Opening {symbol} position ({long_dex.upper()} vs {short_dex.upper()})",
            )
            
            # ⭐ CRITICAL: Initialize contract attributes for this symbol
            log_stage(self.logger, f"{symbol} • Opportunity Validation", icon="📋", stage_id="1")
            self.logger.log(
                f"Ensuring {symbol} is tradeable on both {long_dex} and {short_dex}",
                "INFO"
            )
            
            long_init_ok = await self._ensure_contract_attributes(long_client, symbol)
            short_init_ok = await self._ensure_contract_attributes(short_client, symbol)
            
            if not long_init_ok:
                self.logger.log(
                    f"⛔ [SKIP] Cannot trade {symbol}: Not supported on {long_dex.upper()} (long side)",
                    "WARNING"
                )
                self.failed_symbols.add(symbol)
                return False
            if not short_init_ok:
                self.logger.log(
                    f"⛔ [SKIP] Cannot trade {symbol}: Not supported on {short_dex.upper()} (short side)",
                    "WARNING"
                )
                self.failed_symbols.add(symbol)
                return False
            
            self.logger.log(
                f"✅ {symbol} available on both {long_dex.upper()} and {short_dex.upper()}",
                "INFO"
            )

            # ⭐ LEVERAGE VALIDATION & NORMALIZATION ⭐
            log_stage(self.logger, "Leverage Validation & Normalization", icon="🔍", stage_id="2")
            
            from strategies.execution.core.leverage_validator import LeverageValidator
            
            leverage_validator = LeverageValidator()
            
            try:
                leverage_prep = await leverage_validator.prepare_leverage(
                    exchange_clients=[long_client, short_client],
                    symbol=symbol,
                    requested_size_usd=size_usd,
                    min_position_usd=Decimal("5"),
                    check_balance=True,
                    normalize_leverage=True
                )
            except Exception as e:
                self.logger.log(
                    f"⛔ [SKIP] {symbol}: Leverage preparation failed - {e}",
                    "WARNING"
                )
                self.failed_symbols.add(symbol)
                return False
            
            size_usd = leverage_prep.adjusted_size_usd
            
            if leverage_prep.below_minimum:
                self.logger.log(
                    f"⛔ {symbol}: Position size too small after leverage adjustment (${size_usd:.2f})",
                    "WARNING"
                )
                self.failed_symbols.add(symbol)
                return False
            
            self.logger.log(
                f"🎯 Execution plan for {symbol}: "
                f"Long {long_dex.upper()} (${size_usd:.2f}) | "
                f"Short {short_dex.upper()} (${size_usd:.2f}) | "
                f"Divergence {opportunity.divergence*100:.3f}%",
                "INFO"
            )

            log_stage(self.logger, "Atomic Multi-Order Execution", icon="🧨", stage_id="3")
            
            # ⭐ ATOMIC EXECUTION: Both sides fill or neither ⭐
            result: AtomicExecutionResult = await self.atomic_executor.execute_atomically(
                orders=[
                    OrderSpec(
                        exchange_client=long_client,
                        symbol=symbol,
                        side="buy",
                        size_usd=size_usd,
                        execution_mode="limit_with_fallback",
                        timeout_seconds=30.0
                    ),
                    OrderSpec(
                        exchange_client=short_client,
                        symbol=symbol,
                        side="sell",
                        size_usd=size_usd,
                        execution_mode="limit_with_fallback",
                        timeout_seconds=30.0
                    )
                ],
                rollback_on_partial=True,  # 🚨 CRITICAL: Both must fill or rollback
                pre_flight_check=True,  # Check liquidity before placing
                skip_preflight_leverage=True,  # Already validated & normalized
                stage_prefix="3"
            )
            
            # Check if atomic execution succeeded
            if not result.all_filled:
                self.logger.log(
                    f"❌ {symbol}: Atomic execution failed - {result.error_message}",
                    "ERROR"
                )
                
                if result.rollback_performed:
                    self.logger.log(
                        f"🔄 Emergency rollback performed, cost: ${result.rollback_cost_usd:.2f}",
                        "WARNING"
                    )
                
                self.failed_symbols.add(symbol)
                return False  # Don't create position if execution failed
            
            # ✅ Both sides filled successfully
            long_fill = result.filled_orders[0]
            short_fill = result.filled_orders[1]
            
            # Calculate entry fees
            entry_fees = self.fee_calculator.calculate_total_cost(
                long_dex, short_dex, size_usd, is_maker=True
            )
            
            # Add actual slippage
            total_cost = entry_fees + result.total_slippage_usd
            
            # Create position record
            position = FundingArbPosition(
                id=uuid4(),
                symbol=symbol,
                long_dex=long_dex,
                short_dex=short_dex,
                size_usd=size_usd,
                entry_long_rate=opportunity.long_rate,
                entry_short_rate=opportunity.short_rate,
                entry_divergence=opportunity.divergence,
                opened_at=datetime.now(),
                total_fees_paid=total_cost
            )

            partial_fee = entry_fees / Decimal("2") if entry_fees else Decimal("0")
            timestamp_iso = datetime.now(timezone.utc).isoformat()
            position.metadata.update(
                {
                    "legs": {
                        long_dex: {
                            "side": "long",
                            "entry_price": long_fill.get("fill_price"),
                            "quantity": long_fill.get("filled_quantity"),
                            "fees_paid": partial_fee,
                            "slippage_usd": long_fill.get("slippage_usd"),
                            "execution_mode": long_fill.get("execution_mode_used"),
                            "exposure_usd": size_usd,
                            "last_updated": timestamp_iso,
                        },
                        short_dex: {
                            "side": "short",
                            "entry_price": short_fill.get("fill_price"),
                            "quantity": short_fill.get("filled_quantity"),
                            "fees_paid": partial_fee,
                            "slippage_usd": short_fill.get("slippage_usd"),
                            "execution_mode": short_fill.get("execution_mode_used"),
                            "exposure_usd": size_usd,
                            "last_updated": timestamp_iso,
                        },
                    },
                    "total_slippage_usd": result.total_slippage_usd,
                }
            )
            
            await self.position_manager.add_position(position)
            
            # 🔒 Mark that we've opened a position this session (Issue #4 fix)
            self.position_opened_this_session = True
            
            self.logger.log(
                f"✅ Position opened {symbol}: "
                f"Long @ ${long_fill['fill_price']}, "
                f"Short @ ${short_fill['fill_price']}, "
                f"Slippage: ${result.total_slippage_usd:.2f}, "
                f"Fees: ${entry_fees:.2f}",
                "INFO"
            )
            
            if self.one_position_per_session:
                self.logger.log(
                    "📊 Session limit: Will not open more positions this session (one_position_per_session=True)",
                    "INFO"
                )

            await self._set_dashboard_stage(
                LifecycleStage.MONITORING,
                f"Position opened {symbol}",
                category=TimelineCategory.EXECUTION,
            )
            await self._publish_dashboard_snapshot(f"Position opened {symbol}")
            
            return True  # Success
            
        except Exception as e:
            self.logger.log(
                f"❌ {opportunity.symbol}: Unexpected error - {e}",
                "ERROR"
            )
            self.failed_symbols.add(opportunity.symbol)
            return False
    
    # ========================================================================
    # Helper Methods
    # ========================================================================
    
    def _has_capacity(self) -> bool:
        """
        Check if can open more positions.
        
        Checks both:
        1. Global position limit (max_positions config)
        2. Session-level limit (one_position_per_session flag)
        
        Returns:
            True if under max position limit AND session limit allows
        """
        # Check global position limit
        open_count = len(self.position_manager._positions)
        if open_count >= self.config.max_positions:
            if not self._max_position_warning_logged:
                self.logger.log(
                    f"🚫 Max positions reached ({open_count}/{self.config.max_positions}). "
                    "Skipping new opportunities until capacity frees up.",
                    "INFO"
                )
                self._max_position_warning_logged = True
            return False
        else:
            self._max_position_warning_logged = False
        
        # 🔒 Check session-level limit (Issue #4 fix)
        # If one_position_per_session is enabled and we've already opened one, stop
        if self.one_position_per_session and self.position_opened_this_session:
            if not self._session_limit_warning_logged:
                self.logger.log(
                    "📊 Session limit reached: already opened 1 position this session. "
                    "Set one_position_per_session=False to allow multiple positions.",
                    "INFO"
                )
                self._session_limit_warning_logged = True
            return False
        else:
            self._session_limit_warning_logged = False

        return True
    
    def _calculate_total_exposure(self) -> Decimal:
        """
        Calculate total exposure across all positions.
        
        Returns:
            Total USD exposure
        """
        positions = self.position_manager._positions.values()
        return sum(p.size_usd for p in positions if p.status == "open")
    
    # ========================================================================
    # Strategy Status
    # ========================================================================
    
    async def get_strategy_status(self) -> Dict[str, Any]:
        """
        Get current strategy status.
        
        Returns:
            Dict with strategy metrics
        """
        positions = await self.position_manager.get_open_positions()
        
        total_exposure = self._calculate_total_exposure()
        total_pnl = sum(p.get_net_pnl() for p in positions)
        
        return {
            "strategy": "funding_arbitrage",
            "status": "running" if self.status.name == "RUNNING" else "stopped",
            "open_positions": len(positions),
            "total_exposure_usd": float(total_exposure),
            "total_pnl_usd": float(total_pnl),
            "exchanges": self.config.exchanges,
            "max_positions": self.config.max_positions,
            "positions": [p.to_dict() for p in positions]
        }

    # ========================================================================
    # Dashboard Helpers
    # ========================================================================

    async def _set_dashboard_stage(
        self,
        stage: LifecycleStage,
        message: Optional[str] = None,
        category: TimelineCategory = TimelineCategory.STAGE,
    ) -> None:
        if not getattr(self, "dashboard_service", None) or not self.dashboard_service.enabled:
            return

        stage_changed = stage != self._current_dashboard_stage
        if stage_changed:
            self._current_dashboard_stage = stage
            await self.dashboard_service.update_session(lifecycle_stage=stage)

        if message and (stage_changed or category != TimelineCategory.STAGE):
            event = TimelineEvent(
                ts=datetime.now(timezone.utc),
                category=category,
                message=message,
                metadata={},
            )
            await self.dashboard_service.publish_event(event)

    async def _publish_dashboard_snapshot(self, note: Optional[str] = None) -> None:
        if not getattr(self, "dashboard_service", None) or not self.dashboard_service.enabled:
            return

        positions = await self.position_manager.get_open_positions()
        snapshot = self._build_dashboard_snapshot(positions)
        await self.dashboard_service.publish_snapshot(snapshot)

        if note:
            event = TimelineEvent(
                ts=datetime.now(timezone.utc),
                category=TimelineCategory.INFO,
                message=note,
                metadata={},
            )
            await self.dashboard_service.publish_event(event)

    def _build_dashboard_snapshot(self, positions: List[FundingArbPosition]) -> DashboardSnapshot:
        position_snapshots = [self._position_to_snapshot(p) for p in positions]

        total_notional = sum((p.size_usd for p in positions), start=Decimal("0"))
        net_unrealized = sum((p.get_net_pnl() for p in positions), start=Decimal("0"))
        funding_total = sum(
            (self.position_manager.get_cumulative_funding(p.id) for p in positions),
            start=Decimal("0"),
        )

        portfolio = PortfolioSnapshot(
            total_positions=len(positions),
            total_notional_usd=total_notional,
            net_unrealized_pnl=net_unrealized,
            net_realized_pnl=Decimal("0"),
            funding_accrued=funding_total,
            alerts=[],
        )

        funding_snapshot = FundingSnapshot(
            total_accrued=funding_total,
            weighted_average_rate=None,
            next_event_countdown_seconds=None,
            rates=[],
        )

        return DashboardSnapshot(
            session=self.dashboard_service.session_state,
            positions=position_snapshots,
            portfolio=portfolio,
            funding=funding_snapshot,
            recent_events=[],
            generated_at=datetime.now(timezone.utc),
        )

    def _position_to_snapshot(self, position: FundingArbPosition) -> PositionSnapshot:
        legs_metadata = position.metadata.get("legs", {})
        leg_snapshots: List[PositionLegSnapshot] = []

        for venue, meta in legs_metadata.items():
            entry_price = meta.get("entry_price")
            if entry_price is not None and not isinstance(entry_price, Decimal):
                entry_price = Decimal(str(entry_price))

            quantity = meta.get("quantity")
            if quantity is not None and not isinstance(quantity, Decimal):
                quantity = Decimal(str(quantity))

            exposure = meta.get("exposure_usd", position.size_usd)
            if not isinstance(exposure, Decimal):
                exposure = Decimal(str(exposure))

            if quantity is None and entry_price and entry_price != 0:
                quantity = exposure / entry_price
            if quantity is None:
                quantity = Decimal("0")

            mark_price = meta.get("mark_price")
            if mark_price is not None and not isinstance(mark_price, Decimal):
                mark_price = Decimal(str(mark_price))

            leverage = meta.get("leverage")
            if leverage is not None and not isinstance(leverage, Decimal):
                leverage = Decimal(str(leverage))

            fees_paid = meta.get("fees_paid", Decimal("0"))
            if not isinstance(fees_paid, Decimal):
                fees_paid = Decimal(str(fees_paid))

            funding_accrued = meta.get("funding_accrued", Decimal("0"))
            if not isinstance(funding_accrued, Decimal):
                funding_accrued = Decimal(str(funding_accrued))

            realized_pnl = meta.get("realized_pnl", Decimal("0"))
            if not isinstance(realized_pnl, Decimal):
                realized_pnl = Decimal(str(realized_pnl))

            margin_reserved = meta.get("margin_reserved")
            if margin_reserved is not None and not isinstance(margin_reserved, Decimal):
                margin_reserved = Decimal(str(margin_reserved))

            updated_at = meta.get("last_updated", position.last_check or position.opened_at)
            if isinstance(updated_at, str):
                updated_at = datetime.fromisoformat(updated_at)

            leg_snapshots.append(
                PositionLegSnapshot(
                    venue=venue,
                    side=meta.get("side", "long"),
                    quantity=quantity,
                    exposure_usd=exposure,
                    entry_price=entry_price or Decimal("0"),
                    mark_price=mark_price,
                    leverage=leverage,
                    realized_pnl=realized_pnl,
                    fees_paid=fees_paid,
                    funding_accrued=funding_accrued,
                    margin_reserved=margin_reserved,
                    last_updated=updated_at,
                )
            )

        if not leg_snapshots:
            now = position.last_check or position.opened_at
            leg_snapshots = [
                PositionLegSnapshot(
                    venue=position.long_dex,
                    side="long",
                    quantity=Decimal("0"),
                    exposure_usd=position.size_usd,
                    entry_price=Decimal("0"),
                    last_updated=now,
                ),
                PositionLegSnapshot(
                    venue=position.short_dex,
                    side="short",
                    quantity=Decimal("0"),
                    exposure_usd=position.size_usd,
                    entry_price=Decimal("0"),
                    last_updated=now,
                ),
            ]

        erosion_ratio = position.get_profit_erosion()
        profit_erosion_pct = (Decimal("1") - erosion_ratio) * Decimal("100")

        funding_accrued = self.position_manager.get_cumulative_funding(position.id)
        lifecycle_stage = {
            "open": LifecycleStage.MONITORING,
            "pending_close": LifecycleStage.CLOSING,
            "closed": LifecycleStage.COMPLETE,
        }.get(position.status, LifecycleStage.MONITORING)

        last_update = position.last_check or position.opened_at

        return PositionSnapshot(
            position_id=position.id,
            symbol=position.symbol,
            strategy_tag="funding_arbitrage",
            opened_at=position.opened_at,
            last_update=last_update,
            lifecycle_stage=lifecycle_stage,
            legs=leg_snapshots,
            notional_exposure_usd=position.size_usd,
            entry_divergence_pct=position.entry_divergence,
            current_divergence_pct=position.current_divergence,
            profit_erosion_pct=profit_erosion_pct,
            unrealized_pnl=position.get_net_pnl(),
            realized_pnl=position.pnl_usd or Decimal("0"),
            funding_accrued=funding_accrued,
            rebalance_pending=position.rebalance_pending,
            max_position_age_seconds=int(self.config.risk_config.max_position_age_hours * 3600),
            custom_metadata={
                "rebalance_reason": position.rebalance_reason,
                "exit_reason": position.exit_reason,
            },
        )

    async def _handle_dashboard_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        command_type = payload.get("type")
        if command_type == "ping":
            return {"ok": True, "message": "pong"}

        if command_type == "close_position":
            position_id_raw = payload.get("position_id")
            if not position_id_raw:
                return {"ok": False, "error": "position_id missing"}
            try:
                position_id = UUID(str(position_id_raw))
            except ValueError:
                return {"ok": False, "error": "invalid position_id"}

            success, message = await self._handle_close_position_command(position_id)
            return {"ok": success, "message": message}

        if command_type == "pause_strategy":
            if self._manual_pause:
                return {"ok": True, "message": "Strategy already paused"}
            self._manual_pause = True
            await self._set_dashboard_stage(
                LifecycleStage.IDLE,
                "Strategy paused via control API",
                category=TimelineCategory.INFO,
            )
            await self.dashboard_service.publish_event(
                TimelineEvent(
                    ts=datetime.now(timezone.utc),
                    category=TimelineCategory.INFO,
                    message="Strategy paused via control API",
                    metadata={},
                )
            )
            self.logger.log("Strategy paused via control API", "INFO")
            return {"ok": True, "message": "Strategy paused"}

        if command_type == "resume_strategy":
            if not self._manual_pause:
                return {"ok": True, "message": "Strategy already running"}
            self._manual_pause = False
            await self._set_dashboard_stage(
                LifecycleStage.IDLE,
                "Strategy resumed via control API",
                category=TimelineCategory.INFO,
            )
            await self.dashboard_service.publish_event(
                TimelineEvent(
                    ts=datetime.now(timezone.utc),
                    category=TimelineCategory.INFO,
                    message="Strategy resumed via control API",
                    metadata={},
                )
            )
            self.logger.log("Strategy resumed via control API", "INFO")
            return {"ok": True, "message": "Strategy resumed"}

        return {"ok": False, "error": f"unknown command '{command_type}'"}

    async def _handle_close_position_command(self, position_id: UUID) -> Tuple[bool, str]:
        position = await self.position_manager.get_position(position_id)
        if not isinstance(position, FundingArbPosition):
            return False, "Position not found or already closed"

        if position.status != "open":
            return False, f"Position status is '{position.status}', cannot close"

        try:
            await self._set_dashboard_stage(
                LifecycleStage.CLOSING,
                f"Manual close requested for {position.symbol}",
                category=TimelineCategory.EXECUTION,
            )
            await self._close_position(position, "MANUAL_CLOSE")
            await self.dashboard_service.publish_event(
                TimelineEvent(
                    ts=datetime.now(timezone.utc),
                    category=TimelineCategory.INFO,
                    message=f"Manual close executed for {position.symbol}",
                    metadata={"position_id": str(position_id)},
                )
            )
            return True, "Close initiated"
        except Exception as exc:  # pragma: no cover - log and surface error
            self.logger.log(f"Manual close failed for {position_id}: {exc}", "ERROR")
            return False, str(exc)
    
    # ========================================================================
    # Abstract Method Implementations (Required by BaseStrategy)
    # ========================================================================
    
    async def _initialize_strategy(self):
        """Strategy-specific initialization logic."""
        # Initialize position and state managers
        await self.position_manager.initialize()
        await self.state_manager.initialize()
        if self.dashboard_service.enabled:
            await self.dashboard_service.start()
            await self._set_dashboard_stage(
                LifecycleStage.IDLE,
                "Strategy initialized",
                category=TimelineCategory.INFO,
            )
            await self._publish_dashboard_snapshot("Startup state captured")
            if self.control_server:
                self.control_server.register_command_handler(self._handle_dashboard_command)
                await self.control_server.start()
                self._control_server_started = True

        self.logger.log("FundingArbitrageStrategy initialized successfully")
    
    async def should_execute(self, market_data) -> bool:
        """
        Determine if strategy should execute based on market conditions.

        For funding arbitrage, we always check for opportunities.
        """
        return not self._manual_pause
    
    async def execute_strategy(self, market_data):
        """
        Execute the funding arbitrage strategy.
        
        This is the main entry point called by the trading bot.
        """
        from strategies.base_strategy import StrategyResult, StrategyAction
        
        try:
            # Run the 3-phase execution loop
            await self._set_dashboard_stage(
                LifecycleStage.MONITORING,
                "Monitoring active positions",
            )
            await self._monitor_positions()
            await self._publish_dashboard_snapshot()

            await self._set_dashboard_stage(
                LifecycleStage.CLOSING,
                "Evaluating exit conditions",
            )
            await self._check_exit_conditions() 

            await self._set_dashboard_stage(
                LifecycleStage.SCANNING,
                "Scanning for new opportunities",
            )
            await self._scan_opportunities()
            await self._set_dashboard_stage(
                LifecycleStage.IDLE,
                "Funding arbitrage cycle completed",
                category=TimelineCategory.INFO,
            )
            await self._publish_dashboard_snapshot()
            
            return StrategyResult(
                action=StrategyAction.WAIT,
                message="Funding arbitrage cycle completed"
            )
            
        except Exception as e:
            self.logger.log(f"Strategy execution failed: {e}")
            return StrategyResult(
                action=StrategyAction.NONE,
                message=f"Execution error: {e}"
            )
    
    def get_strategy_name(self) -> str:
        """Get the strategy name."""
        return "funding_arbitrage"
    
    def get_required_parameters(self) -> List[str]:
        """Get list of required strategy parameters."""
        return [
            "target_exposure",
            "min_profit_rate", 
            "exchanges"
        ]
    
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
    
