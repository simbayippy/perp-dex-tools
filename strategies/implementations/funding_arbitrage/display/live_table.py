"""
Real-time position monitoring with Rich live table display.

Uses WebSocket BBO streams to calculate and display live uPnL updates
without making REST API calls.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Set

from rich.console import Console
from rich.table import Table
from rich.text import Text

from exchange_clients.base_websocket import BBOData
from ..models import FundingArbPosition

if TYPE_CHECKING:
    from exchange_clients.base_client import BaseExchangeClient


class LiveTableDisplay:
    """
    Real-time position table that updates on WebSocket BBO price changes.

    Features:
    - Subscribes to BBO streams for each position's symbols
    - Calculates uPnL client-side: (mark_price - entry_price) * quantity
    - Updates Rich Live table at 4 FPS
    - Throttles per-position updates to 200ms
    """

    def __init__(
        self,
        exchange_clients: Dict[str, BaseExchangeClient],
        logger: Any,
        strategy_config: Any = None,
    ):
        """
        Initialize live table display.

        Args:
            exchange_clients: Dict of exchange name -> client instance
            logger: Logger instance
            strategy_config: Strategy configuration for display settings
        """
        self.exchange_clients = exchange_clients
        self.logger = logger
        self.strategy_config = strategy_config
        self.console = Console()

        # Active positions indexed by position ID
        self.positions: Dict[str, FundingArbPosition] = {}

        # Cached mark prices from WebSocket BBO
        # Format: {(exchange, symbol): {"mark_price": Decimal, "timestamp": float, "bid": Decimal, "ask": Decimal}}
        self.cached_prices: Dict[tuple, Dict[str, Any]] = {}

        # Last update timestamp per position (for throttling)
        self.last_update: Dict[str, float] = {}

        # BBO listener functions (to allow unregistration)
        # Format: {(exchange, symbol): listener_func}
        self.listeners: Dict[tuple, Callable] = {}

        # Set of exchanges that have successfully registered listeners
        self.registered_exchanges: Set[tuple] = set()

        # Throttle interval (seconds)
        self.update_interval = 0.2  # 200ms

        # Active flag
        self.is_active = False

    async def add_position(self, position: FundingArbPosition) -> None:
        """
        Add a position to the live table and register BBO listeners.

        Args:
            position: Position to display
        """
        if position.id in self.positions:
            self.logger.debug(f"[LIVE_TABLE] Position {position.symbol} already in table")
            return

        self.positions[position.id] = position
        self.logger.info(f"[LIVE_TABLE] Added position {position.symbol} (ID: {position.id})")

        # Register BBO listeners for both legs
        await self._register_listeners_for_position(position)

    async def remove_position(self, position: FundingArbPosition) -> None:
        """
        Remove a position from the live table and unregister BBO listeners.

        Args:
            position: Position to remove
        """
        if position.id not in self.positions:
            return

        # Unregister listeners first
        await self._unregister_listeners_for_position(position)

        # Remove from positions dict
        del self.positions[position.id]
        if position.id in self.last_update:
            del self.last_update[position.id]

        self.logger.info(f"[LIVE_TABLE] Removed position {position.symbol} (ID: {position.id})")

    async def _register_listeners_for_position(self, position: FundingArbPosition) -> None:
        """Register BBO listeners for a position's long and short legs."""
        symbol = position.symbol.upper()

        for dex, side in [(position.long_dex, "long"), (position.short_dex, "short")]:
            if not dex:
                continue

            dex_key = dex.lower()
            client = self.exchange_clients.get(dex_key)
            if not client:
                self.logger.warning(f"[LIVE_TABLE] No client for {dex}, skipping listener registration")
                continue

            ws_manager = getattr(client, 'ws_manager', None)
            if not ws_manager:
                self.logger.warning(f"[LIVE_TABLE] No WebSocket manager for {dex}")
                continue

            if not hasattr(ws_manager, 'register_bbo_listener'):
                self.logger.warning(f"[LIVE_TABLE] {dex} does not support BBO listeners")
                continue

            # Create listener for this exchange-symbol pair
            listener = self._create_listener(position, dex, side)
            key = (dex_key, symbol)

            # Store listener reference for later unregistration
            self.listeners[key] = listener

            try:
                ws_manager.register_bbo_listener(listener)
                self.registered_exchanges.add(key)
                self.logger.debug(f"[LIVE_TABLE] Registered BBO listener for {dex}/{symbol} ({side})")
            except Exception as e:
                self.logger.warning(f"[LIVE_TABLE] Failed to register listener for {dex}/{symbol}: {e}")

    async def _unregister_listeners_for_position(self, position: FundingArbPosition) -> None:
        """Unregister BBO listeners for a position's long and short legs."""
        symbol = position.symbol.upper()

        for dex in [position.long_dex, position.short_dex]:
            if not dex:
                continue

            dex_key = dex.lower()
            key = (dex_key, symbol)

            if key not in self.listeners:
                continue

            client = self.exchange_clients.get(dex_key)
            if not client:
                continue

            ws_manager = getattr(client, 'ws_manager', None)
            if not ws_manager or not hasattr(ws_manager, 'unregister_bbo_listener'):
                continue

            try:
                listener = self.listeners[key]
                ws_manager.unregister_bbo_listener(listener)
                self.registered_exchanges.discard(key)
                del self.listeners[key]
                self.logger.debug(f"[LIVE_TABLE] Unregistered BBO listener for {dex}/{symbol}")
            except Exception as e:
                self.logger.warning(f"[LIVE_TABLE] Failed to unregister listener for {dex}/{symbol}: {e}")

    def _create_listener(
        self,
        position: FundingArbPosition,
        dex: str,
        side: str
    ) -> Callable[[BBOData], Optional[asyncio.Future]]:
        """
        Create a BBO listener callback for a specific position leg.

        Args:
            position: Position to monitor
            dex: Exchange name
            side: "long" or "short"

        Returns:
            Async callback function for BBO updates
        """
        position_id = position.id
        symbol = position.symbol.upper()
        dex_lower = dex.lower()

        async def listener(bbo: BBOData) -> None:
            # Filter by symbol (handle different formats)
            if not self._symbol_matches(bbo.symbol, symbol):
                return

            # Check if position still exists
            if position_id not in self.positions:
                return

            # Throttle updates (200ms per position)
            now = time.time()
            last = self.last_update.get(position_id, 0)
            if now - last < self.update_interval:
                return

            self.last_update[position_id] = now

            # Calculate mark price from BBO midpoint
            try:
                mark_price = (Decimal(str(bbo.bid)) + Decimal(str(bbo.ask))) / Decimal("2")
            except Exception as e:
                self.logger.debug(f"[LIVE_TABLE] Failed to calculate mark price from BBO: {e}")
                return

            # Cache price data
            key = (dex_lower, symbol)
            self.cached_prices[key] = {
                "mark_price": mark_price,
                "timestamp": bbo.timestamp,
                "bid": Decimal(str(bbo.bid)),
                "ask": Decimal(str(bbo.ask)),
            }

        return listener

    def _symbol_matches(self, bbo_symbol: str, position_symbol: str) -> bool:
        """
        Check if BBO symbol matches position symbol.

        Handles different symbol formats across exchanges:
        - Aster: "BTCUSDT"
        - Lighter: "BTC" or numeric market_id
        - Paradex: "BTC-USD-PERP"
        - Backpack: "BTC_USDC_PERP"

        Args:
            bbo_symbol: Symbol from BBO update
            position_symbol: Symbol from position (normalized)

        Returns:
            True if symbols match
        """
        if not bbo_symbol or not position_symbol:
            return False

        bbo_norm = str(bbo_symbol).upper().replace("-", "").replace("_", "").replace("PERP", "")
        pos_norm = str(position_symbol).upper()

        # Direct match
        if bbo_norm == pos_norm:
            return True

        # Base symbol match (e.g., "BTC" in "BTCUSDT")
        if bbo_norm in pos_norm or pos_norm in bbo_norm:
            return True

        # Add USDT/USD suffix if missing
        for suffix in ["USDT", "USD", "USDC"]:
            if bbo_norm + suffix == pos_norm or pos_norm + suffix == bbo_norm:
                return True

        return False

    def generate_table(self) -> Table:
        """
        Generate Rich table with current position data.

        Returns:
            Rich Table object
        """
        table = Table(title="[bold cyan]Live Positions[/bold cyan]", show_header=True, header_style="bold magenta")

        # Add columns
        table.add_column("Symbol", style="cyan", no_wrap=True)
        table.add_column("Exchange", style="yellow")
        table.add_column("Side", style="blue")
        table.add_column("Qty", justify="right")
        table.add_column("Entry", justify="right")
        table.add_column("Mark", justify="right", style="yellow")
        table.add_column("uPnL", justify="right")
        table.add_column("Funding", justify="right")
        table.add_column("APY", justify="right")
        table.add_column("Age", justify="right")

        if not self.positions:
            table.add_row("", "", "", "", "", "", "[dim]No open positions[/dim]", "", "", "")
            return table

        # Add rows for each position
        for position in sorted(self.positions.values(), key=lambda p: p.symbol):
            self._add_position_rows(table, position)

        return table

    def _add_position_rows(self, table: Table, position: FundingArbPosition) -> None:
        """Add rows for a position's long and short legs."""
        legs_metadata = position.metadata.get("legs", {})
        symbol = position.symbol

        # Calculate age
        age_str = self._format_age(position)

        # Get funding rates
        rate_map = position.metadata.get("rate_map", {})

        for dex, side in [(position.long_dex, "long"), (position.short_dex, "short")]:
            if not dex:
                continue

            leg_meta = legs_metadata.get(dex, {})

            # Get cached or snapshot data
            mark_price = self._get_mark_price(dex, symbol, leg_meta)
            entry_price = leg_meta.get("entry_price")
            quantity = leg_meta.get("quantity", Decimal("0"))

            # Calculate uPnL
            upnl = self._calculate_upnl(side, entry_price, mark_price, quantity)
            upnl_str = self._format_upnl(upnl)

            # Format other fields
            qty_str = f"{quantity:.4f}" if quantity else "n/a"
            entry_str = f"{entry_price:.6f}" if entry_price else "n/a"
            mark_str = f"{mark_price:.6f}" if mark_price else "n/a"

            funding = leg_meta.get("funding_accrued")
            funding_str = f"{funding:.2f}" if funding is not None else "n/a"

            # Get funding APY
            funding_rate = rate_map.get(dex)
            if funding_rate is not None:
                try:
                    rate_decimal = Decimal(str(funding_rate))
                    apy = float(rate_decimal * Decimal("3") * Decimal("365") * Decimal("100"))
                    apy_str = f"{apy:.2f}%"
                except Exception as e:
                    self.logger.debug(f"[LIVE_TABLE] Error calculating APY: {e}")
                    apy_str = "n/a"
            else:
                apy_str = "n/a"

            # Add row
            table.add_row(
                symbol if dex == position.long_dex else "",  # Only show symbol on first row
                dex.upper(),
                side,
                qty_str,
                entry_str,
                mark_str,
                upnl_str,
                funding_str,
                apy_str,
                age_str if dex == position.long_dex else "",  # Only show age on first row
            )

    def _get_mark_price(self, dex: str, symbol: str, leg_meta: Dict) -> Optional[Decimal]:
        """Get mark price from cache or fallback to snapshot."""
        dex_key = dex.lower()
        symbol_key = symbol.upper()
        key = (dex_key, symbol_key)

        # Try cached WebSocket price first (fresh within 60s)
        cached = self.cached_prices.get(key)
        if cached:
            age = time.time() - cached.get("timestamp", 0)
            if age < 60:  # Fresh data
                return cached.get("mark_price")

        # Fallback to REST snapshot mark price
        mark_from_snapshot = leg_meta.get("mark_price")
        if mark_from_snapshot is not None:
            return Decimal(str(mark_from_snapshot))

        return None

    def _calculate_upnl(
        self,
        side: str,
        entry_price: Optional[Decimal],
        mark_price: Optional[Decimal],
        quantity: Decimal
    ) -> Optional[Decimal]:
        """Calculate unrealized P&L for a leg."""
        if entry_price is None or mark_price is None or quantity == 0:
            return None

        try:
            entry = Decimal(str(entry_price))
            mark = Decimal(str(mark_price))
            qty = Decimal(str(quantity))

            if side == "long":
                return (mark - entry) * qty
            else:  # short
                return (entry - mark) * qty
        except Exception as e:
            self.logger.debug(f"[LIVE_TABLE] Error calculating uPnL: {e}")
            return None

    def _format_upnl(self, upnl: Optional[Decimal]) -> Text:
        """Format uPnL with color coding."""
        if upnl is None:
            return Text("n/a", style="dim")

        upnl_float = float(upnl)
        if upnl_float > 0:
            return Text(f"+${upnl_float:.2f}", style="bold green")
        elif upnl_float < 0:
            return Text(f"-${abs(upnl_float):.2f}", style="bold red")
        else:
            return Text("$0.00", style="dim")

    def _format_age(self, position: FundingArbPosition) -> str:
        """Format position age as HH:MM:SS."""
        if not position.opened_at:
            return "n/a"

        # Ensure both datetimes are timezone-aware for comparison
        opened_at = position.opened_at
        if opened_at.tzinfo is None:
            # Make timezone-aware if naive
            opened_at = opened_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        age_seconds = (now - opened_at).total_seconds()
        hours = int(age_seconds // 3600)
        minutes = int((age_seconds % 3600) // 60)
        seconds = int(age_seconds % 60)

        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def has_positions(self) -> bool:
        """Check if there are any active positions."""
        return len(self.positions) > 0

    async def cleanup(self) -> None:
        """Cleanup all listeners and resources."""
        # Unregister all listeners
        for position in list(self.positions.values()):
            await self._unregister_listeners_for_position(position)

        self.positions.clear()
        self.cached_prices.clear()
        self.last_update.clear()
        self.listeners.clear()
        self.registered_exchanges.clear()
        self.is_active = False

        self.logger.info("[LIVE_TABLE] Cleanup complete")
