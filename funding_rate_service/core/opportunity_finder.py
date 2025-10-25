"""
Opportunity Finder

Analyzes funding rates across multiple DEXs to find profitable arbitrage opportunities.
Filters by volume, OI, spread, and other criteria.
"""

from decimal import Decimal
from typing import List, Optional, Dict
from datetime import datetime
from funding_rate_service.core.fee_calculator import FundingArbFeeCalculator
from funding_rate_service.models.opportunity import ArbitrageOpportunity
from funding_rate_service.models.filters import OpportunityFilter
from database.connection import Database
from funding_rate_service.core.mappers import DEXMapper, SymbolMapper
from funding_rate_service.utils.logger import logger


class OpportunityFinder:
    """
    Find and analyze funding rate arbitrage opportunities
    
    Strategy:
    1. Fetch latest funding rates from all DEXs
    2. For each symbol, find all DEX pairs
    3. Calculate funding rate divergence
    4. Calculate fees and net profit
    5. Filter by minimum profitability, volume, OI, etc.
    6. Rank opportunities by profitability
    """
    
    def __init__(
        self,
        database: Database,
        fee_calculator: FundingArbFeeCalculator,
        dex_mapper: DEXMapper,
        symbol_mapper: SymbolMapper
    ):
        """
        Initialize opportunity finder
        
        Args:
            database: Database connection
            fee_calculator: Fee calculator instance
            dex_mapper: DEX ID <-> name mapper
            symbol_mapper: Symbol ID <-> name mapper
        """
        self.db = database
        self.fee_calc = fee_calculator
        self.dex_mapper = dex_mapper
        self.symbol_mapper = symbol_mapper
        logger.info("OpportunityFinder initialized")
    
    async def find_opportunities(
        self,
        filters: Optional[OpportunityFilter] = None
    ) -> List[ArbitrageOpportunity]:
        """
        Find all arbitrage opportunities based on latest funding rates
        
        Args:
            filters: Optional filters to apply
            
        Returns:
            List of arbitrage opportunities, sorted by profitability
        """
        if filters is None:
            filters = OpportunityFilter()
        
        logger.debug(f"Finding opportunities with filters: {filters}")
        
        # Fetch latest funding rates with market data
        rates_data = await self._fetch_latest_rates_with_market_data(filters)
        
        if not rates_data:
            logger.warning("No funding rates available")
            return []
        
        # Find all profitable combinations
        opportunities = []
        
        # Group by symbol
        symbols_dict: Dict[str, List[Dict]] = {}
        for rate in rates_data:
            symbol = rate['symbol']
            if symbol not in symbols_dict:
                symbols_dict[symbol] = []
            symbols_dict[symbol].append(rate)
        
        # For each symbol, compare all DEX pairs
        for symbol, dex_rates in symbols_dict.items():
            # Skip if symbol filter doesn't match
            if filters.symbol and symbol != filters.symbol:
                continue
            
            # Need at least 2 DEXs for arbitrage
            if len(dex_rates) < 2:
                continue
            
            # Compare all pairs
            for i in range(len(dex_rates)):
                for j in range(i + 1, len(dex_rates)):
                    rate1 = dex_rates[i]
                    rate2 = dex_rates[j]
                    
                    # Try both directions
                    opp1 = self._create_opportunity(rate1, rate2, symbol, filters)
                    if opp1:
                        opportunities.append(opp1)
                    
                    opp2 = self._create_opportunity(rate2, rate1, symbol, filters)
                    if opp2:
                        opportunities.append(opp2)
        
        # Filter and sort
        filtered_opportunities = self._apply_filters(opportunities, filters)
        sorted_opportunities = self._sort_opportunities(filtered_opportunities, filters)
        
        # Apply limit
        limited_opportunities = sorted_opportunities[:filters.limit]
        
        logger.info(
            f"Found {len(opportunities)} raw opportunities, "
            f"{len(filtered_opportunities)} after filtering, "
            f"returning top {len(limited_opportunities)}"
        )
        
        return limited_opportunities
    
    async def find_best_opportunity(
        self,
        filters: Optional[OpportunityFilter] = None
    ) -> Optional[ArbitrageOpportunity]:
        """
        Find the single best opportunity
        
        Args:
            filters: Optional filters to apply
            
        Returns:
            Best opportunity or None
        """
        if filters is None:
            filters = OpportunityFilter()
        
        # Set limit to 1
        filters.limit = 1
        
        opportunities = await self.find_opportunities(filters)
        
        return opportunities[0] if opportunities else None
    
    async def find_opportunities_for_symbol(
        self,
        symbol: str,
        filters: Optional[OpportunityFilter] = None
    ) -> List[ArbitrageOpportunity]:
        """
        Find opportunities for a specific symbol
        
        Args:
            symbol: Symbol to search for
            filters: Optional additional filters
            
        Returns:
            List of opportunities for the symbol
        """
        if filters is None:
            filters = OpportunityFilter()
        
        filters.symbol = symbol
        
        return await self.find_opportunities(filters)
    
    async def compare_dexes(
        self,
        dex1: str,
        dex2: str,
        symbol: Optional[str] = None
    ) -> List[Dict]:
        """
        Compare opportunities between two specific DEXs
        
        Args:
            dex1: First DEX name
            dex2: Second DEX name
            symbol: Optional symbol filter
            
        Returns:
            List of comparison results
        """
        filters = OpportunityFilter(
            include_dexes=[dex1, dex2],
            symbol=symbol
        )
        
        opportunities = await self.find_opportunities(filters)
        
        # Format results
        results = []
        for opp in opportunities:
            if (opp.long_dex == dex1 and opp.short_dex == dex2) or \
               (opp.long_dex == dex2 and opp.short_dex == dex1):
                
                results.append({
                    'symbol': opp.symbol,
                    'dex1_rate': opp.long_rate if opp.long_dex == dex1 else opp.short_rate,
                    'dex2_rate': opp.short_rate if opp.short_dex == dex2 else opp.long_rate,
                    'rate_diff': abs(opp.divergence),
                    'better_on': dex2 if opp.divergence > 0 else dex1,
                    'recommendation': f"{'short' if opp.short_dex == dex1 else 'long'}_on_{dex1}_{'long' if opp.long_dex == dex2 else 'short'}_on_{dex2}",
                    'net_profit_percent': opp.net_profit_percent,
                    'annualized_apy': opp.annualized_apy,
                })
        
        return results
    
    def _create_opportunity(
        self,
        long_rate_data: Dict,
        short_rate_data: Dict,
        symbol: str,
        filters: OpportunityFilter
    ) -> Optional[ArbitrageOpportunity]:
        """
        Create an opportunity from two DEX rates
        
        Args:
            long_rate_data: Data for the long position DEX (lower rate)
            short_rate_data: Data for the short position DEX (higher rate)
            symbol: Symbol name
            filters: Filters to check
            
        Returns:
            ArbitrageOpportunity or None if not profitable/doesn't meet criteria
        """
        dex_long = long_rate_data['dex_name']
        dex_short = short_rate_data['dex_name']
        dex_long_lower = dex_long.lower()
        dex_short_lower = dex_short.lower()
        
        rate_long = long_rate_data['funding_rate']
        rate_short = short_rate_data['funding_rate']
        
        # Divergence (what we profit from funding)
        divergence = rate_short - rate_long
        
        # Check minimum divergence
        if divergence < filters.min_divergence:
            return None
        
        # Calculate fees and net profit
        costs = self.fee_calc.calculate_costs(
            dex_long=dex_long,
            dex_short=dex_short,
            funding_rate_long=rate_long,
            funding_rate_short=rate_short,
            use_maker_orders=True
        )
        
        # Check if profitable
        if not costs.is_profitable:
            return None
        
        # Check minimum profit
        if costs.net_rate < filters.min_profit_percent:
            return None
        
        # Apply DEX filters (position-agnostic)

        required_dex_lower: Optional[str] = None
        if filters.required_dex:
            required_dex_lower = filters.required_dex.lower()

        # Single DEX filter (must be on one side)
        if filters.dex and not (dex_long_lower == filters.dex or dex_short_lower == filters.dex):
            return None

        # DEX pair filter (must be exactly these two DEXs in any order)
        if filters.dex_pair:
            if not (
                (dex_long_lower in filters.dex_pair and dex_short_lower in filters.dex_pair) and
                dex_long_lower != dex_short_lower
            ):
                return None

        # DEXes filter (at least one DEX must be involved)
        if filters.dexes and not (dex_long_lower in filters.dexes or dex_short_lower in filters.dexes):
            return None

        # Whitelist filter (both DEXs must be in whitelist)
        if filters.whitelist_dexes and not (dex_long_lower in filters.whitelist_dexes and dex_short_lower in filters.whitelist_dexes):
            return None

        # Exclude filter (neither DEX should be in exclude list)
        if filters.exclude_dexes and (dex_long_lower in filters.exclude_dexes or dex_short_lower in filters.exclude_dexes):
            return None

        # Required DEX filter (must appear on at least one side)
        if required_dex_lower:
            if dex_long_lower != required_dex_lower and dex_short_lower != required_dex_lower:
                return None
        
        # Volume metrics
        long_volume = long_rate_data.get('volume_24h')
        short_volume = short_rate_data.get('volume_24h')
        min_volume = min(long_volume, short_volume) if long_volume and short_volume else None
        
        # Check volume filters
        if filters.min_volume_24h and min_volume:
            if min_volume < filters.min_volume_24h:
                return None
        if filters.max_volume_24h and min_volume:
            if min_volume > filters.max_volume_24h:
                return None
        
        # OI metrics
        long_oi_raw = long_rate_data.get('open_interest_usd')
        short_oi_raw = short_rate_data.get('open_interest_usd')
        try:
            long_oi = Decimal(str(long_oi_raw)) if long_oi_raw is not None else None
        except Exception:
            long_oi = None
        try:
            short_oi = Decimal(str(short_oi_raw)) if short_oi_raw is not None else None
        except Exception:
            short_oi = None
        
        min_oi = None
        max_oi = None
        oi_ratio = None
        oi_imbalance = None
        
        if long_oi is not None and short_oi is not None:
            min_oi = min(long_oi, short_oi)
            max_oi = max(long_oi, short_oi)
            
            if short_oi > 0:
                oi_ratio = long_oi / short_oi
                
                if oi_ratio > Decimal('1.2'):
                    oi_imbalance = 'long_heavy'
                elif oi_ratio < Decimal('0.8'):
                    oi_imbalance = 'short_heavy'
                else:
                    oi_imbalance = 'balanced'
        
        # Check OI filters
        if filters.min_oi_usd and min_oi:
            if min_oi < filters.min_oi_usd:
                return None
        if filters.max_oi_usd:
            if required_dex_lower:
                if dex_long.lower() == required_dex_lower:
                    target_oi = long_oi
                elif dex_short.lower() == required_dex_lower:
                    target_oi = short_oi
                else:
                    logger.info(
                        f"[OPP] {symbol} skipped: required_dex={required_dex_lower} not present ({dex_long}/{dex_short})"
                    )
                    return None

                if target_oi is not None and not isinstance(target_oi, Decimal):
                    try:
                        target_oi = Decimal(str(target_oi))
                    except Exception:
                        logger.info(
                            f"[OPP] {symbol} skipped: unable to coerce target OI {target_oi} for dex {required_dex_lower}"
                        )
                        return None

                if target_oi is None:
                    logger.info(
                        f"[OPP] {symbol} skipped: missing OI for required dex {required_dex_lower}"
                    )
                    return None

                if filters.max_oi_usd is not None and target_oi > filters.max_oi_usd:
                    logger.debug(
                        f"[OPP] {symbol} skipped: required dex {required_dex_lower} OI {target_oi} exceeds cap {filters.max_oi_usd}"
                    )
                    return None

                logger.debug(
                    f"[OPP] {symbol} accepted: required dex {required_dex_lower} OI {target_oi} within cap {filters.max_oi_usd}"
                )
            else:
                if min_oi and min_oi > filters.max_oi_usd:
                    logger.info(
                        f"[OPP] {symbol} skipped: min OI {min_oi} exceeds cap {filters.max_oi_usd} ({dex_long}/{dex_short})"
                    )
                    return None

                logger.debug(
                    f"[OPP] {symbol} accepted: min OI {min_oi} within cap {filters.max_oi_usd} ({dex_long}/{dex_short})"
                )
        if filters.oi_ratio_min and oi_ratio:
            if oi_ratio < filters.oi_ratio_min:
                return None
        if filters.oi_ratio_max and oi_ratio:
            if oi_ratio > filters.oi_ratio_max:
                return None
        
        # Spread metrics
        long_spread = long_rate_data.get('spread_bps')
        short_spread = short_rate_data.get('spread_bps')
        avg_spread = None
        
        if long_spread is not None and short_spread is not None:
            avg_spread = (long_spread + short_spread) // 2
        
        # Check spread filter
        if filters.max_spread_bps and avg_spread:
            if avg_spread > filters.max_spread_bps:
                return None
        
        # Create opportunity
        opportunity = ArbitrageOpportunity(
            symbol=symbol,
            long_dex=dex_long,
            short_dex=dex_short,
            long_rate=rate_long,
            short_rate=rate_short,
            divergence=divergence,
            estimated_fees=costs.total_fee,
            net_profit_percent=costs.net_rate,
            annualized_apy=costs.net_apy,
            long_dex_volume_24h=long_volume,
            short_dex_volume_24h=short_volume,
            min_volume_24h=min_volume,
            long_dex_oi_usd=long_oi,
            short_dex_oi_usd=short_oi,
            min_oi_usd=min_oi,
            max_oi_usd=max_oi,
            oi_ratio=oi_ratio,
            oi_imbalance=oi_imbalance,
            long_dex_spread_bps=long_spread,
            short_dex_spread_bps=short_spread,
            avg_spread_bps=avg_spread,
            discovered_at=datetime.utcnow()
        )
        
        return opportunity
    
    def _apply_filters(
        self,
        opportunities: List[ArbitrageOpportunity],
        filters: OpportunityFilter
    ) -> List[ArbitrageOpportunity]:
        """
        Apply additional filters to opportunities
        
        Args:
            opportunities: List of opportunities
            filters: Filters to apply
            
        Returns:
            Filtered list
        """
        # All filtering is done in _create_opportunity
        return opportunities
    
    def _sort_opportunities(
        self,
        opportunities: List[ArbitrageOpportunity],
        filters: OpportunityFilter
    ) -> List[ArbitrageOpportunity]:
        """
        Sort opportunities by specified criteria
        
        Args:
            opportunities: List of opportunities
            filters: Filter containing sort criteria
            
        Returns:
            Sorted list
        """
        # Get sort field
        sort_field = filters.sort_by
        reverse = filters.sort_desc
        
        # Define sort key function
        def get_sort_key(opp: ArbitrageOpportunity):
            value = getattr(opp, sort_field, None)
            if value is None:
                return Decimal('-inf') if reverse else Decimal('inf')
            return value
        
        try:
            sorted_opps = sorted(opportunities, key=get_sort_key, reverse=reverse)
            return sorted_opps
        except Exception as e:
            logger.warning(f"Error sorting by {sort_field}: {e}, using default sort")
            # Default: sort by net profit
            return sorted(
                opportunities,
                key=lambda x: x.net_profit_percent,
                reverse=True
            )
    
    async def _fetch_latest_rates_with_market_data(
        self,
        filters: OpportunityFilter
    ) -> List[Dict]:
        """
        Fetch latest funding rates with market data (volume, OI, spreads)
        
        Args:
            filters: Filters to apply (symbol, DEXs)
            
        Returns:
            List of rate data dictionaries
        """
        # Build query
        query = """
            SELECT 
                d.name as dex_name,
                s.symbol,
                lfr.funding_rate,
                ds.volume_24h,
                ds.open_interest_usd,
                ds.spread_bps,
                lfr.updated_at
            FROM latest_funding_rates lfr
            JOIN dexes d ON lfr.dex_id = d.id
            JOIN symbols s ON lfr.symbol_id = s.id
            LEFT JOIN dex_symbols ds ON ds.dex_id = d.id AND ds.symbol_id = s.id
            WHERE d.is_active = TRUE
        """
        
        params = {}
        
        # Add symbol filter
        if filters.symbol:
            query += " AND s.symbol = :symbol"
            params["symbol"] = filters.symbol
        
        # Add DEX filters at database level (for efficiency)
        # Note: Most filtering happens in _create_opportunity, but we can optimize here
        
        # Single DEX filter - can't optimize at DB level (need to check pairs)
        # DEX pair filter - can optimize: only fetch these two DEXs
        if filters.dex_pair:
            placeholders = ','.join([f":dex_pair_{i}" for i in range(len(filters.dex_pair))])
            query += f" AND d.name IN ({placeholders})"
            for i, dex in enumerate(filters.dex_pair):
                params[f"dex_pair_{i}"] = dex
        
        # Whitelist filter - only fetch from whitelist
        elif filters.whitelist_dexes:
            placeholders = ','.join([f":whitelist_{i}" for i in range(len(filters.whitelist_dexes))])
            query += f" AND d.name IN ({placeholders})"
            for i, dex in enumerate(filters.whitelist_dexes):
                params[f"whitelist_{i}"] = dex
        
        # Exclude filter - exclude at DB level
        if filters.exclude_dexes:
            placeholders = ','.join([f":exclude_dex_{i}" for i in range(len(filters.exclude_dexes))])
            query += f" AND d.name NOT IN ({placeholders})"
            for i, dex in enumerate(filters.exclude_dexes):
                params[f"exclude_dex_{i}"] = dex
        
        # Execute query
        try:
            logger.debug(f"Executing query with params: {params}")
            rows = await self.db.fetch_all(query, values=params)
            
            # Convert to list of dicts
            results = []
            for row in rows:
                results.append({
                    'dex_name': row['dex_name'],
                    'symbol': row['symbol'],
                    'funding_rate': row['funding_rate'],
                    'volume_24h': row['volume_24h'],
                    'open_interest_usd': row['open_interest_usd'],
                    'spread_bps': row['spread_bps'],
                    'updated_at': row['updated_at']
                })
            
            logger.debug(f"Fetched {len(results)} funding rates from database for symbol={filters.symbol}")
            return results
        
        except Exception as e:
            logger.error(f"Error fetching rates from database: {e}", exc_info=True)
            logger.error(f"Query: {query}")
            logger.error(f"Params: {params}")
            return []
