"""
Modular Trading Bot - Supports multiple exchanges
"""

import os
import time
import asyncio
import traceback
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Dict, Any

from exchange_clients.factory import ExchangeFactory
from helpers.unified_logger import get_logger
from helpers.lark_bot import LarkBot
from helpers.telegram_bot import TelegramBot
from strategies import StrategyFactory
from networking import ProxySelector, SessionProxyManager
from helpers.networking import ProxyHealthMonitor, detect_egress_ip


@dataclass
class TradingConfig:
    """Configuration class for trading parameters."""
    # Universal parameters
    ticker: str
    contract_id: str
    quantity: Decimal
    tick_size: Decimal
    exchange: str
    strategy: str
    order_notional_usd: Optional[Decimal] = None
    target_leverage: Optional[Decimal] = None
    
    # Strategy-specific parameters
    strategy_params: Dict[str, Any] = None

    def __post_init__(self):
        """Post-initialization to handle strategy parameters."""
        if self.strategy_params is None:
            self.strategy_params = {}

class TradingBot:
    """Modular Trading Bot - Main trading logic supporting multiple exchanges."""

    def __init__(
        self,
        config: TradingConfig,
        account_credentials: Optional[Dict[str, Dict[str, Any]]] = None,
        proxy_selector: Optional[ProxySelector] = None,
    ):
        """
        Initialize Trading Bot.
        
        Args:
            config: Trading configuration
            account_credentials: Optional credentials dict mapping exchange names to credentials.
                               If provided, credentials will be used instead of environment variables.
            proxy_selector: Optional proxy selector derived from account assignments.
        """
        self.config = config
        self.account_credentials = account_credentials
        self.proxy_selector = proxy_selector
        self.logger = get_logger("bot", config.strategy, context={"exchange": config.exchange, "ticker": config.ticker}, log_to_console=True)
        self._proxy_health_monitor: Optional[ProxyHealthMonitor] = None
        self._proxy_rotation_attempts = 0
        self._control_server_task: Optional[asyncio.Task] = None
        self._control_server: Optional[Any] = None  # Store uvicorn.Server instance for manual shutdown
        self._control_server_enabled = os.getenv("CONTROL_API_ENABLED", "false").lower() in ("true", "1", "yes")

        # Log account info if credentials provided
        if account_credentials:
            account_name = config.strategy_params.get('_account_name', 'unknown')
            self.logger.info(f"Using database credentials for account: {account_name}")
            self.logger.info(f"Available exchanges: {list(account_credentials.keys())}")

        if proxy_selector:
            if SessionProxyManager.is_active():
                active_display = SessionProxyManager.describe(mask_password=True)
                assignment = proxy_selector.current_assignment()
                if not active_display and assignment:
                    active_display = assignment.proxy.masked_label()
                if active_display:
                    self.logger.info(f"Session proxy active: {active_display}")
            else:
                self.logger.warning("Proxy assignments loaded but session proxy is disabled")

        # Determine if strategy needs multiple exchanges
        multi_exchange_strategies = ['funding_arbitrage']
        is_multi_exchange = config.strategy in multi_exchange_strategies
        
        # Create exchange client(s)
        try:
            if is_multi_exchange:
                # Multi-exchange mode (for funding arbitrage, etc.)
                # Get list of exchanges from strategy params
                raw_exchange_list = config.strategy_params.get('scan_exchanges')

                if isinstance(raw_exchange_list, str):
                    exchange_list = [ex.strip().lower() for ex in raw_exchange_list.split(',') if ex.strip()]
                elif raw_exchange_list:
                    exchange_list = [str(ex).strip().lower() for ex in raw_exchange_list if str(ex).strip()]
                else:
                    exchange_list = []

                mandatory_exchange = (
                    config.strategy_params.get('mandatory_exchange')
                    or config.strategy_params.get('primary_exchange')
                )
                if isinstance(mandatory_exchange, str):
                    mandatory_exchange = mandatory_exchange.strip().lower() or None
                else:
                    mandatory_exchange = None

                if not exchange_list:
                    raise ValueError(
                        "Funding arbitrage requires at least one exchange in 'scan_exchanges'."
                    )

                if mandatory_exchange and mandatory_exchange not in exchange_list:
                    exchange_list.append(mandatory_exchange)

                self.logger.info(f"Creating clients for exchanges: {exchange_list}")

                # Create multiple exchange clients (with or without credentials)
                self.exchange_clients = ExchangeFactory.create_multiple_exchanges(
                    exchange_names=exchange_list,
                    config=config,
                    primary_exchange=mandatory_exchange,
                    account_credentials=account_credentials,  # Pass credentials to factory
                )

                if not self.exchange_clients:
                    raise ValueError("Failed to instantiate any exchange clients.")

                if proxy_selector:
                    for client in self.exchange_clients.values():
                        setattr(client, "proxy_selector", proxy_selector)

                # Set a representative exchange client for backward compatibility
                self.exchange_client = next(iter(self.exchange_clients.values()))

                self.logger.info(
                    f"Created {len(self.exchange_clients)} exchange clients: {list(self.exchange_clients.keys())}"
                )
            else:
                # Single exchange mode (for grid strategy, etc.)
                # Get credentials for this specific exchange
                exchange_creds = None
                if account_credentials and config.exchange in account_credentials:
                    exchange_creds = account_credentials[config.exchange]

                self.exchange_client = ExchangeFactory.create_exchange(
                    config.exchange,
                    config,
                    exchange_creds,  # Pass credentials to factory
                )
                self.exchange_clients = None  # Not used for single-exchange strategies

                if proxy_selector and self.exchange_client:
                    setattr(self.exchange_client, "proxy_selector", proxy_selector)

                if hasattr(self.exchange_client, "order_fill_callback"):
                    self.exchange_client.order_fill_callback = self._handle_order_fill
                
        except ValueError as e:
            raise ValueError(f"Failed to create exchange client: {e}")

        # Trading state
        self.last_log_time = 0
        self.shutdown_requested = False
        self._force_shutdown = False  # Flag for immediate shutdown (double CTRL+C)
        self.loop = None
        self._last_confirmed_proxy_ip: Optional[str] = self.config.strategy_params.get("_proxy_egress_ip")

        # Initialize strategy
        try:
            if is_multi_exchange:
                # Pass all exchange clients to multi-exchange strategies
                self.strategy = StrategyFactory.create_strategy(
                    self.config.strategy,
                    self.config,
                    exchange_client=self.exchange_client,  # Primary for backward compat
                    exchange_clients=self.exchange_clients  # All clients
                )
            else:
                # Pass single exchange client to single-exchange strategies
                self.strategy = StrategyFactory.create_strategy(
                    self.config.strategy,
                    self.config,
                    self.exchange_client
                )
            self.logger.info(f"Strategy '{self.config.strategy}' created successfully")
        except ValueError as e:
            raise ValueError(f"Failed to create strategy: {e}")
        
    async def _setup_contract_attributes(self):
        """Setup contract attributes based on strategy type."""
        multi_symbol_strategies = ['funding_arbitrage']
        
        if self.config.strategy not in multi_symbol_strategies:
            self.config.contract_id, self.config.tick_size = await self.exchange_client.get_contract_attributes()
        else:
            # Multi-symbol strategy - set placeholder values
            self.config.contract_id = "MULTI_SYMBOL"
            self.config.tick_size = Decimal("0.01")  # Placeholder
            self.logger.info("Multi-symbol strategy: Contract attributes will be fetched per-symbol")

    def _log_configuration(self):
        """Log the current trading configuration."""
        multi_symbol_strategies = ['funding_arbitrage']
        
        # Sensitive keys that should not be logged
        SENSITIVE_KEYS = {
            '_account_credentials',
            'secret_key',
            'api_key',
            'private_key',
            'public_key',
            'l2_private_key_hex',
            'l1_address',
            'l2_address',
        }
        
        def _is_sensitive_key(key: str) -> bool:
            """Check if a key or any part of it contains sensitive information."""
            key_lower = key.lower()
            return any(sensitive in key_lower for sensitive in SENSITIVE_KEYS)
        
        self.logger.info("=== Trading Configuration ===")
        self.logger.info(f"Ticker: {self.config.ticker}")
        if self.config.strategy not in multi_symbol_strategies:
            self.logger.info(f"Contract ID: {self.config.contract_id}")
        self.logger.info(f"Quantity: {self.config.quantity}")
        if getattr(self.config, "order_notional_usd", None) is not None:
            self.logger.info(f"Order Notional (USD): {self.config.order_notional_usd}")
        if getattr(self.config, "target_leverage", None) is not None:
            self.logger.info(f"Target Leverage: {self.config.target_leverage}x")
        self.logger.info(f"Exchange: {self.config.exchange}")
        self.logger.info(f"Strategy: {self.config.strategy}")
        
        # Log strategy parameters (excluding sensitive credentials)
        if self.config.strategy_params:
            self.logger.info("Strategy Parameters:")
            for key, value in self.config.strategy_params.items():
                if _is_sensitive_key(key):
                    self.logger.info(f"  {key}: [REDACTED]")
                else:
                    self.logger.info(f"  {key}: {value}")
            
        self.logger.info("=============================")

    async def _connect_exchanges(self):
        """Connect to exchange(s) based on mode."""
        if self.exchange_clients:
            # Multi-exchange mode: connect all clients
            for exchange_name, client in self.exchange_clients.items():
                self.logger.info(f"Connecting to {exchange_name}...")
                await client.connect()
                self.logger.info(f"Connected to {exchange_name}")
        else:
            # Single exchange mode
            await self.exchange_client.connect()

    async def _run_trading_loop(self):
        """Execute the main trading loop."""
        while not self.shutdown_requested:
            try:
                if await self.strategy.should_execute():
                    if self.shutdown_requested:
                        break
                    await self.strategy.execute_strategy()
                else:
                    await asyncio.sleep(0.5)
                    if self.shutdown_requested:
                        break
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Strategy execution error: {e}")
                if self.shutdown_requested:
                    break
                await asyncio.sleep(5)  # Wait longer on error

    async def _handle_order_fill(
        self,
        order_id: str,
        price: Decimal,
        quantity: Decimal,
        sequence: Optional[int],
    ) -> None:
        """Relay exchange fill notifications to the active strategy."""
        try:
            if hasattr(self.strategy, "notify_order_filled"):
                price_dec = price if isinstance(price, Decimal) else Decimal(str(price))
                qty_dec = quantity if isinstance(quantity, Decimal) else Decimal(str(quantity))
                self.strategy.notify_order_filled(price_dec, qty_dec, order_id=order_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.error(f"Failed to process order fill callback for {order_id}: {exc}")

    async def graceful_shutdown(self, reason: str = "Unknown"):
        """Perform graceful shutdown of the trading bot."""
        # Check for force shutdown flag
        if self._force_shutdown:
            self.logger.warning("⚠️ Force shutdown requested - skipping graceful cleanup")
            return
        
        self.logger.info(f"🛑 Graceful shutdown initiated: {reason}")
        self.shutdown_requested = True

        # Stop proxy health monitor with timeout
        if self._proxy_health_monitor:
            try:
                await asyncio.wait_for(self._proxy_health_monitor.stop(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass  # Non-critical, continue shutdown
            self._proxy_health_monitor = None

        try:
            # Cleanup strategy with timeout
            if hasattr(self, 'strategy') and self.strategy:
                try:
                    await asyncio.wait_for(self.strategy.cleanup(), timeout=30.0)
                except asyncio.TimeoutError:
                    self.logger.warning("⚠️ Strategy cleanup timed out")
                except Exception as e:
                    self.logger.error(f"❌ Strategy cleanup error: {e}")
            
            # Stop control server if running
            if self._control_server_task:
                try:
                    await asyncio.wait_for(self._stop_control_server(), timeout=5.0)
                except (asyncio.TimeoutError, Exception):
                    if not self._control_server_task.done():
                        self._control_server_task.cancel()
            
            # Disconnect from exchange(s)
            disconnected = []
            if hasattr(self, 'exchange_clients') and self.exchange_clients:
                # Multi-exchange mode: disconnect all clients
                for exchange_name, client in self.exchange_clients.items():
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=10.0)
                        disconnected.append(exchange_name)
                    except (asyncio.TimeoutError, Exception):
                        pass
            elif hasattr(self, 'exchange_client') and self.exchange_client:
                # Single exchange mode
                try:
                    await asyncio.wait_for(self.exchange_client.disconnect(), timeout=10.0)
                    disconnected.append(self.config.exchange)
                except (asyncio.TimeoutError, Exception):
                    pass
            
            if disconnected:
                self.logger.info(f"✅ Disconnected from: {', '.join(disconnected)}")
            self.logger.info("✅ Shutdown complete")

        except Exception as e:
            self.logger.error(f"Error during graceful shutdown: {e}")

    def _is_port_in_use(self, host: str, port: int) -> bool:
        """
        Check if a port is already in use.
        
        Args:
            host: Host address to check
            port: Port number to check
            
        Returns:
            True if port is in use, False otherwise
        """
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                result = sock.connect_ex((host, port))
                return result == 0
        except Exception:
            # If we can't check, assume port is available (let uvicorn handle the error)
            return False

    async def _start_control_server(self):
        """Start the control API server."""
        try:
            # Debug logging
            self.logger.info(f"🔧 _start_control_server called: strategy={self.config.strategy}, CONTROL_API_ENABLED={os.getenv('CONTROL_API_ENABLED', 'not set')}")
            
            from strategies.control.server import app, set_strategy_controller
            from strategies.control.funding_arb_controller import FundingArbStrategyController
            import uvicorn
            
            # Only start for funding arbitrage strategy for now
            if self.config.strategy != "funding_arbitrage":
                self.logger.info(f"Control API only supports funding_arbitrage strategy (skipping, current: {self.config.strategy})")
                return
            
            # Create controller
            controller = FundingArbStrategyController(self.strategy)
            set_strategy_controller(controller)
            
            # Get server config
            host = os.getenv("CONTROL_API_HOST", "127.0.0.1")
            port_env = os.getenv("CONTROL_API_PORT", "8766")
            port = int(port_env)
            
            self.logger.info(f"🔧 Embedded server config: host={host}, port={port} (from CONTROL_API_PORT={port_env})")
            
            # Check if port is already in use (only warn, don't skip)
            # We'll let uvicorn handle the actual binding error if port is truly in use
            if self._is_port_in_use(host, port):
                self.logger.warning(
                    f"⚠️  Control API port {host}:{port} appears to be in use. "
                    f"Attempting to start embedded server anyway - uvicorn will handle binding errors. "
                    f"If port {port} == 8766, the standalone control API server may be running."
                )
            else:
                self.logger.info(f"Starting control API server on {host}:{port}")
            
            # Run uvicorn in background task
            # IMPORTANT: uvicorn.Server installs its own signal handlers which override ours
            # We need to ensure our shutdown handler stops the server
            config = uvicorn.Config(
                app=app,
                host=host,
                port=port,
                log_level="warning",  # Reduce uvicorn logs
                access_log=False,
                loop="asyncio"
            )
            server = uvicorn.Server(config)
            
            # Store server reference so we can stop it manually
            self._control_server = server
            
            # Start server in background task
            # IMPORTANT: uvicorn.Server.serve() installs its own signal handlers which override ours
            # We need to re-register our signal handlers after uvicorn starts
            async def _run_server_with_error_handling():
                """Run uvicorn server with error handling."""
                try:
                    await server.serve()
                except OSError as e:
                    if "address already in use" in str(e).lower() or e.errno == 98:
                        self.logger.error(
                            f"❌ Failed to start embedded control API server: "
                            f"Port {host}:{port} is already in use. "
                            f"Hot-reload and position closing via API will not be available for this strategy."
                        )
                    else:
                        self.logger.error(f"❌ Failed to start embedded control API server: {e}")
                except Exception as e:
                    self.logger.error(f"❌ Unexpected error starting embedded control API server: {e}")
            
            self._control_server_task = asyncio.create_task(_run_server_with_error_handling())
            
            # Wait for uvicorn to initialize and bind to port
            # Uvicorn needs time to actually start listening, so we check multiple times
            server_started = False
            for attempt in range(10):  # Check up to 10 times (5 seconds total)
                await asyncio.sleep(0.5)
                if self._is_port_in_use(host, port):
                    server_started = True
                    break
            
            # Verify server actually started by checking if port is now listening
            if not server_started:
                self.logger.error(
                    f"❌ Embedded control API server failed to start on {host}:{port} after 5 seconds. "
                    f"Port is not listening. This may indicate: "
                    f"1) Port {port} is already in use by another process, "
                    f"2) Uvicorn failed to bind (check for errors above), "
                    f"3) Firewall/network issue. "
                    f"Hot-reload and position closing via API will not be available."
                )
            else:
                self.logger.info(f"✅ Verified embedded control API server is listening on {host}:{port}")
            
            # Re-register signal handlers after uvicorn starts
            # This ensures our signal handlers take precedence over uvicorn's
            
            # Re-register our signal handlers AFTER uvicorn starts
            # Create a signal handler that calls our shutdown logic
            def _override_signal_handler(signum, frame):
                """Signal handler that ensures our shutdown logic runs."""
                signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
                self.logger.info(f"📡 Signal handler called: {signal_name}")
                # Set shutdown flag to trigger graceful shutdown
                self.shutdown_requested = True
                # Also stop uvicorn server if it's running
                if hasattr(self, '_control_server') and self._control_server:
                    try:
                        self._control_server.should_exit = True
                    except Exception:
                        pass
            
            loop = asyncio.get_running_loop()
            if hasattr(loop, 'add_signal_handler'):
                try:
                    import signal
                    loop.add_signal_handler(signal.SIGINT, _override_signal_handler, signal.SIGINT, None)
                    loop.add_signal_handler(signal.SIGTERM, _override_signal_handler, signal.SIGTERM, None)
                    self.logger.info("✅ Re-registered signal handlers after uvicorn start")
                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to re-register signal handlers: {e}")
            
            # Only log success message if server is actually listening
            if self._is_port_in_use(host, port):
                self.logger.info(f"✅ Control API server started on http://{host}:{port}")
                self.logger.info("   Endpoints:")
                self.logger.info("   - GET  /api/v1/status")
                self.logger.info("   - GET  /api/v1/accounts")
                self.logger.info("   - GET  /api/v1/positions")
                self.logger.info("   - POST /api/v1/positions/{id}/close")
                self.logger.info("   - POST /api/v1/config/reload")
            else:
                self.logger.error(
                    f"❌ Control API server failed to start on {host}:{port}. "
                    f"Port is not listening. Hot-reload and position closing via API will not be available. "
                    f"Check logs above for binding errors."
                )
            
        except Exception as e:
            self.logger.error(f"Failed to start control API server: {e}")
            self.logger.error(traceback.format_exc())
            # Don't fail bot startup if control server fails
    
    async def _stop_control_server(self):
        """Stop the control API server."""
        if self._control_server_task:
            # Stop uvicorn server gracefully if we have a reference
            if hasattr(self, '_control_server') and self._control_server:
                try:
                    self._control_server.should_exit = True
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
            
            # Cancel the task if it's still running
            if not self._control_server_task.done():
                self._control_server_task.cancel()
                try:
                    await asyncio.wait_for(self._control_server_task, timeout=3.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
            
            self._control_server_task = None
            if hasattr(self, '_control_server'):
                self._control_server = None

    async def _on_proxy_unhealthy(self, failure_count: int) -> None:
        """Rotate to the next proxy when health checks continue to fail."""
        if not self.proxy_selector:
            self.logger.error(
                "Proxy health monitor reported failures but no proxy selector is configured"
            )
            return

        current_assignment = self.proxy_selector.current_assignment()
        current_proxy_id = current_assignment.proxy.id if current_assignment else None

        new_proxy = self.proxy_selector.rotate()
        if not new_proxy:
            self.logger.error(
                "Proxy health monitor reported failures but no alternate proxy assignments are available"
            )
            return

        if current_proxy_id and new_proxy.id == current_proxy_id:
            self.logger.error(
                "Proxy health monitor reported failures but only one active proxy assignment exists"
            )
            return

        try:
            masked_label = new_proxy.masked_label() if hasattr(new_proxy, "masked_label") else new_proxy.url_with_auth(mask_password=True)
            self.logger.warning(
                f"Rotating session proxy after {failure_count} failed health checks -> {masked_label}"
            )
            SessionProxyManager.rotate(new_proxy)
            self._proxy_rotation_attempts += 1

            ip_result = await detect_egress_ip()
            if ip_result.address:
                self._last_confirmed_proxy_ip = ip_result.address
                self.logger.info(
                    f"Proxy egress IP after rotation: {ip_result.address} (via {ip_result.source})"
                )
                self.config.strategy_params["_proxy_egress_ip"] = ip_result.address
                self.config.strategy_params["_proxy_egress_source"] = ip_result.source
                self.config.strategy_params["_proxy_rotation_count"] = self._proxy_rotation_attempts
            else:
                reason = ip_result.error or "no response"
                self.logger.warning(
                    f"Proxy rotation complete but unable to confirm new egress IP (reason: {reason})"
                )
        except Exception as exc:
            self.logger.error(f"Proxy rotation failed: {exc}")

    async def run(self):
        """Main trading loop."""
        try:
            # Setup phase
            await self._setup_contract_attributes()
            self._log_configuration()
            
            # Capture the running event loop for thread-safe callbacks
            self.loop = asyncio.get_running_loop()

            if SessionProxyManager.is_active():
                if not self._proxy_health_monitor:
                    self._proxy_health_monitor = ProxyHealthMonitor(
                        logger=self.logger,
                        interval_seconds=1800.0, # every 30 mins
                        timeout=10.0,
                        on_unhealthy=self._on_proxy_unhealthy,
                    )
                self._proxy_health_monitor.start()
            
            # Connection phase
            await self._connect_exchanges()
            await self.strategy.initialize()
            
            # Start control API server if enabled
            self.logger.info(f"🔧 Control API enabled check: {self._control_server_enabled} (CONTROL_API_ENABLED={os.getenv('CONTROL_API_ENABLED', 'not set')})")
            if self._control_server_enabled:
                await self._start_control_server()
            else:
                self.logger.info("Control API disabled (CONTROL_API_ENABLED not set to 'true')")
            
            # Execution phase
            await self._run_trading_loop()
            
            # If shutdown was requested, perform graceful shutdown
            if self.shutdown_requested:
                await self.graceful_shutdown("Shutdown requested")

        except KeyboardInterrupt:
            await self.graceful_shutdown("User interruption (Ctrl+C)")
        except Exception as e:
            self.logger.error(f"Critical error: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            await self.graceful_shutdown(f"Critical error: {e}")
            raise
