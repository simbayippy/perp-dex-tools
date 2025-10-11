# Goal
The goal of this is to fix the 4 mentioned bugs below.

## Issue 1
- mon 5x on aster, 3x on Lighter -> trade still executed. theres no fking leverage normalization / leverage_to_use = get_min(exchange1, exchange2)

Check why this is so, we already have a precheck for max leverage, and managed to fetch the symbols max leverage, but we need to SET the leverage to use

## Issue 2
- Finish execution, but get this FundingARrbFeeCalcualtor error
2025-10-11 18:03:17 | INFO     | patterns.atomic_multi_order:execute_at...:202 | ✅ Atomic execution successful: 2/2 filled

2025-10-11 18:03:17 | ERROR    | funding_arbitrage.strategy:_open_position:870 | ❌ MON: Unexpected error 'FundingArbFeeCalculator' object has no attribute 'calculate_total_cost'

## Issue 3
the limits arent remotely close enough, perhaps as we are using the cached bbo from initial fetch, not the most updated one? do we need to instead implement a websocket here for getting bbo prices between the 2 target exchanges

This thus led to a timeout for limit order -> attempts to execute market order

As such the trade prices which we eventually got executed at (both market orders) is less than ideal:
- Aster: Long 0.0884300, 113 MON
- Lighter: Short $0.08793, 113 MON


## Issue 4
After executing an order succesfuly where both sides fill, its still trying to execute and search for more opportunites. After executing Mon -> finds / uses the other opportunity for PROVE, tries to open another delta neutral

we should only manage 1 per session, keeping it simple first. perhaps the config builder?