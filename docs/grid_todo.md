## Grid Strategy TODOs

- [ ] Port deterministic client/server order-id mapping to **Aster** and ensure
      `_recover_from_canceled_entry()` can translate ids the way it does for Lighter.
- [ ] Verify Aster/Backpack connectors surface an accurate position snapshot so
      the new `close_order_missing_retracked` guard can hand canceled closes back
      to the retry loop instead of dropping exposure.
- [ ] Run live or simulated fills on Aster/Backpack to confirm websocket
      fill callbacks carry the client id and that the strategy realigns tracked
      positions correctly.
- [ ] Extend the unit/integration suite to cover multi-exchange flows once the
      above adapters are implemented.
