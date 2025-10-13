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

# Funding_arb operation helpers
from .monitoring import PositionMonitor
from .operations.position_opener import PositionOpener
from .operations.opportunity_scanner import OpportunityScanner
from .operations.position_closer import PositionCloser


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
            await self.position_monitor.monitor()
            
            # Phase 2: Check exits
            self.logger.log("Phase 2: Checking exit conditions", "INFO")
            closed = await self.position_closer.evaluate()
            actions_taken.extend(closed)
            
            # Phase 3: New opportunities (if capacity available)
            if self.opportunity_scanner.has_capacity():
                self.logger.log("Phase 3: Scanning new opportunities", "INFO")
                opportunities = await self.opportunity_scanner.scan()
                for opportunity in opportunities:
                    if not self.opportunity_scanner.has_capacity():
                        break
                    if not self.opportunity_scanner.should_take(opportunity):
                        continue
                    opened = await self.position_opener.open(opportunity)
                    if opened:
                        actions_taken.append(
                            f"Opened {opportunity.symbol} on {opportunity.long_dex}/{opportunity.short_dex}"
                        )
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
    # Strategy Status
    # ========================================================================
    
    async def get_strategy_status(self) -> Dict[str, Any]:
        """
        Get current strategy status.
        
        Returns:
            Dict with strategy metrics
        """
        positions = await self.position_manager.get_open_positions()
        
        total_exposure = self.opportunity_scanner.calculate_total_exposure()
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
            await self.position_closer.close(position, "MANUAL_CLOSE")
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
            await self.position_monitor.monitor()
            await self._publish_dashboard_snapshot()

            await self._set_dashboard_stage(
                LifecycleStage.CLOSING,
                "Evaluating exit conditions",
            )
            
            await self.position_closer.evaluate() 

            await self._set_dashboard_stage(
                LifecycleStage.SCANNING,
                "Scanning for new opportunities",
            )
            opportunities = await self.opportunity_scanner.scan()
            for opportunity in opportunities:
                if not self.opportunity_scanner.has_capacity():
                    break
                if not self.opportunity_scanner.should_take(opportunity):
                    continue
                await self.position_opener.open(opportunity)
            await self._set_dashboard_stage(
                LifecycleStage.IDLE,
                "Funding arbitrage cycle completed",
                category=TimelineCategory.INFO,
            )
            await self._publish_dashboard_snapshot()
            
            return StrategyResult(
                action=StrategyAction.WAIT,
                message="Funding arbitrage cycle completed",
                wait_time=self.config.risk_config.check_interval_seconds,
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
    
