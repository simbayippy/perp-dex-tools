"""
Funding Arbitrage Strategy Controller

Implements control operations for funding arbitrage strategy.
"""

from typing import List, Dict, Any, Optional
from uuid import UUID
from decimal import Decimal

from strategies.control.strategy_controller import BaseStrategyController
from strategies.implementations.funding_arbitrage.strategy import FundingArbitrageStrategy


class FundingArbStrategyController(BaseStrategyController):
    """Controller for funding arbitrage strategy"""
    
    def __init__(self, strategy: FundingArbitrageStrategy):
        """
        Initialize funding arbitrage controller.
        
        Args:
            strategy: FundingArbitrageStrategy instance
        """
        self.strategy = strategy
    
    def get_strategy_name(self) -> str:
        """Get the strategy name."""
        return "funding_arbitrage"
    
    async def get_positions(
        self,
        account_ids: List[str],
        account_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get active positions for specified accounts.
        
        Args:
            account_ids: List of account IDs (UUIDs as strings) that user can access
            account_name: Optional account name filter
            
        Returns:
            Dict with positions grouped by account
        """
        from database.connection import database
        
        # Ensure database is connected
        if not database.is_connected:
            await database.connect()
        
        # Get account names for the account IDs
        if account_name:
            # Filter by specific account name
            account_rows = await database.fetch_all("""
                SELECT id::text as account_id, account_name
                FROM accounts
                WHERE id::text = ANY(:account_ids)
                  AND account_name = :account_name
                  AND is_active = TRUE
            """, {
                "account_ids": account_ids,
                "account_name": account_name
            })
        else:
            # Get all accessible accounts
            account_rows = await database.fetch_all("""
                SELECT id::text as account_id, account_name
                FROM accounts
                WHERE id::text = ANY(:account_ids)
                  AND is_active = TRUE
            """, {"account_ids": account_ids})
        
        if not account_rows:
            return {
                "accounts": []
            }
        
        # Get positions directly from database filtered by account_ids
        # This is more efficient than creating multiple position managers
        from strategies.implementations.funding_arbitrage.models import FundingArbPosition
        from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
        
        # Ensure mappers are loaded
        if not dex_mapper.is_loaded():
            from database.repositories.dex_repository import DEXRepository
            dex_repo = DEXRepository(database)
            dexes = await dex_repo.list_all()
            for dex in dexes:
                dex_mapper.add(dex['id'], dex['name'])
        
        if not symbol_mapper.is_loaded():
            from database.repositories.symbol_repository import SymbolRepository
            symbol_repo = SymbolRepository(database)
            symbols = await symbol_repo.list_all()
            for sym in symbols:
                symbol_mapper.add(sym['id'], sym['symbol'])
        
        # Query positions for all accessible accounts
        position_rows = await database.fetch_all("""
            SELECT 
                p.id,
                s.symbol,
                p.long_dex_id,
                p.short_dex_id,
                p.size_usd,
                p.entry_long_rate,
                p.entry_short_rate,
                p.entry_divergence,
                p.opened_at,
                p.current_divergence,
                p.last_check,
                p.status,
                p.rebalance_pending,
                p.rebalance_reason,
                p.exit_reason,
                p.closed_at,
                p.pnl_usd,
                p.cumulative_funding_usd,
                p.metadata,
                a.account_name,
                a.id::text as account_id
            FROM strategy_positions p
            JOIN symbols s ON p.symbol_id = s.id
            JOIN accounts a ON p.account_id = a.id
            WHERE p.status = 'open'
              AND p.account_id::text = ANY(:account_ids)
            ORDER BY a.account_name, p.opened_at DESC
        """, {"account_ids": account_ids})
        
        # Group positions by account
        accounts_dict = {row['account_name']: {
            "account_name": row['account_name'],
            "account_id": row['account_id'],
            "strategy": "funding_arbitrage",
            "positions": []
        } for row in account_rows}
        
        # Convert position rows to position objects and group by account
        for row in position_rows:
            long_dex_name = dex_mapper.get_name(row['long_dex_id'])
            short_dex_name = dex_mapper.get_name(row['short_dex_id'])
            
            if not long_dex_name or not short_dex_name:
                continue
            
            position = FundingArbPosition(
                id=row['id'],
                symbol=row['symbol'],
                long_dex=long_dex_name,
                short_dex=short_dex_name,
                size_usd=row['size_usd'],
                entry_long_rate=row['entry_long_rate'],
                entry_short_rate=row['entry_short_rate'],
                entry_divergence=row['entry_divergence'],
                opened_at=row['opened_at'],
                current_divergence=row['current_divergence'],
                last_check=row['last_check'],
                status=row['status'],
                rebalance_pending=row['rebalance_pending'],
                rebalance_reason=row['rebalance_reason'],
                exit_reason=row['exit_reason'],
                closed_at=row['closed_at'],
                pnl_usd=row['pnl_usd'],
                cumulative_funding=row['cumulative_funding_usd'] or Decimal("0"),
            )
            
            # Parse metadata if present
            if row['metadata']:
                import json
                try:
                    if isinstance(row['metadata'], str):
                        position.metadata = json.loads(row['metadata'])
                    else:
                        position.metadata = row['metadata']
                except Exception:
                    position.metadata = {}
            
            account_name_val = row['account_name']
            if account_name_val in accounts_dict:
                accounts_dict[account_name_val]["positions"].append({
                    "id": str(position.id),
                    "symbol": position.symbol,
                    "long_dex": position.long_dex,
                    "short_dex": position.short_dex,
                    "size_usd": float(position.size_usd),
                    "age_hours": position.get_age_hours(),
                    "net_pnl_usd": float(position.get_net_pnl()),
                    "net_pnl_pct": float(position.get_net_pnl_pct()),
                    "entry_divergence": float(position.entry_divergence) if position.entry_divergence else None,
                    "current_divergence": float(position.current_divergence) if position.current_divergence else None,
                    "opened_at": position.opened_at.isoformat() if position.opened_at else None,
                    "status": position.status,
                    "cumulative_funding": float(position.cumulative_funding),
                    "total_fees_paid": float(position.total_fees_paid),
                })
        
        # Convert to list (include all accounts, even if they have no positions)
        accounts_data = []
        for account_row in account_rows:
            account_name_val = account_row['account_name']
            # Get or create account entry in accounts_dict
            if account_name_val not in accounts_dict:
                accounts_dict[account_name_val] = {
                    "account_name": account_name_val,
                    "account_id": account_row['account_id'],
                    "strategy": "funding_arbitrage",
                    "positions": []
                }
            accounts_data.append(accounts_dict[account_name_val])
        
        return {
            "accounts": accounts_data
        }
    
    async def close_position(
        self,
        position_id: str,
        account_ids: List[str],
        order_type: str = "market",
        reason: str = "manual_close"
    ) -> Dict[str, Any]:
        """
        Close a position.
        
        Args:
            position_id: Position ID (UUID as string)
            account_ids: List of account IDs user can access (for validation)
            order_type: "market" or "limit"
            reason: Reason for closing
            
        Returns:
            Dict with close operation result
        """
        from database.connection import database
        
        # Validate position belongs to accessible account
        position_row = await database.fetch_one("""
            SELECT p.id, p.account_id::text, a.account_name
            FROM strategy_positions p
            JOIN accounts a ON p.account_id = a.id
            WHERE p.id = :position_id
              AND p.status = 'open'
        """, {"position_id": position_id})
        
        if not position_row:
            raise ValueError(f"Position {position_id} not found or already closed")
        
        if position_row['account_id'] not in account_ids:
            raise ValueError(f"Position {position_id} does not belong to accessible accounts")
        
        # Get position from strategy's position manager
        position = await self.strategy.position_manager.get(UUID(position_id))
        if not position:
            raise ValueError(f"Position {position_id} not found in strategy")
        
        # Close position using strategy's position closer
        # For market orders, we'll need to modify the closer to support manual market closes
        # For now, use the existing close method with a flag for market orders
        
        try:
            # Use the position closer's close method with explicit order_type
            await self.strategy.position_closer.close(
                position=position,
                reason=reason,
                live_snapshots=None,
                order_type=order_type
            )
            
            return {
                "success": True,
                "position_id": position_id,
                "account_name": position_row['account_name'],
                "order_type": order_type,
                "message": f"Position {position_id} closed successfully"
            }
        except Exception as e:
            return {
                "success": False,
                "position_id": position_id,
                "error": str(e),
                "message": f"Failed to close position: {e}"
            }

