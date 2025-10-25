## ERROR 1 - cant set leverage for FLOKI on Aster

2025-10-25 00:06:52 | INFO     | EXCHANGE:ASTER:ticker=ALL           | [ASTER] Setting leverage for FLOKI to 5x...
2025-10-25 00:06:52 | DEBUG    | EXCHANGE:ASTER:ticker=ALL           | POST /fapi/v1/leverage - Params: {'timestamp': 1761350812227, 'recvWindow': 5000}, Data: {'symbol': 'FLOKIUSDT', 'leverage': 5}
2025-10-25 00:06:52 | DEBUG    | EXCHANGE:ASTER:ticker=ALL           | Response 400: N/A
2025-10-25 00:06:52 | ERROR    | EXCHANGE:ASTER:ticker=ALL           | [ASTER] Error setting leverage for FLOKI to 5x: API request failed: {'code': -1121, 'msg': 'Invalid symbol.'}
2025-10-25 00:06:52 | ERROR    | CORE:LEVERAGE_VALIDATOR             | ❌ [ASTER] Failed to set leverage to 5x
2025-10-25 00:06:52 | INFO     | CORE:LEVERAGE_VALIDATOR             | ✅ [LEVERAGE] All exchanges normalized to 5x for FLOKI

## ERROR 2 - wrong symbol for Lighter flokiusdt
2025-10-25 00:06:50 | WARNING  | EXCHANGE:LIGHTER:ticker=ALL         | ❌ [LIGHTER] Symbol 'FLOKI' NOT found in Lighter markets. Available symbols: PAXG, TON, 1000PEPE, MON, PENGU, SPX, XRP, APT, WIF, LINEA...

**but on lighter, its kFLOKI**

## ERROR 3 - seem to have a mixup in markets? STBL & MET in 1 tx for Aster

2025-10-25 00:29:06 | INFO     | CORE:ATOMIC_MULTI_ORDER             | =======================================================
2025-10-25 00:29:06 | INFO     | CORE:ATOMIC_MULTI_ORDER             | 3.2. 🚀 Order Placement
2025-10-25 00:29:06 | INFO     | CORE:ATOMIC_MULTI_ORDER             | =======================================================
2025-10-25 00:29:06 | INFO     | CORE:ATOMIC_MULTI_ORDER             | 🚀 Placing all orders simultaneously...
2025-10-25 00:29:06 | INFO     | CORE:ORDER_EXECUTOR                 | 🟢 [LIGHTER] Executing buy STBL ($22.166742 qty=22.1) in mode limit_only
2025-10-25 00:29:06 | INFO     | EXCHANGE:LIGHTER:ticker=ALL         | 📡 [LIGHTER] Using real-time BBO from WebSocket
2025-10-25 00:29:06 | INFO     | CORE:PRICE_PROVIDER                 | ✅ [LIGHTER] BBO: bid=1.00189, ask=1.00375
2025-10-25 00:29:06 | INFO     | CORE:ORDER_EXECUTOR                 | [LIGHTER] Placing limit buy STBL (contract_id=41): 22.1 @ $1.003549250 (mid: $1.00282, offset: 0.0200%)
2025-10-25 00:29:06 | INFO     | EXCHANGE:LIGHTER:ticker=ALL         | 📤 [LIGHTER] Submitting order: market=41 client_id=146403 side=BID price=100354 amount=221
2025-10-25 00:29:06 | INFO     | CORE:ORDER_EXECUTOR                 | 🔴 [ASTER] Executing sell STBL ($2.44868 qty=22.1) in mode limit_only
2025-10-25 00:29:06 | INFO     | EXCHANGE:ASTER:ticker=ALL           | 📡 [ASTER] Using real-time BBO from WebSocket
2025-10-25 00:29:06 | INFO     | CORE:PRICE_PROVIDER                 | ✅ [ASTER] BBO: bid=0.11076, ask=0.1109
2025-10-25 00:29:06 | INFO     | CORE:ORDER_EXECUTOR                 | [ASTER] Placing limit sell STBL (contract_id=METUSDT): 22 @ $0.110782152 (mid: $0.11083, offset: 0.0200%)
2025-10-25 00:29:06 | DEBUG    | EXCHANGE:ASTER:ticker=ALL           | Using contract_id for order: 'METUSDT'
2025-10-25 00:29:06 | DEBUG    | EXCHANGE:ASTER:ticker=ALL           | Rounded quantity: 22.0 → 22 (step_size=1)
2025-10-25 00:29:06 | ERROR    | EXCHANGE:ASTER:ticker=ALL           | [ASTER] Order notional $2.4376 below minimum $5
2025-10-25 00:29:06 | ERROR    | CORE:ORDER_EXECUTOR                 | [ASTER] Limit order execution failed for STBL: [ASTER] Order notional $2.4376 below minimum $5
2025-10-25 00:29:07 | INFO     | EXCHANGE:LIGHTER:ticker=ALL         | [WEBSOCKET] [LIGHTER] OPEN 22.1 @ 1.00354
2025-10-25 00:29:09 | INFO     | EXCHANGE:LIGHTER:ticker=ALL         | [WEBSOCKET] [LIGHTER] FILLED 22.1 @ 1.00354
2025-10-25 00:29:09 | INFO     | EXCHANGE:LIGHTER:ticker=ALL         | TRANSACTION: BUY 22.1 @ 1.00354 | Order: 146403 | Status: FILLED
2025-10-25 00:29:09 | INFO     | CORE:ORDER_EXECUTOR                 | [LIGHTER] Limit order filled: 22.1 @ $1.00354
2025-10-25 00:29:09 | INFO     | CORE:ATOMIC_MULTI_ORDER             | ⚡ Hedging STBL on ASTER for remaining qty=22.1, $2.45
2025-10-25 00:29:09 | INFO     | CORE:ORDER_EXECUTOR                 | 🔴 [ASTER] Executing sell STBL (qty=22.1) in mode market_only
2025-10-25 00:29:09 | INFO     | EXCHANGE:ASTER:ticker=ALL           | 📡 [ASTER] Using real-time BBO from WebSocket
2025-10-25 00:29:09 | INFO     | CORE:PRICE_PROVIDER                 | ✅ [ASTER] BBO: bid=0.11072, ask=0.11085
2025-10-25 00:29:09 | INFO     | CORE:ORDER_EXECUTOR                 | [ASTER] Placing market sell STBL (contract_id=METUSDT): 22 @ ~$0.11072
2025-10-25 00:29:09 | DEBUG    | EXCHANGE:ASTER:ticker=ALL           | 🔍 [ASTER] Using contract_id for market order: 'METUSDT'


Here, obviously its wrong whereby its `sell STBL (contract_id=METUSDT)`, We are trying to be in the STBL market, but it uses the METUSDT contract_id