# Directory Structure

```
├── .coveragerc
├── .dockerignore
├── .flake8
├── .github
│   ├── actions
│   │   └── install_env_and_hb
│   │       └── action.yml
│   ├── CODEOWNERS
│   ├── FUNDING.yml
│   ├── ISSUE_TEMPLATE
│   │   ├── bounty_request.yml
│   │   ├── bug_report.yml
│   │   └── feature_request.yml
│   ├── pull_request_template.md
│   └── workflows
│       ├── docker_buildx_workflow.yml
│       └── workflow.yml
├── .gitignore
├── .pre-commit-config.yaml
├── bin
│   ├── __init__.py
│   ├── .gitignore
│   ├── conf_migration_script.py
│   ├── hummingbot_quickstart.py
│   ├── hummingbot.py
│   └── path_util.py
├── clean
├── CODE_OF_CONDUCT.md
├── compile
├── compile.bat
├── conf
│   ├── __init__.py
│   ├── .gitignore
│   ├── connectors
│   │   ├── __init__.py
│   │   └── .gitignore
│   ├── controllers
│   │   ├── __init__.py
│   │   └── .gitignore
│   ├── scripts
│   │   ├── __init__.py
│   │   └── .gitignore
│   └── strategies
│       ├── __init__.py
│       └── .gitignore
├── CONTRIBUTING.md
├── controllers
│   ├── __init__.py
│   ├── directional_trading
│   │   ├── __init__.py
│   │   ├── ai_livestream.py
│   │   ├── bollinger_v1.py
│   │   ├── dman_v3.py
│   │   ├── macd_bb_v1.py
│   │   └── supertrend_v1.py
│   ├── generic
│   │   ├── __init__.py
│   │   ├── arbitrage_controller.py
│   │   ├── basic_order_example.py
│   │   ├── basic_order_open_close_example.py
│   │   ├── grid_strike.py
│   │   ├── multi_grid_strike.py
│   │   ├── pmm_adjusted.py
│   │   ├── pmm.py
│   │   ├── quantum_grid_allocator.py
│   │   ├── stat_arb.py
│   │   └── xemm_multiple_levels.py
│   └── market_making
│       ├── __init__.py
│       ├── dman_maker_v2.py
│       ├── pmm_dynamic.py
│       └── pmm_simple.py
├── CURSOR_VSCODE_SETUP.md
├── docker-compose.yml
├── Dockerfile
├── hummingbot
│   ├── __init__.py
│   ├── client
│   │   ├── __init__.py
│   │   ├── command
│   │   │   ├── __init__.py
│   │   │   ├── balance_command.py
│   │   │   ├── command_utils.py
│   │   │   ├── config_command.py
│   │   │   ├── connect_command.py
│   │   │   ├── create_command.py
│   │   │   ├── exit_command.py
│   │   │   ├── export_command.py
│   │   │   ├── gateway_api_manager.py
│   │   │   ├── gateway_approve_command.py
│   │   │   ├── gateway_command.py
│   │   │   ├── gateway_config_command.py
│   │   │   ├── gateway_lp_command.py
│   │   │   ├── gateway_pool_command.py
│   │   │   ├── gateway_swap_command.py
│   │   │   ├── gateway_token_command.py
│   │   │   ├── help_command.py
│   │   │   ├── history_command.py
│   │   │   ├── import_command.py
│   │   │   ├── lp_command_utils.py
│   │   │   ├── mqtt_command.py
│   │   │   ├── order_book_command.py
│   │   │   ├── rate_command.py
│   │   │   ├── silly_commands.py
│   │   │   ├── silly_resources
│   │   │   │   ├── dennis_1.txt
│   │   │   │   ├── dennis_2.txt
│   │   │   │   ├── dennis_3.txt
│   │   │   │   ├── dennis_4.txt
│   │   │   │   ├── dennis_loading_1.txt
│   │   │   │   ├── dennis_loading_2.txt
│   │   │   │   ├── dennis_loading_3.txt
│   │   │   │   ├── dennis_loading_4.txt
│   │   │   │   ├── hb_with_flower_1.txt
│   │   │   │   ├── hb_with_flower_2.txt
│   │   │   │   ├── hb_with_flower_up_close_1.txt
│   │   │   │   ├── hb_with_flower_up_close_2.txt
│   │   │   │   ├── jack_1.txt
│   │   │   │   ├── jack_2.txt
│   │   │   │   ├── money-fly_1.txt
│   │   │   │   ├── money-fly_2.txt
│   │   │   │   ├── rein_1.txt
│   │   │   │   ├── rein_2.txt
│   │   │   │   ├── rein_3.txt
│   │   │   │   ├── roger_1.txt
│   │   │   │   ├── roger_2.txt
│   │   │   │   ├── roger_3.txt
│   │   │   │   ├── roger_4.txt
│   │   │   │   └── roger_alert.txt
│   │   │   ├── start_command.py
│   │   │   ├── status_command.py
│   │   │   ├── stop_command.py
│   │   │   └── ticker_command.py
│   │   ├── config
│   │   │   ├── __init__.py
│   │   │   ├── client_config_map.py
│   │   │   ├── conf_migration.py
│   │   │   ├── config_crypt.py
│   │   │   ├── config_data_types.py
│   │   │   ├── config_helpers.py
│   │   │   ├── config_methods.py
│   │   │   ├── config_validators.py
│   │   │   ├── config_var.py
│   │   │   ├── fee_overrides_config_map.py
│   │   │   ├── gateway_ssl_config_map.py
│   │   │   ├── global_config_map.py
│   │   │   ├── security.py
│   │   │   ├── strategy_config_data_types.py
│   │   │   └── trade_fee_schema_loader.py
│   │   ├── data_type
│   │   │   ├── __init__.py
│   │   │   └── currency_amount.py
│   │   ├── hummingbot_application.py
│   │   ├── performance.py
│   │   ├── platform.py
│   │   ├── settings.py
│   │   ├── tab
│   │   │   ├── __init__.py
│   │   │   ├── data_types.py
│   │   │   ├── order_book_tab.py
│   │   │   ├── tab_base.py
│   │   │   └── tab_example_tab.py
│   │   └── ui
│   │       ├── __init__.py
│   │       ├── completer.py
│   │       ├── custom_widgets.py
│   │       ├── hummingbot_cli.py
│   │       ├── interface_utils.py
│   │       ├── keybindings.py
│   │       ├── layout.py
│   │       ├── parser.py
│   │       ├── scroll_handlers.py
│   │       ├── stdout_redirection.py
│   │       └── style.py
│   ├── connector
│   │   ├── __init__.py
│   │   ├── budget_checker.py
│   │   ├── client_order_tracker.py
│   │   ├── connector_base.pxd
│   │   ├── connector_base.pyx
│   │   ├── connector_metrics_collector.py
│   │   ├── constants.py
│   │   ├── derivative
│   │   │   ├── __init__.py
│   │   │   ├── binance_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── binance_perpetual_api_order_book_data_source.py
│   │   │   │   ├── binance_perpetual_auth.py
│   │   │   │   ├── binance_perpetual_constants.py
│   │   │   │   ├── binance_perpetual_derivative.py
│   │   │   │   ├── binance_perpetual_user_stream_data_source.py
│   │   │   │   ├── binance_perpetual_utils.py
│   │   │   │   ├── binance_perpetual_web_utils.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   └── dummy.pyx
│   │   │   ├── bitget_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bitget_perpetual_api_order_book_data_source.py
│   │   │   │   ├── bitget_perpetual_auth.py
│   │   │   │   ├── bitget_perpetual_constants.py
│   │   │   │   ├── bitget_perpetual_derivative.py
│   │   │   │   ├── bitget_perpetual_user_stream_data_source.py
│   │   │   │   ├── bitget_perpetual_utils.py
│   │   │   │   └── bitget_perpetual_web_utils.py
│   │   │   ├── bitmart_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bitmart_perpetual_api_order_book_data_source.py
│   │   │   │   ├── bitmart_perpetual_auth.py
│   │   │   │   ├── bitmart_perpetual_constants.py
│   │   │   │   ├── bitmart_perpetual_derivative.py
│   │   │   │   ├── bitmart_perpetual_user_stream_data_source.py
│   │   │   │   ├── bitmart_perpetual_utils.py
│   │   │   │   └── bitmart_perpetual_web_utils.py
│   │   │   ├── bybit_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bybit_perpetual_api_order_book_data_source.py
│   │   │   │   ├── bybit_perpetual_auth.py
│   │   │   │   ├── bybit_perpetual_constants.py
│   │   │   │   ├── bybit_perpetual_derivative.py
│   │   │   │   ├── bybit_perpetual_user_stream_data_source.py
│   │   │   │   ├── bybit_perpetual_utils.py
│   │   │   │   ├── bybit_perpetual_web_utils.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   └── dummy.pyx
│   │   │   ├── derive_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── derive_perpetual_api_order_book_data_source.py
│   │   │   │   ├── derive_perpetual_api_user_stream_data_source.py
│   │   │   │   ├── derive_perpetual_auth.py
│   │   │   │   ├── derive_perpetual_constants.py
│   │   │   │   ├── derive_perpetual_derivative.py
│   │   │   │   ├── derive_perpetual_utils.py
│   │   │   │   ├── derive_perpetual_web_utils.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   └── dummy.pyx
│   │   │   ├── dydx_v4_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── data_sources
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── dydx_v4_data_source.py
│   │   │   │   │   ├── keypairs.py
│   │   │   │   │   └── tx.py
│   │   │   │   ├── dydx_v4_perpetual_api_order_book_data_source.py
│   │   │   │   ├── dydx_v4_perpetual_constants.py
│   │   │   │   ├── dydx_v4_perpetual_derivative.py
│   │   │   │   ├── dydx_v4_perpetual_user_stream_data_source.py
│   │   │   │   ├── dydx_v4_perpetual_utils.py
│   │   │   │   └── dydx_v4_perpetual_web_utils.py
│   │   │   ├── gate_io_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   ├── dummy.pyx
│   │   │   │   ├── gate_io_perpetual_api_order_book_data_source.py
│   │   │   │   ├── gate_io_perpetual_auth.py
│   │   │   │   ├── gate_io_perpetual_constants.py
│   │   │   │   ├── gate_io_perpetual_derivative.py
│   │   │   │   ├── gate_io_perpetual_user_stream_data_source.py
│   │   │   │   ├── gate_io_perpetual_utils.py
│   │   │   │   └── gate_io_perpetual_web_utils.py
│   │   │   ├── hyperliquid_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   ├── dummy.pyx
│   │   │   │   ├── hyperliquid_perpetual_api_order_book_data_source.py
│   │   │   │   ├── hyperliquid_perpetual_auth.py
│   │   │   │   ├── hyperliquid_perpetual_constants.py
│   │   │   │   ├── hyperliquid_perpetual_derivative.py
│   │   │   │   ├── hyperliquid_perpetual_user_stream_data_source.py
│   │   │   │   ├── hyperliquid_perpetual_utils.py
│   │   │   │   └── hyperliquid_perpetual_web_utils.py
│   │   │   ├── injective_v2_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── injective_constants.py
│   │   │   │   ├── injective_v2_perpetual_api_order_book_data_source.py
│   │   │   │   ├── injective_v2_perpetual_derivative.py
│   │   │   │   ├── injective_v2_perpetual_utils.py
│   │   │   │   ├── injective_v2_perpetual_web_utils.py
│   │   │   │   └── README.md
│   │   │   ├── kucoin_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   ├── dummy.pyx
│   │   │   │   ├── kucoin_perpetual_api_order_book_data_source.py
│   │   │   │   ├── kucoin_perpetual_api_user_stream_data_source.py
│   │   │   │   ├── kucoin_perpetual_auth.py
│   │   │   │   ├── kucoin_perpetual_constants.py
│   │   │   │   ├── kucoin_perpetual_derivative.py
│   │   │   │   ├── kucoin_perpetual_utils.py
│   │   │   │   └── kucoin_perpetual_web_utils.py
│   │   │   ├── okx_perpetual
│   │   │   │   ├── __init__.py
│   │   │   │   ├── okx_perpetual_api_order_book_data_source.py
│   │   │   │   ├── okx_perpetual_auth.py
│   │   │   │   ├── okx_perpetual_constants.py
│   │   │   │   ├── okx_perpetual_derivative.py
│   │   │   │   ├── okx_perpetual_user_stream_data_source.py
│   │   │   │   ├── okx_perpetual_utils.py
│   │   │   │   └── okx_perpetual_web_utils.py
│   │   │   ├── perpetual_budget_checker.py
│   │   │   └── position.py
│   │   ├── derivative_base.py
│   │   ├── exchange
│   │   │   ├── __init__.py
│   │   │   ├── ascend_ex
│   │   │   │   ├── __init__.py
│   │   │   │   ├── ascend_ex_api_order_book_data_source.py
│   │   │   │   ├── ascend_ex_api_user_stream_data_source.py
│   │   │   │   ├── ascend_ex_auth.py
│   │   │   │   ├── ascend_ex_constants.py
│   │   │   │   ├── ascend_ex_exchange.py
│   │   │   │   ├── ascend_ex_utils.py
│   │   │   │   └── ascend_ex_web_utils.py
│   │   │   ├── binance
│   │   │   │   ├── __init__.py
│   │   │   │   ├── binance_api_order_book_data_source.py
│   │   │   │   ├── binance_api_user_stream_data_source.py
│   │   │   │   ├── binance_auth.py
│   │   │   │   ├── binance_constants.py
│   │   │   │   ├── binance_exchange.py
│   │   │   │   ├── binance_order_book.py
│   │   │   │   ├── binance_utils.py
│   │   │   │   ├── binance_web_utils.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   └── dummy.pyx
│   │   │   ├── bing_x
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bing_x_api_order_book_data_source.py
│   │   │   │   ├── bing_x_api_user_stream_data_source.py
│   │   │   │   ├── bing_x_auth.py
│   │   │   │   ├── bing_x_constants.py
│   │   │   │   ├── bing_x_exchange.py
│   │   │   │   ├── bing_x_order_book.py
│   │   │   │   ├── bing_x_utils.py
│   │   │   │   └── bing_x_web_utils.py
│   │   │   ├── bitmart
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bitmart_api_order_book_data_source.py
│   │   │   │   ├── bitmart_api_user_stream_data_source.py
│   │   │   │   ├── bitmart_auth.py
│   │   │   │   ├── bitmart_constants.py
│   │   │   │   ├── bitmart_exchange.py
│   │   │   │   ├── bitmart_utils.py
│   │   │   │   ├── bitmart_web_utils.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   └── dummy.pyx
│   │   │   ├── bitrue
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bitrue_api_order_book_data_source.py
│   │   │   │   ├── bitrue_auth.py
│   │   │   │   ├── bitrue_constants.py
│   │   │   │   ├── bitrue_exchange.py
│   │   │   │   ├── bitrue_order_book.py
│   │   │   │   ├── bitrue_user_stream_data_source.py
│   │   │   │   ├── bitrue_utils.py
│   │   │   │   └── bitrue_web_utils.py
│   │   │   ├── bitstamp
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bitstamp_api_order_book_data_source.py
│   │   │   │   ├── bitstamp_api_user_stream_data_source.py
│   │   │   │   ├── bitstamp_auth.py
│   │   │   │   ├── bitstamp_constants.py
│   │   │   │   ├── bitstamp_exchange.py
│   │   │   │   ├── bitstamp_order_book.py
│   │   │   │   ├── bitstamp_utils.py
│   │   │   │   ├── bitstamp_web_utils.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   └── dummy.pyx
│   │   │   ├── btc_markets
│   │   │   │   ├── __init__.py
│   │   │   │   ├── btc_markets_api_order_book_data_source.py
│   │   │   │   ├── btc_markets_api_user_stream_data_source.py
│   │   │   │   ├── btc_markets_auth.py
│   │   │   │   ├── btc_markets_constants.py
│   │   │   │   ├── btc_markets_exchange.py
│   │   │   │   ├── btc_markets_order_book.py
│   │   │   │   ├── btc_markets_utils.py
│   │   │   │   └── btc_markets_web_utils.py
│   │   │   ├── bybit
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bybit_api_order_book_data_source.py
│   │   │   │   ├── bybit_api_user_stream_data_source.py
│   │   │   │   ├── bybit_auth.py
│   │   │   │   ├── bybit_constants.py
│   │   │   │   ├── bybit_exchange.py
│   │   │   │   ├── bybit_order_book.py
│   │   │   │   ├── bybit_utils.py
│   │   │   │   ├── bybit_web_utils.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   └── dummy.pyx
│   │   │   ├── coinbase_advanced_trade
│   │   │   │   ├── __init__.py
│   │   │   │   ├── coinbase_advanced_trade_api_order_book_data_source.py
│   │   │   │   ├── coinbase_advanced_trade_api_user_stream_data_source.py
│   │   │   │   ├── coinbase_advanced_trade_auth.py
│   │   │   │   ├── coinbase_advanced_trade_constants.py
│   │   │   │   ├── coinbase_advanced_trade_exchange.py
│   │   │   │   ├── coinbase_advanced_trade_order_book.py
│   │   │   │   ├── coinbase_advanced_trade_utils.py
│   │   │   │   ├── coinbase_advanced_trade_web_utils.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   └── dummy.pyx
│   │   │   ├── cube
│   │   │   │   ├── __init__.py
│   │   │   │   ├── cube_api_order_book_data_source.py
│   │   │   │   ├── cube_api_user_stream_data_source.py
│   │   │   │   ├── cube_auth.py
│   │   │   │   ├── cube_constants.py
│   │   │   │   ├── cube_exchange.py
│   │   │   │   ├── cube_order_book.py
│   │   │   │   ├── cube_utils.py
│   │   │   │   ├── cube_web_utils.py
│   │   │   │   ├── cube_ws_protobufs
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── market_data_pb2.py
│   │   │   │   │   ├── market_data_pb2.pyi
│   │   │   │   │   ├── py.typed
│   │   │   │   │   ├── trade_pb2.py
│   │   │   │   │   └── trade_pb2.pyi
│   │   │   │   ├── dummy.pxd
│   │   │   │   └── dummy.pyx
│   │   │   ├── derive
│   │   │   │   ├── __init__.py
│   │   │   │   ├── derive_api_order_book_data_source.py
│   │   │   │   ├── derive_api_user_stream_data_source.py
│   │   │   │   ├── derive_auth.py
│   │   │   │   ├── derive_constants.py
│   │   │   │   ├── derive_exchange.py
│   │   │   │   ├── derive_utils.py
│   │   │   │   └── derive_web_utils.py
│   │   │   ├── dexalot
│   │   │   │   ├── __init__.py
│   │   │   │   ├── data_sources
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── dexalot_data_source.py
│   │   │   │   ├── dexalot_api_order_book_data_source.py
│   │   │   │   ├── dexalot_api_user_stream_data_source.py
│   │   │   │   ├── dexalot_auth.py
│   │   │   │   ├── dexalot_constants.py
│   │   │   │   ├── dexalot_exchange.py
│   │   │   │   ├── dexalot_utils.py
│   │   │   │   └── dexalot_web_utils.py
│   │   │   ├── foxbit
│   │   │   │   ├── __init__.py
│   │   │   │   ├── foxbit_api_order_book_data_source.py
│   │   │   │   ├── foxbit_api_user_stream_data_source.py
│   │   │   │   ├── foxbit_auth.py
│   │   │   │   ├── foxbit_connector.pxd
│   │   │   │   ├── foxbit_connector.pyx
│   │   │   │   ├── foxbit_constants.py
│   │   │   │   ├── foxbit_exchange.py
│   │   │   │   ├── foxbit_order_book.py
│   │   │   │   ├── foxbit_utils.py
│   │   │   │   └── foxbit_web_utils.py
│   │   │   ├── gate_io
│   │   │   │   ├── __init__.py
│   │   │   │   ├── gate_io_api_order_book_data_source.py
│   │   │   │   ├── gate_io_api_user_stream_data_source.py
│   │   │   │   ├── gate_io_auth.py
│   │   │   │   ├── gate_io_constants.py
│   │   │   │   ├── gate_io_exchange.py
│   │   │   │   ├── gate_io_utils.py
│   │   │   │   ├── gate_io_web_utils.py
│   │   │   │   ├── placeholder.pxd
│   │   │   │   └── placeholder.pyx
│   │   │   ├── htx
│   │   │   │   ├── __init__.py
│   │   │   │   ├── htx_api_order_book_data_source.py
│   │   │   │   ├── htx_api_user_stream_data_source.py
│   │   │   │   ├── htx_auth.py
│   │   │   │   ├── htx_constants.py
│   │   │   │   ├── htx_exchange.py
│   │   │   │   ├── htx_utils.py
│   │   │   │   └── htx_web_utils.py
│   │   │   ├── hyperliquid
│   │   │   │   ├── __init__.py
│   │   │   │   ├── hyperliquid_api_order_book_data_source.py
│   │   │   │   ├── hyperliquid_api_user_stream_data_source.py
│   │   │   │   ├── hyperliquid_auth.py
│   │   │   │   ├── hyperliquid_constants.py
│   │   │   │   ├── hyperliquid_exchange.py
│   │   │   │   ├── hyperliquid_order_book.py
│   │   │   │   ├── hyperliquid_utils.py
│   │   │   │   └── hyperliquid_web_utils.py
│   │   │   ├── injective_v2
│   │   │   │   ├── __init__.py
│   │   │   │   ├── account_delegation_script.py
│   │   │   │   ├── data_sources
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── injective_data_source.py
│   │   │   │   │   ├── injective_grantee_data_source.py
│   │   │   │   │   ├── injective_read_only_data_source.py
│   │   │   │   │   └── injective_vaults_data_source.py
│   │   │   │   ├── injective_constants.py
│   │   │   │   ├── injective_events.py
│   │   │   │   ├── injective_market.py
│   │   │   │   ├── injective_query_executor.py
│   │   │   │   ├── injective_v2_api_order_book_data_source.py
│   │   │   │   ├── injective_v2_exchange.py
│   │   │   │   ├── injective_v2_utils.py
│   │   │   │   ├── injective_v2_web_utils.py
│   │   │   │   └── README.md
│   │   │   ├── kraken
│   │   │   │   ├── __init__.py
│   │   │   │   ├── kraken_api_order_book_data_source.py
│   │   │   │   ├── kraken_api_user_stream_data_source.py
│   │   │   │   ├── kraken_auth.py
│   │   │   │   ├── kraken_constants.py
│   │   │   │   ├── kraken_exchange.py
│   │   │   │   ├── kraken_order_book.py
│   │   │   │   ├── kraken_utils.py
│   │   │   │   └── kraken_web_utils.py
│   │   │   ├── kucoin
│   │   │   │   ├── __init__.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   ├── dummy.pyx
│   │   │   │   ├── kucoin_api_order_book_data_source.py
│   │   │   │   ├── kucoin_api_user_stream_data_source.py
│   │   │   │   ├── kucoin_auth.py
│   │   │   │   ├── kucoin_constants.py
│   │   │   │   ├── kucoin_exchange.py
│   │   │   │   ├── kucoin_order_book_message.py
│   │   │   │   ├── kucoin_utils.py
│   │   │   │   └── kucoin_web_utils.py
│   │   │   ├── mexc
│   │   │   │   ├── __init__.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   ├── dummy.pyx
│   │   │   │   ├── mexc_api_order_book_data_source.py
│   │   │   │   ├── mexc_api_user_stream_data_source.py
│   │   │   │   ├── mexc_auth.py
│   │   │   │   ├── mexc_constants.py
│   │   │   │   ├── mexc_exchange.py
│   │   │   │   ├── mexc_order_book.py
│   │   │   │   ├── mexc_post_processor.py
│   │   │   │   ├── mexc_utils.py
│   │   │   │   ├── mexc_web_utils.py
│   │   │   │   └── protobuf
│   │   │   │       ├── __init__.py
│   │   │   │       ├── PrivateAccountV3Api_pb2.py
│   │   │   │       ├── PrivateAccountV3Api_pb2.pyi
│   │   │   │       ├── PrivateDealsV3Api_pb2.py
│   │   │   │       ├── PrivateDealsV3Api_pb2.pyi
│   │   │   │       ├── PrivateOrdersV3Api_pb2.py
│   │   │   │       ├── PrivateOrdersV3Api_pb2.pyi
│   │   │   │       ├── PublicAggreBookTickerV3Api_pb2.py
│   │   │   │       ├── PublicAggreBookTickerV3Api_pb2.pyi
│   │   │   │       ├── PublicAggreDealsV3Api_pb2.py
│   │   │   │       ├── PublicAggreDealsV3Api_pb2.pyi
│   │   │   │       ├── PublicAggreDepthsV3Api_pb2.py
│   │   │   │       ├── PublicAggreDepthsV3Api_pb2.pyi
│   │   │   │       ├── PublicBookTickerBatchV3Api_pb2.py
│   │   │   │       ├── PublicBookTickerBatchV3Api_pb2.pyi
│   │   │   │       ├── PublicBookTickerV3Api_pb2.py
│   │   │   │       ├── PublicBookTickerV3Api_pb2.pyi
│   │   │   │       ├── PublicDealsV3Api_pb2.py
│   │   │   │       ├── PublicDealsV3Api_pb2.pyi
│   │   │   │       ├── PublicIncreaseDepthsBatchV3Api_pb2.py
│   │   │   │       ├── PublicIncreaseDepthsBatchV3Api_pb2.pyi
│   │   │   │       ├── PublicIncreaseDepthsV3Api_pb2.py
│   │   │   │       ├── PublicIncreaseDepthsV3Api_pb2.pyi
│   │   │   │       ├── PublicLimitDepthsV3Api_pb2.py
│   │   │   │       ├── PublicLimitDepthsV3Api_pb2.pyi
│   │   │   │       ├── PublicMiniTickersV3Api_pb2.py
│   │   │   │       ├── PublicMiniTickersV3Api_pb2.pyi
│   │   │   │       ├── PublicMiniTickerV3Api_pb2.py
│   │   │   │       ├── PublicMiniTickerV3Api_pb2.pyi
│   │   │   │       ├── PublicSpotKlineV3Api_pb2.py
│   │   │   │       ├── PublicSpotKlineV3Api_pb2.pyi
│   │   │   │       ├── PushDataV3ApiWrapper_pb2.py
│   │   │   │       └── PushDataV3ApiWrapper_pb2.pyi
│   │   │   ├── ndax
│   │   │   │   ├── __init__.py
│   │   │   │   ├── ndax_api_order_book_data_source.py
│   │   │   │   ├── ndax_api_user_stream_data_source.py
│   │   │   │   ├── ndax_auth.py
│   │   │   │   ├── ndax_constants.py
│   │   │   │   ├── ndax_exchange.py
│   │   │   │   ├── ndax_order_book_message.py
│   │   │   │   ├── ndax_order_book.py
│   │   │   │   ├── ndax_utils.py
│   │   │   │   ├── ndax_web_utils.py
│   │   │   │   └── ndax_websocket_adaptor.py
│   │   │   ├── okx
│   │   │   │   ├── __init__.py
│   │   │   │   ├── okx_api_order_book_data_source.py
│   │   │   │   ├── okx_api_user_stream_data_source.py
│   │   │   │   ├── okx_auth.py
│   │   │   │   ├── okx_constants.py
│   │   │   │   ├── okx_exchange.py
│   │   │   │   ├── okx_utils.py
│   │   │   │   └── okx_web_utils.py
│   │   │   ├── paper_trade
│   │   │   │   ├── __init__.py
│   │   │   │   ├── market_config.py
│   │   │   │   ├── paper_trade_exchange.pxd
│   │   │   │   ├── paper_trade_exchange.pyx
│   │   │   │   └── trading_pair.py
│   │   │   ├── vertex
│   │   │   │   ├── __init__.py
│   │   │   │   ├── dummy.pxd
│   │   │   │   ├── dummy.pyx
│   │   │   │   ├── vertex_api_order_book_data_source.py
│   │   │   │   ├── vertex_api_user_stream_data_source.py
│   │   │   │   ├── vertex_auth.py
│   │   │   │   ├── vertex_constants.py
│   │   │   │   ├── vertex_eip712_structs.py
│   │   │   │   ├── vertex_exchange.py
│   │   │   │   ├── vertex_order_book.py
│   │   │   │   ├── vertex_utils.py
│   │   │   │   └── vertex_web_utils.py
│   │   │   └── xrpl
│   │   │       ├── __init__.py
│   │   │       ├── dummy.pxd
│   │   │       ├── dummy.pyx
│   │   │       ├── xrpl_api_order_book_data_source.py
│   │   │       ├── xrpl_api_user_stream_data_source.py
│   │   │       ├── xrpl_auth.py
│   │   │       ├── xrpl_constants.py
│   │   │       ├── xrpl_exchange.py
│   │   │       ├── xrpl_order_book.py
│   │   │       ├── xrpl_order_placement_strategy.py
│   │   │       ├── xrpl_utils.py
│   │   │       └── xrpl_web_utils.py
│   │   ├── exchange_base.pxd
│   │   ├── exchange_base.pyx
│   │   ├── exchange_py_base.py
│   │   ├── gateway
│   │   │   ├── __init__.py
│   │   │   ├── common_types.py
│   │   │   ├── gateway_base.py
│   │   │   ├── gateway_in_flight_order.py
│   │   │   ├── gateway_lp.py
│   │   │   ├── gateway_order_tracker.py
│   │   │   └── gateway_swap.py
│   │   ├── in_flight_order_base.pxd
│   │   ├── in_flight_order_base.pyx
│   │   ├── markets_recorder.py
│   │   ├── other
│   │   │   ├── __init__.py
│   │   │   └── derive_common_utils.py
│   │   ├── parrot.py
│   │   ├── perpetual_derivative_py_base.py
│   │   ├── perpetual_trading.py
│   │   ├── test_support
│   │   │   ├── __init__.py
│   │   │   ├── exchange_connector_test.py
│   │   │   ├── mock_order_tracker.py
│   │   │   ├── mock_paper_exchange.pxd
│   │   │   ├── mock_paper_exchange.pyx
│   │   │   ├── mock_pure_python_paper_exchange.py
│   │   │   ├── network_mocking_assistant.py
│   │   │   ├── oms_exchange_connector_test.py
│   │   │   └── perpetual_derivative_test.py
│   │   ├── time_synchronizer.py
│   │   ├── trading_rule.pxd
│   │   ├── trading_rule.pyx
│   │   ├── utilities
│   │   │   ├── __init__.py
│   │   │   └── oms_connector
│   │   │       ├── __init__.py
│   │   │       ├── oms_connector_api_order_book_data_source.py
│   │   │       ├── oms_connector_api_user_stream_data_source.py
│   │   │       ├── oms_connector_auth.py
│   │   │       ├── oms_connector_constants.py
│   │   │       ├── oms_connector_exchange.py
│   │   │       ├── oms_connector_utils.py
│   │   │       └── oms_connector_web_utils.py
│   │   └── utils.py
│   ├── core
│   │   ├── __init__.py
│   │   ├── api_throttler
│   │   │   ├── __init__.py
│   │   │   ├── async_request_context_base.py
│   │   │   ├── async_throttler_base.py
│   │   │   ├── async_throttler.py
│   │   │   └── data_types.py
│   │   ├── clock_mode.py
│   │   ├── clock.pxd
│   │   ├── clock.pyx
│   │   ├── connector_manager.py
│   │   ├── cpp
│   │   │   ├── .gitignore
│   │   │   ├── compile.sh
│   │   │   ├── LimitOrder.cpp
│   │   │   ├── LimitOrder.h
│   │   │   ├── OrderBookEntry.cpp
│   │   │   ├── OrderBookEntry.h
│   │   │   ├── OrderExpirationEntry.cpp
│   │   │   ├── OrderExpirationEntry.h
│   │   │   ├── PyRef.cpp
│   │   │   ├── PyRef.h
│   │   │   ├── TestOrderBookEntry.cpp
│   │   │   ├── Utils.cpp
│   │   │   └── Utils.h
│   │   ├── data_type
│   │   │   ├── __init__.py
│   │   │   ├── cancellation_result.py
│   │   │   ├── common.py
│   │   │   ├── composite_order_book.pxd
│   │   │   ├── composite_order_book.pyx
│   │   │   ├── funding_info.py
│   │   │   ├── in_flight_order.py
│   │   │   ├── limit_order.pxd
│   │   │   ├── limit_order.pyx
│   │   │   ├── LimitOrder.pxd
│   │   │   ├── market_order.py
│   │   │   ├── order_book_message.py
│   │   │   ├── order_book_query_result.pxd
│   │   │   ├── order_book_query_result.pyx
│   │   │   ├── order_book_row.py
│   │   │   ├── order_book_tracker_data_source.py
│   │   │   ├── order_book_tracker_entry.py
│   │   │   ├── order_book_tracker.py
│   │   │   ├── order_book.pxd
│   │   │   ├── order_book.pyx
│   │   │   ├── order_candidate.py
│   │   │   ├── order_expiration_entry.pxd
│   │   │   ├── order_expiration_entry.pyx
│   │   │   ├── OrderBookEntry.pxd
│   │   │   ├── OrderExpirationEntry.pxd
│   │   │   ├── perpetual_api_order_book_data_source.py
│   │   │   ├── remote_api_order_book_data_source.py
│   │   │   ├── trade_fee.py
│   │   │   ├── trade.py
│   │   │   ├── transaction_tracker.pxd
│   │   │   ├── transaction_tracker.pyx
│   │   │   ├── user_stream_tracker_data_source.py
│   │   │   └── user_stream_tracker.py
│   │   ├── event
│   │   │   ├── __init__.py
│   │   │   ├── event_forwarder.py
│   │   │   ├── event_listener.pxd
│   │   │   ├── event_listener.pyx
│   │   │   ├── event_logger.pxd
│   │   │   ├── event_logger.pyx
│   │   │   ├── event_reporter.pxd
│   │   │   ├── event_reporter.pyx
│   │   │   └── events.py
│   │   ├── gateway
│   │   │   ├── __init__.py
│   │   │   ├── gateway_http_client.py
│   │   │   └── utils.py
│   │   ├── management
│   │   │   ├── __init__.py
│   │   │   ├── console.py
│   │   │   └── diagnosis.py
│   │   ├── network_base.py
│   │   ├── network_iterator.pxd
│   │   ├── network_iterator.pyx
│   │   ├── pubsub.pxd
│   │   ├── pubsub.pyx
│   │   ├── py_time_iterator.pxd
│   │   ├── py_time_iterator.pyx
│   │   ├── PyRef.pxd
│   │   ├── rate_oracle
│   │   │   ├── __init__.py
│   │   │   ├── rate_oracle.py
│   │   │   ├── sources
│   │   │   │   ├── __init__.py
│   │   │   │   ├── ascend_ex_rate_source.py
│   │   │   │   ├── binance_rate_source.py
│   │   │   │   ├── binance_us_rate_source.py
│   │   │   │   ├── coin_cap_rate_source.py
│   │   │   │   ├── coin_gecko_rate_source.py
│   │   │   │   ├── coinbase_advanced_trade_rate_source.py
│   │   │   │   ├── cube_rate_source.py
│   │   │   │   ├── derive_rate_source.py
│   │   │   │   ├── dexalot_rate_source.py
│   │   │   │   ├── gate_io_rate_source.py
│   │   │   │   ├── hyperliquid_rate_source.py
│   │   │   │   ├── kucoin_rate_source.py
│   │   │   │   ├── mexc_rate_source.py
│   │   │   │   └── rate_source_base.py
│   │   │   └── utils.py
│   │   ├── time_iterator.pxd
│   │   ├── time_iterator.pyx
│   │   ├── trading_core.py
│   │   ├── utils
│   │   │   ├── __init__.py
│   │   │   ├── async_call_scheduler.py
│   │   │   ├── async_retry.py
│   │   │   ├── async_utils.py
│   │   │   ├── estimate_fee.py
│   │   │   ├── fixed_rate_source.py
│   │   │   ├── gateway_config_utils.py
│   │   │   ├── kill_switch.py
│   │   │   ├── market_price.py
│   │   │   ├── ssl_cert.py
│   │   │   ├── ssl_client_request.py
│   │   │   ├── tracking_nonce.py
│   │   │   └── trading_pair_fetcher.py
│   │   ├── Utils.pxd
│   │   └── web_assistant
│   │       ├── __init__.py
│   │       ├── auth.py
│   │       ├── connections
│   │       │   ├── __init__.py
│   │       │   ├── connections_factory.py
│   │       │   ├── data_types.py
│   │       │   ├── rest_connection.py
│   │       │   └── ws_connection.py
│   │       ├── rest_assistant.py
│   │       ├── rest_post_processors.py
│   │       ├── rest_pre_processors.py
│   │       ├── web_assistants_factory.py
│   │       ├── ws_assistant.py
│   │       ├── ws_post_processors.py
│   │       └── ws_pre_processors.py
│   ├── data_feed
│   │   ├── __init__.py
│   │   ├── amm_gateway_data_feed.py
│   │   ├── candles_feed
│   │   │   ├── __init__.py
│   │   │   ├── ascend_ex_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── ascend_ex_spot_candles.py
│   │   │   │   └── constants.py
│   │   │   ├── binance_perpetual_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── binance_perpetual_candles.py
│   │   │   │   └── constants.py
│   │   │   ├── binance_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── binance_spot_candles.py
│   │   │   │   └── constants.py
│   │   │   ├── bitmart_perpetual_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bitmart_perpetual_candles.py
│   │   │   │   └── constants.py
│   │   │   ├── btc_markets_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── btc_markets_spot_candles.py
│   │   │   │   └── constants.py
│   │   │   ├── bybit_perpetual_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bybit_perpetual_candles.py
│   │   │   │   └── constants.py
│   │   │   ├── bybit_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── bybit_spot_candles.py
│   │   │   │   └── constants.py
│   │   │   ├── candles_base.py
│   │   │   ├── candles_factory.py
│   │   │   ├── data_types.py
│   │   │   ├── dexalot_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── dexalot_spot_candles.py
│   │   │   ├── gate_io_perpetual_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── gate_io_perpetual_candles.py
│   │   │   ├── gate_io_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── gate_io_spot_candles.py
│   │   │   ├── hyperliquid_perpetual_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── hyperliquid_perpetual_candles.py
│   │   │   ├── hyperliquid_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── hyperliquid_spot_candles.py
│   │   │   ├── kraken_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── kraken_spot_candles.py
│   │   │   ├── kucoin_perpetual_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── kucoin_perpetual_candles.py
│   │   │   ├── kucoin_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── kucoin_spot_candles.py
│   │   │   ├── mexc_perpetual_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── mexc_perpetual_candles.py
│   │   │   ├── mexc_spot_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── mexc_spot_candles.py
│   │   │   ├── okx_perpetual_candles
│   │   │   │   ├── __init__.py
│   │   │   │   ├── constants.py
│   │   │   │   └── okx_perpetual_candles.py
│   │   │   └── okx_spot_candles
│   │   │       ├── __init__.py
│   │   │       ├── constants.py
│   │   │       └── okx_spot_candles.py
│   │   ├── coin_cap_data_feed
│   │   │   ├── __init__.py
│   │   │   ├── coin_cap_constants.py
│   │   │   └── coin_cap_data_feed.py
│   │   ├── coin_gecko_data_feed
│   │   │   ├── __init__.py
│   │   │   ├── coin_gecko_constants.py
│   │   │   └── coin_gecko_data_feed.py
│   │   ├── custom_api_data_feed.py
│   │   ├── data_feed_base.py
│   │   ├── liquidations_feed
│   │   │   ├── __init__.py
│   │   │   ├── binance
│   │   │   │   ├── __init__.py
│   │   │   │   ├── binance_liquidations.py
│   │   │   │   └── constants.py
│   │   │   ├── liquidations_base.py
│   │   │   └── liquidations_factory.py
│   │   ├── market_data_provider.py
│   │   └── wallet_tracker_data_feed.py
│   ├── exceptions.py
│   ├── logger
│   │   ├── __init__.py
│   │   ├── application_warning.py
│   │   ├── cli_handler.py
│   │   ├── log_server_client.py
│   │   ├── logger.py
│   │   └── struct_logger.py
│   ├── model
│   │   ├── __init__.py
│   │   ├── controllers.py
│   │   ├── db_migration
│   │   │   ├── __init__.py
│   │   │   ├── base_transformation.py
│   │   │   ├── migrator.py
│   │   │   └── transformations.py
│   │   ├── decimal_type_decorator.py
│   │   ├── executors.py
│   │   ├── funding_payment.py
│   │   ├── inventory_cost.py
│   │   ├── market_data.py
│   │   ├── market_state.py
│   │   ├── metadata.py
│   │   ├── order_status.py
│   │   ├── order.py
│   │   ├── position.py
│   │   ├── range_position_collected_fees.py
│   │   ├── range_position_update.py
│   │   ├── sql_connection_manager.py
│   │   ├── trade_fill.py
│   │   └── transaction_base.py
│   ├── notifier
│   │   ├── __init__.py
│   │   └── notifier_base.py
│   ├── README.md
│   ├── remote_iface
│   │   ├── __init__.py
│   │   ├── messages.py
│   │   └── mqtt.py
│   ├── strategy
│   │   ├── __init__.py
│   │   ├── __utils__
│   │   │   ├── __init__.py
│   │   │   ├── ring_buffer.pxd
│   │   │   ├── ring_buffer.pyx
│   │   │   └── trailing_indicators
│   │   │       ├── __init__.py
│   │   │       ├── base_trailing_indicator.py
│   │   │       ├── exponential_moving_average.py
│   │   │       ├── historical_volatility.py
│   │   │       ├── instant_volatility.py
│   │   │       ├── trading_intensity.pxd
│   │   │       └── trading_intensity.pyx
│   │   ├── amm_arb
│   │   │   ├── __init__.py
│   │   │   ├── amm_arb_config_map.py
│   │   │   ├── amm_arb.py
│   │   │   ├── data_types.py
│   │   │   ├── dummy.pxd
│   │   │   ├── dummy.pyx
│   │   │   ├── start.py
│   │   │   └── utils.py
│   │   ├── api_asset_price_delegate.pxd
│   │   ├── api_asset_price_delegate.pyx
│   │   ├── asset_price_delegate.pxd
│   │   ├── asset_price_delegate.pyx
│   │   ├── avellaneda_market_making
│   │   │   ├── __init__.py
│   │   │   ├── avellaneda_market_making_config_map_pydantic.py
│   │   │   ├── avellaneda_market_making.pxd
│   │   │   ├── avellaneda_market_making.pyx
│   │   │   └── start.py
│   │   ├── conditional_execution_state.py
│   │   ├── cross_exchange_market_making
│   │   │   ├── __init__.py
│   │   │   ├── cross_exchange_market_making_config_map_pydantic.py
│   │   │   ├── cross_exchange_market_making.py
│   │   │   ├── order_id_market_pair_tracker.pxd
│   │   │   ├── order_id_market_pair_tracker.pyx
│   │   │   └── start.py
│   │   ├── cross_exchange_mining
│   │   │   ├── __init__.py
│   │   │   ├── cross_exchange_mining_config_map_pydantic.py
│   │   │   ├── cross_exchange_mining_pair.py
│   │   │   ├── cross_exchange_mining.pxd
│   │   │   ├── cross_exchange_mining.pyx
│   │   │   ├── order_id_market_pair_tracker.pxd
│   │   │   ├── order_id_market_pair_tracker.pyx
│   │   │   └── start.py
│   │   ├── data_types.py
│   │   ├── directional_strategy_base.py
│   │   ├── hanging_orders_tracker.py
│   │   ├── hedge
│   │   │   ├── __init__.py
│   │   │   ├── hedge_config_map_pydantic.py
│   │   │   ├── hedge.py
│   │   │   └── start.py
│   │   ├── liquidity_mining
│   │   │   ├── __init__.py
│   │   │   ├── data_types.py
│   │   │   ├── dummy.pxd
│   │   │   ├── dummy.pyx
│   │   │   ├── liquidity_mining_config_map.py
│   │   │   ├── liquidity_mining.py
│   │   │   └── start.py
│   │   ├── maker_taker_market_pair.py
│   │   ├── market_trading_pair_tuple.py
│   │   ├── order_book_asset_price_delegate.pxd
│   │   ├── order_book_asset_price_delegate.pyx
│   │   ├── order_tracker.pxd
│   │   ├── order_tracker.pyx
│   │   ├── perpetual_market_making
│   │   │   ├── __init__.py
│   │   │   ├── data_types.py
│   │   │   ├── dummy.pxd
│   │   │   ├── dummy.pyx
│   │   │   ├── perpetual_market_making_config_map.py
│   │   │   ├── perpetual_market_making_order_tracker.py
│   │   │   ├── perpetual_market_making.py
│   │   │   └── start.py
│   │   ├── pure_market_making
│   │   │   ├── __init__.py
│   │   │   ├── data_types.py
│   │   │   ├── inventory_cost_price_delegate.py
│   │   │   ├── inventory_skew_calculator.pxd
│   │   │   ├── inventory_skew_calculator.pyx
│   │   │   ├── moving_price_band.py
│   │   │   ├── pure_market_making_config_map.py
│   │   │   ├── pure_market_making_order_tracker.pxd
│   │   │   ├── pure_market_making_order_tracker.pyx
│   │   │   ├── pure_market_making.pxd
│   │   │   ├── pure_market_making.pyx
│   │   │   └── start.py
│   │   ├── script_strategy_base.py
│   │   ├── spot_perpetual_arbitrage
│   │   │   ├── __init__.py
│   │   │   ├── arb_proposal.py
│   │   │   ├── dummy.pxd
│   │   │   ├── dummy.pyx
│   │   │   ├── spot_perpetual_arbitrage_config_map.py
│   │   │   ├── spot_perpetual_arbitrage.py
│   │   │   ├── start.py
│   │   │   └── utils.py
│   │   ├── strategy_base.pxd
│   │   ├── strategy_base.pyx
│   │   ├── strategy_py_base.pxd
│   │   ├── strategy_py_base.pyx
│   │   ├── strategy_v2_base.py
│   │   └── utils.py
│   ├── strategy_v2
│   │   ├── __init__.py
│   │   ├── backtesting
│   │   │   ├── __init__.py
│   │   │   ├── backtesting_data_provider.py
│   │   │   ├── backtesting_engine_base.py
│   │   │   ├── executor_simulator_base.py
│   │   │   └── executors_simulator
│   │   │       ├── __init__.py
│   │   │       ├── dca_executor_simulator.py
│   │   │       └── position_executor_simulator.py
│   │   ├── controllers
│   │   │   ├── __init__.py
│   │   │   ├── controller_base.py
│   │   │   ├── directional_trading_controller_base.py
│   │   │   └── market_making_controller_base.py
│   │   ├── executors
│   │   │   ├── __init__.py
│   │   │   ├── arbitrage_executor
│   │   │   │   ├── __init__.py
│   │   │   │   ├── arbitrage_executor.py
│   │   │   │   └── data_types.py
│   │   │   ├── data_types.py
│   │   │   ├── dca_executor
│   │   │   │   ├── __init__.py
│   │   │   │   ├── data_types.py
│   │   │   │   └── dca_executor.py
│   │   │   ├── executor_base.py
│   │   │   ├── executor_orchestrator.py
│   │   │   ├── grid_executor
│   │   │   │   ├── __init__.py
│   │   │   │   ├── data_types.py
│   │   │   │   └── grid_executor.py
│   │   │   ├── order_executor
│   │   │   │   ├── __init__.py
│   │   │   │   ├── data_types.py
│   │   │   │   └── order_executor.py
│   │   │   ├── position_executor
│   │   │   │   ├── __init__.py
│   │   │   │   ├── data_types.py
│   │   │   │   └── position_executor.py
│   │   │   ├── twap_executor
│   │   │   │   ├── __init__.py
│   │   │   │   ├── data_types.py
│   │   │   │   └── twap_executor.py
│   │   │   └── xemm_executor
│   │   │       ├── __init__.py
│   │   │       ├── data_types.py
│   │   │       └── xemm_executor.py
│   │   ├── models
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── executor_actions.py
│   │   │   ├── executors_info.py
│   │   │   ├── executors.py
│   │   │   └── position_config.py
│   │   ├── runnable_base.py
│   │   └── utils
│   │       ├── __init__.py
│   │       ├── common.py
│   │       ├── config_encoder_decoder.py
│   │       ├── distributions.py
│   │       └── order_level_builder.py
│   ├── templates
│   │   ├── conf_amm_arb_strategy_TEMPLATE.yml
│   │   ├── conf_amm_v3_lp_strategy_TEMPLATE.yml
│   │   ├── conf_fee_overrides_TEMPLATE.yml
│   │   ├── conf_liquidity_mining_strategy_TEMPLATE.yml
│   │   ├── conf_perpetual_market_making_strategy_TEMPLATE.yml
│   │   ├── conf_pure_market_making_strategy_TEMPLATE.yml
│   │   ├── conf_spot_perpetual_arbitrage_strategy_TEMPLATE.yml
│   │   └── hummingbot_logs_TEMPLATE.yml
│   ├── user
│   │   ├── __init__.py
│   │   └── user_balances.py
│   └── VERSION
├── install
├── LICENSE
├── logs
│   └── .gitignore
├── Makefile
├── pyproject.toml
├── README.md
├── scripts
│   ├── amm_data_feed_example.py
│   ├── amm_trade_example.py
│   ├── basic
│   │   ├── buy_only_three_times_example.py
│   │   ├── format_status_example.py
│   │   ├── log_price_example.py
│   │   └── simple_order_example.py
│   ├── community
│   │   ├── 1overN_portfolio.py
│   │   ├── adjusted_mid_price.py
│   │   ├── buy_dip_example.py
│   │   ├── buy_low_sell_high.py
│   │   ├── directional_strategy_bb_rsi_multi_timeframe.py
│   │   ├── directional_strategy_macd_bb.py
│   │   ├── directional_strategy_rsi_spot.py
│   │   ├── directional_strategy_trend_follower.py
│   │   ├── directional_strategy_widening_ema_bands.py
│   │   ├── fixed_grid.py
│   │   ├── macd_bb_directional_strategy.py
│   │   ├── pmm_with_shifted_mid_dynamic_spreads.py
│   │   ├── simple_arbitrage_example.py
│   │   ├── simple_pmm_no_config.py
│   │   ├── simple_rsi_no_config.py
│   │   ├── simple_vwap_no_config.py
│   │   ├── simple_xemm_no_config.py
│   │   ├── spot_perp_arb.py
│   │   └── triangular_arbitrage.py
│   ├── download_order_book_and_trades.py
│   ├── lp_manage_position.py
│   ├── simple_pmm.py
│   ├── simple_vwap.py
│   ├── simple_xemm.py
│   ├── utility
│   │   ├── backtest_mm_example.py
│   │   ├── batch_order_update_market_orders.py
│   │   ├── batch_order_update.py
│   │   ├── candles_example.py
│   │   ├── dca_example.py
│   │   ├── download_candles.py
│   │   ├── external_events_example.py
│   │   ├── liquidations_example.py
│   │   ├── microprice_calculator.py
│   │   ├── screener_volatility.py
│   │   ├── v2_pmm_single_level.py
│   │   └── wallet_hedge_example.py
│   ├── v2_directional_rsi.py
│   ├── v2_funding_rate_arb.py
│   ├── v2_twap_multiple_pairs.py
│   ├── v2_with_controllers.py
│   ├── xrpl_arb_example.py
│   └── xrpl_liquidity_example.py
├── setup
│   ├── environment_dydx.yml
│   ├── environment.yml
│   └── pip_packages.txt
├── setup.py
├── start
├── test
│   ├── __init__.py
│   ├── hummingbot
│   │   ├── __init__.py
│   │   ├── client
│   │   │   ├── __init__.py
│   │   │   ├── command
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_balance_command.py
│   │   │   │   ├── test_config_command.py
│   │   │   │   ├── test_connect_command.py
│   │   │   │   ├── test_create_command.py
│   │   │   │   ├── test_gateway_lp_command.py
│   │   │   │   ├── test_history_command.py
│   │   │   │   ├── test_import_command.py
│   │   │   │   ├── test_mqtt_command.py
│   │   │   │   ├── test_order_book_command.py
│   │   │   │   ├── test_rate_command.py
│   │   │   │   ├── test_status_command.py
│   │   │   │   └── test_ticker_command.py
│   │   │   ├── config
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_config_data_types.py
│   │   │   │   ├── test_config_helpers.py
│   │   │   │   ├── test_config_templates.py
│   │   │   │   ├── test_config_validators.py
│   │   │   │   ├── test_config_var.py
│   │   │   │   ├── test_security.py
│   │   │   │   ├── test_strategy_config_data_types.py
│   │   │   │   └── test_trade_fee_schema_loader.py
│   │   │   ├── test_connector_setting.py
│   │   │   ├── test_formatter.py
│   │   │   ├── test_hummingbot_application.py
│   │   │   ├── test_performance.py
│   │   │   ├── test_settings.py
│   │   │   └── ui
│   │   │       ├── __init__.py
│   │   │       ├── test_custom_widgets.py
│   │   │       ├── test_hummingbot_cli.py
│   │   │       ├── test_interface_utils.py
│   │   │       ├── test_layout.py
│   │   │       ├── test_login_prompt.py
│   │   │       ├── test_stdout_redirection.py
│   │   │       └── test_style.py
│   │   ├── connector
│   │   │   ├── __init__.py
│   │   │   ├── derivative
│   │   │   │   ├── __init__.py
│   │   │   │   ├── binance_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_binance_perpetual_api_order_book_data_source.py
│   │   │   │   │   ├── test_binance_perpetual_auth.py
│   │   │   │   │   ├── test_binance_perpetual_derivative.py
│   │   │   │   │   ├── test_binance_perpetual_user_stream_data_source.py
│   │   │   │   │   ├── test_binance_perpetual_utils.py
│   │   │   │   │   └── test_binance_perpetual_web_utils.py
│   │   │   │   ├── bitget_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_bitget_perpetual_auth.py
│   │   │   │   │   ├── test_bitget_perpetual_derivative.py
│   │   │   │   │   ├── test_bitget_perpetual_order_book_data_source.py
│   │   │   │   │   ├── test_bitget_perpetual_user_stream_data_source.py
│   │   │   │   │   └── test_bitget_perpetual_web_utils.py
│   │   │   │   ├── bitmart_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_bitmart_perpetual_api_order_book_data_source.py
│   │   │   │   │   ├── test_bitmart_perpetual_auth.py
│   │   │   │   │   ├── test_bitmart_perpetual_derivative.py
│   │   │   │   │   ├── test_bitmart_perpetual_user_stream_data_source.py
│   │   │   │   │   ├── test_bitmart_perpetual_utils.py
│   │   │   │   │   └── test_bitmart_perpetual_web_utils.py
│   │   │   │   ├── bybit_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_bybit_perpetual_api_order_book_data_source.py
│   │   │   │   │   ├── test_bybit_perpetual_auth.py
│   │   │   │   │   ├── test_bybit_perpetual_derivative.py
│   │   │   │   │   ├── test_bybit_perpetual_user_stream_data_source.py
│   │   │   │   │   ├── test_bybit_perpetual_utils.py
│   │   │   │   │   └── test_bybit_perpetual_web_utils.py
│   │   │   │   ├── derive_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_derive_perpetual_api_order_book_data_source.py
│   │   │   │   │   ├── test_derive_perpetual_api_user_stream_data_source.py
│   │   │   │   │   ├── test_derive_perpetual_auth.py
│   │   │   │   │   ├── test_derive_perpetual_derivative.py
│   │   │   │   │   ├── test_derive_perpetual_utils.py
│   │   │   │   │   └── test_derive_perpetual_web_utils.py
│   │   │   │   ├── dydx_v4_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── data_sources
│   │   │   │   │   │   ├── __init__.py
│   │   │   │   │   │   └── test_dydx_v4_data_source.py
│   │   │   │   │   ├── programmable_v4_client.py
│   │   │   │   │   ├── test_dydx_v4_perpetual_api_order_book_data_source.py
│   │   │   │   │   ├── test_dydx_v4_perpetual_derivative.py
│   │   │   │   │   ├── test_dydx_v4_perpetual_user_stream_data_source.py
│   │   │   │   │   ├── test_dydx_v4_perpetual_utils.py
│   │   │   │   │   └── test_dydx_v4_perpetual_web_utils.py
│   │   │   │   ├── gate_io_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_gate_io_perpetual_api_order_book_data_source.py
│   │   │   │   │   ├── test_gate_io_perpetual_auth.py
│   │   │   │   │   ├── test_gate_io_perpetual_derivative.py
│   │   │   │   │   ├── test_gate_io_perpetual_user_stream_data_source.py
│   │   │   │   │   ├── test_gate_io_perpetual_utils.py
│   │   │   │   │   └── test_gate_io_perpetual_web_utils.py
│   │   │   │   ├── hyperliquid_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_hyperliquid_perpetual_api_order_book_data_source.py
│   │   │   │   │   ├── test_hyperliquid_perpetual_auth.py
│   │   │   │   │   ├── test_hyperliquid_perpetual_derivative.py
│   │   │   │   │   ├── test_hyperliquid_perpetual_user_stream_data_source.py
│   │   │   │   │   ├── test_hyperliquid_perpetual_utils.py
│   │   │   │   │   └── test_hyperliquid_perpetual_web_utils.py
│   │   │   │   ├── injective_v2_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_injective_v2_perpetual_derivative_for_delegated_account.py
│   │   │   │   │   ├── test_injective_v2_perpetual_derivative_for_offchain_vault.py
│   │   │   │   │   ├── test_injective_v2_perpetual_order_book_data_source.py
│   │   │   │   │   └── test_injective_v2_perpetual_utils.py
│   │   │   │   ├── kucoin_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_kucoin_perpetual_api_order_book_data_source.py
│   │   │   │   │   ├── test_kucoin_perpetual_api_user_stream_data_source.py
│   │   │   │   │   ├── test_kucoin_perpetual_auth.py
│   │   │   │   │   ├── test_kucoin_perpetual_derivative.py
│   │   │   │   │   ├── test_kucoin_perpetual_utils.py
│   │   │   │   │   └── test_kucoin_perpetual_web_utils.py
│   │   │   │   ├── okx_perpetual
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_okx_perpetual_api_order_book_data_source.py
│   │   │   │   │   ├── test_okx_perpetual_auth.py
│   │   │   │   │   ├── test_okx_perpetual_derivative.py
│   │   │   │   │   ├── test_okx_perpetual_user_stream_data_source.py
│   │   │   │   │   ├── test_okx_perpetual_utils.py
│   │   │   │   │   └── test_okx_perpetual_web_utils.py
│   │   │   │   └── test_perpetual_budget_checker.py
│   │   │   ├── exchange
│   │   │   │   ├── __init__.py
│   │   │   │   ├── ascend_ex
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_ascend_ex_api_order_book_data_source.py
│   │   │   │   │   ├── test_ascend_ex_api_user_stream_datasource.py
│   │   │   │   │   ├── test_ascend_ex_auth.py
│   │   │   │   │   ├── test_ascend_ex_exchange.py
│   │   │   │   │   ├── test_ascend_ex_utils.py
│   │   │   │   │   └── test_ascend_ex_web_utils.py
│   │   │   │   ├── binance
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_binance_api_order_book_data_source.py
│   │   │   │   │   ├── test_binance_auth.py
│   │   │   │   │   ├── test_binance_exchange.py
│   │   │   │   │   ├── test_binance_order_book.py
│   │   │   │   │   ├── test_binance_user_stream_data_source.py
│   │   │   │   │   ├── test_binance_utils.py
│   │   │   │   │   └── test_binance_web_utils.py
│   │   │   │   ├── bing_x
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_bing_x_api_order_book_data_source.py
│   │   │   │   │   ├── test_bing_x_api_user_stream_data_source.py
│   │   │   │   │   ├── test_bing_x_auth.py
│   │   │   │   │   ├── test_bing_x_exchange.py
│   │   │   │   │   └── test_bing_x_web_utils.py
│   │   │   │   ├── bitmart
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_bitmart_api_order_book_data_source.py
│   │   │   │   │   ├── test_bitmart_api_user_stream_data_source.py
│   │   │   │   │   └── test_bitmart_exchange.py
│   │   │   │   ├── bitrue
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_bitrue_api_order_book_data_source.py
│   │   │   │   │   ├── test_bitrue_auth.py
│   │   │   │   │   ├── test_bitrue_exchange.py
│   │   │   │   │   ├── test_bitrue_user_stream_data_source.py
│   │   │   │   │   ├── test_bitrue_utils.py
│   │   │   │   │   └── test_bitrue_web_utils.py
│   │   │   │   ├── bitstamp
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_bitstamp_api_order_book_data_source.py
│   │   │   │   │   ├── test_bitstamp_api_user_stream_data_source.py
│   │   │   │   │   ├── test_bitstamp_auth.py
│   │   │   │   │   ├── test_bitstamp_exchange.py
│   │   │   │   │   ├── test_bitstamp_order_book.py
│   │   │   │   │   ├── test_bitstamp_utils.py
│   │   │   │   │   └── test_bitstamp_web_utils.py
│   │   │   │   ├── btc_markets
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_btc_markets_api_order_book_data_source.py
│   │   │   │   │   ├── test_btc_markets_api_user_stream_data_source.py
│   │   │   │   │   ├── test_btc_markets_auth.py
│   │   │   │   │   ├── test_btc_markets_exchange.py
│   │   │   │   │   ├── test_btc_markets_order_book.py
│   │   │   │   │   ├── test_btc_markets_utils.py
│   │   │   │   │   └── test_btc_markets_web_utils.py
│   │   │   │   ├── bybit
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_bybit_api_order_book_data_source.py
│   │   │   │   │   ├── test_bybit_api_user_stream_data_source.py
│   │   │   │   │   ├── test_bybit_auth.py
│   │   │   │   │   ├── test_bybit_exchange.py
│   │   │   │   │   └── test_bybit_web_utils.py
│   │   │   │   ├── coinbase_advanced_trade
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_coinbase_advanced_trade_api_order_book_data_source.py
│   │   │   │   │   ├── test_coinbase_advanced_trade_api_user_stream_data_source.py
│   │   │   │   │   ├── test_coinbase_advanced_trade_auth.py
│   │   │   │   │   ├── test_coinbase_advanced_trade_exchange.py
│   │   │   │   │   ├── test_coinbase_advanced_trade_order_book.py
│   │   │   │   │   ├── test_coinbase_advanced_trade_utils.py
│   │   │   │   │   └── test_coinbase_advanced_trade_web_utils.py
│   │   │   │   ├── cube
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_cube_api_order_book_data_source.py
│   │   │   │   │   ├── test_cube_api_user_stream_data_source.py
│   │   │   │   │   ├── test_cube_auth.py
│   │   │   │   │   ├── test_cube_exchange.py
│   │   │   │   │   ├── test_cube_order_book.py
│   │   │   │   │   ├── test_cube_types.py
│   │   │   │   │   ├── test_cube_utils.py
│   │   │   │   │   └── test_cube_web_utils.py
│   │   │   │   ├── derive
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_derive_api_order_book_data_source.py
│   │   │   │   │   ├── test_derive_api_user_stream_data_source.py
│   │   │   │   │   ├── test_derive_auth.py
│   │   │   │   │   ├── test_derive_exchange.py
│   │   │   │   │   ├── test_derive_utils.py
│   │   │   │   │   └── test_derive_web_utils.py
│   │   │   │   ├── dexalot
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── data_sources
│   │   │   │   │   │   ├── __init__.py
│   │   │   │   │   │   └── test_dexalot_data_source.py
│   │   │   │   │   ├── programmable_client.py
│   │   │   │   │   ├── test_dexalot_api_order_book_data_source.py
│   │   │   │   │   ├── test_dexalot_auth.py
│   │   │   │   │   ├── test_dexalot_exchange.py
│   │   │   │   │   ├── test_dexalot_user_stream_data_source.py
│   │   │   │   │   ├── test_dexalot_utils.py
│   │   │   │   │   └── test_dexalot_web_utils.py
│   │   │   │   ├── foxbit
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_foxbit_api_order_book_data_source.py
│   │   │   │   │   ├── test_foxbit_auth.py
│   │   │   │   │   ├── test_foxbit_exchange.py
│   │   │   │   │   ├── test_foxbit_order_book.py
│   │   │   │   │   ├── test_foxbit_user_stream_data_source.py
│   │   │   │   │   ├── test_foxbit_utils.py
│   │   │   │   │   └── test_foxbit_web_utils.py
│   │   │   │   ├── gate_io
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_gate_io_api_order_book_data_source.py
│   │   │   │   │   ├── test_gate_io_api_user_stream_data_source.py
│   │   │   │   │   └── test_gate_io_exchange.py
│   │   │   │   ├── htx
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_htx_api_order_book_data_source.py
│   │   │   │   │   ├── test_htx_api_user_stream_data_source.py
│   │   │   │   │   ├── test_htx_auth.py
│   │   │   │   │   ├── test_htx_exchange.py
│   │   │   │   │   ├── test_htx_utility_functions.py
│   │   │   │   │   └── test_htx_ws_post_processor.py
│   │   │   │   ├── hyperliquid
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_hyperliquid_api_order_book_data_source.py
│   │   │   │   │   ├── test_hyperliquid_auth.py
│   │   │   │   │   ├── test_hyperliquid_exchange.py
│   │   │   │   │   ├── test_hyperliquid_order_book.py
│   │   │   │   │   ├── test_hyperliquid_user_stream_data_source.py
│   │   │   │   │   ├── test_hyperliquid_utils.py
│   │   │   │   │   └── test_hyperliquid_web_utils.py
│   │   │   │   ├── injective_v2
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── data_sources
│   │   │   │   │   │   ├── __init__.py
│   │   │   │   │   │   └── test_injective_data_source.py
│   │   │   │   │   ├── programmable_query_executor.py
│   │   │   │   │   ├── test_injective_market.py
│   │   │   │   │   ├── test_injective_v2_api_order_book_data_source.py
│   │   │   │   │   ├── test_injective_v2_exchange_for_delegated_account.py
│   │   │   │   │   ├── test_injective_v2_exchange_for_offchain_vault.py
│   │   │   │   │   └── test_injective_v2_utils.py
│   │   │   │   ├── kraken
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_kraken_api_order_book_data_source.py
│   │   │   │   │   ├── test_kraken_api_user_stream_data_source.py
│   │   │   │   │   ├── test_kraken_auth.py
│   │   │   │   │   ├── test_kraken_exchange.py
│   │   │   │   │   ├── test_kraken_order_book.py
│   │   │   │   │   ├── test_kraken_utils.py
│   │   │   │   │   └── test_kraken_web_utils.py
│   │   │   │   ├── kucoin
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_kucoin_api_order_book_data_source.py
│   │   │   │   │   ├── test_kucoin_api_user_stream_data_source.py
│   │   │   │   │   ├── test_kucoin_auth.py
│   │   │   │   │   └── test_kucoin_exchange.py
│   │   │   │   ├── mexc
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_mexc_api_order_book_data_source.py
│   │   │   │   │   ├── test_mexc_auth.py
│   │   │   │   │   ├── test_mexc_exchange.py
│   │   │   │   │   ├── test_mexc_order_book.py
│   │   │   │   │   ├── test_mexc_user_stream_data_source.py
│   │   │   │   │   ├── test_mexc_utils.py
│   │   │   │   │   └── test_mexc_web_utils.py
│   │   │   │   ├── ndax
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_ndax_api_order_book_data_source.py
│   │   │   │   │   ├── test_ndax_api_user_stream_data_source.py
│   │   │   │   │   ├── test_ndax_auth.py
│   │   │   │   │   ├── test_ndax_exchange.py
│   │   │   │   │   ├── test_ndax_order_book_message.py
│   │   │   │   │   └── test_ndax_utils.py
│   │   │   │   ├── okx
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_okx_api_order_book_data_source.py
│   │   │   │   │   ├── test_okx_auth.py
│   │   │   │   │   ├── test_okx_exchange.py
│   │   │   │   │   └── test_okx_user_stream_data_source.py
│   │   │   │   ├── paper_trade
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_paper_trade_exchange.py
│   │   │   │   ├── test_dummy_test_to_trigger_pycharm_menu.py
│   │   │   │   ├── vertex
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_vertex_api_order_book_data_source.py
│   │   │   │   │   ├── test_vertex_api_user_stream_data_source.py
│   │   │   │   │   ├── test_vertex_auth.py
│   │   │   │   │   ├── test_vertex_exchange.py
│   │   │   │   │   ├── test_vertex_order_book.py
│   │   │   │   │   ├── test_vertex_utils.py
│   │   │   │   │   └── test_vertex_web_utils.py
│   │   │   │   └── xrpl
│   │   │   │       ├── __init__.py
│   │   │   │       ├── test_xrpl_amm.py
│   │   │   │       ├── test_xrpl_api_order_book_data_source.py
│   │   │   │       ├── test_xrpl_api_user_stream_data_source.py
│   │   │   │       ├── test_xrpl_auth.py
│   │   │   │       ├── test_xrpl_exchange.py
│   │   │   │       ├── test_xrpl_order_book.py
│   │   │   │       ├── test_xrpl_order_placement_strategy.py
│   │   │   │       ├── test_xrpl_submit_transaction.py
│   │   │   │       └── test_xrpl_utils.py
│   │   │   ├── gateway
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_command_utils_lp.py
│   │   │   │   ├── test_gateway_in_flight_order.py
│   │   │   │   ├── test_gateway_lp.py
│   │   │   │   └── test_gateway_order_tracker.py
│   │   │   ├── other
│   │   │   │   ├── __init__.py
│   │   │   │   └── test_derive_common_utils.py
│   │   │   ├── test_budget_checker.py
│   │   │   ├── test_client_order_tracker.py
│   │   │   ├── test_connector_base.py
│   │   │   ├── test_connector_metrics_collector.py
│   │   │   ├── test_markets_recorder.py
│   │   │   ├── test_parrot.py
│   │   │   ├── test_perpetual_trading.py
│   │   │   ├── test_time_synchronizer.py
│   │   │   ├── test_utils.py
│   │   │   └── utilities
│   │   │       ├── __init__.py
│   │   │       └── oms_connector
│   │   │           ├── __init__.py
│   │   │           ├── test_oms_connector_api_order_book_data_source.py
│   │   │           ├── test_oms_connector_api_user_stream_data_source.py
│   │   │           ├── test_oms_connector_auth.py
│   │   │           └── test_oms_connector_web_utils.py
│   │   ├── core
│   │   │   ├── __init__.py
│   │   │   ├── api_throttler
│   │   │   │   ├── __init__.py
│   │   │   │   └── test_async_throttler.py
│   │   │   ├── data_type
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_common.py
│   │   │   │   ├── test_in_flight_order.py
│   │   │   │   ├── test_limit_order.py
│   │   │   │   ├── test_order_book_message.py
│   │   │   │   ├── test_order_book.py
│   │   │   │   ├── test_trade_fee.py
│   │   │   │   ├── test_user_stream_tracker_data_source.py
│   │   │   │   └── test_user_stream_tracker.py
│   │   │   ├── rate_oracle
│   │   │   │   ├── __init__.py
│   │   │   │   ├── sources
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── test_ascend_ex_rate_source.py
│   │   │   │   │   ├── test_binance_rate_source.py
│   │   │   │   │   ├── test_binance_us_rate_source.py
│   │   │   │   │   ├── test_coin_cap_rate_source.py
│   │   │   │   │   ├── test_coin_gecko_rate_source.py
│   │   │   │   │   ├── test_coinbase_advanced_trade_rate_source.py
│   │   │   │   │   ├── test_cube_rate_source.py
│   │   │   │   │   ├── test_derive_rate_source.py
│   │   │   │   │   ├── test_dexalot_rate_source.py
│   │   │   │   │   ├── test_gate_io_rate_source.py
│   │   │   │   │   ├── test_hyperliquid_rate_source.py
│   │   │   │   │   ├── test_kucoin_rate_source.py
│   │   │   │   │   └── test_mexc_rate_source.py
│   │   │   │   └── test_rate_oracle.py
│   │   │   ├── test_clock.py
│   │   │   ├── test_connector_manager.py
│   │   │   ├── test_events.py
│   │   │   ├── test_network_base.py
│   │   │   ├── test_network_iterator.py
│   │   │   ├── test_pubsub.py
│   │   │   ├── test_py_time_iterator.py
│   │   │   ├── test_time_iterator.py
│   │   │   ├── test_trading_core.py
│   │   │   ├── utils
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_async_retry.py
│   │   │   │   ├── test_async_ttl_cache.py
│   │   │   │   ├── test_estimate_fee.py
│   │   │   │   ├── test_fixed_rate_source.py
│   │   │   │   ├── test_gateway_config_utils.py
│   │   │   │   ├── test_map_df_to_str.py
│   │   │   │   ├── test_market_price.py
│   │   │   │   ├── test_nonce_creator.py
│   │   │   │   ├── test_ssl_cert.py
│   │   │   │   ├── test_tracking_nonce.py
│   │   │   │   └── test_trading_pair_fetcher.py
│   │   │   └── web_assistant
│   │   │       ├── __init__.py
│   │   │       ├── connections
│   │   │       │   ├── __init__.py
│   │   │       │   ├── test_connections_factory.py
│   │   │       │   ├── test_data_types.py
│   │   │       │   ├── test_rest_connection.py
│   │   │       │   └── test_ws_connection.py
│   │   │       ├── test_rest_assistant.py
│   │   │       ├── test_web_assistants_factory.py
│   │   │       └── test_ws_assistant.py
│   │   ├── data_feed
│   │   │   ├── __init__.py
│   │   │   ├── candles_feed
│   │   │   │   ├── __init__.py
│   │   │   │   ├── ascend_ex_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_ascend_ex_spot_candles.py
│   │   │   │   ├── binance_perpetual_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_binance_perpetual_candles.py
│   │   │   │   ├── binance_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_binance_spot_candles.py
│   │   │   │   ├── bitmart_perpetual_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_bitmart_perpetual_candles.py
│   │   │   │   ├── btc_markets_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_btc_markets_spot_candles.py
│   │   │   │   ├── bybit_perpetual_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_bybit_perpetual_candles.py
│   │   │   │   ├── bybit_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_bybit_spot_candles.py
│   │   │   │   ├── dexalot_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_dexalot_spot_candles.py
│   │   │   │   ├── gate_io_perpetual_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_gate_io_perpetual_candles.py
│   │   │   │   ├── gate_io_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_gate_io_spot_candles.py
│   │   │   │   ├── hyperliquid_perpetual_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_hyperliquid_perpetual_candles.py
│   │   │   │   ├── hyperliquid_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_hyperliquid_spot_candles.py
│   │   │   │   ├── kraken_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_kraken_spot_candles.py
│   │   │   │   ├── kucoin_perpetual_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_kucoin_perpetual_candles.py
│   │   │   │   ├── kucoin_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_kucoin_spot_candles.py
│   │   │   │   ├── mexc_perpetual_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_mexc_perpetual_candles.py
│   │   │   │   ├── mexc_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_mexc_spot_candles.py
│   │   │   │   ├── okx_perpetual_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_okx_perpetual_candles.py
│   │   │   │   ├── okx_spot_candles
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_okx_spot_candles.py
│   │   │   │   ├── test_candles_base.py
│   │   │   │   └── test_candles_factory.py
│   │   │   ├── liquidations_feed
│   │   │   │   ├── __init__.py
│   │   │   │   ├── binance
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   └── test_binance_liquidations.py
│   │   │   │   └── test_liquidations_factory.py
│   │   │   ├── test_amm_gateway_data_feed.py
│   │   │   ├── test_coin_gecko_data_feed.py
│   │   │   ├── test_market_data_provider.py
│   │   │   └── test_wallet_tracker_data_feed.py
│   │   ├── logger
│   │   │   ├── __init__.py
│   │   │   └── test_logger_util_functions.py
│   │   ├── model
│   │   │   ├── __init__.py
│   │   │   ├── db_migration
│   │   │   │   ├── __init__.py
│   │   │   │   └── test_transformations.py
│   │   │   └── test_trade_fill.py
│   │   ├── notifier
│   │   │   ├── __init__.py
│   │   │   └── test_notifier_base.py
│   │   ├── remote_iface
│   │   │   ├── __init__.py
│   │   │   └── test_mqtt.py
│   │   ├── strategy
│   │   │   ├── __init__.py
│   │   │   ├── amm_arb
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_amm_arb_start.py
│   │   │   │   ├── test_data_types.py
│   │   │   │   └── test_utils.py
│   │   │   ├── avellaneda_market_making
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_avellaneda_market_making_config_map_pydantic.py
│   │   │   │   ├── test_avellaneda_market_making_start.py
│   │   │   │   ├── test_avellaneda_market_making.py
│   │   │   │   └── test_config.yml
│   │   │   ├── cross_exchange_market_making
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_config.yml
│   │   │   │   ├── test_cross_exchange_market_making_config_map_pydantic.py
│   │   │   │   ├── test_cross_exchange_market_making_start.py
│   │   │   │   └── test_cross_exchange_market_making.py
│   │   │   ├── hedge
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_config.yml
│   │   │   │   ├── test_hedge_config_map.py
│   │   │   │   ├── test_hedge_start.py
│   │   │   │   └── test_hedge.py
│   │   │   ├── liquidity_mining
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_liquidity_mining_config_map.py
│   │   │   │   ├── test_liquidity_mining_start.py
│   │   │   │   └── test_liquidity_mining.py
│   │   │   ├── perpetual_market_making
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_perpetual_market_making_config_map.py
│   │   │   │   ├── test_perpetual_market_making_start.py
│   │   │   │   └── test_perpetual_market_making.py
│   │   │   ├── pure_market_making
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_inventory_cost_price_delegate.py
│   │   │   │   ├── test_inventory_skew_calculator.py
│   │   │   │   ├── test_moving_price_band.py
│   │   │   │   ├── test_pmm_config_map.py
│   │   │   │   ├── test_pmm_ping_pong.py
│   │   │   │   ├── test_pmm_refresh_tolerance.py
│   │   │   │   ├── test_pmm_take_if_cross.py
│   │   │   │   ├── test_pmm.py
│   │   │   │   └── test_pure_market_making_start.py
│   │   │   ├── spot_perpetual_arbitrage
│   │   │   │   ├── __init__.py
│   │   │   │   ├── test_arb_proposal.py
│   │   │   │   ├── test_spot_perpetual_arbitrage_config_map.py
│   │   │   │   ├── test_spot_perpetual_arbitrage_start.py
│   │   │   │   └── test_spot_perpetual_arbitrage.py
│   │   │   ├── test_conditional_execution_state.py
│   │   │   ├── test_directional_strategy_base.py
│   │   │   ├── test_hanging_orders_tracker.py
│   │   │   ├── test_market_trading_pair_tuple.py
│   │   │   ├── test_order_tracker.py
│   │   │   ├── test_script_strategy_base.py
│   │   │   ├── test_strategy_base.py
│   │   │   ├── test_strategy_py_base.py
│   │   │   ├── test_strategy_v2_base.py
│   │   │   └── utils
│   │   │       ├── __init__.py
│   │   │       ├── test_ring_buffer.py
│   │   │       ├── test_utils.py
│   │   │       └── trailing_indicators
│   │   │           ├── __init__.py
│   │   │           ├── test_historical_volatility.py
│   │   │           ├── test_instant_volatility.py
│   │   │           └── test_trading_intensity.py
│   │   └── strategy_v2
│   │       ├── __init__.py
│   │       ├── controllers
│   │       │   ├── __init__.py
│   │       │   ├── test_controller_base.py
│   │       │   ├── test_directional_trading_controller_base.py
│   │       │   └── test_market_making_controller_base.py
│   │       ├── executors
│   │       │   ├── __init__.py
│   │       │   ├── arbitrage_executor
│   │       │   │   ├── __init__.py
│   │       │   │   └── test_arbitrage_executor.py
│   │       │   ├── dca_executor
│   │       │   │   ├── __init__.py
│   │       │   │   └── test_dca_executor.py
│   │       │   ├── grid_executor
│   │       │   │   ├── __init__.py
│   │       │   │   └── test_grid_executor.py
│   │       │   ├── order_executor
│   │       │   │   ├── __init__.py
│   │       │   │   └── test_order_executor.py
│   │       │   ├── position_executor
│   │       │   │   ├── __init__.py
│   │       │   │   ├── test_data_types.py
│   │       │   │   └── test_position_executor.py
│   │       │   ├── test_executor_base.py
│   │       │   ├── test_executor_orchestrator.py
│   │       │   ├── twap_executor
│   │       │   │   ├── __init__.py
│   │       │   │   └── test_twap_executor.py
│   │       │   └── xemm_executor
│   │       │       ├── __init__.py
│   │       │       └── test_xemm_executor.py
│   │       ├── test_runnable_base.py
│   │       └── utils
│   │           ├── __init__.py
│   │           ├── test_distributions.py
│   │           └── test_order_level_builder.py
│   ├── isolated_asyncio_wrapper_test_case.py
│   ├── logger_mixin_for_test.py
│   ├── mock
│   │   ├── __init__.py
│   │   ├── http_recorder.py
│   │   ├── mock_api_order_book_data_source.py
│   │   ├── mock_asset_price_delegate.py
│   │   ├── mock_cli.py
│   │   ├── mock_events.py
│   │   ├── mock_mqtt_server.py
│   │   └── mock_perp_connector.py
│   ├── test_isolated_asyncio_wrapper_test_case.py
│   ├── test_local_class_event_loop_wrapper_test_case.py
│   ├── test_local_test_event_loop_wrapper_test_case.py
│   └── test_logger_mixin_for_test.py
└── uninstall
```