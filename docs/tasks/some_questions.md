is the current design of the perp-dex-tools suitable for a multi-dex + multi-strategy requirement?

so yes, the design is ok, with a factory strategy with individual strategies + ./exchanges with the base.py for the base function executions


but what about for a strategy such as the funding_arbitrage, which might require a lot more addition of files (see ./docs/tasks/funding_arb_client_server_design.md), for example not just differing strategies for funding_arb, but also postion management, introduction of db to track positions etc (instead of in memory)

how will the file structure look like?

is this current design good enough? is it extensible, and modular, whereby developer experience will not be hindered?

a very important consideration of mine is system design
- should i instead split the codebase into individual strategies + its own market executor per dex
- or stick to the current design, but add some improvements?

**VERY IMPORTANTLY**, the goal should not just be a quick fix, but one that PRIORTISES long term developmer experience and modularity.