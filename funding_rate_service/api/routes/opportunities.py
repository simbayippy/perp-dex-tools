"""
Opportunities API Routes

Endpoints for finding and filtering arbitrage opportunities.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime

from funding_rate_service.core.opportunity_finder import OpportunityFinder
from funding_rate_service.core.dependencies import get_opportunity_finder
from funding_rate_service.models.opportunity import ArbitrageOpportunity
from funding_rate_service.models.filters import OpportunityFilter
from funding_rate_service.utils.logger import logger


router = APIRouter()


@router.get("/opportunities", response_model=Dict[str, Any])
async def get_opportunities(
    finder: OpportunityFinder = Depends(get_opportunity_finder),
    # Symbol filter
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    
    # DEX filters (position-agnostic)
    dex: Optional[str] = Query(None, description="Show opportunities involving this DEX (long or short)"),
    dex_pair: Optional[str] = Query(None, description="Two DEXs separated by comma (e.g., 'lighter,grvt')"),
    dexes: Optional[str] = Query(None, description="Comma-separated list - show opps involving ANY of these"),
    whitelist_dexes: Optional[str] = Query(None, description="Comma-separated list - BOTH sides must be from this list"),
    exclude_dexes: Optional[str] = Query(None, description="Comma-separated list - exclude opps with these DEXs"),
    required_dex: Optional[str] = Query(None, description="DEX that must be present on at least one leg"),
    
    # Profitability filters
    min_divergence: Optional[Decimal] = Query(Decimal('0.0001'), description="Minimum divergence"),
    min_profit: Optional[Decimal] = Query(Decimal('0'), description="Minimum net profit percent"),
    
    # Volume filters
    min_volume: Optional[Decimal] = Query(None, description="Minimum 24h volume in USD"),
    max_volume: Optional[Decimal] = Query(None, description="Maximum 24h volume in USD"),
    
    # OI filters (for low OI farming!)
    min_oi: Optional[Decimal] = Query(None, description="Minimum open interest in USD"),
    max_oi: Optional[Decimal] = Query(None, description="Maximum open interest in USD (for low OI farming)"),
    oi_ratio_min: Optional[Decimal] = Query(None, description="Minimum OI ratio (long/short)"),
    oi_ratio_max: Optional[Decimal] = Query(None, description="Maximum OI ratio (long/short)"),
    
    # Liquidity filters
    max_spread: Optional[int] = Query(None, description="Maximum spread in basis points"),
    
    # Sorting and pagination
    limit: int = Query(10, ge=1, le=100, description="Number of results"),
    sort_by: str = Query("net_profit_percent", description="Sort field"),
    sort_desc: bool = Query(True, description="Sort descending")
) -> Dict[str, Any]:
    """
    Get all arbitrage opportunities with flexible filtering
    
    Perfect for:
    - Finding the most profitable opportunities
    - Low OI farming strategies (use max_oi parameter)
    - Specific DEX pair comparisons
    - Volume-based filtering
    """
    try:
        # Parse DEX filters
        dex_pair_list = None
        if dex_pair:
            dex_pair_list = [d.strip().lower() for d in dex_pair.split(',')]
            if len(dex_pair_list) != 2:
                raise HTTPException(status_code=400, detail="dex_pair must contain exactly 2 DEXs")
        
        dexes_list = [d.strip().lower() for d in dexes.split(',')] if dexes else None
        whitelist_dexes_list = [d.strip().lower() for d in whitelist_dexes.split(',')] if whitelist_dexes else None
        exclude_dexes_list = [d.strip().lower() for d in exclude_dexes.split(',')] if exclude_dexes else None
        
        # Create filter
        filters = OpportunityFilter(
            symbol=symbol.upper() if symbol else None,
            dex=dex.lower() if dex else None,
            dex_pair=dex_pair_list,
            dexes=dexes_list,
            whitelist_dexes=whitelist_dexes_list,
            exclude_dexes=exclude_dexes_list,
            required_dex=required_dex.strip().lower() if required_dex else None,
            min_divergence=min_divergence,
            min_profit_percent=min_profit,
            min_volume_24h=min_volume,
            max_volume_24h=max_volume,
            min_oi_usd=min_oi,
            max_oi_usd=max_oi,
            oi_ratio_min=oi_ratio_min,
            oi_ratio_max=oi_ratio_max,
            max_spread_bps=max_spread,
            limit=limit,
            sort_by=sort_by,
            sort_desc=sort_desc
        )
        
        # Find opportunities using injected dependency
        opportunities = await finder.find_opportunities(filters)
        
        # Convert to response format
        opportunities_data = []
        for opp in opportunities:
            opportunities_data.append({
                "symbol": opp.symbol,
                "long_dex": opp.long_dex,
                "short_dex": opp.short_dex,
                "long_rate": float(opp.long_rate),
                "short_rate": float(opp.short_rate),
                "divergence": float(opp.divergence),
                "estimated_fees": float(opp.estimated_fees),
                "net_profit_percent": float(opp.net_profit_percent),
                "annualized_apy": float(opp.annualized_apy),
                "long_dex_volume_24h": float(opp.long_dex_volume_24h) if opp.long_dex_volume_24h else None,
                "short_dex_volume_24h": float(opp.short_dex_volume_24h) if opp.short_dex_volume_24h else None,
                "min_volume_24h": float(opp.min_volume_24h) if opp.min_volume_24h else None,
                "long_dex_oi_usd": float(opp.long_dex_oi_usd) if opp.long_dex_oi_usd else None,
                "short_dex_oi_usd": float(opp.short_dex_oi_usd) if opp.short_dex_oi_usd else None,
                "min_oi_usd": float(opp.min_oi_usd) if opp.min_oi_usd else None,
                "max_oi_usd": float(opp.max_oi_usd) if opp.max_oi_usd else None,
                "oi_ratio": float(opp.oi_ratio) if opp.oi_ratio else None,
                "oi_imbalance": opp.oi_imbalance,
                "long_dex_spread_bps": opp.long_dex_spread_bps,
                "short_dex_spread_bps": opp.short_dex_spread_bps,
                "avg_spread_bps": opp.avg_spread_bps,
                "discovered_at": opp.discovered_at.isoformat()
            })
        
        return {
            "opportunities": opportunities_data,
            "total_count": len(opportunities),
            "filters_applied": {
                "symbol": symbol,
                "min_divergence": float(min_divergence),
                "min_profit_percent": float(min_profit),
                "min_volume_24h": float(min_volume) if min_volume else None,
                "max_oi_usd": float(max_oi) if max_oi else None,
                "limit": limit
            },
            "generated_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error finding opportunities: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/opportunities/best", response_model=Dict[str, Any])
async def get_best_opportunity(
    finder: OpportunityFinder = Depends(get_opportunity_finder),
    # Filters
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    dex: Optional[str] = Query(None, description="Show opportunities involving this DEX"),
    whitelist_dexes: Optional[str] = Query(None, description="Comma-separated list - BOTH sides must be from this list"),
    exclude_dexes: Optional[str] = Query(None, description="Comma-separated list - exclude opps with these DEXs"),
    required_dex: Optional[str] = Query(None, description="DEX that must be present on at least one leg"),
    min_profit: Optional[Decimal] = Query(Decimal('0'), description="Minimum net profit percent"),
    max_oi: Optional[Decimal] = Query(None, description="Maximum open interest (for low OI farming)")
) -> Dict[str, Any]:
    """
    Get the single best opportunity (highest net profit)
    
    Perfect for automated trading bots that want the top opportunity
    """
    try:
        # Parse DEX filters
        whitelist_dexes_list = [d.strip().lower() for d in whitelist_dexes.split(',')] if whitelist_dexes else None
        exclude_dexes_list = [d.strip().lower() for d in exclude_dexes.split(',')] if exclude_dexes else None
        
        # Create filter
        filters = OpportunityFilter(
            symbol=symbol.upper() if symbol else None,
            dex=dex.lower() if dex else None,
            whitelist_dexes=whitelist_dexes_list,
            exclude_dexes=exclude_dexes_list,
            required_dex=required_dex.strip().lower() if required_dex else None,
            min_profit_percent=min_profit,
            max_oi_usd=max_oi,
            limit=1  # Only get the best one
        )
        
        # Find best opportunity
        best = await finder.find_best_opportunity(filters)
        
        if not best:
            return {
                "opportunity": None,
                "message": "No profitable opportunities found with the given filters",
                "generated_at": datetime.utcnow().isoformat()
            }
        
        return {
            "opportunity": {
                "symbol": best.symbol,
                "long_dex": best.long_dex,
                "short_dex": best.short_dex,
                "long_rate": float(best.long_rate),
                "short_rate": float(best.short_rate),
                "divergence": float(best.divergence),
                "estimated_fees": float(best.estimated_fees),
                "net_profit_percent": float(best.net_profit_percent),
                "annualized_apy": float(best.annualized_apy),
                "min_volume_24h": float(best.min_volume_24h) if best.min_volume_24h else None,
                "min_oi_usd": float(best.min_oi_usd) if best.min_oi_usd else None,
                "oi_imbalance": best.oi_imbalance,
                "discovered_at": best.discovered_at.isoformat()
            },
            "rank": 1,
            "generated_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error finding best opportunity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/opportunities/symbol/{symbol}", response_model=Dict[str, Any])
async def get_opportunities_for_symbol(
    symbol: str,
    finder: OpportunityFinder = Depends(get_opportunity_finder),
    min_profit: Optional[Decimal] = Query(Decimal('0'), description="Minimum net profit percent"),
    limit: int = Query(10, ge=1, le=100, description="Number of results")
) -> Dict[str, Any]:
    """
    Get opportunities for a specific symbol
    
    Useful for focusing on a particular asset (e.g., BTC, ETH)
    """
    try:
        opportunities = await finder.find_opportunities_for_symbol(
            symbol=symbol.upper(),
            filters=OpportunityFilter(
                min_profit_percent=min_profit,
                limit=limit
            )
        )
        
        # Convert to response format
        opportunities_data = []
        for opp in opportunities:
            opportunities_data.append({
                "long_dex": opp.long_dex,
                "short_dex": opp.short_dex,
                "long_rate": float(opp.long_rate),
                "short_rate": float(opp.short_rate),
                "divergence": float(opp.divergence),
                "net_profit_percent": float(opp.net_profit_percent),
                "annualized_apy": float(opp.annualized_apy),
                "min_oi_usd": float(opp.min_oi_usd) if opp.min_oi_usd else None
            })
        
        return {
            "symbol": symbol.upper(),
            "opportunities": opportunities_data,
            "count": len(opportunities),
            "generated_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error finding opportunities for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
