"""
Profit-taking orchestration for funding arbitrage positions.

Coordinates profit opportunity evaluation and execution, serving as the
main interface between real-time monitoring and position closing.
"""

from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from ...strategy import FundingArbitrageStrategy
    from ...models import FundingArbPosition
    from exchange_clients.base_models import ExchangePositionSnapshot
    from exchange_clients.base_websocket import BBOData

from .profit_evaluator import ProfitEvaluator


class ProfitTaker:
    """
    Orchestrates profit-taking operations for funding arbitrage positions.

    Responsibilities:
    - Evaluate profit opportunities using ProfitEvaluator
    - Verify profitability before execution
    - Delegate actual position closing to PositionCloser
    - Coordinate with RealTimeProfitMonitor for WebSocket triggers

    This is the main interface that other modules interact with for
    profit-taking operations.
    """

    def __init__(self, strategy: "FundingArbitrageStrategy") -> None:
        """
        Initialize profit taker.

        Args:
            strategy: Parent funding arbitrage strategy instance
        """
        self._strategy = strategy
        self._logger = strategy.logger

        # Initialize profit evaluator
        self._profit_evaluator = ProfitEvaluator(strategy)

        self._logger.debug("ProfitTaker initialized")

    async def evaluate_and_execute(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
        bbo_prices: Optional[Dict[str, "BBOData"]] = None,
        trigger_source: str = "unknown"
    ) -> bool:
        """
        Evaluate profit opportunity and execute close if profitable.

        Args:
            position: Position to evaluate
            snapshots: Current position snapshots
            bbo_prices: Optional fresh BBO prices from WebSocket (exchange -> BBOData)
            trigger_source: Source of the trigger (e.g., "websocket", "polling")

        Returns:
            True if position was closed, False otherwise
        """
        try:
            # 1. Evaluate profit opportunity using fresh BBO prices if available
            should_close, reason = await self._profit_evaluator.check_immediate_profit_opportunity(
                position, snapshots, bbo_prices=bbo_prices
            )

            if not should_close:
                return False

            self._logger.info(
                f"💰 [{trigger_source.upper()}] Profit opportunity detected for {position.symbol}: {reason}"
            )

            # 2. Always verify profitability before execution (prevents false positives)
            is_verified, verification_reason = await self._verify_profit_opportunity(
                position, snapshots
            )

            if not is_verified:
                self._logger.warning(
                    f"[{trigger_source.upper()}] Profit opportunity disappeared for {position.symbol}: "
                    f"{verification_reason}"
                )
                return False

            # 3. Execute close with aggressive limit orders (optimal: maker fees, better fill)
            await self._strategy.position_closer.close(
                position,
                reason,
                live_snapshots=snapshots,
                order_type="aggressive_limit"
            )

            self._logger.info(
                f"✅ [{trigger_source.upper()}] Position closed for profit: {position.symbol}"
            )

            return True

        except Exception as exc:
            self._logger.error(
                f"[{trigger_source.upper()}] Error in profit-taking for {position.symbol}: {exc}",
                exc_info=True
            )
            return False

    async def _verify_profit_opportunity(
        self,
        position: "FundingArbPosition",
        snapshots: Dict[str, Optional["ExchangePositionSnapshot"]],
    ) -> tuple[bool, Optional[str]]:
        """
        Verify profit opportunity using fresh BBO prices before execution.

        This double-checks profitability to prevent executing closes when
        the profit opportunity has disappeared due to price movements.

        Args:
            position: Position to verify
            snapshots: Current position snapshots

        Returns:
            Tuple of (is_verified, reason)
        """
        try:
            # Delegate to position_closer's verification logic
            # (This method fetches fresh BBO and re-checks profitability)
            return await self._strategy.position_closer._verify_profit_opportunity_pre_execution(
                position, snapshots
            )

        except Exception as exc:
            self._logger.error(
                f"Error verifying profit opportunity for {position.symbol}: {exc}. "
                f"Aborting close to prevent unprofitable execution (fail-closed)."
            )
            # Fail-closed: if verification fails, don't proceed to prevent unprofitable closes
            return False, f"Verification failed: {str(exc)}"

    async def register_position(self, position: "FundingArbPosition") -> None:
        """
        Register position for profit-taking monitoring.

        Delegates to RealTimeProfitMonitor if available.

        Args:
            position: Position to register
        """
        monitor = getattr(self._strategy, 'profit_monitor', None)
        if monitor:
            await monitor.register_position(position)

    async def unregister_position(self, position: "FundingArbPosition") -> None:
        """
        Unregister position from profit-taking monitoring.

        Delegates to RealTimeProfitMonitor if available.

        Args:
            position: Position to unregister
        """
        monitor = getattr(self._strategy, 'profit_monitor', None)
        if monitor:
            await monitor.unregister_position(position)
