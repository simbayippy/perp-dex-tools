"""
Slippage Calculator - Tracks expected vs actual slippage.

⭐ Used in all Hummingbot profitability calculations ⭐

Calculates:
- Expected slippage from order book depth
- Actual slippage from execution
- Slippage cost in USD and percentage
- Execution quality metrics

Key features:
- Pre-execution slippage estimation
- Post-execution slippage tracking
- Profitability impact calculation
"""

from typing import Dict, List
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


class SlippageCalculator:
    """
    Calculates expected vs actual slippage.
    
    ⭐ Used in all Hummingbot profitability calculations ⭐
    
    Example:
        calculator = SlippageCalculator()
        
        # Estimate expected slippage from order book
        expected = calculator.calculate_expected_slippage(
            order_book={'asks': [...], 'bids': [...]},
            side="buy",
            size_usd=Decimal("1000")
        )
        print(f"Expected slippage: ${expected}")
        
        # After execution, calculate actual slippage
        actual = calculator.calculate_actual_slippage(
            expected_price=Decimal("50000"),
            actual_fill_price=Decimal("50050"),
            quantity=Decimal("0.02")
        )
        print(f"Actual slippage: ${actual}")
    """
    
    def __init__(self):
        """Initialize slippage calculator."""
        self.logger = logging.getLogger(__name__)
    
    def calculate_expected_slippage(
        self,
        order_book: Dict,
        side: str,
        size_usd: Decimal
    ) -> Decimal:
        """
        Estimate slippage from order book depth.
        
        Args:
            order_book: Order book with 'asks' and 'bids' lists
            side: "buy" or "sell"
            size_usd: Order size in USD
        
        Returns:
            Expected slippage in USD
        """
        try:
            # Get relevant side of book
            book_side = order_book['asks'] if side == 'buy' else order_book['bids']
            
            # Get best price (what we'd pay for 1 unit)
            best_price = Decimal(str(book_side[0]['price']))
            
            # Calculate average fill price
            avg_fill_price = self._calculate_average_fill_price(
                book_side=book_side,
                size_usd=size_usd
            )
            
            # Slippage = difference between best price and average fill price
            if avg_fill_price > 0:
                # Calculate quantity that would be filled
                quantity = size_usd / avg_fill_price
                slippage_usd = abs(avg_fill_price - best_price) * quantity
            else:
                slippage_usd = Decimal('0')
            
            self.logger.debug(
                f"Expected slippage for {side} ${size_usd}: ${slippage_usd:.2f} "
                f"(best: ${best_price}, avg: ${avg_fill_price})"
            )
            
            return slippage_usd
        
        except Exception as e:
            self.logger.error(f"Expected slippage calculation failed: {e}")
            # Return pessimistic estimate
            return size_usd * Decimal("0.01")  # Assume 1% slippage
    
    def calculate_actual_slippage(
        self,
        expected_price: Decimal,
        actual_fill_price: Decimal,
        quantity: Decimal
    ) -> Decimal:
        """
        Calculate actual slippage from fill.
        
        Args:
            expected_price: Price we expected to pay
            actual_fill_price: Price we actually paid
            quantity: Quantity filled
        
        Returns:
            Slippage in USD (positive = worse than expected)
        """
        try:
            price_diff = abs(actual_fill_price - expected_price)
            slippage_usd = price_diff * quantity
            
            self.logger.debug(
                f"Actual slippage: ${slippage_usd:.2f} "
                f"(expected: ${expected_price}, actual: ${actual_fill_price}, qty: {quantity})"
            )
            
            return slippage_usd
        
        except Exception as e:
            self.logger.error(f"Actual slippage calculation failed: {e}")
            return Decimal('0')
    
    def calculate_slippage_percentage(
        self,
        expected_price: Decimal,
        actual_fill_price: Decimal
    ) -> Decimal:
        """
        Calculate slippage as percentage.
        
        Args:
            expected_price: Price we expected
            actual_fill_price: Price we got
        
        Returns:
            Slippage percentage (0.01 = 1%)
        """
        if expected_price == 0:
            return Decimal('0')
        
        return abs(actual_fill_price - expected_price) / expected_price
    
    def _calculate_average_fill_price(
        self,
        book_side: List[Dict],
        size_usd: Decimal
    ) -> Decimal:
        """
        Calculate average price if order were filled from book.
        
        Args:
            book_side: Order book side (asks or bids)
            size_usd: Order size in USD
        
        Returns:
            Average fill price
        """
        remaining_usd = size_usd
        total_quantity = Decimal('0')
        total_cost = Decimal('0')
        
        for level in book_side:
            price = Decimal(str(level['price']))
            quantity = Decimal(str(level['quantity']))
            level_usd = price * quantity
            
            if remaining_usd <= level_usd:
                # This level satisfies remaining size
                needed_quantity = remaining_usd / price
                total_quantity += needed_quantity
                total_cost += remaining_usd
                break
            else:
                # Consume entire level
                total_quantity += quantity
                total_cost += level_usd
                remaining_usd -= level_usd
        
        if total_quantity > 0:
            return total_cost / total_quantity
        else:
            return Decimal('0')
    
    def compare_execution_quality(
        self,
        expected_slippage_usd: Decimal,
        actual_slippage_usd: Decimal,
        size_usd: Decimal
    ) -> Dict:
        """
        Compare expected vs actual execution quality.
        
        Args:
            expected_slippage_usd: Estimated slippage
            actual_slippage_usd: Actual slippage
            size_usd: Order size
        
        Returns:
            {
                'expected_slippage_pct': Decimal,
                'actual_slippage_pct': Decimal,
                'slippage_surprise': Decimal,  # actual - expected
                'quality_rating': str  # "excellent", "good", "poor"
            }
        """
        expected_pct = expected_slippage_usd / size_usd if size_usd > 0 else Decimal('0')
        actual_pct = actual_slippage_usd / size_usd if size_usd > 0 else Decimal('0')
        slippage_surprise = actual_slippage_usd - expected_slippage_usd
        
        # Rate quality
        if actual_pct < Decimal('0.001'):  # < 0.1%
            quality_rating = "excellent"
        elif actual_pct < Decimal('0.005'):  # < 0.5%
            quality_rating = "good"
        elif actual_pct < Decimal('0.01'):  # < 1%
            quality_rating = "acceptable"
        else:
            quality_rating = "poor"
        
        return {
            'expected_slippage_pct': expected_pct,
            'actual_slippage_pct': actual_pct,
            'slippage_surprise': slippage_surprise,
            'quality_rating': quality_rating
        }

