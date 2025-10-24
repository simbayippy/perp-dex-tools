"""
Opportunity Analysis Task

Periodic task to analyze funding rate opportunities and cache results.
Runs every 2-3 minutes to provide fast API responses for opportunity queries.
"""

from typing import Dict, Any, List
from datetime import datetime
from decimal import Decimal

from funding_rate_service.tasks.base_task import BaseTask
from funding_rate_service.core.opportunity_finder import OpportunityFinder
from funding_rate_service.core.fee_calculator import fee_calculator
from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
from database.connection import database
from funding_rate_service.models.filters import OpportunityFilter
from funding_rate_service.utils.logger import logger


class OpportunityTask(BaseTask):
    """
    Background task for periodic opportunity analysis
    
    This task:
    1. Analyzes latest funding rates to find arbitrage opportunities
    2. Calculates profitability after fees
    3. Caches top opportunities for fast API responses
    4. Tracks opportunity metrics and trends
    
    Designed for VPS 24/7 operation with intelligent caching.
    """
    
    def __init__(self, max_retries: int = 2):
        """
        Initialize opportunity analysis task
        
        Args:
            max_retries: Max retries per analysis cycle
        """
        super().__init__("opportunity_analysis", max_retries)
        self.opportunity_finder = None
        self._finder_initialized = False
        
        # Cache for frequently requested opportunity types
        self._opportunity_cache = {
            'best_overall': None,
            'low_oi_opportunities': None,  # For low OI farming
            'high_volume_opportunities': None,
            'by_symbol': {},  # Cache per symbol
            'last_cache_time': None
        }
    
    async def _initialize_finder(self) -> OpportunityFinder:
        """
        Initialize opportunity finder if not already done
        
        Returns:
            OpportunityFinder instance
        """
        if self._finder_initialized and self.opportunity_finder:
            return self.opportunity_finder
        
        logger.info("Initializing opportunity finder...")
        
        # Ensure mappers are loaded
        if not dex_mapper.is_loaded():
            await dex_mapper.load_from_db(database)
        if not symbol_mapper.is_loaded():
            await symbol_mapper.load_from_db(database)
        
        # Initialize opportunity finder
        self.opportunity_finder = OpportunityFinder(
            database=database,
            fee_calculator=fee_calculator,
            dex_mapper=dex_mapper,
            symbol_mapper=symbol_mapper
        )
        
        self._finder_initialized = True
        logger.info("Opportunity finder initialized")
        
        return self.opportunity_finder
    
    async def execute(self) -> Dict[str, Any]:
        """
        Execute opportunity analysis and caching
        
        Returns:
            Dictionary with analysis results and cache statistics
        """
        # Ensure finder is initialized
        finder = await self._initialize_finder()
        
        logger.info("Starting opportunity analysis...")
        
        analysis_results = {
            'opportunities_analyzed': 0,
            'profitable_opportunities': 0,
            'cache_updates': 0,
            'top_opportunities': [],
            'analysis_timestamp': datetime.utcnow().isoformat()
        }
        
        # 1. Find best overall opportunities
        logger.debug("Finding best overall opportunities...")
        best_opportunities = await finder.find_opportunities(
            filters=OpportunityFilter(
                min_profit_percent=Decimal('0.0001'),  # 0.01% minimum
                limit=20,
                sort_by="net_profit_percent",
                sort_desc=True
            )
        )
        
        analysis_results['opportunities_analyzed'] += len(best_opportunities)
        profitable_opps = [opp for opp in best_opportunities if opp.net_profit_percent > 0]
        analysis_results['profitable_opportunities'] += len(profitable_opps)
        
        # Cache best overall
        if profitable_opps:
            self._opportunity_cache['best_overall'] = profitable_opps[:10]
            analysis_results['cache_updates'] += 1
            analysis_results['top_opportunities'] = [
                {
                    'symbol': opp.symbol,
                    'long_dex': opp.long_dex,
                    'short_dex': opp.short_dex,
                    'net_profit_percent': float(opp.net_profit_percent),
                    'annualized_apy': float(opp.annualized_apy) if opp.annualized_apy else None
                }
                for opp in profitable_opps[:5]  # Top 5 for logging
            ]
        
        # 2. Find low OI opportunities (for low OI farming strategy)
        logger.debug("Finding low OI opportunities...")
        low_oi_opportunities = await finder.find_opportunities(
            filters=OpportunityFilter(
                min_profit_percent=Decimal('0.0001'),
                max_oi_usd=Decimal('5000000'),  # < $5M OI
                limit=15,
                sort_by="net_profit_percent",
                sort_desc=True
            )
        )
        
        if low_oi_opportunities:
            profitable_low_oi = [opp for opp in low_oi_opportunities if opp.net_profit_percent > 0]
            self._opportunity_cache['low_oi_opportunities'] = profitable_low_oi[:10]
            analysis_results['cache_updates'] += 1
            analysis_results['low_oi_count'] = len(profitable_low_oi)
        
        # 3. Find high volume opportunities (for safer trading)
        logger.debug("Finding high volume opportunities...")
        high_volume_opportunities = await finder.find_opportunities(
            filters=OpportunityFilter(
                min_profit_percent=Decimal('0.0001'),
                min_volume_24h=Decimal('1000000'),  # > $1M volume
                limit=15,
                sort_by="net_profit_percent",
                sort_desc=True
            )
        )
        
        if high_volume_opportunities:
            profitable_high_vol = [opp for opp in high_volume_opportunities if opp.net_profit_percent > 0]
            self._opportunity_cache['high_volume_opportunities'] = profitable_high_vol[:10]
            analysis_results['cache_updates'] += 1
            analysis_results['high_volume_count'] = len(profitable_high_vol)
        
        # 4. Cache opportunities by popular symbols
        popular_symbols = ['BTC', 'ETH', 'SOL', 'AVAX', 'ARB']  # Add more as needed
        symbol_cache_updates = 0
        
        for symbol in popular_symbols:
            try:
                symbol_opportunities = await finder.find_opportunities_for_symbol(
                    symbol=symbol,
                    filters=OpportunityFilter(
                        min_profit_percent=Decimal('0.0001'),
                        limit=10
                    )
                )
                
                if symbol_opportunities:
                    profitable_symbol_opps = [opp for opp in symbol_opportunities if opp.net_profit_percent > 0]
                    if profitable_symbol_opps:
                        self._opportunity_cache['by_symbol'][symbol] = profitable_symbol_opps[:5]
                        symbol_cache_updates += 1
                        
            except Exception as e:
                logger.warning(f"Failed to analyze opportunities for {symbol}: {e}")
        
        analysis_results['cache_updates'] += symbol_cache_updates
        analysis_results['symbols_cached'] = symbol_cache_updates
        
        # Update cache timestamp
        self._opportunity_cache['last_cache_time'] = datetime.utcnow()
        
        # Log summary
        logger.info(
            f"📈 Opportunity Analysis Complete: "
            f"{analysis_results['profitable_opportunities']} profitable opportunities found, "
            f"{analysis_results['cache_updates']} cache updates"
        )
        
        if analysis_results['top_opportunities']:
            logger.info(f"🏆 Top opportunity: {analysis_results['top_opportunities'][0]}")
        
        return analysis_results
    
    def get_cached_opportunities(self, cache_type: str = 'best_overall') -> List[Dict[str, Any]]:
        """
        Get cached opportunities
        
        Args:
            cache_type: Type of cached opportunities to retrieve
                       ('best_overall', 'low_oi_opportunities', 'high_volume_opportunities')
        
        Returns:
            List of cached opportunities or empty list
        """
        cached = self._opportunity_cache.get(cache_type, [])
        if not cached:
            return []
        
        # Convert to dict format for API responses
        return [
            {
                'symbol': opp.symbol,
                'long_dex': opp.long_dex,
                'short_dex': opp.short_dex,
                'long_rate': float(opp.long_rate),
                'short_rate': float(opp.short_rate),
                'divergence': float(opp.divergence),
                'net_profit_percent': float(opp.net_profit_percent),
                'annualized_apy': float(opp.annualized_apy) if opp.annualized_apy else None,
                'min_oi_usd': float(opp.min_oi_usd) if opp.min_oi_usd else None,
                'discovered_at': opp.discovered_at.isoformat()
            }
            for opp in cached
        ]
    
    def get_cached_symbol_opportunities(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Get cached opportunities for a specific symbol
        
        Args:
            symbol: Symbol to get opportunities for
            
        Returns:
            List of cached opportunities for the symbol
        """
        symbol_cache = self._opportunity_cache['by_symbol'].get(symbol.upper(), [])
        if not symbol_cache:
            return []
        
        return [
            {
                'long_dex': opp.long_dex,
                'short_dex': opp.short_dex,
                'long_rate': float(opp.long_rate),
                'short_rate': float(opp.short_rate),
                'divergence': float(opp.divergence),
                'net_profit_percent': float(opp.net_profit_percent),
                'annualized_apy': float(opp.annualized_apy) if opp.annualized_apy else None,
                'discovered_at': opp.discovered_at.isoformat()
            }
            for opp in symbol_cache
        ]
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics
        
        Returns:
            Dictionary with cache statistics
        """
        return {
            'last_cache_time': self._opportunity_cache['last_cache_time'].isoformat() if self._opportunity_cache['last_cache_time'] else None,
            'best_overall_count': len(self._opportunity_cache.get('best_overall', [])),
            'low_oi_count': len(self._opportunity_cache.get('low_oi_opportunities', [])),
            'high_volume_count': len(self._opportunity_cache.get('high_volume_opportunities', [])),
            'symbols_cached': len(self._opportunity_cache.get('by_symbol', {})),
            'cached_symbols': list(self._opportunity_cache.get('by_symbol', {}).keys())
        }
    
    async def force_analysis(self) -> Dict[str, Any]:
        """
        Force an immediate opportunity analysis
        
        Returns:
            Analysis results
        """
        logger.info("🔄 Force opportunity analysis triggered")
        return await self.run()
