1. Position mark price: Currently fetched from markets_summary in position_manager.py. Confirm if this is the best source or if there's a dedicated endpoint.
2. Leverage brackets: get_leverage_info() uses max_leverage from market metadata. Confirm if Paradex has leverage brackets like Aster.
3. Order size increment: Uses order_size_increment from market metadata. Confirm this is always available.
4. WebSocket liquidation stream: Placeholder in websocket_handlers.py. Need to check if Paradex provides liquidation notifications.