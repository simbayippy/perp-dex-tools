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
from strategies.components import FeeCalculator, InMemoryPositionManager
from .config import FundingArbConfig
from .models import FundingArbPosition, OpportunityData
from .funding_analyzer import FundingRateAnalyzer

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

from typing import Dict, Any, List, Tuple
from decimal import Decimal
from datetime import datetime
from uuid import uuid4

# Execution layer imports
from strategies.execution.patterns.atomic_multi_order import (
    AtomicMultiOrderExecutor,
    OrderSpec,
    AtomicExecutionResult
)
from strategies.execution.core.liquidity_analyzer import LiquidityAnalyzer


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
        
        super().__init__(funding_config, exchange_client)
        self.config = funding_config  # Store the converted config
        self.exchange_clients = exchange_clients  # Store the exchange clients dict
        
        # For funding arbitrage, we need multiple exchanges
        # If only one exchange client provided, log a warning but continue
        available_exchanges = list(exchange_clients.keys())
        required_exchanges = funding_config.exchanges
        
        missing_exchanges = [dex for dex in required_exchanges if dex not in exchange_clients]
        if missing_exchanges:
            self.logger.log(f"Warning: Missing exchange clients for: {missing_exchanges}")
            self.logger.log(f"Available exchanges: {available_exchanges}")
            self.logger.log("Strategy will only look for opportunities between available exchanges")
            
            # Update config to only use available exchanges
            funding_config.exchanges = [dex for dex in required_exchanges if dex in exchange_clients]
            
            if not funding_config.exchanges:
                raise ValueError(f"No valid exchange clients available. Required: {required_exchanges}, Available: {available_exchanges}")
        
        # ⭐ Core components from Hummingbot pattern
        self.analyzer = FundingRateAnalyzer()
        self.fee_calculator = FeeCalculator()
        
        # ⭐ Direct internal services (no HTTP, shared database)
        if not FUNDING_SERVICE_AVAILABLE:
            raise RuntimeError(
                "Funding rate service is not available. "
                "Please ensure the funding_rate_service is properly configured and the database is accessible."
            )
        
        from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
        self.opportunity_finder = OpportunityFinder(
            database=database,
            fee_calculator=self.fee_calculator,
            dex_mapper=dex_mapper,
            symbol_mapper=symbol_mapper
        )
        self.funding_rate_repo = FundingRateRepository(database)
        
        # ⭐ Execution layer (atomic delta-neutral execution)
        self.atomic_executor = AtomicMultiOrderExecutor()
        self.liquidity_analyzer = LiquidityAnalyzer(
            max_slippage_pct=Decimal("0.005"),  # 0.5% max slippage
            max_spread_bps=50,  # 50 basis points
            min_liquidity_score=0.6
        )
        
        # ⭐ Position and state management (database-backed)
        from .position_manager import FundingArbPositionManager
        from .state_manager import FundingArbStateManager
        
        self.position_manager = FundingArbPositionManager()
        self.state_manager = FundingArbStateManager()
        
        # Tracking
        self.cumulative_funding = {}  # {position_id: Decimal}
    
    
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
        exchanges_str = strategy_params.get('exchanges', trading_config.exchange)
        if isinstance(exchanges_str, str):
            exchanges = [ex.strip() for ex in exchanges_str.split(',')]
        else:
            exchanges = [trading_config.exchange]
        
        from .config import RiskManagementConfig
        from funding_rate_service.config import settings
        
        return FundingArbConfig(
            exchange=trading_config.exchange,  # Primary exchange
            exchanges=exchanges,  # All exchanges for arbitrage
            symbols=[trading_config.ticker],
            max_positions=strategy_params.get('max_positions', 5),
            max_position_size_usd=Decimal(str(getattr(trading_config, 'target_exposure', 100.0))),
            min_profit=Decimal(str(getattr(trading_config, 'min_profit_rate', 0.0001))),
            max_oi_usd=Decimal(str(strategy_params.get('max_oi_usd', 10000000.0))),  # 10M default
            max_new_positions_per_cycle=strategy_params.get('max_new_positions_per_cycle', 2),
            # Required database URL from funding_rate_service settings
            database_url=settings.database_url,
            # Risk management defaults
            risk_config=RiskManagementConfig(),
            # Ticker for logging
            ticker=trading_config.ticker
            # Note: bridge_settings not implemented yet
        )
 
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
            # ⭐ Direct internal service call (no HTTP)
            # Use opportunity finder to find opportunities
            from funding_rate_service.models.filters import OpportunityFilter
            
            filters = OpportunityFilter(
                min_profit_percent=self.config.min_profit,
                max_oi_usd=self.config.max_oi_usd,
                whitelist_dexes=self.config.exchanges if self.config.exchanges else None,
                symbol=None,  # Don't filter by symbol - look at all opportunities
                limit=10
            )
            
            # 🔍 DEBUG: Log the filters being used
            self.logger.log(f"DEBUG: Filters - min_profit: {self.config.min_profit}, max_oi_usd: {self.config.max_oi_usd}, whitelist_dexes: {self.config.exchanges}")
            
            opportunities = await self.opportunity_finder.find_opportunities(filters)
            
            self.logger.log(
                f"Found {len(opportunities)} opportunities",
                "INFO"
            )
            
            # Take top opportunities up to limit
            max_new = self.config.max_new_positions_per_cycle
            for opp in opportunities[:max_new]:
                if self._should_take_opportunity(opp):
                    await self._open_position(opp)
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
    
    async def _open_position(self, opportunity):
        """
        Open delta-neutral position from opportunity using atomic execution.
        
        ⭐ Uses AtomicMultiOrderExecutor for safety ⭐
        
        Args:
            opportunity: ArbitrageOpportunity object
        """
        try:
            symbol = opportunity.symbol
            long_dex = opportunity.long_dex
            short_dex = opportunity.short_dex
            size_usd = self.config.default_position_size_usd
            
            # Get exchange clients
            self.logger.log(f"DEBUG: Available exchange clients: {list(self.exchange_clients.keys())}")
            self.logger.log(f"DEBUG: Looking for long_dex: {long_dex}, short_dex: {short_dex}")
            
            long_client = self.exchange_clients.get(long_dex)
            short_client = self.exchange_clients.get(short_dex)
            
            if long_client is None:
                self.logger.log(f"ERROR: No exchange client found for long_dex: {long_dex}")
                return
            if short_client is None:
                self.logger.log(f"ERROR: No exchange client found for short_dex: {short_dex}")
                return
            
            self.logger.log(
                f"🎯 Opening {symbol}: "
                f"Long {long_dex}, Short {short_dex}, "
                f"Size=${size_usd}, Divergence={opportunity.divergence*100:.3f}%",
                "INFO"
            )
            
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
                pre_flight_check=True  # Check liquidity before placing
            )
            
            # Check if atomic execution succeeded
            if not result.all_filled:
                self.logger.log(
                    f"❌ Atomic execution failed for {symbol}: {result.error_message}",
                    "ERROR"
                )
                
                if result.rollback_performed:
                    self.logger.log(
                        f"🔄 Emergency rollback performed, cost: ${result.rollback_cost_usd:.2f}",
                        "WARNING"
                    )
                
                return  # Don't create position if execution failed
            
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
            
            await self.position_manager.add_position(position)
            
            self.logger.log(
                f"✅ Position opened {symbol}: "
                f"Long @ ${long_fill['fill_price']}, "
                f"Short @ ${short_fill['fill_price']}, "
                f"Slippage: ${result.total_slippage_usd:.2f}, "
                f"Fees: ${entry_fees:.2f}",
                "INFO"
            )
            
        except Exception as e:
            self.logger.log(
                f"Error opening position for {opportunity.symbol}: {e}",
                "ERROR"
            )
            raise
    
    # ========================================================================
    # Helper Methods
    # ========================================================================
    
    def _has_capacity(self) -> bool:
        """
        Check if can open more positions.
        
        Returns:
            True if under max position limit
        """
        # Use synchronous call since we're in sync context
        # In real implementation, would use async properly
        open_count = len(self.position_manager._positions_cache)
        return open_count < self.config.max_positions
    
    def _calculate_total_exposure(self) -> Decimal:
        """
        Calculate total exposure across all positions.
        
        Returns:
            Total USD exposure
        """
        positions = self.position_manager._positions_cache.values()
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
    # Abstract Method Implementations (Required by BaseStrategy)
    # ========================================================================
    
    async def _initialize_strategy(self):
        """Strategy-specific initialization logic."""
        # Initialize position and state managers
        await self.position_manager.initialize()
        await self.state_manager.initialize()
        
        self.logger.log("FundingArbitrageStrategy initialized successfully")
    
    async def should_execute(self, market_data) -> bool:
        """
        Determine if strategy should execute based on market conditions.
        
        For funding arbitrage, we always check for opportunities.
        """
        return True
    
    async def execute_strategy(self, market_data):
        """
        Execute the funding arbitrage strategy.
        
        This is the main entry point called by the trading bot.
        """
        from strategies.base_strategy import StrategyResult, StrategyAction
        
        try:
            # Run the 3-phase execution loop
            await self._monitor_positions()
            await self._check_exit_conditions() 
            await self._scan_opportunities()
            
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
        
        await super().cleanup()
    
