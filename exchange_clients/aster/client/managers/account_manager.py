"""
Account manager module for Aster client.

Handles account queries, balance, leverage management, and min order notional.
"""

from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from exchange_clients.aster.client.utils.helpers import to_decimal
from exchange_clients.aster.common import get_aster_symbol_format


class AsterAccountManager:
    """
    Account manager for Aster exchange.
    
    Handles:
    - Account balance queries
    - Leverage management (get/set)
    - Leverage info with brackets
    - Min order notional queries
    """
    
    def __init__(
        self,
        make_request_fn: Callable,
        config: Any,
        logger: Any,
        min_order_notional: Dict[str, Decimal],
        normalize_symbol_fn: Optional[Callable[[str], str]] = None,
    ):
        """
        Initialize account manager.
        
        Args:
            make_request_fn: Function to make authenticated API requests
            config: Trading configuration object
            logger: Logger instance
            min_order_notional: Min order notional cache dict
            normalize_symbol_fn: Function to normalize symbols
        """
        self._make_request = make_request_fn
        self.config = config
        self.logger = logger
        self.min_order_notional = min_order_notional
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
    
    async def get_account_balance(self) -> Optional[Decimal]:
        """
        Get available account balance from Aster.
        
        Uses GET /fapi/v4/account endpoint to get account information.
        Returns available balance that can be used to open new positions.
        
        Returns:
            Available balance in USDT, or None if query fails
        """
        try:
            result = await self._make_request('GET', '/fapi/v4/account')
            
            # Get available balance from response
            # Can use either:
            # 1. availableBalance (top-level, total available across all assets)
            # 2. assets[].availableBalance (per-asset breakdown)
            
            available_balance = result.get('availableBalance')
            if available_balance is not None:
                balance = Decimal(str(available_balance))
                self.logger.debug(
                    f"[ASTER] Available balance: ${balance:.2f}"
                )
                return balance
            
            # Fallback: Sum available balance from assets array
            assets = result.get('assets', [])
            total_available = Decimal('0')
            for asset in assets:
                if asset.get('asset') == 'USDT':  # Primary trading asset
                    asset_available = asset.get('availableBalance', 0)
                    total_available += Decimal(str(asset_available))
            
            if total_available > 0:
                self.logger.debug(
                    f"[ASTER] Available balance (from assets): ${total_available:.2f}"
                )
                return total_available
            
            # No balance data available
            self.logger.warning("[ASTER] No balance data in account response")
            return None
            
        except Exception as e:
            self.logger.warning(f"[ASTER] Failed to get account balance: {e}")
            return None

    async def get_account_leverage(self, symbol: str) -> Optional[int]:
        """
        Get current account leverage setting for a symbol from Aster.
        
        Aster uses Binance Futures-compatible API.
        Endpoint: GET /fapi/v2/positionRisk
        
        Args:
            symbol: Trading symbol (e.g., "ZORA", or full contract_id like "1000FLOKIUSDT")
            
        Returns:
            Current leverage multiplier (e.g., 10 for 10x), or None if unavailable
        """
        try:
            # Use same normalization logic as set_account_leverage
            if symbol.upper().endswith("USDT"):
                normalized_symbol = symbol.upper()
            elif hasattr(self.config, 'contract_id') and self.config.contract_id:
                contract_id = self.config.contract_id.upper()
                symbol_upper = symbol.upper()
                if symbol_upper in contract_id and contract_id.endswith("USDT"):
                    normalized_symbol = contract_id
                else:
                    normalized_symbol = f"{symbol_upper}USDT"
            else:
                normalized_symbol = f"{symbol.upper()}USDT"
            result = await self._make_request('GET', '/fapi/v2/positionRisk', {'symbol': normalized_symbol})
            
            if result and len(result) > 0:
                # positionRisk returns array, take first position
                position_info = result[0]
                leverage = int(position_info.get('leverage', 0))
                
                self.logger.debug(
                    f"📊 [ASTER] Account leverage for {symbol}: {leverage}x"
                )
                
                return leverage if leverage > 0 else None
            
            return None
        
        except Exception as e:
            self.logger.warning(f"Could not get account leverage for {symbol}: {e}")
            return None
    
    async def set_account_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set account leverage for a symbol on Aster.
        
        ⚠️ WARNING: Only call this if you want to change leverage settings!
        This is a TRADE endpoint that modifies account settings.
        
        Endpoint: POST /fapi/v1/leverage
        
        Args:
            symbol: Trading symbol (e.g., "ZORA", or full contract_id like "1000FLOKIUSDT")
            leverage: Target leverage (1 to 125)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if leverage < 1 or leverage > 125:
                self.logger.error(
                    f"[ASTER] Invalid leverage value: {leverage}. Must be between 1 and 125"
                )
                return False
            
            # Use contract_id if available (handles multipliers like 1000FLOKIUSDT)
            # Otherwise construct from symbol
            if symbol.upper().endswith("USDT"):
                # Already in full format (e.g., "1000FLOKIUSDT", "BTCUSDT")
                normalized_symbol = symbol.upper()
            elif hasattr(self.config, 'contract_id') and self.config.contract_id:
                # Use pre-fetched contract_id if available (most reliable)
                # Check if contract_id contains our symbol (handles 1000FLOKIUSDT for FLOKI)
                contract_id = self.config.contract_id.upper()
                symbol_upper = symbol.upper()
                # Match patterns: "FLOKIUSDT", "1000FLOKIUSDT", "kFLOKIUSDT" all contain "FLOKI"
                if symbol_upper in contract_id and contract_id.endswith("USDT"):
                    normalized_symbol = contract_id
                else:
                    # Fallback: construct from symbol
                    normalized_symbol = f"{symbol_upper}USDT"
            else:
                # Fallback: simple concatenation
                normalized_symbol = f"{symbol.upper()}USDT"
            
            self.logger.info(
                f"[ASTER] Setting leverage for {symbol} to {leverage}x..."
            )
            
            result = await self._make_request(
                'POST',
                '/fapi/v1/leverage',
                data={
                    'symbol': normalized_symbol,
                    'leverage': leverage
                }
            )
            
            # Response format:
            # {
            #   "leverage": 21,
            #   "maxNotionalValue": "1000000",
            #   "symbol": "BTCUSDT"
            # }
            
            if 'leverage' in result:
                actual_leverage = result.get('leverage')
                max_notional = result.get('maxNotionalValue')
                
                return True
            else:
                self.logger.warning(
                    f"[ASTER] Unexpected response when setting leverage: {result}"
                )
                return False
        
        except Exception as e:
            self.logger.error(
                f"[ASTER] Error setting leverage for {symbol} to {leverage}x: {e}"
            )
            return False
    
    async def get_leverage_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get leverage and position limit information for a symbol.
        
        ⚠️ CRITICAL: Queries BOTH symbol limits AND account leverage settings.
        Aster requires account leverage to be manually set per symbol.
        
        Uses:
        - GET /fapi/v1/leverageBracket (for symbol-level max leverage)
        - GET /fapi/v2/positionRisk (for account leverage setting)
        
        Args:
            symbol: Trading symbol (e.g., "ZORA", "BTC")
            
        Returns:
            Dictionary with leverage limits:
            {
                'max_leverage': Decimal or None,  # From symbol config
                'max_notional': Decimal or None,  # From leverage brackets
                'account_leverage': int or None,  # Current account setting
                'margin_requirement': Decimal or None,
                'brackets': List or None
            }
        """
        try:
            # Use same normalization logic as set_account_leverage
            if symbol.upper().endswith("USDT"):
                normalized_symbol = symbol.upper()
            elif hasattr(self.config, 'contract_id') and self.config.contract_id:
                contract_id = self.config.contract_id.upper()
                symbol_upper = symbol.upper()
                if symbol_upper in contract_id and contract_id.endswith("USDT"):
                    normalized_symbol = contract_id
                else:
                    normalized_symbol = f"{symbol_upper}USDT"
            else:
                normalized_symbol = f"{symbol.upper()}USDT"
            
            leverage_info = {
                'max_leverage': None,
                'max_notional': None,
                'account_leverage': None,
                'margin_requirement': None,
                'brackets': None
            }
            
            # Step 1: Get symbol leverage brackets (more efficient than exchangeInfo)
            # Endpoint: GET /fapi/v1/leverageBracket
            try:
                brackets_result = await self._make_request(
                    'GET', 
                    '/fapi/v1/leverageBracket',
                    {'symbol': normalized_symbol}
                )
                
                self.logger.debug(
                    f"[ASTER] Leverage brackets API response for {symbol}: {brackets_result}"
                )
                
                # Response format can be either:
                # 1. List: [{"symbol": "PROVEUSDT", "brackets": [...]}] (when symbol specified)
                # 2. Dict: {"symbol": "ETHUSDT", "brackets": [...]} (alternative format)
                
                symbol_data = None
                if isinstance(brackets_result, list) and len(brackets_result) > 0:
                    # Format 1: List response
                    symbol_data = brackets_result[0]
                elif isinstance(brackets_result, dict):
                    # Format 2: Dict response
                    symbol_data = brackets_result
                
                if symbol_data and 'brackets' in symbol_data:
                    brackets = symbol_data['brackets']
                    leverage_info['brackets'] = brackets
                    
                    if brackets and len(brackets) > 0:
                        # 🔍 CRITICAL: Find the MAXIMUM leverage across all brackets
                        # Bracket 1 typically has highest leverage (for smaller positions)
                        # But let's find the actual maximum to be safe
                        max_leverage_value = 0
                        max_notional_value = None
                        
                        for bracket in brackets:
                            initial_leverage = bracket.get('initialLeverage', 0)
                            if initial_leverage > max_leverage_value:
                                max_leverage_value = initial_leverage
                            
                            # Get the highest notional cap (from the last bracket)
                            notional_cap = bracket.get('notionalCap')
                            if notional_cap:
                                max_notional_value = max(
                                    max_notional_value or 0, 
                                    notional_cap
                                )
                        
                        if max_leverage_value > 0:
                            leverage_info['max_leverage'] = Decimal(str(max_leverage_value))
                        
                        if max_notional_value:
                            leverage_info['max_notional'] = Decimal(str(max_notional_value))
                    else:
                        self.logger.warning(
                            f"[ASTER] Symbol {symbol} has empty brackets array"
                        )
                else:
                    self.logger.warning(
                        f"[ASTER] Invalid leverage bracket response format for {symbol}"
                    )
                        
            except Exception as e:
                # Fallback: If leverageBracket endpoint fails, try exchangeInfo
                self.logger.debug(
                    f"[ASTER] leverageBracket endpoint failed for {symbol}, "
                    f"falling back to exchangeInfo: {e}"
                )
                
                result = await self._make_request('GET', '/fapi/v1/exchangeInfo')
                
                for symbol_info in result.get('symbols', []):
                    if symbol_info.get('symbol') == normalized_symbol:
                        # Extract max notional from filters
                        for filter_info in symbol_info.get('filters', []):
                            if filter_info.get('filterType') == 'NOTIONAL':
                                max_notional = filter_info.get('maxNotional')
                                if max_notional:
                                    leverage_info['max_notional'] = Decimal(str(max_notional))
                        
                        # Check for leverage brackets in symbol info
                        if 'leverageBrackets' in symbol_info:
                            leverage_info['brackets'] = symbol_info['leverageBrackets']
                            if leverage_info['brackets']:
                                leverage_info['max_leverage'] = Decimal(
                                    str(leverage_info['brackets'][0].get('initialLeverage', 10))
                                )
                        break
            
            # VALIDATION: Check if we got valid leverage data
            if leverage_info['max_leverage'] is None:
                self.logger.warning(
                    f"⚠️  [ASTER] Could not determine max leverage for {symbol}. "
                    f"This could indicate the symbol is not supported for leverage trading on Aster."
                )
                # Don't fail completely - use conservative fallback
                # But log clearly that this is estimated
                leverage_info['max_leverage'] = Decimal('5')  # Conservative for most Aster symbols
                leverage_info['margin_requirement'] = Decimal('0.20')  # 20% = 5x leverage
                
                self.logger.info(
                    f"📊 [ASTER] Using conservative fallback for {symbol}: 5x leverage"
                )
            
            # Step 2: Get ACTUAL account leverage setting (CRITICAL!)
            # This is what the exchange actually uses for margin calculations
            # Endpoint: GET /fapi/v2/positionRisk
            account_leverage = await self.get_account_leverage(symbol)
            
            if account_leverage and account_leverage > 0:
                leverage_info['account_leverage'] = account_leverage
                
                # Use account leverage as the effective limit
                # This is what actually determines your max position size
                effective_leverage = Decimal(str(account_leverage))
                leverage_info['margin_requirement'] = Decimal('1') / effective_leverage
                
            else:
                # No account leverage set - this will likely cause trading errors!
                self.logger.warning(
                    f"⚠️  [ASTER] No account leverage configured for {symbol}! "
                    f"You need to set leverage on Aster before trading. "
                    f"Use: POST /fapi/v1/leverage with symbol={normalized_symbol}"
                )
                # Use symbol max as fallback for margin requirement calculation
                if leverage_info['max_leverage']:
                    leverage_info['margin_requirement'] = Decimal('1') / leverage_info['max_leverage']
            
            # Log comprehensive info
            self.logger.info(
                f"📊 [ASTER] Leverage info for {symbol}:\n"
                f"  - Symbol max leverage: {leverage_info.get('max_leverage')}x\n"
                f"  - Account leverage: {leverage_info.get('account_leverage')}x\n"
                f"  - Max notional: ${leverage_info.get('max_notional')}\n"
                f"  - Margin requirement: {leverage_info.get('margin_requirement')} "
                f"({(leverage_info.get('margin_requirement', 0) * 100):.1f}%)"
            )
            
            return leverage_info
        
        except Exception as e:
            self.logger.error(f"Error getting leverage info for {symbol}: {e}")
            import traceback
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
            
            # Conservative fallback
            return {
                'max_leverage': Decimal('10'),
                'max_notional': None,
                'account_leverage': None,
                'margin_requirement': Decimal('0.10'),
                'brackets': None
            }

    def get_min_order_notional(self, symbol: Optional[str]) -> Optional[Decimal]:
        """
        Return the minimum notional requirement for the given symbol if known.
        """
        if not symbol:
            return getattr(self.config, "min_order_notional", None)

        key = str(symbol).upper()
        if key in self.min_order_notional:
            return self.min_order_notional[key]

        # Try stripping common quote assets
        for suffix in ("USDT", "USD"):
            if key.endswith(suffix):
                alt = key[: -len(suffix)]
                if alt in self.min_order_notional:
                    return self.min_order_notional[alt]

        # Fallback to current config value if it matches this contract
        contract_key = str(getattr(self.config, "contract_id", "")).upper()
        if contract_key and key == contract_key:
            return getattr(self.config, "min_order_notional", None)

        ticker_key = str(getattr(self.config, "ticker", "")).upper()
        if ticker_key and key == ticker_key:
            return getattr(self.config, "min_order_notional", None)

        return None

