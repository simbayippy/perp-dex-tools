"""
Account manager module for Lighter client.

Handles account balance, leverage info, user stats, and account queries.
"""

import asyncio
from decimal import Decimal
from typing import Any, Dict, Optional

import lighter

class LighterAccountManager:
    """
    Account manager for Lighter exchange.
    
    Handles:
    - Account balance queries (WebSocket-first)
    - User stats WebSocket updates
    - Leverage information
    - Account value queries
    """
    
    def __init__(
        self,
        account_api: Any,
        order_api: Any,
        api_client: Any,
        config: Any,
        logger: Any,
        account_index: int,
        user_stats: Optional[Dict[str, Any]],
        user_stats_lock: asyncio.Lock,
        user_stats_ready: asyncio.Event,
        min_order_notional: Dict[str, Decimal],
        market_data_manager: Optional[Any] = None,
        normalize_symbol_fn: Optional[Any] = None,
    ):
        """
        Initialize account manager.
        
        Args:
            account_api: Lighter AccountApi instance
            order_api: Lighter OrderApi instance (for leverage queries)
            api_client: Lighter ApiClient instance (for creating OrderApi if needed)
            config: Trading configuration object
            logger: Logger instance
            account_index: Account index
            user_stats: Dictionary to cache user stats (client._user_stats)
            user_stats_lock: Lock for thread-safe user stats access
            user_stats_ready: Event signaling user stats are ready
            min_order_notional: Dictionary of min order notional per symbol
            market_data_manager: Optional market data manager (for market ID lookup)
            normalize_symbol_fn: Function to normalize symbols
        """
        self.account_api = account_api
        self.order_api = order_api
        self.api_client = api_client
        self.config = config
        self.logger = logger
        self.account_index = account_index
        self.user_stats = user_stats
        self.user_stats_lock = user_stats_lock
        self.user_stats_ready = user_stats_ready
        self.min_order_notional = min_order_notional
        self.market_data = market_data_manager
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
    
    async def handle_user_stats_update(self, payload: Dict[str, Any]) -> None:
        """
        Process user stats update from WebSocket (includes real-time balance).
        
        ⚡ OPTIMIZATION: WebSocket balance updates are FREE (0 weight) vs 300 weight REST call!
        """
        stats = payload.get("stats")
        if not stats:
            return
        
        # Check if this is first update (before setting the flag)
        is_first_update = not self.user_stats_ready.is_set()
        
        async with self.user_stats_lock:
            # Update the reference dictionary (pointed to by client)
            if self.user_stats is not None:
                self.user_stats.clear()
                self.user_stats.update(stats)
            self.user_stats_ready.set()
        
        # Log first balance update for visibility
        if is_first_update:
            available_balance = stats.get("available_balance", "N/A")
            self.logger.debug(
                f"[LIGHTER] Received user stats via WebSocket: balance={available_balance} (0 weight)"
            )
    
    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Get current account balance (WebSocket-first for 0 weight).
        
        ⚡ OPTIMIZATION: Uses WebSocket user_stats stream to save 300 weight REST call!
        The user_stats WebSocket provides real-time balance updates for FREE.
        """
        try:
            # Try WebSocket user stats first (0 weight!)
            async with self.user_stats_lock:
                if self.user_stats is not None:
                    available_balance = self.user_stats.get("available_balance")
                    if available_balance is not None:
                        try:
                            return Decimal(str(available_balance))
                        except Exception:
                            pass
            
            # Wait briefly for WebSocket data if not ready yet
            try:
                await asyncio.wait_for(self.user_stats_ready.wait(), timeout=0.5)
                # Try again after waiting
                async with self.user_stats_lock:
                    if self.user_stats is not None:
                        available_balance = self.user_stats.get("available_balance")
                        if available_balance is not None:
                            try:
                                return Decimal(str(available_balance))
                            except Exception:
                                pass
            except asyncio.TimeoutError:
                pass
            
            # Fall back to REST only if WebSocket not available (300 weight)
            if not self.account_api:
                return None
            
            self.logger.info("[LIGHTER] get_account_balance WebSocket user_stats not available, using REST fallback (300 weight)")
            account_data = await self.account_api.account(by="index", value=str(self.account_index))
            if account_data and account_data.accounts:
                return Decimal(account_data.accounts[0].available_balance or "0")
            return None
        except Exception as e:
            self.logger.error(f"Error getting account balance: {e}")
            return None
    
    def get_min_order_notional(self, symbol: str) -> Optional[Decimal]:
        """
        Return the minimum quote notional required for orders on the given symbol.
        """
        normalized = self.normalize_symbol(symbol)
        return self.min_order_notional.get(normalized)
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage information for Lighter by querying market configuration.
        
        Leverages Lighter SDK's order_book_details endpoint to get margin requirements.
        
        Args:
            symbol: Trading symbol (e.g., "ZORA", "BTC")
            
        Returns:
            Dictionary with leverage limits based on margin fractions
        """
        try:
            # Initialize order API if needed
            if not self.order_api:
                self.order_api = lighter.OrderApi(self.api_client)
            
            # Normalize symbol and get market ID
            normalized_symbol = self.normalize_symbol(symbol)
            market_id = None
            
            if self.market_data:
                market_id = await self.market_data.get_market_id_for_symbol(normalized_symbol)
            
            if market_id is None:
                self.logger.error(
                    f"[LIGHTER] Could not find market for {symbol} - symbol may not be listed"
                )
                return {
                    'max_leverage': None,
                    'max_notional': None,
                    'account_leverage': None,
                    'margin_requirement': None,
                    'brackets': None,
                    'error': f"Symbol {symbol} not found on Lighter"
                }
            
            # Query market details
            market_details_response = await self.order_api.order_book_details(
                market_id=market_id,
                _request_timeout=10
            )
            
            if not market_details_response or not market_details_response.order_book_details:
                self.logger.error(
                    f"[LIGHTER] No market details found for {symbol} (market_id={market_id})"
                )
                return {
                    'max_leverage': None,
                    'max_notional': None,
                    'account_leverage': None,
                    'margin_requirement': None,
                    'brackets': None,
                    'error': f"No market details available for {symbol} on Lighter"
                }
            
            # Get first (and should be only) market detail
            market_detail = market_details_response.order_book_details[0]
            
            # Extract margin fractions
            # ⚠️ CRITICAL FIX: Lighter uses BASIS POINTS, not 1e18!
            # Based on SDK code: imf = int(10_000 / leverage) → 10,000 = 100%
            BASIS_POINTS_DIVISOR = Decimal('10000')  # 10,000 = 100%
            
            # min_initial_margin_fraction is the minimum margin requirement
            # Max leverage = 1 / min_initial_margin_fraction
            min_margin_fraction_int = market_detail.min_initial_margin_fraction
            min_margin_fraction = Decimal(str(min_margin_fraction_int)) / BASIS_POINTS_DIVISOR
            
            # Calculate max leverage
            if min_margin_fraction > 0:
                max_leverage = Decimal('1') / min_margin_fraction
            else:
                max_leverage = Decimal('20')  # Fallback
            
            # Also get maintenance margin for reference
            maintenance_margin_int = market_detail.maintenance_margin_fraction
            maintenance_margin = Decimal(str(maintenance_margin_int)) / BASIS_POINTS_DIVISOR
            
            # Try to get account-level leverage (current usage)
            account_leverage = None
            try:
                if self.account_api:
                    account_data = await self.account_api.account(
                        by="index", 
                        value=str(self.account_index)
                    )
                    
                    if account_data and account_data.accounts:
                        account = account_data.accounts[0]
                        
                        # Look for position in this specific market
                        for position in account.positions:
                            if position.market_id == market_id:
                                # Found position - get its leverage
                                if hasattr(position, 'initial_margin_fraction'):
                                    imf_int = position.initial_margin_fraction
                                    imf = Decimal(str(imf_int)) / BASIS_POINTS_DIVISOR
                                    if imf > 0:
                                        account_leverage = Decimal('1') / imf
                                break
                        
                        # Fallback: check account-level leverage
                        if account_leverage is None and hasattr(account, 'leverage'):
                            account_leverage = Decimal(str(account.leverage))
                            
            except Exception as e:
                self.logger.debug(f"Could not get account leverage: {e}")
            
            self.logger.info(
                f"📊 [LIGHTER] Leverage info for {symbol}:\n"
                f"  - Symbol max leverage: {max_leverage:.1f}x\n"
                f"  - Account leverage: {account_leverage}x\n"
                f"  - Max notional: None\n"
                f"  - Margin requirement: {min_margin_fraction} ({min_margin_fraction*100:.1f}%)"
            )
            
            return {
                'max_leverage': max_leverage,
                'max_notional': None,  # Lighter doesn't have explicit notional limits per se
                'account_leverage': account_leverage,
                'margin_requirement': min_margin_fraction,
                'brackets': None,  # Lighter uses fixed margin, not brackets
                'error': None  # No error - successful query
            }
        
        except Exception as e:
            self.logger.error(
                f"❌ [LIGHTER] Error getting leverage info for {symbol}: {e}"
            )
            # Return error state instead of fallback
            return {
                'max_leverage': None,
                'max_notional': None,
                'account_leverage': None,
                'margin_requirement': None,
                'brackets': None,
                'error': f"Failed to query leverage info: {str(e)}"
            }
    
    async def get_total_asset_value(self) -> Optional[Decimal]:
        """Get total account asset value using Lighter SDK."""
        try:
            if not self.account_api:
                return None
                
            account_data = await self.account_api.account(by="index", value=str(self.account_index))
            if account_data and account_data.accounts:
                # Lighter provides total_value or similar field
                account = account_data.accounts[0]
                if hasattr(account, 'total_value'):
                    return Decimal(str(account.total_value))
                # Fallback: sum positions + balance
                total = Decimal(str(account.available_balance or "0"))
                for position in account.positions:
                    if hasattr(position, 'position_value'):
                        total += Decimal(str(position.position_value))
                return total
            return None
        except Exception as e:
            self.logger.error(f"Error getting total asset value: {e}")
            return None

