"""
Rollback manager for atomic multi-order execution.

Handles emergency rollback of filled orders when atomic execution fails.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional

from helpers.unified_logger import get_core_logger

from ..contexts import OrderContext
from ..utils import context_to_filled_dict, coerce_decimal


class RollbackManager:
    """Manages rollback of filled orders during atomic execution failures."""

    def __init__(self, logger=None):
        self.logger = logger or get_core_logger("rollback_manager")

    async def rollback(
        self,
        filled_orders: List[Dict[str, Any]],
        stage_prefix: Optional[str] = None
    ) -> Decimal:
        """
        Rollback helper for atomic execution failures.

        CRITICAL: When rolling back a position CLOSE operation (detected via reduce_only flag or stage_prefix),
        we query actual open positions instead of trying to "undo" the close orders.
        This prevents creating new positions when the original positions were already closed.

        When rolling back a position OPEN operation, we undo the open orders (current behavior).

        Args:
            filled_orders: List of filled order dictionaries to rollback
            stage_prefix: Optional stage prefix for logging

        Returns:
            Total rollback cost in USD
        """
        # Detect close operation using reduce_only flag (more reliable) or stage_prefix (fallback)
        # Check reduce_only flag from filled orders if available
        has_reduce_only_flag = any(
            order.get("reduce_only", False) is True
            for order in filled_orders
        )
        is_close_operation = has_reduce_only_flag or (stage_prefix == "close")

        if is_close_operation:
            self.logger.warning(
                f"🚨 EMERGENCY ROLLBACK (CLOSE OPERATION): Querying actual positions "
                f"for {len(filled_orders)} exchanges"
            )
        else:
            self.logger.warning(
                f"🚨 EMERGENCY ROLLBACK (OPEN OPERATION): Closing {len(filled_orders)} filled orders"
            )

        total_rollback_cost = Decimal("0")

        self.logger.info("Step 1/4: Canceling all orders to prevent further fills...")
        cancel_tasks = []
        for order in filled_orders:
            if order.get("order_id"):
                try:
                    cancel_task = order["exchange_client"].cancel_order(order["order_id"])
                    cancel_tasks.append(cancel_task)
                except Exception as exc:
                    self.logger.error(
                        f"Failed to create cancel task for {order.get('order_id')}: {exc}"
                    )

        if cancel_tasks:
            cancel_results = await asyncio.gather(*cancel_tasks, return_exceptions=True)
            for i, result in enumerate(cancel_results):
                if isinstance(result, Exception):
                    self.logger.warning(f"Cancel failed for order {i}: {result}")
            await asyncio.sleep(0.5)

        if is_close_operation:
            # For close operations: Query actual open positions instead of using filled quantities
            self.logger.info("Step 2/4: Querying actual open positions from exchanges...")
            actual_fills = []
            for order in filled_orders:
                exchange_client = order["exchange_client"]
                symbol = order["symbol"]
                exchange_config = getattr(exchange_client, "config", None)
                contract_id = getattr(exchange_config, "contract_id", symbol)

                try:
                    # Query position snapshot to get both size and direction
                    position_snapshot = await exchange_client.get_position_snapshot(symbol)

                    if position_snapshot and hasattr(position_snapshot, 'quantity'):
                        position_qty = coerce_decimal(position_snapshot.quantity) or Decimal("0")
                        position_size = abs(position_qty)

                        if position_size <= Decimal("0.0001"):
                            self.logger.info(
                                f"✅ [{exchange_client.get_exchange_name()}] {symbol}: No open position "
                                f"(already closed or never opened)"
                            )
                            continue

                        # Positive quantity = long, negative = short
                        is_long = position_qty > Decimal("0")
                        close_side = "sell" if is_long else "buy"
                    else:
                        # Fallback: Query absolute position size if snapshot unavailable
                        try:
                            # Try with contract_id parameter first
                            position_size = await exchange_client.get_account_positions(contract_id)
                        except TypeError:
                            # Fallback to no-arg version if contract_id not supported
                            position_size = await exchange_client.get_account_positions()

                        if position_size is None:
                            position_size = Decimal("0")
                        else:
                            position_size = coerce_decimal(position_size) or Decimal("0")

                        if position_size <= Decimal("0.0001"):
                            self.logger.info(
                                f"✅ [{exchange_client.get_exchange_name()}] {symbol}: No open position "
                                f"(already closed or never opened)"
                            )
                            continue

                        # Without snapshot, we can't determine direction - assume long
                        self.logger.warning(
                            f"⚠️ [{exchange_client.get_exchange_name()}] Could not get position snapshot "
                            f"for {symbol}, assuming long position"
                        )
                        close_side = "sell"

                    actual_fills.append(
                        {
                            "exchange_client": exchange_client,
                            "symbol": symbol,
                            "side": close_side,  # Side to close (opposite of position)
                            "filled_quantity": position_size,  # Actual position size
                            "fill_price": Decimal("0"),  # Price not relevant for close rollback
                        }
                    )
                    self.logger.info(
                        f"📊 [{exchange_client.get_exchange_name()}] {symbol}: Found open position "
                        f"{position_size} tokens, will close via {close_side}"
                    )
                except Exception as exc:
                    self.logger.error(
                        f"❌ [{exchange_client.get_exchange_name()}] Failed to query position for "
                        f"{symbol}: {exc}"
                    )
                    # Fallback to original logic if position query fails
                    fallback_quantity = coerce_decimal(order.get("filled_quantity"))
                    if fallback_quantity and fallback_quantity > Decimal("0"):
                        self.logger.warning(
                            f"⚠️ Falling back to filled quantity {fallback_quantity} for {symbol}"
                        )
                        original_side = order.get("side")
                        # For close operations, reverse the side to undo the close
                        close_side = "sell" if original_side == "buy" else "buy"
                        actual_fills.append(
                            {
                                "exchange_client": exchange_client,
                                "symbol": symbol,
                                "side": close_side,
                                "filled_quantity": fallback_quantity,
                                "fill_price": coerce_decimal(order.get("fill_price")) or Decimal("0"),
                            }
                        )
        else:
            # For open operations: Use original logic (undo the open orders)
            self.logger.info("Step 2/4: Querying actual filled amounts...")
            actual_fills = []
            for order in filled_orders:
                exchange_client = order["exchange_client"]
                symbol = order["symbol"]
                side = order["side"]
                order_id = order.get("order_id")
                fallback_quantity = coerce_decimal(order.get("filled_quantity"))
                fallback_price = coerce_decimal(order.get("fill_price")) or Decimal("0")

                self.logger.debug(
                    f"Rollback order info: {symbol} ({side}), "
                    f"order_id={order_id}, "
                    f"payload_quantity={fallback_quantity}"
                )

                actual_quantity: Optional[Decimal] = None

                if order_id:
                    try:
                        order_info = await exchange_client.get_order_info(order_id)
                    except Exception as exc:
                        self.logger.error(f"Failed to get actual fill for {order_id}: {exc}")
                        order_info = None

                    if order_info is not None:
                        reported_qty = coerce_decimal(getattr(order_info, "filled_size", None))

                        if reported_qty is not None and reported_qty > Decimal("0"):
                            actual_quantity = reported_qty

                            if (
                                fallback_quantity is not None
                                and abs(reported_qty - fallback_quantity) > Decimal("0.0001")
                            ):
                                self.logger.warning(
                                    f"⚠️ Fill amount changed for {symbol}: "
                                    f"{fallback_quantity} → {reported_qty} "
                                    f"(Δ={reported_qty - fallback_quantity})"
                                )
                        else:
                            if fallback_quantity is not None and fallback_quantity > Decimal("0"):
                                self.logger.warning(
                                    f"⚠️ Exchange reported 0 filled size for {symbol} after cancel; "
                                    f"falling back to cached filled quantity {fallback_quantity}"
                                )
                                actual_quantity = fallback_quantity
                            else:
                                self.logger.warning(
                                    f"⚠️ No filled quantity reported for {symbol} ({order_id}); nothing to close"
                                )
                if actual_quantity is None:
                    if fallback_quantity is not None and fallback_quantity > Decimal("0"):
                        actual_quantity = fallback_quantity
                    else:
                        self.logger.warning(
                            f"⚠️ Skipping rollback close for {symbol}: unable to determine filled quantity"
                        )
                        continue

                actual_fills.append(
                    {
                        "exchange_client": exchange_client,
                        "symbol": symbol,
                        "side": side,
                        "filled_quantity": actual_quantity,
                        "fill_price": fallback_price,
                    }
                )

            # ⚠️ DEFENSE-IN-DEPTH: For OPEN operations, also query actual positions
            # This catches any positions that weren't tracked in contexts (e.g., partial fills from cancelled market orders)
            if not is_close_operation:
                self.logger.info("Step 2.5/4: Querying actual positions as safety check for OPEN operations...")
                position_check_fills = []

                # Get unique exchange-symbol pairs from filled_orders
                checked_pairs = set()
                for order in filled_orders:
                    exchange_client = order["exchange_client"]
                    symbol = order["symbol"]
                    exchange_name = exchange_client.get_exchange_name()
                    pair_key = (exchange_name, symbol)

                    if pair_key in checked_pairs:
                        continue
                    checked_pairs.add(pair_key)

                    exchange_config = getattr(exchange_client, "config", None)
                    contract_id = getattr(exchange_config, "contract_id", symbol)

                    try:
                        # Query actual position
                        position_snapshot = await exchange_client.get_position_snapshot(symbol)

                        if position_snapshot and hasattr(position_snapshot, 'quantity'):
                            position_qty = coerce_decimal(position_snapshot.quantity) or Decimal("0")
                            position_size = abs(position_qty)

                            if position_size > Decimal("0.0001"):
                                # Found an open position
                                is_long = position_qty > Decimal("0")
                                close_side = "sell" if is_long else "buy"

                                # Check if this position is already in actual_fills
                                already_tracked = False
                                for existing_fill in actual_fills:
                                    if (existing_fill["exchange_client"] == exchange_client and
                                        existing_fill["symbol"] == symbol):
                                        # Position already tracked - verify quantity matches
                                        tracked_qty = existing_fill["filled_quantity"]
                                        if abs(position_size - tracked_qty) > Decimal("0.0001"):
                                            self.logger.warning(
                                                f"⚠️ [{exchange_name}] Position size mismatch for {symbol}: "
                                                f"tracked={tracked_qty}, actual={position_size}. "
                                                f"Using actual position size."
                                            )
                                            existing_fill["filled_quantity"] = position_size
                                        already_tracked = True
                                        break

                                if not already_tracked:
                                    # Position not tracked - this is a safety catch!
                                    self.logger.warning(
                                        f"🚨 [{exchange_name}] SAFETY CATCH: Found untracked position for {symbol}: "
                                        f"{position_size} tokens ({'long' if is_long else 'short'}). "
                                        f"This position was not in rollback payload but exists on exchange!"
                                    )
                                    position_check_fills.append(
                                        {
                                            "exchange_client": exchange_client,
                                            "symbol": symbol,
                                            "side": close_side,  # Side to close (opposite of position)
                                            "filled_quantity": position_size,
                                            "fill_price": Decimal("0"),  # Price not available from position snapshot
                                        }
                                    )
                    except Exception as exc:
                        self.logger.debug(
                            f"⚠️ [{exchange_name}] Could not query position snapshot for {symbol} "
                            f"during safety check: {exc}"
                        )

                # Add any untracked positions to actual_fills
                if position_check_fills:
                    self.logger.warning(
                        f"⚠️ Found {len(position_check_fills)} untracked positions that will be closed during rollback"
                    )
                    actual_fills.extend(position_check_fills)

        self.logger.info(f"Step 3/4: Closing {len(actual_fills)} filled positions...")
        rollback_tasks = []
        for fill in actual_fills:
            try:
                close_side = "sell" if fill["side"] == "buy" else "buy"
                close_quantity = fill["filled_quantity"]
                exchange_client = fill["exchange_client"]
                exchange_config = getattr(exchange_client, "config", None)
                contract_id = getattr(exchange_config, "contract_id", fill["symbol"])

                self.logger.info(
                    f"Rollback: {close_side} {fill['symbol']} {close_quantity} @ market "
                    f"(contract_id={contract_id}, exchange={exchange_client.get_exchange_name()})"
                )

                # Log multiplier info if available
                try:
                    multiplier = exchange_client.get_quantity_multiplier(fill["symbol"])
                    if multiplier != 1:
                        actual_tokens = close_quantity * Decimal(str(multiplier))
                        self.logger.debug(
                            f"Rollback quantity multiplier: {close_quantity} units × {multiplier} = "
                            f"{actual_tokens} actual tokens"
                        )
                except Exception:
                    pass  # Ignore multiplier errors

                self.logger.debug(
                    f"Rollback: Using contract_id='{contract_id}' for symbol '{fill['symbol']}'"
                )

                # ⭐ CRITICAL: Always use reduce_only=True for rollback orders
                # This ensures we can ONLY close positions, never open new ones.
                # For OPEN operations: We're closing a position that was accidentally opened
                # For CLOSE operations: We're closing a position that was accidentally reopened
                close_task = exchange_client.place_market_order(
                    contract_id=contract_id,
                    quantity=float(close_quantity),
                    side=close_side,
                    reduce_only=True,  # Always True - rollback should only close, never open
                )
                rollback_tasks.append((close_task, fill))
            except Exception as exc:
                self.logger.error(f"Failed to create rollback order for {fill['symbol']}: {exc}")

        if rollback_tasks:
            rollback_results = await asyncio.gather(
                *(task for task, _ in rollback_tasks), return_exceptions=True
            )

            # Wait a moment for fills to complete and be reported via WebSocket
            await asyncio.sleep(1.0)

            for (task, fill), result in zip(rollback_tasks, rollback_results):
                if isinstance(result, Exception):
                    self.logger.warning(
                        f"Rollback market order failed for {fill['symbol']}: {result}"
                    )
                    continue

                # Query actual fill price from order info if available
                # The initial result.price may be None or a placeholder
                actual_exit_price = None
                order_id = getattr(result, "order_id", None)

                if order_id:
                    try:
                        # Try with force_refresh first (Paradex), fall back to no args if not supported
                        try:
                            order_info = await fill["exchange_client"].get_order_info(order_id, force_refresh=True)
                        except TypeError:
                            # Exchange doesn't support force_refresh parameter
                            order_info = await fill["exchange_client"].get_order_info(order_id)

                        if order_info and hasattr(order_info, "price") and order_info.price:
                            actual_exit_price = coerce_decimal(order_info.price)
                            self.logger.debug(
                                f"📊 [{fill['exchange_client'].get_exchange_name()}] "
                                f"Rollback order {order_id} actual fill price: ${actual_exit_price}"
                            )
                    except Exception as exc:
                        self.logger.debug(
                            f"Could not query order info for rollback cost calculation: {exc}"
                        )

                # Use actual fill price if available, otherwise fall back to result.price or entry price
                entry_price = fill["fill_price"] or Decimal("0")
                if actual_exit_price is not None:
                    exit_price = actual_exit_price
                elif getattr(result, "price", None):
                    exit_price = Decimal(str(result.price))
                else:
                    exit_price = entry_price
                    self.logger.warning(
                        f"⚠️ [{fill['exchange_client'].get_exchange_name()}] "
                        f"Could not determine rollback exit price for {fill['symbol']}, "
                        f"using entry price (cost may be inaccurate)"
                    )

                cost = abs(exit_price - entry_price) * fill["filled_quantity"]
                total_rollback_cost += cost
                self.logger.warning(
                    f"Rollback cost for {fill['symbol']}: ${cost:.2f} "
                    f"(entry: ${entry_price}, exit: ${exit_price})"
                )

            # Step 4: Verify positions are actually closed
            self.logger.info("Step 4/4: Verifying positions are closed...")
            for fill in actual_fills:
                exchange_client = fill["exchange_client"]
                symbol = fill["symbol"]
                exchange_name = exchange_client.get_exchange_name()

                try:
                    position_snapshot = await exchange_client.get_position_snapshot(symbol)
                    if position_snapshot and hasattr(position_snapshot, 'quantity'):
                        position_qty = coerce_decimal(position_snapshot.quantity) or Decimal("0")
                        position_size = abs(position_qty)

                        if position_size > Decimal("0.0001"):
                            self.logger.error(
                                f"❌ [{exchange_name}] Rollback FAILED: Position still open for {symbol}: "
                                f"{position_qty} tokens (expected: 0)"
                            )
                            # Attempt emergency close with reduce_only
                            try:
                                close_side = "sell" if position_qty > 0 else "buy"
                                exchange_config = getattr(exchange_client, "config", None)
                                contract_id = getattr(exchange_config, "contract_id", symbol)

                                self.logger.warning(
                                    f"🔄 [{exchange_name}] Attempting emergency close of residual position: "
                                    f"{close_side} {position_size} @ market (reduce_only=True)"
                                )

                                emergency_close = await exchange_client.place_market_order(
                                    contract_id=contract_id,
                                    quantity=float(position_size),
                                    side=close_side,
                                    reduce_only=True,
                                )

                                if isinstance(emergency_close, Exception) or not getattr(emergency_close, "success", False):
                                    self.logger.error(
                                        f"❌ [{exchange_name}] Emergency close failed: {emergency_close}"
                                    )
                                else:
                                    self.logger.info(
                                        f"✅ [{exchange_name}] Emergency close order placed: {emergency_close.order_id}"
                                    )
                            except Exception as exc:
                                self.logger.error(
                                    f"❌ [{exchange_name}] Failed to place emergency close order: {exc}"
                                )
                        else:
                            self.logger.info(
                                f"✅ [{exchange_name}] {symbol}: Position verified closed"
                            )
                    else:
                        self.logger.debug(
                            f"✅ [{exchange_name}] {symbol}: No position snapshot (likely closed)"
                        )
                except Exception as exc:
                    self.logger.warning(
                        f"⚠️ [{exchange_name}] Could not verify position closure for {symbol}: {exc}"
                    )

        self.logger.warning(
            f"✅ Rollback complete. Total cost: ${total_rollback_cost:.2f}"
        )
        return total_rollback_cost

    async def perform_emergency_rollback(
        self,
        contexts: List[OrderContext],
        reason: str,
        imbalance_tokens: Decimal,
        imbalance_pct: Decimal,
        stage_prefix: Optional[str] = None,
    ) -> Decimal:
        """
        Perform emergency rollback of all filled orders.

        Args:
            contexts: List of order contexts to rollback
            reason: Reason for rollback (for logging)
            imbalance_tokens: Quantity imbalance amount (in actual tokens, normalized)
            imbalance_pct: Percentage imbalance

        Returns:
            Rollback cost in USD
        """
        # Log context state for debugging
        for c in contexts:
            if c.filled_quantity > Decimal("0"):
                result_qty = Decimal("0")
                if c.result:
                    result_qty = coerce_decimal(c.result.get("filled_quantity")) or Decimal("0")
                self.logger.debug(
                    f"Rollback ({reason}) context for {c.spec.symbol} ({c.spec.side}): "
                    f"accumulated={c.filled_quantity}, "
                    f"result_dict={result_qty}, "
                    f"match={'✓' if abs(c.filled_quantity - result_qty) < Decimal('0.0001') else '✗ MISMATCH'}"
                )

        # Safety check: Only rollback contexts with actual fills
        # Double-check that filled_quantity matches what the exchange reports
        rollback_payload = []
        for c in contexts:
            if c.filled_quantity > Decimal("0") and c.result:
                # Additional safety: verify filled_quantity is reasonable
                spec_qty = getattr(c.spec, "quantity", None)
                if spec_qty is not None:
                    spec_qty_dec = Decimal(str(spec_qty))
                    # If filled_quantity exceeds spec.quantity significantly, something is wrong
                    if c.filled_quantity > spec_qty_dec * Decimal("1.1"):
                        self.logger.error(
                            f"⚠️ ROLLBACK SKIP: {c.spec.symbol} ({c.spec.side}) has suspicious filled_quantity: "
                            f"{c.filled_quantity} exceeds spec.quantity={spec_qty_dec} by >10%. "
                            f"This likely indicates a bug. Skipping rollback for this context."
                        )
                        continue

                rollback_payload.append(context_to_filled_dict(c))

        rollback_cost = await self.rollback(
            rollback_payload, stage_prefix=stage_prefix
        )
        self.logger.warning(
            f"🛡️ Emergency rollback completed; cost=${rollback_cost:.4f}. "
            f"Prevented {imbalance_tokens:.6f} tokens ({imbalance_pct*100:.2f}%) quantity imbalance."
        )

        # Clear filled quantities to prevent position creation
        for ctx in contexts:
            ctx.filled_quantity = Decimal("0")
            ctx.filled_usd = Decimal("0")

        return rollback_cost

