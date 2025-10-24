"""
Health API Routes

Endpoints for service health checks and system status.
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from datetime import datetime, timedelta

from database.connection import database
from funding_rate_service.utils.logger import logger


router = APIRouter()


@router.get("/health", response_model=Dict[str, Any])
async def get_service_health() -> Dict[str, Any]:
    """
    Get comprehensive service health status
    
    Includes:
    - Overall service status
    - Individual DEX health
    - Data freshness
    - Database connectivity
    """
    try:
        # Check database connectivity
        try:
            await database.fetch_val("SELECT 1")
            db_healthy = True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            db_healthy = False
        
        # Get DEX health
        dex_health_query = """
            SELECT 
                d.name as dex_name,
                d.is_active,
                d.last_successful_fetch,
                d.consecutive_errors,
                d.last_error,
                COUNT(DISTINCT lfr.symbol_id) as active_symbols
            FROM dexes d
            LEFT JOIN latest_funding_rates lfr ON lfr.dex_id = d.id
            GROUP BY d.id, d.name, d.is_active, d.last_successful_fetch, 
                     d.consecutive_errors, d.last_error
            ORDER BY d.name
        """
        
        dex_rows = await database.fetch_all(dex_health_query)
        
        dex_health = []
        healthy_dexes = 0
        total_active_dexes = 0
        
        for row in dex_rows:
            if row['is_active']:
                total_active_dexes += 1
            
            is_healthy = (
                row['is_active'] and 
                row['consecutive_errors'] == 0 and
                row['last_successful_fetch'] is not None
            )
            
            if is_healthy:
                healthy_dexes += 1
            
            # Calculate time since last fetch
            time_since_fetch = None
            if row['last_successful_fetch']:
                time_since_fetch = (datetime.utcnow() - row['last_successful_fetch']).total_seconds()
            
            dex_health.append({
                "dex_name": row['dex_name'],
                "is_healthy": is_healthy,
                "is_active": row['is_active'],
                "last_successful_fetch": row['last_successful_fetch'].isoformat() if row['last_successful_fetch'] else None,
                "time_since_fetch_seconds": int(time_since_fetch) if time_since_fetch else None,
                "consecutive_errors": row['consecutive_errors'],
                "last_error": row['last_error'].isoformat() if row['last_error'] else None,
                "active_symbols": row['active_symbols']
            })
        
        # Get data freshness
        freshness_query = """
            SELECT 
                MIN(updated_at) as oldest_update,
                MAX(updated_at) as latest_update,
                COUNT(*) as total_rates
            FROM latest_funding_rates
        """
        
        freshness_row = await database.fetch_one(freshness_query)
        
        oldest_data_age = None
        if freshness_row and freshness_row['oldest_update']:
            oldest_data_age = (datetime.utcnow() - freshness_row['oldest_update']).total_seconds()
        
        # Determine overall status
        if not db_healthy:
            status = "unhealthy"
        elif healthy_dexes == 0:
            status = "unhealthy"
        elif healthy_dexes < total_active_dexes:
            status = "degraded"
        else:
            status = "healthy"
        
        return {
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
            "database": {
                "connected": db_healthy
            },
            "dex_health": dex_health,
            "dex_summary": {
                "total": len(dex_rows),
                "active": total_active_dexes,
                "healthy": healthy_dexes
            },
            "data_freshness": {
                "oldest_data_age_seconds": int(oldest_data_age) if oldest_data_age else None,
                "latest_update": freshness_row['latest_update'].isoformat() if freshness_row and freshness_row['latest_update'] else None,
                "total_rates": freshness_row['total_rates'] if freshness_row else 0
            }
        }
        
    except Exception as e:
        logger.error(f"Error checking service health: {e}")
        return {
            "status": "unhealthy",
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(e)
        }


@router.get("/health/simple")
async def get_simple_health() -> Dict[str, str]:
    """
    Simple health check (fast, no database queries)
    
    Just checks if the service is running
    """
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/health/database")
async def get_database_health() -> Dict[str, Any]:
    """
    Check database connectivity and basic stats
    """
    try:
        # Test database connection
        result = await database.fetch_val("SELECT COUNT(*) FROM dexes")
        
        # Get table sizes
        stats_query = """
            SELECT 
                (SELECT COUNT(*) FROM symbols) as symbol_count,
                (SELECT COUNT(*) FROM dexes) as dex_count,
                (SELECT COUNT(*) FROM latest_funding_rates) as latest_rates_count,
                (SELECT COUNT(*) FROM funding_rates WHERE time >= NOW() - INTERVAL '24 hours') as rates_24h
        """
        
        stats = await database.fetch_one(stats_query)
        
        return {
            "status": "healthy",
            "connected": True,
            "statistics": {
                "symbols": stats['symbol_count'],
                "dexes": stats['dex_count'],
                "latest_rates": stats['latest_rates_count'],
                "rates_last_24h": stats['rates_24h']
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            "status": "unhealthy",
            "connected": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


@router.get("/health/dex/{dex}")
async def get_dex_health(dex: str) -> Dict[str, Any]:
    """
    Get health status for a specific DEX
    """
    try:
        query = """
            SELECT 
                d.name,
                d.is_active,
                d.last_successful_fetch,
                d.last_error,
                d.consecutive_errors,
                COUNT(DISTINCT lfr.symbol_id) as active_symbols,
                COUNT(DISTINCT CASE 
                    WHEN lfr.updated_at >= NOW() - INTERVAL '5 minutes' 
                    THEN lfr.symbol_id 
                END) as recent_updates
            FROM dexes d
            LEFT JOIN latest_funding_rates lfr ON lfr.dex_id = d.id
            WHERE LOWER(d.name) = LOWER(:dex_name)
            GROUP BY d.id, d.name, d.is_active, d.last_successful_fetch, 
                     d.last_error, d.consecutive_errors
        """
        
        row = await database.fetch_one(query, values={"dex_name": dex.lower()})
        
        if not row:
            raise HTTPException(status_code=404, detail=f"DEX '{dex}' not found")
        
        # Calculate time since last fetch
        time_since_fetch = None
        if row['last_successful_fetch']:
            time_since_fetch = (datetime.utcnow() - row['last_successful_fetch']).total_seconds()
        
        # Determine health status
        is_healthy = (
            row['is_active'] and 
            row['consecutive_errors'] == 0 and
            time_since_fetch is not None and
            time_since_fetch < 300  # Less than 5 minutes
        )
        
        return {
            "dex_name": row['name'],
            "status": "healthy" if is_healthy else "unhealthy",
            "is_active": row['is_active'],
            "last_successful_fetch": row['last_successful_fetch'].isoformat() if row['last_successful_fetch'] else None,
            "time_since_fetch_seconds": int(time_since_fetch) if time_since_fetch else None,
            "consecutive_errors": row['consecutive_errors'],
            "last_error": row['last_error'].isoformat() if row['last_error'] else None,
            "active_symbols": row['active_symbols'],
            "recent_updates": row['recent_updates'],
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking health for {dex}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

