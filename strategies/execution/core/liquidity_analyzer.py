"""
Liquidity Analyzer - Pre-flight checks for order execution.

⭐ Inspired by Hummingbot's budget_checker and order book analysis ⭐

Analyzes order book depth before placing orders to:
- Check if sufficient liquidity exists
- Estimate expected slippage
- Calculate liquidity quality score
- Recommend execution mode

Key features:
- Order book depth analysis
- Slippage estimation
- Spread calculation
- Execution mode recommendation
"""

from typing import Any, Dict, List, Optional
from decimal import Decimal
from dataclasses import dataclass
from helpers.unified_logger import get_core_logger

logger = get_core_logger("liquidity_analyzer")


@dataclass
class LiquidityReport:
    """
    Analysis of order book liquidity for an order.
    
    Contains all metrics needed to decide if/how to execute an order.
    """
    # Core metrics
    depth_sufficient: bool
    expected_slippage_pct: Decimal
    expected_avg_price: Decimal
    
    # Quality indicators
    spread_bps: int  # Basis points (1% = 100 bps)
    liquidity_score: float  # 0-1, higher is better
    
    # Recommendation
    recommendation: str  # "use_limit", "use_market", "insufficient_depth", etc.
    
    # Details
    required_levels: int  # How many order book levels needed to fill
    total_depth_usd: Decimal  # Total liquidity available
    
    # Price context
    mid_price: Decimal
    best_bid: Decimal
    best_ask: Decimal


class LiquidityAnalyzer:
    """
    Analyzes order book depth before placing orders.
    
    ⭐ Inspired by Hummingbot's budget_checker pattern ⭐
    
    Use cases:
    - Pre-flight check before placing orders
    - Decide between limit vs market orders
    - Estimate execution quality
    - Reject trades with insufficient liquidity
    
    Example:
        analyzer = LiquidityAnalyzer()
        
        report = await analyzer.check_execution_feasibility(
            exchange_client=client,
            symbol="BTC-PERP",
            side="buy",
            size_usd=Decimal("1000")
        )
        
        if report.recommendation == "insufficient_depth":
            logger.warning("Not enough liquidity, skipping trade")
            return
        
        if report.liquidity_score < 0.5:
            logger.warning(f"Low liquidity score: {report.liquidity_score}")
    """
    
    def __init__(
        self,
        max_slippage_pct: Decimal = Decimal("0.005"),  # 0.5% max slippage
        max_spread_bps: int = 50,  # 50 basis points = 0.5%
        min_liquidity_score: float = 0.6,
        price_provider = None  # Optional PriceProvider for caching
    ):
        """
        Initialize liquidity analyzer.
        
        Args:
            max_slippage_pct: Maximum acceptable slippage (0.005 = 0.5%)
            max_spread_bps: Maximum acceptable spread in basis points
            min_liquidity_score: Minimum acceptable liquidity score (0-1)
            price_provider: Optional PriceProvider for caching order book data
        """
        self.max_slippage_pct = max_slippage_pct
        self.max_spread_bps = max_spread_bps
        self.min_liquidity_score = min_liquidity_score
        self.price_provider = price_provider
        self.logger = get_core_logger("liquidity_analyzer")
    
    async def check_execution_feasibility(
        self,
        exchange_client: Any,
        symbol: str,
        side: str,
        size_usd: Decimal,
        depth_levels: int = 20
    ) -> LiquidityReport:
        """
        Pre-flight check: Can this order execute with acceptable slippage?
        
        Args:
            exchange_client: Exchange client instance
            symbol: Trading pair (e.g., "BTC-PERP")
            side: "buy" or "sell"
            size_usd: Order size in USD
            depth_levels: Number of order book levels to analyze
        
        Returns:
            LiquidityReport with recommendation
        """
        try:
            # Get order book depth
            # Note: Each exchange client handles symbol normalization internally
            order_book = await exchange_client.get_order_book_depth(
                symbol, 
                levels=depth_levels
            )
            
            # Validate order book is not empty
            bids_count = len(order_book.get('bids', []))
            asks_count = len(order_book.get('asks', []))
            
            if not order_book.get('bids') or not order_book.get('asks'):
                self.logger.warning(
                    f"Order book for {symbol} is empty or unavailable. "
                    f"Received {bids_count} bids, {asks_count} asks. "
                    f"Exchange may have no liquidity for this symbol, or API call failed."
                )
                return LiquidityReport(
                    depth_sufficient=False,
                    expected_slippage_pct=Decimal('1.0'),
                    expected_avg_price=Decimal('0'),
                    spread_bps=9999,
                    liquidity_score=0.0,
                    recommendation="insufficient_depth",
                    required_levels=0,
                    total_depth_usd=Decimal('0'),
                    mid_price=Decimal('0'),
                    best_bid=Decimal('0'),
                    best_ask=Decimal('0')
                )
            
            # Extract best bid/ask
            best_bid = Decimal(str(order_book['bids'][0]['price']))
            best_ask = Decimal(str(order_book['asks'][0]['price']))
            mid_price = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            spread_bps = int((spread / mid_price) * 10000)
            
            # Log order book summary
            self.logger.info(
                f"📊 [LIQUIDITY] {symbol}: {bids_count} bids, {asks_count} asks | "
                f"Best: {best_bid}/{best_ask} | Spread: {spread_bps} bps"
            )
            
            # Determine which side of book to check
            book_side = order_book['asks'] if side == 'buy' else order_book['bids']
            side_name = "asks (selling to us)" if side == 'buy' else "bids (buying from us)"
            
            self.logger.info(
                f"💰 [LIQUIDITY] Analyzing {side} order for ${size_usd} on {side_name}"
            )
            
            # Calculate expected fill
            fill_analysis = self._analyze_order_fill(
                book_side=book_side,
                size_usd=size_usd,
                side=side
            )
            
            # Log fill analysis results
            self.logger.info(
                f"📈 [LIQUIDITY] Fill analysis: "
                f"Can fill: {fill_analysis['filled_completely']}, "
                f"Levels needed: {fill_analysis['levels_consumed']}/{len(book_side)}, "
                f"Total available: ${fill_analysis['total_cost']:.2f}"
            )
            
            if not fill_analysis['filled_completely']:
                self.logger.warning(
                    f"⚠️  [LIQUIDITY] INSUFFICIENT DEPTH! "
                    f"Need ${size_usd}, only ${fill_analysis['total_cost']:.2f} available. "
                    f"Shortfall: ${fill_analysis['remaining_usd']:.2f}"
                )
            
            # Check if order book has enough depth
            depth_sufficient = fill_analysis['filled_completely']
            
            # Calculate expected average fill price
            if fill_analysis['total_quantity'] > 0:
                avg_fill_price = fill_analysis['total_cost'] / fill_analysis['total_quantity']
                expected_price = best_ask if side == 'buy' else best_bid
                slippage_pct = abs(avg_fill_price - expected_price) / expected_price
            else:
                avg_fill_price = Decimal('0')
                slippage_pct = Decimal('1.0')  # 100% slippage = infinite
            
            # Calculate spread
            spread = best_ask - best_bid
            spread_bps = int((spread / mid_price) * 10000)
            
            # Calculate total available depth
            total_depth_usd = fill_analysis['total_cost']
            
            # Liquidity score (0-1, higher is better)
            liquidity_score = self._calculate_liquidity_score(
                depth_sufficient=depth_sufficient,
                slippage_pct=slippage_pct,
                spread_bps=spread_bps
            )
            
            # Generate recommendation
            recommendation = self._generate_recommendation(
                depth_sufficient=depth_sufficient,
                slippage_pct=slippage_pct,
                spread_bps=spread_bps,
                liquidity_score=liquidity_score
            )
            
            # Cache order book data for later use (if price_provider available)
            if self.price_provider:
                exchange_name = exchange_client.get_exchange_name()
                self.price_provider.cache_order_book(
                    exchange_name=exchange_name,
                    symbol=symbol,
                    order_book=order_book,
                    source="liquidity_check"
                )
            
            # Log final verdict
            verdict_emoji = "✅" if recommendation in ["use_limit", "use_market"] else "❌"
            self.logger.info(
                f"{verdict_emoji} [LIQUIDITY] VERDICT for {side} ${size_usd} {symbol}: "
                f"Recommendation='{recommendation}' | "
                f"Score={liquidity_score:.2f} | "
                f"Slippage={slippage_pct*100:.3f}% | "
                f"Spread={spread_bps}bps"
            )
            
            return LiquidityReport(
                depth_sufficient=depth_sufficient,
                expected_slippage_pct=slippage_pct,
                expected_avg_price=avg_fill_price,
                spread_bps=spread_bps,
                liquidity_score=liquidity_score,
                recommendation=recommendation,
                required_levels=fill_analysis['levels_consumed'],
                total_depth_usd=total_depth_usd,
                mid_price=mid_price,
                best_bid=best_bid,
                best_ask=best_ask
            )
        
        except Exception as e:
            self.logger.error(f"Liquidity analysis failed: {e}", exc_info=True)
            # Return pessimistic report on error
            return LiquidityReport(
                depth_sufficient=False,
                expected_slippage_pct=Decimal('1.0'),
                expected_avg_price=Decimal('0'),
                spread_bps=9999,
                liquidity_score=0.0,
                recommendation="analysis_failed",
                required_levels=0,
                total_depth_usd=Decimal('0'),
                mid_price=Decimal('0'),
                best_bid=Decimal('0'),
                best_ask=Decimal('0')
            )
    
    def _analyze_order_fill(
        self,
        book_side: List[Dict],
        size_usd: Decimal,
        side: str
    ) -> Dict:
        """
        Simulate filling an order from the order book.
        
        Returns:
            {
                'filled_completely': bool,
                'total_quantity': Decimal,
                'total_cost': Decimal,
                'levels_consumed': int,
                'remaining_usd': Decimal
            }
        """
        remaining_usd = size_usd
        total_quantity = Decimal('0')
        total_cost = Decimal('0')
        levels_consumed = 0
        
        for level in book_side:
            price = Decimal(str(level['price']))
            quantity = Decimal(str(level['size']))  # Changed from 'quantity' to 'size' to match order book format
            level_usd = price * quantity
            
            levels_consumed += 1
            
            if remaining_usd <= level_usd:
                # This level satisfies remaining size
                needed_quantity = remaining_usd / price
                total_quantity += needed_quantity
                total_cost += remaining_usd
                remaining_usd = Decimal('0')
                break
            else:
                # Consume entire level
                total_quantity += quantity
                total_cost += level_usd
                remaining_usd -= level_usd
        
        return {
            'filled_completely': remaining_usd == Decimal('0'),
            'total_quantity': total_quantity,
            'total_cost': total_cost,
            'levels_consumed': levels_consumed,
            'remaining_usd': remaining_usd
        }
    
    def _calculate_liquidity_score(
        self,
        depth_sufficient: bool,
        slippage_pct: Decimal,
        spread_bps: int
    ) -> float:
        """
        Combined liquidity score (0-1, higher is better).
        
        Factors:
        - Depth availability (50% weight)
        - Low slippage (30% weight)
        - Tight spread (20% weight)
        
        ⭐ Inspired by Hummingbot's quality metrics ⭐
        """
        # Depth score (binary)
        depth_score = 1.0 if depth_sufficient else 0.0
        
        # Slippage score (penalize >1% slippage heavily)
        # 0% slippage = 1.0, 0.5% slippage = 0.5, 1% slippage = 0.0
        slippage_score = max(0.0, 1.0 - float(slippage_pct) * 100)
        
        # Spread score (penalize >100bps spread)
        # 0 bps = 1.0, 50 bps = 0.5, 100 bps = 0.0
        spread_score = max(0.0, 1.0 - spread_bps / 100.0)
        
        # Weighted combination
        liquidity_score = (
            depth_score * 0.5 +
            slippage_score * 0.3 +
            spread_score * 0.2
        )
        
        return liquidity_score
    
    def _generate_recommendation(
        self,
        depth_sufficient: bool,
        slippage_pct: Decimal,
        spread_bps: int,
        liquidity_score: float
    ) -> str:
        """
        Generate execution recommendation based on metrics.
        
        Returns:
            One of:
            - "insufficient_depth" - Not enough liquidity, don't trade
            - "use_limit" - Good liquidity, use limit order
            - "use_market_acceptable" - Ok liquidity, market order acceptable
            - "high_slippage_warning" - High slippage expected
            - "wide_spread_warning" - Wide spread, poor market quality
        """
        if not depth_sufficient:
            return "insufficient_depth"
        
        # Check for critical issues
        if slippage_pct > self.max_slippage_pct:
            return "high_slippage_warning"
        
        if spread_bps > self.max_spread_bps:
            return "wide_spread_warning"
        
        # Good liquidity conditions
        if slippage_pct < Decimal('0.001'):  # < 0.1% slippage
            return "use_limit"
        
        if slippage_pct < Decimal('0.005'):  # < 0.5% slippage
            return "use_market_acceptable"
        
        # Default
        return "moderate_liquidity"
    
    def is_execution_acceptable(self, report: LiquidityReport) -> bool:
        """
        Quick check: Is this execution quality acceptable?
        
        Args:
            report: LiquidityReport from check_execution_feasibility()
        
        Returns:
            True if execution should proceed, False otherwise
        """
        # Must have sufficient depth
        if not report.depth_sufficient:
            return False
        
        # Check slippage threshold
        if report.expected_slippage_pct > self.max_slippage_pct:
            self.logger.warning(
                f"Slippage {report.expected_slippage_pct*100:.3f}% exceeds "
                f"max {self.max_slippage_pct*100:.3f}%"
            )
            return False
        
        # Check spread threshold
        if report.spread_bps > self.max_spread_bps:
            self.logger.warning(
                f"Spread {report.spread_bps}bps exceeds max {self.max_spread_bps}bps"
            )
            return False
        
        # Check liquidity score
        if report.liquidity_score < self.min_liquidity_score:
            self.logger.warning(
                f"Liquidity score {report.liquidity_score:.2f} below "
                f"min {self.min_liquidity_score:.2f}"
            )
            return False
        
        return True

