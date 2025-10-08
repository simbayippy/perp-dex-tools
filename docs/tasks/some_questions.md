is the current design of the perp-dex-tools suitable for a multi-dex + multi-strategy requirement?


**VERY IMPORTANTLY**, the goal should not just be a quick fix, but one that PRIORTISES long term developmer experience and modularity.

Key questions:
- how / where should i add to strategies for the rebalancing and funding-fee arbitrage needs?
  - I will definitely need a position tracker
  - I will need a position monitor
  - I will need also a rebalancing logic executor for current positions
  - I will also need a way to transfer between funds of dexes when one side of the position gets liquidated / when one side loses money but other side gains -> fund needs to flow from profitable to loser to maintain margin

- Basically, where will all of these files go? given the current design for /strategies is quite simple, and does not offer much extensibility or modularity.

- basically, how can i ensure for each strategy, it has room to "expand" for its own needs