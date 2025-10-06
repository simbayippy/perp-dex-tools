"""
Historical Analyzer

Analyzes historical funding rate data to provide statistical insights.
Used for trend analysis, volatility calculations, and backtesting.
"""

from decimal import Decimal
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import statistics

from database.connection import Database
from core.mappers import DEXMapper, SymbolMapper
from models.history import FundingRateHistory, FundingRateStats
from utils.logger import logger


class HistoricalAnalyzer:
    """
    Analyze historical funding rate data
    
    Provides statistical analysis including:
    - Average, median, standard deviation
    - Min, max, percentiles
    - Volatility (std dev / mean)
    - Annualized APY
    - Positive rate frequency
    """
    
    # Funding rate constants
    FUNDING_INTERVAL_HOURS = Decimal('8')
    HOURS_PER_YEAR = Decimal('8760')  # 365 * 24
    
    def __init__(
        self,
        database: Database,
        dex_mapper: DEXMapper,
        symbol_mapper: SymbolMapper
    ):
        """
        Initialize historical analyzer
        
        Args:
            database: Database connection
            dex_mapper: DEX ID <-> name mapper
            symbol_mapper: Symbol ID <-> name mapper
        """
        self.db = database
        self.dex_mapper = dex_mapper
        self.symbol_mapper = symbol_mapper
        logger.info("HistoricalAnalyzer initialized")
    
    async def get_funding_rate_history(
        self,
        symbol: str,
        dex_name: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        period_days: Optional[int] = None,
        limit: Optional[int] = 1000
    ) -> FundingRateHistory:
        """
        Get historical funding rates with statistics
        
        Args:
            symbol: Symbol to analyze
            dex_name: Optional DEX filter (None = all DEXs)
            start_time: Start of time period
            end_time: End of time period (default: now)
            period_days: Alternative to start_time (e.g., last 7 days)
            limit: Max data points to return
            
        Returns:
            FundingRateHistory with data points and statistics
        """
        # Determine time period
        if end_time is None:
            end_time = datetime.utcnow()
        
        if start_time is None:
            if period_days:
                start_time = end_time - timedelta(days=period_days)
            else:
                start_time = end_time - timedelta(days=7)  # Default: 7 days
        
        # Build query
        query = """
            SELECT 
                fr.time,
                fr.funding_rate,
                d.name as dex_name
            FROM funding_rates fr
            JOIN symbols s ON fr.symbol_id = s.id
            JOIN dexes d ON fr.dex_id = d.id
            WHERE s.symbol = $1
              AND fr.time >= $2
              AND fr.time <= $3
        """
        
        params = [symbol, start_time, end_time]
        
        # Add DEX filter
        if dex_name:
            query += " AND d.name = $4"
            params.append(dex_name)
        
        # Order and limit
        query += " ORDER BY fr.time ASC"
        if limit:
            query += f" LIMIT ${len(params) + 1}"
            params.append(limit)
        
        # Execute query
        try:
            rows = await self.db.fetch_all(query, *params)
            
            if not rows:
                logger.warning(f"No historical data found for {symbol} on {dex_name or 'all DEXs'}")
                # Return empty result
                return FundingRateHistory(
                    dex_name=dex_name or "all",
                    symbol=symbol,
                    data_points=[],
                    avg_rate=Decimal('0'),
                    median_rate=Decimal('0'),
                    std_dev=Decimal('0'),
                    min_rate=Decimal('0'),
                    max_rate=Decimal('0'),
                    period_start=start_time,
                    period_end=end_time
                )
            
            # Convert to data points
            data_points = []
            rates = []
            
            for row in rows:
                data_points.append({
                    'time': row['time'].isoformat(),
                    'rate': float(row['funding_rate']),
                    'dex_name': row['dex_name']
                })
                rates.append(float(row['funding_rate']))
            
            # Calculate statistics
            avg_rate = Decimal(str(statistics.mean(rates)))
            median_rate = Decimal(str(statistics.median(rates)))
            std_dev = Decimal(str(statistics.stdev(rates))) if len(rates) > 1 else Decimal('0')
            min_rate = Decimal(str(min(rates)))
            max_rate = Decimal(str(max(rates)))
            
            logger.info(
                f"Retrieved {len(data_points)} historical data points for {symbol} "
                f"on {dex_name or 'all DEXs'}"
            )
            
            return FundingRateHistory(
                dex_name=dex_name or "all",
                symbol=symbol,
                data_points=data_points,
                avg_rate=avg_rate,
                median_rate=median_rate,
                std_dev=std_dev,
                min_rate=min_rate,
                max_rate=max_rate,
                period_start=start_time,
                period_end=end_time
            )
        
        except Exception as e:
            logger.error(f"Error fetching historical data: {e}")
            raise
    
    async def get_funding_rate_stats(
        self,
        symbol: str,
        dex_name: Optional[str] = None,
        period_days: int = 30
    ) -> FundingRateStats:
        """
        Get comprehensive statistical analysis of funding rates
        
        Args:
            symbol: Symbol to analyze
            dex_name: Optional DEX filter (None = all DEXs)
            period_days: Analysis period in days
            
        Returns:
            FundingRateStats with comprehensive statistics
        """
        # Determine time period
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=period_days)
        
        # Build query
        query = """
            SELECT 
                fr.funding_rate
            FROM funding_rates fr
            JOIN symbols s ON fr.symbol_id = s.id
            JOIN dexes d ON fr.dex_id = d.id
            WHERE s.symbol = $1
              AND fr.time >= $2
              AND fr.time <= $3
        """
        
        params = [symbol, start_time, end_time]
        
        # Add DEX filter
        if dex_name:
            query += " AND d.name = $4"
            params.append(dex_name)
        
        # Execute query
        try:
            rows = await self.db.fetch_all(query, *params)
            
            if not rows:
                logger.warning(f"No data found for {symbol} on {dex_name or 'all DEXs'}")
                # Return zero stats
                return self._create_empty_stats(symbol, dex_name, period_days, start_time, end_time)
            
            # Extract rates
            rates = [float(row['funding_rate']) for row in rows]
            
            # Basic statistics
            avg_rate = statistics.mean(rates)
            median_rate = statistics.median(rates)
            std_dev = statistics.stdev(rates) if len(rates) > 1 else 0.0
            min_rate = min(rates)
            max_rate = max(rates)
            
            # Volatility (coefficient of variation)
            volatility = (std_dev / abs(avg_rate)) if avg_rate != 0 else 0.0
            
            # Percentiles
            sorted_rates = sorted(rates)
            percentile_25 = self._calculate_percentile(sorted_rates, 25)
            percentile_75 = self._calculate_percentile(sorted_rates, 75)
            
            # Annualized APY
            # funding_rate is per 8 hours, so multiply by (365 * 3) for annual
            payments_per_year = self.HOURS_PER_YEAR / self.FUNDING_INTERVAL_HOURS
            avg_annualized_apy = Decimal(str(avg_rate)) * payments_per_year * Decimal('100')
            
            # Positive rate frequency (% of time rate was positive)
            positive_count = sum(1 for r in rates if r > 0)
            positive_rate_frequency = positive_count / len(rates) if rates else 0.0
            
            logger.info(
                f"Calculated stats for {symbol} on {dex_name or 'all DEXs'}: "
                f"{len(rates)} data points, avg={avg_rate:.6f}, "
                f"volatility={volatility:.2f}"
            )
            
            return FundingRateStats(
                symbol=symbol,
                dex_name=dex_name,
                period_days=period_days,
                period_start=start_time,
                period_end=end_time,
                avg_funding_rate=Decimal(str(avg_rate)),
                median_funding_rate=Decimal(str(median_rate)),
                std_dev=Decimal(str(std_dev)),
                volatility=Decimal(str(volatility)),
                min_rate=Decimal(str(min_rate)),
                max_rate=Decimal(str(max_rate)),
                percentile_25=Decimal(str(percentile_25)),
                percentile_75=Decimal(str(percentile_75)),
                avg_annualized_apy=avg_annualized_apy,
                positive_rate_frequency=positive_rate_frequency
            )
        
        except Exception as e:
            logger.error(f"Error calculating statistics: {e}")
            raise
    
    async def compare_dex_stats(
        self,
        symbol: str,
        dex_names: List[str],
        period_days: int = 30
    ) -> Dict[str, FundingRateStats]:
        """
        Compare statistics across multiple DEXs for a symbol
        
        Args:
            symbol: Symbol to analyze
            dex_names: List of DEX names to compare
            period_days: Analysis period
            
        Returns:
            Dictionary mapping DEX name to its statistics
        """
        results = {}
        
        for dex_name in dex_names:
            try:
                stats = await self.get_funding_rate_stats(
                    symbol=symbol,
                    dex_name=dex_name,
                    period_days=period_days
                )
                results[dex_name] = stats
            except Exception as e:
                logger.error(f"Error getting stats for {dex_name}: {e}")
                results[dex_name] = self._create_empty_stats(
                    symbol,
                    dex_name,
                    period_days,
                    datetime.utcnow() - timedelta(days=period_days),
                    datetime.utcnow()
                )
        
        return results
    
    async def get_symbol_summary(
        self,
        symbol: str,
        period_days: int = 7
    ) -> Dict[str, Any]:
        """
        Get a quick summary of a symbol across all DEXs
        
        Args:
            symbol: Symbol to analyze
            period_days: Analysis period
            
        Returns:
            Summary dictionary with key metrics
        """
        # Get overall stats
        overall_stats = await self.get_funding_rate_stats(
            symbol=symbol,
            dex_name=None,
            period_days=period_days
        )
        
        # Get per-DEX latest rates
        query = """
            SELECT 
                d.name as dex_name,
                lfr.funding_rate,
                lfr.updated_at
            FROM latest_funding_rates lfr
            JOIN symbols s ON lfr.symbol_id = s.id
            JOIN dexes d ON lfr.dex_id = d.id
            WHERE s.symbol = $1
              AND d.is_active = TRUE
            ORDER BY lfr.updated_at DESC
        """
        
        rows = await self.db.fetch_all(query, symbol)
        
        latest_rates = {}
        for row in rows:
            latest_rates[row['dex_name']] = {
                'rate': float(row['funding_rate']),
                'updated_at': row['updated_at'].isoformat()
            }
        
        return {
            'symbol': symbol,
            'period_days': period_days,
            'statistics': {
                'avg_rate': float(overall_stats.avg_funding_rate),
                'median_rate': float(overall_stats.median_funding_rate),
                'volatility': float(overall_stats.volatility),
                'avg_annualized_apy': float(overall_stats.avg_annualized_apy),
                'positive_rate_frequency': overall_stats.positive_rate_frequency
            },
            'latest_rates': latest_rates,
            'dex_count': len(latest_rates)
        }
    
    def _calculate_percentile(self, sorted_values: List[float], percentile: int) -> float:
        """
        Calculate percentile from sorted values
        
        Args:
            sorted_values: Pre-sorted list of values
            percentile: Percentile to calculate (0-100)
            
        Returns:
            Percentile value
        """
        if not sorted_values:
            return 0.0
        
        if len(sorted_values) == 1:
            return sorted_values[0]
        
        # Use linear interpolation
        k = (len(sorted_values) - 1) * (percentile / 100.0)
        f = int(k)
        c = k - f
        
        if f + 1 < len(sorted_values):
            return sorted_values[f] + (c * (sorted_values[f + 1] - sorted_values[f]))
        else:
            return sorted_values[f]
    
    def _create_empty_stats(
        self,
        symbol: str,
        dex_name: Optional[str],
        period_days: int,
        start_time: datetime,
        end_time: datetime
    ) -> FundingRateStats:
        """Create empty statistics object"""
        return FundingRateStats(
            symbol=symbol,
            dex_name=dex_name,
            period_days=period_days,
            period_start=start_time,
            period_end=end_time,
            avg_funding_rate=Decimal('0'),
            median_funding_rate=Decimal('0'),
            std_dev=Decimal('0'),
            volatility=Decimal('0'),
            min_rate=Decimal('0'),
            max_rate=Decimal('0'),
            percentile_25=Decimal('0'),
            percentile_75=Decimal('0'),
            avg_annualized_apy=Decimal('0'),
            positive_rate_frequency=0.0
        )


# Global instance (to be initialized with dependencies)
historical_analyzer: Optional[HistoricalAnalyzer] = None


def init_historical_analyzer(
    database: Database,
    dex_mapper: DEXMapper,
    symbol_mapper: SymbolMapper
) -> HistoricalAnalyzer:
    """
    Initialize global historical analyzer instance
    
    Args:
        database: Database connection
        dex_mapper: DEX mapper
        symbol_mapper: Symbol mapper
        
    Returns:
        Initialized HistoricalAnalyzer
    """
    global historical_analyzer
    historical_analyzer = HistoricalAnalyzer(
        database=database,
        dex_mapper=dex_mapper,
        symbol_mapper=symbol_mapper
    )
    logger.info(f"HistoricalAnalyzer initialized: {historical_analyzer is not None}")
    return historical_analyzer

