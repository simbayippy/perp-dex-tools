"""
Position manager module for Aster client.

Handles position tracking, snapshots, and funding calculations.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional

from exchange_clients.base_models import ExchangePositionSnapshot, query_retry
from exchange_clients.aster.client.utils.helpers import to_decimal
from exchange_clients.aster.common import get_aster_symbol_format


class AsterPositionManager:
    """
    Position manager for Aster exchange.
    
    Handles:
    - Position snapshots
    - Position size queries
    - Funding calculations
    - Position open time estimation
    """
    
    def __init__(
        self,
        make_request_fn: Callable,
        config: Any,
        logger: Any,
        normalize_symbol_fn: Optional[Callable[[str], str]] = None,
    ):
        """
        Initialize position manager.
        
        Args:
            make_request_fn: Function to make authenticated API requests
            config: Trading configuration object
            logger: Logger instance
            normalize_symbol_fn: Function to normalize symbols
        """
        self._make_request = make_request_fn
        self.config = config
        self.logger = logger
        self.normalize_symbol = normalize_symbol_fn or (lambda s: s.upper())
    
    @query_retry(reraise=True)
    async def get_account_positions(self, contract_id: str) -> Decimal:
        """Get account positions from Aster."""
        result = await self._make_request('GET', '/fapi/v2/positionRisk', {'symbol': contract_id})

        for position in result:
            if position.get('symbol') == contract_id:
                position_amt = abs(Decimal(position.get('positionAmt', 0)))
                return position_amt

        return Decimal(0)

    async def _get_position_open_time(self, symbol: str, current_quantity: Decimal) -> Optional[int]:
        """
        Estimate when the current position was opened by analyzing recent trade history.
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            current_quantity: Current position quantity (to correlate with trades)
            
        Returns:
            Timestamp (milliseconds) when position was likely opened, or None if unknown
        """
        try:
            # Fetch recent trades for this symbol
            params = {
                'symbol': symbol,
                'limit': 100  # Last 100 trades should cover most position opens
            }
            
            trades = await self._make_request('GET', '/fapi/v1/userTrades', params)
            
            if not isinstance(trades, list) or not trades:
                return None
            
            # Trades are typically ordered newest first, so reverse to go chronologically
            trades = list(reversed(trades))
            
            # Track running position to find when current position started
            # We'll look for when position qty went from 0 (or different direction) to current qty
            running_qty = Decimal("0")
            position_start_time = None
            
            for trade in trades:
                trade_qty = Decimal(str(trade.get('qty', '0')))
                is_buyer = trade.get('buyer', False)
                
                # Adjust running quantity based on trade side
                if is_buyer:
                    running_qty += trade_qty
                else:
                    running_qty -= trade_qty
                
                trade_time = trade.get('time')
                
                # Check if this trade established a position in the same direction as current
                prev_sign = 1 if running_qty - trade_qty > 0 else -1 if running_qty - trade_qty < 0 else 0
                curr_sign = 1 if running_qty > 0 else -1 if running_qty < 0 else 0
                
                # Position direction changed or started from zero
                if prev_sign != curr_sign and curr_sign != 0:
                    position_start_time = trade_time
            
            return position_start_time
            
        except Exception as exc:
            self.logger.debug(
                f"[ASTER] Failed to determine position open time for {symbol}: {exc}"
            )
            return None

    async def _get_cumulative_funding(self, symbol: str, quantity: Optional[Decimal] = None) -> Optional[Decimal]:
        """
        Fetch cumulative funding fees for the CURRENT position only (not historical positions).
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT", "ETHUSDT")
            quantity: Current position quantity (used to determine when position was opened)
            
        Returns:
            Cumulative funding fees as Decimal (negative means paid, positive means received), None if unavailable
        """
        try:
            # Ensure symbol is in Aster format
            formatted_symbol = symbol.upper()
            if not formatted_symbol.endswith("USDT"):
                formatted_symbol = get_aster_symbol_format(formatted_symbol)
            
            # Try to determine when the current position was opened
            position_start_time = None
            if quantity is not None and quantity != Decimal("0"):
                position_start_time = await self._get_position_open_time(formatted_symbol, quantity)
            
            # Fetch income history filtered by FUNDING_FEE
            params = {
                'symbol': formatted_symbol,
                'incomeType': 'FUNDING_FEE',
                'limit': 100  # Get recent funding payments
            }
            
            # If we know when position started, filter by startTime
            if position_start_time:
                params['startTime'] = position_start_time
                self.logger.debug(
                    f"[ASTER] Filtering funding to only after position opened at {position_start_time}"
                )
            
            result = await self._make_request('GET', '/fapi/v1/income', params)
            
            if not isinstance(result, list):
                self.logger.debug(
                    f"[ASTER] Unexpected income history response format for {formatted_symbol}"
                )
                return None
            
            if not result:
                # No funding history available - return 0 instead of None
                # (indicates position with no funding paid/received yet)
                return Decimal("0")
            
            # Sum up funding fee 'income' values for this position only
            # Note: Negative values mean funding was PAID, positive means RECEIVED
            cumulative = Decimal("0")
            for record in result:
                try:
                    # If we have position start time, only count funding after position opened
                    if position_start_time:
                        record_time = record.get('time', 0)
                        if record_time < position_start_time:
                            continue
                    
                    income = Decimal(str(record.get('income', '0')))
                    cumulative += income
                except (InvalidOperation, TypeError, ValueError) as exc:
                    self.logger.debug(
                        f"[ASTER] Failed to parse funding income: {record.get('income')} ({exc})"
                    )
                    continue
            
            self.logger.debug(
                f"[ASTER] Funding for current {formatted_symbol} position: ${cumulative:.4f} "
                f"(from {len(result)} records{' after position opened' if position_start_time else ' (all history)'})"
            )
            
            return cumulative
            
        except Exception as exc:
            self.logger.debug(
                f"[ASTER] Error fetching funding for {symbol}: {exc}"
            )
            return None

    async def get_position_snapshot(self, symbol: str) -> Optional[ExchangePositionSnapshot]:
        """
        Return the current position snapshot for a symbol.
        """
        formatted_symbol = symbol.upper()
        if not formatted_symbol.endswith("USDT"):
            formatted_symbol = get_aster_symbol_format(formatted_symbol)

        try:
            result = await self._make_request('GET', '/fapi/v2/positionRisk', {'symbol': formatted_symbol})
        except Exception as exc:
            self.logger.warning(f"[ASTER] Failed to fetch position risk for {symbol}: {exc}")
            return None

        if not isinstance(result, list):
            return None

        for position in result:
            if position.get('symbol') != formatted_symbol:
                continue

            quantity = to_decimal(position.get('positionAmt')) or Decimal("0")
            entry_price = to_decimal(position.get('entryPrice'))
            mark_price = to_decimal(position.get('markPrice'))
            unrealized = to_decimal(position.get('unRealizedProfit'))
            leverage = to_decimal(position.get('leverage'))
            isolated_margin = to_decimal(position.get('isolatedMargin'))
            liquidation_price = to_decimal(position.get('liquidationPrice'))
            notional = to_decimal(position.get('notional'))

            exposure = notional.copy_abs() if notional is not None else None
            if exposure is None and mark_price is not None and quantity != 0:
                exposure = mark_price * quantity.copy_abs()

            metadata: Dict[str, Any] = {
                "margin_type": position.get('marginType'),
                "position_side": position.get('positionSide'),
            }
            if notional is not None:
                metadata["notional"] = notional

            side = "long" if quantity > 0 else "short" if quantity < 0 else None
            
            # Fetch cumulative funding fees for THIS SPECIFIC position (not all historical positions)
            funding_accrued = None
            try:
                funding_accrued = await self._get_cumulative_funding(formatted_symbol, quantity=quantity)
            except Exception as exc:
                self.logger.debug(
                    f"[ASTER] Failed to fetch funding for {formatted_symbol}: {exc}"
                )

            return ExchangePositionSnapshot(
                symbol=formatted_symbol,
                quantity=quantity,
                side=side,
                entry_price=entry_price,
                mark_price=mark_price,
                exposure_usd=exposure,
                unrealized_pnl=unrealized,
                realized_pnl=None,
                funding_accrued=funding_accrued,
                margin_reserved=isolated_margin,
                leverage=leverage,
                liquidation_price=liquidation_price,
                timestamp=datetime.now(timezone.utc),
                metadata={k: v for k, v in metadata.items() if v is not None},
            )

        return None

