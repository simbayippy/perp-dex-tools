"""
Account manager module for Paradex client.

Handles account balance, PnL, leverage info, and account queries.
"""

import asyncio
from decimal import Decimal
from typing import Any, Dict, Optional

from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from exchange_clients.paradex.client.utils.helpers import to_decimal
from exchange_clients.paradex.common import normalize_symbol


class ParadexAccountManager:
    """
    Account manager for Paradex exchange.
    
    Handles:
    - Account balance queries
    - Account PnL queries
    - Leverage information
    - Account value queries
    """
    
    def __init__(
        self,
        api_client: Any,
        config: Any,
        logger: Any,
        market_data_manager: Optional[Any] = None,
        normalize_symbol_fn: Optional[Any] = None,
    ):
        """
        Initialize account manager.
        
        Args:
            api_client: Paradex API client instance (paradex.api_client)
            config: Trading configuration object
            logger: Logger instance
            market_data_manager: Optional market data manager (for leverage info)
            normalize_symbol_fn: Function to normalize symbols
        """
        self.api_client = api_client
        self.config = config
        self.logger = logger
        self.market_data = market_data_manager
        self.normalize_symbol = normalize_symbol_fn or normalize_symbol
    
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_fixed(3),
        retry=retry_if_exception_type(Exception),
        reraise=True
    )
    def _fetch_account_summary_sync(self) -> Any:
        """
        Fetch account summary synchronously (SDK is blocking).
        
        Returns:
            AccountSummary object
        """
        return self.api_client.fetch_account_summary()
    
    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Get available account balance from Paradex.
        
        Uses fetch_account_summary() which returns free_collateral.
        Free collateral = Account value in excess of Initial Margin required.
        
        Returns:
            Available balance (free collateral) in USD, or None if query fails
        """
        try:
            # Fetch account summary (synchronous SDK call in executor)
            loop = asyncio.get_event_loop()
            summary = await loop.run_in_executor(
                None,
                self._fetch_account_summary_sync
            )
            
            # Extract free_collateral (available balance)
            if hasattr(summary, 'free_collateral'):
                free_collateral = to_decimal(summary.free_collateral)
            elif isinstance(summary, dict):
                free_collateral = to_decimal(summary.get('free_collateral'))
            else:
                free_collateral = None
            
            if free_collateral is not None:
                self.logger.debug(
                    f"[PARADEX] Available balance (free collateral): ${free_collateral:.2f}"
                )
                return free_collateral
            
            self.logger.warning("[PARADEX] No free_collateral in account summary")
            return None
            
        except Exception as e:
            self.logger.error(f"[PARADEX] Failed to get account balance: {e}")
            return None
    
    async def get_account_pnl(self) -> Optional[Decimal]:
        """
        Get account unrealized P&L from Paradex.
        
        Calculated as: Account Value - Total Collateral (or sum of unrealized PnL from positions).
        
        Returns:
            Unrealized P&L in USD, or None if query fails
        """
        try:
            # Fetch account summary
            loop = asyncio.get_event_loop()
            summary = await loop.run_in_executor(
                None,
                self._fetch_account_summary_sync
            )
            
            # Account value includes unrealized PnL
            # We can calculate unrealized PnL as: account_value - total_collateral
            if hasattr(summary, 'account_value') and hasattr(summary, 'total_collateral'):
                account_value = to_decimal(summary.account_value)
                total_collateral = to_decimal(summary.total_collateral)
                
                if account_value is not None and total_collateral is not None:
                    unrealized_pnl = account_value - total_collateral
                    return unrealized_pnl
            
            # Alternative: Sum unrealized PnL from all positions
            positions_response = self.api_client.fetch_positions()
            if positions_response and 'results' in positions_response:
                total_unrealized = Decimal("0")
                for position in positions_response['results']:
                    if isinstance(position, dict):
                        status = str(position.get('status', '')).upper()
                        if status == 'OPEN':
                            unrealized = to_decimal(position.get('unrealized_pnl'))
                            if unrealized:
                                total_unrealized += unrealized
                
                if total_unrealized != Decimal("0"):
                    return total_unrealized
            
            return None
            
        except Exception as e:
            self.logger.error(f"[PARADEX] Failed to get account PnL: {e}")
            return None
    
    async def get_total_asset_value(self) -> Optional[Decimal]:
        """
        Get total account asset value (balance + unrealized P&L) from Paradex.
        
        This is equivalent to account_value from account summary.
        
        Returns:
            Total asset value in USD, or None if query fails
        """
        try:
            # Fetch account summary
            loop = asyncio.get_event_loop()
            summary = await loop.run_in_executor(
                None,
                self._fetch_account_summary_sync
            )
            
            # Extract account_value
            if hasattr(summary, 'account_value'):
                account_value = to_decimal(summary.account_value)
            elif isinstance(summary, dict):
                account_value = to_decimal(summary.get('account_value'))
            else:
                account_value = None
            
            if account_value is not None:
                return account_value
            
            return None
            
        except Exception as e:
            self.logger.error(f"[PARADEX] Failed to get total asset value: {e}")
            return None
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage and position limit information for a symbol.
        
        ⚠️ CRITICAL for delta-neutral strategies: Different exchanges have different
        leverage limits for the same symbol.
        
        Args:
            symbol: Trading symbol (normalized format, e.g., "BTC", "ETH")
            
        Returns:
            Dictionary with leverage limits:
            {
                'max_leverage': Decimal or None,
                'max_notional': Decimal or None,
                'margin_requirement': Decimal or None,
                'brackets': List or None,
                'error': str or None  # Error message if data unavailable
            }
        """
        contract_id = f"{symbol.upper()}-USD-PERP"
        
        try:
            # Fetch market metadata (includes max_leverage)
            if self.market_data:
                metadata = await self.market_data.get_market_metadata(contract_id)
            else:
                # Fallback: fetch directly
                markets_response = self.api_client.fetch_markets({"market": contract_id})
                if markets_response and 'results' in markets_response:
                    markets = markets_response['results']
                    if markets:
                        market = markets[0]
                        # Extract delta1_cross_margin_params for leverage calculation
                        delta1_params = market.get('delta1_cross_margin_params', {})
                        imf_base = to_decimal(delta1_params.get('imf_base') if isinstance(delta1_params, dict) else None)
                        mmf_factor = to_decimal(delta1_params.get('mmf_factor') if isinstance(delta1_params, dict) else None)
                        
                        metadata = {
                            'max_order_size': to_decimal(market.get('max_order_size')),
                            'position_limit': to_decimal(market.get('position_limit')),
                            'imf_base': imf_base,
                            'mmf_factor': mmf_factor,
                        }
                    else:
                        metadata = None
                else:
                    metadata = None
            
            leverage_info = {
                'max_leverage': None,
                'max_notional': None,
                'margin_requirement': None,
                'brackets': None,
                'error': None,
            }
            
            if metadata:
                # Calculate max leverage from IMF (Paradex doesn't provide max_leverage directly)
                # Similar to Lighter: max_leverage = 1 / initial_margin_fraction
                # Similar to Backpack: max_leverage = 1 / imf_base
                imf_base = metadata.get('imf_base')  # Initial Margin Base (e.g., 0.11 = 11%)
                mmf_factor = metadata.get('mmf_factor')  # Maintenance Margin Factor
                max_order_size = metadata.get('max_order_size')
                position_limit = metadata.get('position_limit')
                
                max_leverage = None
                if imf_base is not None and imf_base > 0:
                    # imf_base is the margin fraction (e.g., 0.11 = 11% margin = 9.09x leverage)
                    max_leverage = Decimal('1') / imf_base
                    self.logger.debug(
                        f"[PARADEX] Calculated max_leverage from imf_base: {max_leverage:.2f}x "
                        f"(imf_base={imf_base})"
                    )
                
                if max_leverage:
                    leverage_info['max_leverage'] = max_leverage
                    # Margin requirement is the inverse of leverage
                    leverage_info['margin_requirement'] = Decimal('1') / max_leverage
                
                # Use position_limit as max_notional if available
                if position_limit:
                    leverage_info['max_notional'] = position_limit
                elif max_order_size:
                    # Estimate max_notional from max_order_size (approximate)
                    leverage_info['max_notional'] = max_order_size
                
                # Include maintenance margin info if available
                if mmf_factor is not None and imf_base is not None:
                    maintenance_margin = imf_base * mmf_factor
                    leverage_info['maintenance_margin'] = maintenance_margin
                
                # Log leverage info (similar to Lighter)
                account_leverage = None  # Paradex doesn't have account-level leverage setting
                margin_req = leverage_info.get('margin_requirement')
                margin_req_str = f"{margin_req:.4f} ({margin_req*100:.1f}%)" if margin_req else "N/A"
                max_notional_str = str(leverage_info.get('max_notional', 'N/A'))
                
                self.logger.info(
                    f"📊 [PARADEX] Leverage info for {symbol}:\n"
                    f"  - Symbol max leverage: {max_leverage:.1f}x\n"
                    f"  - Account leverage: N/Ax\n"
                    f"  - Max notional: {max_notional_str}\n"
                    f"  - Margin requirement: {margin_req_str}"
                )
            else:
                leverage_info['error'] = f"Symbol {symbol} not found on Paradex"
            
            # If no leverage info found, return error
            if leverage_info['max_leverage'] is None and leverage_info['error'] is None:
                leverage_info['error'] = f"Unable to determine leverage limits for {symbol}"
            
            return leverage_info
            
        except Exception as e:
            self.logger.error(f"[PARADEX] Error getting leverage info for {symbol}: {e}")
            return {
                'max_leverage': None,
                'max_notional': None,
                'margin_requirement': None,
                'brackets': None,
                'error': f"Failed to query leverage info: {str(e)}",
            }

