currently, facing a bug whereby when critical imbalance deteced, we are not setting the reduce_only parameter to skip the min notional
logs as follows

2025-10-25 12:17:02 | WARNING  | atomic_multi_order.retry_manager:execu...:116 | ⚠️ Critical imbalance detected during retry attempt 
1: longs=$3.48, shorts=$21.73, imbalance=$18.25. Aborting retries for safety.
2025-10-25 12:17:02 | WARNING  | atomic_multi_order.executor:execute_at...:322 | ⚠️ Retry attempts exhausted; residual imbalance rema
ins.
2025-10-25 12:17:02 | ERROR    | operations.position_closer:close:172          | Error closing position 00ff21de-4a35-4599-ac1b-ab2d
62b6d96d: Atomic close failed for TOSHI: Market order failed: [ASTER] UNEXPECTED: Market order notional $3.4799100 below minimum $5.
 This should have been caught in pre-flight checks!
2025-10-25 12:17:02 | ERROR    | funding_arbitrage.strategy:_monitor_po...:380 | Monitor loop error: Atomic close failed for TOSHI: 
Market order failed: [ASTER] UNEXPECTED: Market order notional $3.4799100 below minimum $5. This should have been caught in pre-flig
ht checks!
Traceback (most recent call last):
  File "/root/perp-dex-tools/strategies/implementations/funding_arbitrage/strategy.py", line 378, in _monitor_positions_loop
    await self.position_closer.evaluateAndClosePositions()
  File "/root/perp-dex-tools/strategies/implementations/funding_arbitrage/operations/position_closer.py", line 52, in evaluateAndClo
sePositions
    await self.close(position, imbalance_reason, live_snapshots=snapshots)
  File "/root/perp-dex-tools/strategies/implementations/funding_arbitrage/operations/position_closer.py", line 148, in close
    await self._close_exchange_positions(
  File "/root/perp-dex-tools/strategies/implementations/funding_arbitrage/operations/position_closer.py", line 645, in _close_exchan
ge_positions
    await self._close_legs_atomically(position, legs, reason=reason)
  File "/root/perp-dex-tools/strategies/implementations/funding_arbitrage/operations/position_closer.py", line 694, in _close_legs_a
tomically
    raise RuntimeError(
RuntimeError: Atomic close failed for TOSHI: Market order failed: [ASTER] UNEXPECTED: Market order notional $3.4799100 below minimum $5. This should have been caught in pre-flight checks!