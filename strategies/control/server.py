"""
Strategy Control API Server

FastAPI server for controlling running strategies via REST API.
"""

from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException, status, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from databases import Database
import os

from strategies.control.auth import APIKeyAuth
from strategies.control.funding_arb_controller import FundingArbStrategyController

# Import database - will be initialized when bot starts
try:
    from database.connection import database
except ImportError:
    database = None


# Request/Response Models
class ClosePositionRequest(BaseModel):
    order_type: str = Field(default="market", description="Order type: 'market' or 'limit'")
    reason: str = Field(default="manual_close", description="Reason for closing")
    confirm_wide_spread: bool = Field(default=False, description="Confirm to proceed despite wide spread warning")


class ErrorResponse(BaseModel):
    detail: str


# Initialize FastAPI app
app = FastAPI(
    title="Strategy Control API",
    description="REST API for controlling running trading strategies",
    version="1.0.0"
)

# Initialize auth (will be set when database is available)
_auth: Optional[APIKeyAuth] = None

# Strategy controller registry (will be populated when bot starts)
_strategy_controller: Optional[FundingArbStrategyController] = None


def set_strategy_controller(controller: FundingArbStrategyController):
    """Set the strategy controller (called by TradingBot)."""
    global _strategy_controller
    _strategy_controller = controller


def get_strategy_controller() -> Optional[FundingArbStrategyController]:
    """Get the strategy controller (may be None in read-only mode)."""
    return _strategy_controller


def require_strategy_controller() -> FundingArbStrategyController:
    """Get the strategy controller, raising error if not available."""
    if _strategy_controller is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy controller not available. This operation requires a running strategy."
        )
    return _strategy_controller


def get_auth() -> APIKeyAuth:
    """Get auth instance (initialized when database is available)."""
    global _auth
    if _auth is None:
        if database is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database not available"
            )
        _auth = APIKeyAuth(database)
    return _auth


async def get_user_info(request: Request) -> Dict[str, Any]:
    """Dependency function to get user info from API key."""
    auth = get_auth()
    return await auth(request)


@app.get("/api/v1/status", response_model=Dict[str, Any])
async def get_status(user_info: Dict[str, Any] = Depends(get_user_info)):
    """
    Get strategy status and account information.
    
    Returns:
        Strategy status, user info, and accessible accounts
    """
    # Create controller if not available (read-only mode)
    controller = get_strategy_controller()
    if controller is None:
        from strategies.control.funding_arb_controller import FundingArbStrategyController
        controller = FundingArbStrategyController(strategy=None)
    
    auth = get_auth()
    
    account_ids = await auth.get_accessible_account_ids(
        user_info["user_id"],
        user_info["is_admin"]
    )
    
    # Get account names
    accounts = await database.fetch_all("""
        SELECT id::text, account_name, is_active, created_at
        FROM accounts
        WHERE id::text = ANY(:account_ids)
        ORDER BY account_name
    """, {"account_ids": account_ids})
    
    return {
        "user": user_info["username"],
        "is_admin": user_info["is_admin"],
        "strategy": controller.get_strategy_name(),
        "status": "running",
        "accessible_accounts": [
            {
                "account_name": acc["account_name"],
                "account_id": acc["id"],
                "is_active": acc["is_active"],
                "created_at": acc["created_at"].isoformat() if acc["created_at"] else None
            }
            for acc in accounts
        ]
    }


@app.get("/api/v1/accounts", response_model=Dict[str, Any])
async def get_accounts(user_info: Dict[str, Any] = Depends(get_user_info)):
    """
    Get list of accounts accessible to the authenticated user.
    
    Returns:
        List of accounts with metadata
    """
    auth = get_auth()
    account_ids = await auth.get_accessible_account_ids(
        user_info["user_id"],
        user_info["is_admin"]
    )
    
    accounts = await database.fetch_all("""
        SELECT id::text, account_name, is_active, created_at, user_id::text
        FROM accounts
        WHERE id::text = ANY(:account_ids)
        ORDER BY account_name
    """, {"account_ids": account_ids})
    
    return {
        "user": user_info["username"],
        "is_admin": user_info["is_admin"],
        "accounts": [
            {
                "account_name": acc["account_name"],
                "account_id": acc["id"],
                "user_id": acc["user_id"],
                "is_active": acc["is_active"],
                "created_at": acc["created_at"].isoformat() if acc["created_at"] else None
            }
            for acc in accounts
        ]
    }


@app.get("/api/v1/positions", response_model=Dict[str, Any])
async def get_positions(
    account_name: Optional[str] = Query(None, description="Filter by account name"),
    user_info: Dict[str, Any] = Depends(get_user_info)
):
    """
    Get active positions for accessible accounts.
    
    Args:
        account_name: Optional account name filter
        
    Returns:
        Positions grouped by account
    """
    # Create controller if not available (read-only mode)
    controller = get_strategy_controller()
    if controller is None:
        from strategies.control.funding_arb_controller import FundingArbStrategyController
        controller = FundingArbStrategyController(strategy=None)
    
    auth = get_auth()
    
    # Validate account access if account_name provided
    account_id = None
    if account_name:
        account_id = await auth.validate_account_access(
            user_info["user_id"],
            user_info["is_admin"],
            account_name
        )
    
    account_ids = await auth.get_accessible_account_ids(
        user_info["user_id"],
        user_info["is_admin"]
    )
    
    result = await controller.get_positions(
        account_ids=account_ids,
        account_name=account_name
    )
    
    # Add user info to response
    result["user"] = user_info["username"]
    result["is_admin"] = user_info["is_admin"]
    
    return result


@app.get("/api/v1/balances", response_model=Dict[str, Any])
async def get_balances(
    account_name: Optional[str] = Query(None, description="Filter by account name"),
    user_info: Dict[str, Any] = Depends(get_user_info)
):
    """
    Get available margin balances for accessible accounts across all exchanges.
    
    Args:
        account_name: Optional account name filter
        
    Returns:
        Balances grouped by account and exchange
    """
    # Create controller if not available (read-only mode)
    controller = get_strategy_controller()
    if controller is None:
        from strategies.control.funding_arb_controller import FundingArbStrategyController
        controller = FundingArbStrategyController(strategy=None)
    
    auth = get_auth()
    
    # Validate account access if account_name provided
    account_id = None
    if account_name:
        account_id = await auth.validate_account_access(
            user_info["user_id"],
            user_info["is_admin"],
            account_name
        )
    
    account_ids = await auth.get_accessible_account_ids(
        user_info["user_id"],
        user_info["is_admin"]
    )
    
    result = await controller.get_balances(
        account_ids=account_ids,
        account_name=account_name
    )
    
    # Add user info to response
    result["user"] = user_info["username"]
    result["is_admin"] = user_info["is_admin"]
    
    return result


@app.post("/api/v1/positions/{position_id}/close", response_model=Dict[str, Any])
async def close_position(
    position_id: str,
    request: ClosePositionRequest,
    user_info: Dict[str, Any] = Depends(get_user_info)
):
    """
    Close a position.
    
    Args:
        position_id: Position ID (UUID)
        request: Close request with order_type and reason
        
    Returns:
        Close operation result
    """
    controller = require_strategy_controller()  # Requires running strategy
    auth = get_auth()
    
    # Validate order_type
    if request.order_type not in ("market", "limit"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid order_type: {request.order_type}. Must be 'market' or 'limit'"
        )
    
    account_ids = await auth.get_accessible_account_ids(
        user_info["user_id"],
        user_info["is_admin"]
    )
    
    try:
        result = await controller.close_position(
            position_id=position_id,
            account_ids=account_ids,
            order_type=request.order_type,
            reason=request.reason,
            confirm_wide_spread=request.confirm_wide_spread
        )
        
        # If wide spread warning, return the warning response (don't raise error)
        if result.get("wide_spread_warning"):
            return result
        
        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("error", "Failed to close position")
            )
        
        return result
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error closing position: {str(e)}"
        )


@app.post("/api/v1/config/reload", response_model=Dict[str, Any])
async def reload_config(user_info: Dict[str, Any] = Depends(get_user_info)):
    """
    Reload strategy configuration from the config file without restarting.
    
    Changes will take effect on the next execution cycle.
    
    Returns:
        Reload operation result
    """
    controller = require_strategy_controller()  # Requires running strategy
    
    try:
        result = await controller.reload_config()
        
        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("error", "Failed to reload config")
            )
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error reloading config: {str(e)}"
        )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"Internal server error: {str(exc)}"}
    )

