## Grid Strategy TODOs

- [ ] Port deterministic client/server order-id mapping to **Aster** and ensure
      `_recover_from_canceled_entry()` can translate ids the way it does for Lighter.
- [ ] Add post-only close retry logic and fallback exit handling to **Backpack**
      (currently only Lighter has the built-in repost/market close workflow).
- [ ] Run live or simulated fills on Aster/Backpack to confirm websocket
      fill callbacks carry the client id and that the strategy realigns tracked
      positions correctly.
- [ ] Extend the unit/integration suite to cover multi-exchange flows once the
      above adapters are implemented.
