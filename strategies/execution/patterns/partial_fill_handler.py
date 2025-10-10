"""
Partial Fill Handler - Emergency rollback for one-sided fills.

⭐ Inspired by Hummingbot's order tracking and error recovery ⭐

Handles scenarios where only one side of a delta-neutral position fills,
creating dangerous directional exposure.

Key features:
- Automatic detection of partial fills
- Emergency market close of filled side
- Incident logging and reporting
- Loss calculation and tracking

Use cases:
- Funding arb: Long filled but short didn't → close long immediately
- Arbitrage: Buy filled but sell didn't → close buy immediately
- Market making: Bid filled but ask canceled → rebalance
"""

from typing import Any, Dict, Optional
from decimal import Decimal
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class PartialFillHandler:
    """
    Handles one-sided fills in delta-neutral strategies.
    
    ⚠️ Critical for funding arb safety ⚠️
    
    When atomic execution fails and one side fills while the other doesn't,
    this handler provides emergency protocols to close the filled position
    and return to neutral state.
    
    Example:
        handler = PartialFillHandler()
        
        # Detect partial fill
        if long_filled and not short_filled:
            result = await handler.handle_one_sided_fill(
                filled_order={
                    'symbol': 'BTC-PERP',
                    'side': 'buy',
                    'fill_price': 50000,
                    'filled_quantity': 0.02
                },
                unfilled_order_id="short_order_123",
                exchange_client=short_client
            )
            
            if result['rollback_successful']:
                logger.info(f"Emergency closed, loss: ${result['final_loss_usd']}")
    """
    
    def __init__(self):
        """Initialize partial fill handler."""
        self.logger = logging.getLogger(__name__)
        self.incident_log = []  # Track all partial fill incidents
    
    async def handle_one_sided_fill(
        self,
        filled_order: Dict,
        unfilled_order_id: Optional[str],
        exchange_client: Any,
        unfilled_exchange_client: Optional[Any] = None
    ) -> Dict:
        """
        Emergency protocol when only one side fills.
        
        Steps:
        1. Cancel unfilled order (if order_id provided)
        2. Market close filled position
        3. Calculate loss
        4. Log incident
        5. Return to neutral state
        
        Args:
            filled_order: Dict with order details {
                'symbol': str,
                'side': str,
                'fill_price': Decimal,
                'filled_quantity': Decimal
            }
            unfilled_order_id: Order ID of unfilled order (to cancel)
            exchange_client: Exchange client for the filled order
            unfilled_exchange_client: Exchange client for unfilled order (if different)
        
        Returns:
            {
                'rollback_successful': bool,
                'final_loss_usd': Decimal,
                'incident_report': str,
                'closed_at_price': Optional[Decimal],
                'slippage_on_close': Optional[Decimal]
            }
        """
        incident_start = datetime.now()
        
        self.logger.error(
            f"⚠️ PARTIAL FILL DETECTED: "
            f"Filled {filled_order['symbol']} {filled_order['side']} @ ${filled_order['fill_price']}, "
            f"but counterpart order {unfilled_order_id} did not fill"
        )
        
        try:
            # Step 1: Cancel unfilled order (if provided)
            if unfilled_order_id:
                cancel_client = unfilled_exchange_client or exchange_client
                try:
                    await cancel_client.cancel_order(unfilled_order_id)
                    self.logger.info(f"Canceled unfilled order {unfilled_order_id}")
                except Exception as e:
                    self.logger.error(f"Failed to cancel unfilled order: {e}")
            
            # Step 2: Market close filled position
            close_side = "sell" if filled_order['side'] == "buy" else "buy"
            
            self.logger.warning(
                f"Emergency closing {filled_order['symbol']}: "
                f"{close_side} {filled_order['filled_quantity']} @ market"
            )
            
            # 🔧 FIX: Get proper contract_id from exchange client
            # Some exchanges (Aster) need "ZORAUSDT", not just "ZORA"
            contract_attrs = await exchange_client.get_contract_attributes(filled_order['symbol'])
            contract_id = contract_attrs.get('contract_id', filled_order['symbol'])
            
            self.logger.debug(
                f"Emergency close: Using contract_id='{contract_id}' for symbol '{filled_order['symbol']}'"
            )
            
            close_result = await exchange_client.place_market_order(
                contract_id=contract_id,
                quantity=float(filled_order['filled_quantity']),
                side=close_side
            )
            
            if not close_result.success:
                # Failed to close - CRITICAL SITUATION
                self.logger.critical(
                    f"🚨 FAILED TO EMERGENCY CLOSE POSITION: {close_result.error_message}"
                )
                
                return {
                    'rollback_successful': False,
                    'final_loss_usd': Decimal('0'),  # Unknown
                    'incident_report': self._generate_incident_report(
                        filled_order=filled_order,
                        unfilled_order_id=unfilled_order_id,
                        close_result=None,
                        error="Failed to place close order"
                    ),
                    'closed_at_price': None,
                    'slippage_on_close': None
                }
            
            # Step 3: Calculate damage
            entry_price = Decimal(str(filled_order['fill_price']))
            exit_price = Decimal(str(close_result.price))
            quantity = Decimal(str(filled_order['filled_quantity']))
            
            # Calculate loss (difference between entry and emergency exit)
            price_diff = abs(entry_price - exit_price)
            loss_usd = price_diff * quantity
            
            # Add market impact / slippage on close
            slippage_on_close = loss_usd
            
            # Step 4: Log incident
            incident_report = self._generate_incident_report(
                filled_order=filled_order,
                unfilled_order_id=unfilled_order_id,
                close_result=close_result,
                entry_price=entry_price,
                exit_price=exit_price,
                loss_usd=loss_usd
            )
            
            self.logger.warning(incident_report)
            
            # Store in incident log
            self.incident_log.append({
                'timestamp': incident_start,
                'filled_order': filled_order,
                'unfilled_order_id': unfilled_order_id,
                'loss_usd': loss_usd,
                'report': incident_report
            })
            
            return {
                'rollback_successful': True,
                'final_loss_usd': loss_usd,
                'incident_report': incident_report,
                'closed_at_price': exit_price,
                'slippage_on_close': slippage_on_close
            }
        
        except Exception as e:
            self.logger.critical(
                f"🚨 PARTIAL FILL HANDLER EXCEPTION: {e}",
                exc_info=True
            )
            
            return {
                'rollback_successful': False,
                'final_loss_usd': Decimal('0'),
                'incident_report': f"Handler exception: {str(e)}",
                'closed_at_price': None,
                'slippage_on_close': None
            }
    
    def _generate_incident_report(
        self,
        filled_order: Dict,
        unfilled_order_id: Optional[str],
        close_result: Optional[Any],
        entry_price: Optional[Decimal] = None,
        exit_price: Optional[Decimal] = None,
        loss_usd: Optional[Decimal] = None,
        error: Optional[str] = None
    ) -> str:
        """
        Generate detailed incident report.
        
        Returns:
            Formatted incident report string
        """
        report_lines = [
            "=" * 60,
            "⚠️  PARTIAL FILL INCIDENT REPORT",
            "=" * 60,
            "",
            f"Timestamp: {datetime.now().isoformat()}",
            "",
            "FILLED ORDER:",
            f"  Symbol: {filled_order['symbol']}",
            f"  Side: {filled_order['side'].upper()}",
            f"  Price: ${filled_order['fill_price']}",
            f"  Quantity: {filled_order['filled_quantity']}",
            "",
            "UNFILLED ORDER:",
            f"  Order ID: {unfilled_order_id or 'Not provided'}",
            ""
        ]
        
        if error:
            report_lines.extend([
                "ERROR:",
                f"  {error}",
                ""
            ])
        elif close_result:
            report_lines.extend([
                "EMERGENCY CLOSE:",
                f"  Exit Price: ${exit_price}",
                f"  Entry Price: ${entry_price}",
                f"  Price Difference: ${abs(entry_price - exit_price) if entry_price and exit_price else 'N/A'}",
                f"  Loss (USD): ${loss_usd:.2f}" if loss_usd else "  Loss: N/A",
                ""
            ])
        
        report_lines.extend([
            "RECOMMENDATION:",
            "  1. Review execution logs",
            "  2. Check exchange connectivity",
            "  3. Verify liquidity conditions",
            "  4. Consider adjusting timeout settings",
            "=" * 60
        ])
        
        return "\n".join(report_lines)
    
    def get_incident_summary(self) -> Dict:
        """
        Get summary of all partial fill incidents.
        
        Returns:
            {
                'total_incidents': int,
                'total_loss_usd': Decimal,
                'incidents': List[Dict]
            }
        """
        total_loss = sum(
            incident['loss_usd'] 
            for incident in self.incident_log
        )
        
        return {
            'total_incidents': len(self.incident_log),
            'total_loss_usd': total_loss,
            'incidents': self.incident_log
        }
    
    def clear_incident_log(self):
        """Clear incident log (use after reviewing)."""
        self.logger.info(f"Clearing {len(self.incident_log)} incidents from log")
        self.incident_log = []

