"""
Background Tasks API Routes

Endpoints for monitoring and controlling background tasks.
Useful for VPS monitoring and manual task management.
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Dict, Any, List, Optional
from datetime import datetime

from utils.logger import logger


router = APIRouter()


@router.get("/tasks/status", response_model=Dict[str, Any])
async def get_tasks_status(request: Request) -> Dict[str, Any]:
    """
    Get status of background tasks
    
    Note: Background tasks run in a separate process (run_tasks.py).
    This endpoint provides information about the task system status
    by checking database records and collection logs.
    """
    try:
        from database.connection import database
        
        # Check recent collection activity
        collection_query = """
            SELECT 
                dex_id,
                started_at,
                completed_at,
                status,
                symbols_fetched,
                error_message
            FROM collection_logs 
            WHERE started_at >= NOW() - INTERVAL '1 hour'
            ORDER BY started_at DESC 
            LIMIT 10
        """
        
        recent_collections = await database.fetch_all(collection_query)
        
        # Check latest funding rates freshness
        freshness_query = """
            SELECT 
                COUNT(*) as total_rates,
                MAX(updated_at) as latest_update,
                MIN(updated_at) as oldest_update
            FROM latest_funding_rates
        """
        
        freshness_data = await database.fetch_one(freshness_query)
        
        # Determine if tasks are running based on recent activity
        recent_activity = len([log for log in recent_collections if log['started_at'] >= datetime.utcnow().replace(minute=datetime.utcnow().minute-5)])
        
        status = "running" if recent_activity > 0 else "inactive"
        
        return {
            "status": status,
            "message": "Tasks run in separate process (run_tasks.py)" if status == "inactive" else "Tasks are active",
            "recent_collections": [
                {
                    "started_at": log['started_at'].isoformat(),
                    "completed_at": log['completed_at'].isoformat() if log['completed_at'] else None,
                    "status": log['status'],
                    "symbols_fetched": log['symbols_fetched'],
                    "error_message": log['error_message']
                }
                for log in recent_collections
            ],
            "data_freshness": {
                "total_rates": freshness_data['total_rates'] if freshness_data else 0,
                "latest_update": freshness_data['latest_update'].isoformat() if freshness_data and freshness_data['latest_update'] else None,
                "oldest_update": freshness_data['oldest_update'].isoformat() if freshness_data and freshness_data['oldest_update'] else None
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting task status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/health", response_model=Dict[str, Any])
async def get_tasks_health(request: Request) -> Dict[str, Any]:
    """
    Get health summary of background tasks
    
    Note: Tasks run in separate process. Health is determined by
    recent collection activity and data freshness.
    """
    try:
        from database.connection import database
        
        # Check recent successful collections (last 10 minutes)
        recent_success_query = """
            SELECT COUNT(*) as successful_collections
            FROM collection_logs 
            WHERE started_at >= NOW() - INTERVAL '10 minutes'
            AND status = 'success'
        """
        
        recent_success = await database.fetch_one(recent_success_query)
        successful_collections = recent_success['successful_collections'] if recent_success else 0
        
        # Check data freshness (should be updated within last 5 minutes)
        freshness_query = """
            SELECT 
                COUNT(*) as total_rates,
                MAX(updated_at) as latest_update
            FROM latest_funding_rates
            WHERE updated_at >= NOW() - INTERVAL '5 minutes'
        """
        
        fresh_data = await database.fetch_one(freshness_query)
        fresh_rates = fresh_data['total_rates'] if fresh_data else 0
        
        # Determine health status
        is_healthy = successful_collections > 0 and fresh_rates > 0
        
        return {
            "status": "healthy" if is_healthy else "unhealthy",
            "message": "Background tasks running in separate process (run_tasks.py)",
            "indicators": {
                "recent_successful_collections": successful_collections,
                "fresh_data_points": fresh_rates,
                "data_is_fresh": fresh_rates > 0,
                "collections_active": successful_collections > 0
            },
            "recommendation": "Start run_tasks.py if status is unhealthy" if not is_healthy else "Tasks are running normally",
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting task health: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/info", response_model=Dict[str, Any])
async def get_tasks_info() -> Dict[str, Any]:
    """
    Get information about the background task system
    
    Returns:
        Information about how to run and monitor background tasks
    """
    return {
        "message": "Background tasks run in a separate process",
        "task_runner": {
            "script": "run_tasks.py",
            "location": "funding_rate_service/run_tasks.py",
            "description": "Standalone script for running background tasks"
        },
        "tasks": {
            "collection_task": {
                "description": "Collects funding rates from all DEXs",
                "frequency": "Every 60 seconds",
                "purpose": "Keep funding rate data fresh"
            },
            "opportunity_task": {
                "description": "Analyzes opportunities and caches results",
                "frequency": "Every 2 minutes",
                "purpose": "Provide fast opportunity API responses"
            },
            "cleanup_task": {
                "description": "Cleans up old database records",
                "frequency": "Daily at 2:00 AM UTC",
                "purpose": "Maintain database performance"
            }
        },
        "usage": {
            "start_all_tasks": "python run_tasks.py",
            "collection_only": "python run_tasks.py --collection-only",
            "no_cleanup": "python run_tasks.py --no-cleanup",
            "run_once": "python run_tasks.py --run-once",
            "background": "nohup python run_tasks.py > tasks.log 2>&1 &"
        },
        "monitoring": {
            "status_endpoint": "/api/v1/tasks/status",
            "health_endpoint": "/api/v1/tasks/health",
            "logs": "Check tasks.log file or console output"
        }
    }


@router.get("/tasks/metrics", response_model=Dict[str, Any])
async def get_task_metrics() -> Dict[str, Any]:
    """
    Get metrics for background tasks from database logs
    
    Returns:
    - Collection statistics from database logs
    - Data freshness metrics
    - Error information
    """
    try:
        from database.connection import database
        
        # Get collection metrics from logs
        metrics_query = """
            SELECT 
                COUNT(*) as total_collections,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_collections,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_collections,
                AVG(symbols_fetched) as avg_symbols_fetched,
                MAX(started_at) as last_collection,
                COUNT(CASE WHEN started_at >= NOW() - INTERVAL '1 hour' THEN 1 END) as collections_last_hour
            FROM collection_logs
            WHERE started_at >= NOW() - INTERVAL '24 hours'
        """
        
        metrics_data = await database.fetch_one(metrics_query)
        
        # Get data freshness
        freshness_query = """
            SELECT 
                COUNT(*) as total_rates,
                COUNT(CASE WHEN updated_at >= NOW() - INTERVAL '5 minutes' THEN 1 END) as fresh_rates,
                MAX(updated_at) as latest_update
            FROM latest_funding_rates
        """
        
        freshness_data = await database.fetch_one(freshness_query)
        
        # Calculate success rate
        total = metrics_data['total_collections'] if metrics_data else 0
        successful = metrics_data['successful_collections'] if metrics_data else 0
        success_rate = (successful / total * 100) if total > 0 else 0
        
        return {
            "collection_metrics": {
                "total_collections_24h": total,
                "successful_collections": successful,
                "failed_collections": metrics_data['failed_collections'] if metrics_data else 0,
                "success_rate_percent": round(success_rate, 2),
                "avg_symbols_per_collection": round(metrics_data['avg_symbols_fetched'], 1) if metrics_data and metrics_data['avg_symbols_fetched'] else 0,
                "collections_last_hour": metrics_data['collections_last_hour'] if metrics_data else 0,
                "last_collection": metrics_data['last_collection'].isoformat() if metrics_data and metrics_data['last_collection'] else None
            },
            "data_freshness": {
                "total_rates": freshness_data['total_rates'] if freshness_data else 0,
                "fresh_rates": freshness_data['fresh_rates'] if freshness_data else 0,
                "freshness_percent": round((freshness_data['fresh_rates'] / freshness_data['total_rates'] * 100) if freshness_data and freshness_data['total_rates'] > 0 else 0, 2),
                "latest_update": freshness_data['latest_update'].isoformat() if freshness_data and freshness_data['latest_update'] else None
            },
            "note": "Metrics based on database logs. Tasks run in separate process (run_tasks.py)",
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting task metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
