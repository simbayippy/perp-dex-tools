"""
Funding Arbitrage Strategy Controller

Implements control operations for funding arbitrage strategy.
"""

from typing import List, Dict, Any, Optional
from uuid import UUID
from decimal import Decimal
from datetime import datetime

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
            
            # Enrich position with live exchange data (similar to position_monitor)
            await self._enrich_position_with_live_data(position)
            
            account_name_val = row['account_name']
            if account_name_val in accounts_dict:
                accounts_dict[account_name_val]["positions"].append(
                    self._format_position_for_api(position)
                )
        
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
    
    async def _enrich_position_with_live_data(self, position: "FundingArbPosition"):
        """Enrich position with live exchange data (similar to position_monitor)."""
        try:
            # Fetch exchange snapshots
            exchange_clients = self.strategy.exchange_clients
            clients_lower = {name.lower(): client for name, client in exchange_clients.items()}
            
            legs_metadata = {}
            total_unrealized = Decimal("0")
            total_funding = Decimal("0")
            
            # Fetch funding rates
            funding_rate_repo = self.strategy.funding_rate_repo
            rate1_data = rate2_data = None
            if funding_rate_repo:
                rate1_data = await funding_rate_repo.get_latest_specific(
                    position.long_dex, position.symbol
                )
                rate2_data = await funding_rate_repo.get_latest_specific(
                    position.short_dex, position.symbol
                )
            
            if rate1_data and rate2_data:
                rate1 = Decimal(str(rate1_data["funding_rate"]))
                rate2 = Decimal(str(rate2_data["funding_rate"]))
                position.current_divergence = rate2 - rate1
                position.last_check = datetime.now()
            
            # Fetch exchange snapshots for each leg
            for dex in [position.long_dex, position.short_dex]:
                if not dex:
                    continue
                
                dex_key = dex.lower()
                client = clients_lower.get(dex_key)
                if not client:
                    continue
                
                try:
                    position_opened_at_ts = None
                    if position.opened_at:
                        position_opened_at_ts = position.opened_at.timestamp()
                    
                    snapshot = await client.get_position_snapshot(
                        position.symbol,
                        position_opened_at=position_opened_at_ts,
                    )
                    
                    if snapshot:
                        leg_meta = {
                            "side": snapshot.side or ("long" if dex == position.long_dex else "short"),
                            "quantity": float(snapshot.quantity.copy_abs()) if snapshot.quantity else 0.0,
                            "entry_price": float(snapshot.entry_price) if snapshot.entry_price else None,
                            "mark_price": float(snapshot.mark_price) if snapshot.mark_price else None,
                            "unrealized_pnl": float(snapshot.unrealized_pnl) if snapshot.unrealized_pnl else None,
                            "funding_accrued": float(snapshot.funding_accrued) if snapshot.funding_accrued else None,
                            "exposure_usd": float(snapshot.exposure_usd) if snapshot.exposure_usd else None,
                            "leverage": float(snapshot.leverage) if snapshot.leverage else None,
                            "liquidation_price": float(snapshot.liquidation_price) if snapshot.liquidation_price else None,
                        }
                        
                        # Calculate leverage if not available in snapshot (for Lighter, Paradex, etc.)
                        if leg_meta["leverage"] is None:
                            leg_meta["leverage"] = await self._calculate_leverage(
                                client, position.symbol, snapshot, dex_key
                            )
                        
                        if snapshot.unrealized_pnl:
                            total_unrealized += snapshot.unrealized_pnl
                        if snapshot.funding_accrued:
                            total_funding += snapshot.funding_accrued
                        
                        # Calculate funding APY
                        if rate1_data and dex_key == position.long_dex.lower():
                            leg_meta["funding_rate"] = float(rate1)
                            leg_meta["funding_apy"] = float(rate1 * Decimal("3") * Decimal("365") * Decimal("100"))
                        elif rate2_data and dex_key == position.short_dex.lower():
                            leg_meta["funding_rate"] = float(rate2)
                            leg_meta["funding_apy"] = float(rate2 * Decimal("3") * Decimal("365") * Decimal("100"))
                        
                        legs_metadata[dex] = leg_meta
                except Exception as e:
                    # Log but don't fail - missing exchange data shouldn't break the API
                    pass
            
            # Update position metadata
            if legs_metadata:
                position.metadata["legs"] = legs_metadata
                position.metadata["exchange_unrealized_pnl"] = float(total_unrealized)
                position.metadata["exchange_funding"] = float(total_funding)
            
            if rate1_data:
                rate_map = position.metadata.setdefault("rate_map", {})
                rate_map[position.long_dex] = Decimal(str(rate1_data["funding_rate"]))
            if rate2_data:
                rate_map = position.metadata.setdefault("rate_map", {})
                rate_map[position.short_dex] = Decimal(str(rate2_data["funding_rate"]))
                
        except Exception as e:
            # Don't fail API call if enrichment fails
            pass
    
    async def _calculate_leverage(
        self,
        client: Any,
        symbol: str,
        snapshot: Any,
        dex_name: str
    ) -> Optional[float]:
        """
        Calculate leverage for exchanges that don't provide it directly (Lighter, Paradex).
        
        Uses the same approach as leverage_validator: calls get_leverage_info() and extracts
        the appropriate leverage field based on exchange type.
        
        Args:
            client: Exchange client instance
            symbol: Trading symbol
            snapshot: ExchangePositionSnapshot
            dex_name: Exchange name (lowercase) to determine which leverage field to use
            
        Returns:
            Leverage as float, or None if cannot be calculated
        """
        try:
            # Method 1: Calculate from exposure and margin_reserved (most accurate for actual leverage)
            if snapshot.exposure_usd and snapshot.margin_reserved:
                if snapshot.margin_reserved > 0:
                    leverage = float(snapshot.exposure_usd / snapshot.margin_reserved)
                    return leverage
            
            # Method 2: Use exchange client's get_leverage_info method (same as leverage_validator)
            if hasattr(client, 'get_leverage_info'):
                leverage_info = await client.get_leverage_info(symbol)
                if leverage_info:
                    # For Lighter: use max_leverage (symbol-level, already correct at 5x)
                    # account_leverage is in wrong format (500/5E+2 instead of 5)
                    if dex_name == 'lighter':
                        max_leverage = leverage_info.get('max_leverage')
                        if max_leverage is not None:
                            return float(max_leverage)
                        # If max_leverage not available, fall through to account_leverage
                    
                    # For other exchanges: use max_leverage like leverage_validator does (line 174)
                    max_leverage = leverage_info.get('max_leverage')
                    if max_leverage is not None:
                        return float(max_leverage)
                    
                    # Fallback: use account_leverage if max_leverage not available
                    account_leverage = leverage_info.get('account_leverage')
                    if account_leverage is not None:
                        return float(account_leverage)
                    
                    # Method 3: Calculate from margin_requirement if available
                    margin_requirement = leverage_info.get('margin_requirement')
                    if margin_requirement and margin_requirement > 0:
                        leverage = float(Decimal("1") / Decimal(str(margin_requirement)))
                        return leverage
            
            return None
        except Exception:
            # Don't fail if leverage calculation fails
            return None
    
    def _format_position_for_api(self, position: "FundingArbPosition") -> Dict[str, Any]:
        """Format position data for API response with comprehensive metrics."""
        from decimal import Decimal
        
        # Calculate yield metrics
        entry_rate_apy = None
        current_rate_apy = None
        if position.entry_divergence:
            entry_rate_apy = float(position.entry_divergence * Decimal("3") * Decimal("365") * Decimal("100"))
        if position.current_divergence:
            current_rate_apy = float(position.current_divergence * Decimal("3") * Decimal("365") * Decimal("100"))
        
        # Calculate profit erosion
        erosion_ratio = float(position.get_profit_erosion())
        erosion_pct = (1.0 - erosion_ratio) * 100 if erosion_ratio <= 1.0 else 0.0
        
        # Get exchange data from metadata
        legs = position.metadata.get("legs", {})
        exchange_unrealized_pnl = position.metadata.get("exchange_unrealized_pnl", 0.0)
        exchange_funding = position.metadata.get("exchange_funding", 0.0)
        
        # Extract per-leg unrealized PnL for easy access
        long_unrealized_pnl = None
        short_unrealized_pnl = None
        long_funding_accrued = None
        short_funding_accrued = None
        
        for dex, leg_meta in legs.items():
            unrealized = leg_meta.get("unrealized_pnl")
            funding = leg_meta.get("funding_accrued")
            
            if dex.lower() == position.long_dex.lower():
                long_unrealized_pnl = unrealized
                long_funding_accrued = funding if funding is not None else 0.0
            elif dex.lower() == position.short_dex.lower():
                short_unrealized_pnl = unrealized
                short_funding_accrued = funding if funding is not None else 0.0
        
        # Build per-leg data
        leg_data = []
        for dex, leg_meta in legs.items():
            leg_data.append({
                "dex": dex.upper(),
                "side": leg_meta.get("side", "unknown"),
                "quantity": leg_meta.get("quantity", 0.0),
                "entry_price": leg_meta.get("entry_price"),
                "mark_price": leg_meta.get("mark_price"),
                "unrealized_pnl": leg_meta.get("unrealized_pnl"),
                "funding_accrued": leg_meta.get("funding_accrued") if leg_meta.get("funding_accrued") is not None else 0.0,
                "funding_apy": leg_meta.get("funding_apy"),
                "exposure_usd": leg_meta.get("exposure_usd"),
                "leverage": leg_meta.get("leverage"),
                "liquidation_price": leg_meta.get("liquidation_price"),
            })
        
        # Get risk config for min/max hold info
        min_hold_hours = None
        max_position_age_hours = None
        min_erosion_threshold = None
        if hasattr(self.strategy, 'config') and hasattr(self.strategy.config, 'risk_config'):
            risk_cfg = self.strategy.config.risk_config
            min_hold_hours = float(risk_cfg.min_hold_hours) if hasattr(risk_cfg, 'min_hold_hours') else None
            max_position_age_hours = float(risk_cfg.max_position_age_hours) if hasattr(risk_cfg, 'max_position_age_hours') else None
            min_erosion_threshold = float(risk_cfg.min_erosion_threshold) if hasattr(risk_cfg, 'min_erosion_threshold') else None
        
        return {
            "id": str(position.id),
            "symbol": position.symbol,
            "long_dex": position.long_dex,
            "short_dex": position.short_dex,
            "size_usd": float(position.size_usd),
            "age_hours": position.get_age_hours(),
            
            # Yield metrics
            "entry_divergence": float(position.entry_divergence) if position.entry_divergence else None,
            "entry_divergence_apy": entry_rate_apy,
            "current_divergence": float(position.current_divergence) if position.current_divergence else None,
            "current_divergence_apy": current_rate_apy,
            "profit_erosion_ratio": erosion_ratio,
            "profit_erosion_pct": erosion_pct,
            "min_erosion_threshold": min_erosion_threshold,
            
            # PnL metrics (summary)
            "net_pnl_usd": float(position.get_net_pnl()),
            "net_pnl_pct": float(position.get_net_pnl_pct()),
            "exchange_unrealized_pnl": exchange_unrealized_pnl,
            
            # Per-leg PnL (individual sides)
            "long_unrealized_pnl": long_unrealized_pnl,
            "short_unrealized_pnl": short_unrealized_pnl,
            
            # Funding metrics (summary)
            "cumulative_funding": float(position.cumulative_funding),
            "exchange_funding_accrued": exchange_funding,
            "total_fees_paid": float(position.total_fees_paid),
            
            # Per-leg funding (individual sides)
            "long_funding_accrued": long_funding_accrued,
            "short_funding_accrued": short_funding_accrued,
            
            # Per-leg data (detailed breakdown)
            "legs": leg_data,
            
            # Risk management config
            "min_hold_hours": min_hold_hours,
            "max_position_age_hours": max_position_age_hours,
            
            # Status
            "opened_at": position.opened_at.isoformat() if position.opened_at else None,
            "last_check": position.last_check.isoformat() if position.last_check else None,
            "status": position.status,
            "rebalance_pending": position.rebalance_pending,
            "rebalance_reason": position.rebalance_reason,
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
    
    async def reload_config(self) -> Dict[str, Any]:
        """
        Reload strategy configuration from the config file.
        
        Returns:
            Dict with reload operation result
        """
        try:
            success = await self.strategy.reload_config()
            if success:
                return {
                    "success": True,
                    "message": "Config reloaded successfully. Changes will take effect on the next cycle."
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to reload config",
                    "message": "Config reload failed. Check logs for details."
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "message": f"Error reloading config: {e}"
            }

