## Funding-Arb Risk Management Action Plan (Future Work)

### Overview
The funding arbitrage strategy now employs `PositionCloser` to evaluate exit conditions and close positions. The evaluation pipeline is:

1. **PositionCloser.evaluateAndClosePositions()** loops through open positions.
2. For each position it fetches exchange snapshots (used for liquidation detection) and current funding rates.
3. It delegates the exit decision to the configured risk manager (`Combined`, `ProfitErosion`, `DivergenceFlip`, etc.).
4. If the risk manager returns `should_exit=True`, the closer currently calls `close()` immediately; otherwise it falls back to legacy heuristics (negative divergence, erosion threshold, max age).

### Risk Manager Interfaces

```python
class BaseRiskManager(ABC):
    def should_exit(
        self,
        position: FundingArbPosition,
        current_rates: Dict[str, Decimal]
    ) -> tuple[bool, str]:
        ...

    def generate_actions(
        self,
        position: FundingArbPosition,
        reason: str
    ) -> list[RebalanceAction]:
        ...
```

- `should_exit(...)` returns a boolean and a reason code, used by `PositionCloser`.
- `generate_actions(...)` is intended to produce a plan describing what to do after the exit trigger fires. Each concrete risk manager (e.g., `CombinedRiskManager`) can return different sequences.

### RebalanceAction Structure

Defined in `strategies/implementations/funding_arbitrage/models.py`:

```python
@dataclass
class RebalanceAction:
    action_type: str            # e.g., "close_position", "transfer_funds"
    position_id: UUID
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
```

Fields:
- `action_type`: Identifier for the operation to perform (`"close_position"`, `"reduce_size"`, `"transfer_funds"`, `"open_position"`, etc.).
- `position_id`: The logical position the action relates to.
- `reason`: The rationale (matches the reason from `should_exit`).
- `details`: Arbitrary metadata to guide execution (e.g., urgency flags, target dex, amount to transfer).

### Current Usage
- The risk managers already populate these actions. For example, `CombinedRiskManager.generate_actions()` returns a single `"close_position"` action with urgency and metrics in `details`.
- **PositionCloser** currently ignores these plans. After determining `should_exit`, it calls `close(position, reason)` directly, without consulting the generated action list.

### Future Directions / TODOs
1. **Action-Oriented Execution**
   - Introduce an action executor component that consumes `RebalanceAction`s and carries them out sequentially or in parallel.
   - Example flow for a funding arb rebalance:
     1. `close_position` (market close both legs).
     2. `transfer_funds` (move surplus USDC from winning exchange back to the losing side).
     3. `open_position` (optionally reopen with new parameters).
   - The risk manager would return all three actions; the orchestrator executes them, applying retries, logging, error handling.

2. **Action Registry / Extensibility**
   - Keep `RebalanceAction` generic so other strategies can define different action types (e.g., inventory rebalance, hedged reopen).
   - Provide mapping from `action_type` → coroutine implementation.

3. **Prioritization & Batching**
   - Use `details` to mark urgency (`"urgent": True`) or additional requirements (`{"transfer_amount": "5000", "to_dex": "lighter"}`).
   - Allow batching of actions to minimize fees (e.g., close all profitable positions, then transfer once).

4. **Persistence / Auditing**
   - Store pending actions alongside positions so restarts pick up where they left off.
   - Track outcomes (success/failure) for monitoring.

5. **Configurable Pipelines**
   - In `RiskManagementConfig`, let users specify post-exit behaviour (e.g., `auto_reopen`, `transfer_on_exit`, thresholds for partial reductions).
   - Risk managers incorporate these preferences when building action plans.

6. **Backward Compatibility**
   - Maintain the current simple behaviour (close immediately) as a fallback when no action executor is configured, or when the plan is an empty list.

### Example Action Plan

```python
[
    RebalanceAction(
        action_type="close_position",
        position_id=pos.id,
        reason="PROFIT_EROSION",
        details={"urgent": True, "expected_cost_usd": "3.5"}
    ),
    RebalanceAction(
        action_type="transfer_funds",
        position_id=pos.id,
        reason="PROFIT_EROSION",
        details={
            "from_dex": pos.short_dex,
            "to_dex": pos.long_dex,
            "amount_usd": "7500",
        }
    )
]
```

An executor would interpret this as:
1. Close the position urgently.
2. Move USD from the winning (short) side back to the losing (long) exchange to rebalance inventory.

### Summary
- Risk managers already produce structured action plans, but the current closer bypasses them in favour of immediate `close()` calls.
- This document outlines how we can evolve toward an action-driven architecture without breaking existing behaviour.
- Immediate TODO for future development: implement an action runner that consumes `RebalanceAction`s, making it easy to extend the strategy with richer rebalances, transfers, and reopen logic.

