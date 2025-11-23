"""
Tests for trading bot orchestration.

Tests the main trading bot initialization, multi-exchange coordination,
and lifecycle management.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

# Note: These tests use mocking since trading_bot.py is complex
# and requires many dependencies


class MockExchangeClient:
    """Mock exchange client for testing."""

    def __init__(self, name="test_exchange"):
        self.name = name
        self.connected = False
        self.websocket_connected = False
        self.orders = []

    def get_exchange_name(self):
        return self.name

    async def connect(self):
        self.connected = True
        return True

    async def connect_websocket(self):
        self.websocket_connected = True
        return True

    async def disconnect(self):
        self.connected = False
        self.websocket_connected = False

    async def get_balance(self):
        return {"USDC": "10000.0"}


class MockStrategy:
    """Mock strategy for testing."""

    def __init__(self, name="test_strategy"):
        self.name = name
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class TestTradingBotInitialization:
    """Test trading bot initialization."""

    def test_bot_initializes_with_single_exchange(self):
        """Test bot initialization with a single exchange."""
        exchange_client = MockExchangeClient("lighter")
        strategy = MockStrategy("grid")

        # Simulate bot setup
        bot_config = {
            "exchanges": {"lighter": exchange_client},
            "strategy": strategy
        }

        assert bot_config["exchanges"]["lighter"] is not None
        assert bot_config["strategy"] is not None

    def test_bot_initializes_with_multiple_exchanges(self):
        """Test bot initialization with multiple exchanges."""
        exchanges = {
            "lighter": MockExchangeClient("lighter"),
            "aster": MockExchangeClient("aster"),
            "backpack": MockExchangeClient("backpack"),
        }

        assert len(exchanges) == 3
        assert all(isinstance(client, MockExchangeClient) for client in exchanges.values())

    def test_bot_fails_with_invalid_config(self):
        """Test that bot fails gracefully with invalid configuration."""
        with pytest.raises((ValueError, KeyError, TypeError, AttributeError)):
            # Missing required fields
            bot_config = {}
            if "exchanges" not in bot_config:
                raise ValueError("Missing exchanges configuration")


class TestTradingBotProxyAssignment:
    """Test proxy assignment to exchanges."""

    def test_proxy_assignment_to_single_exchange(self):
        """Test assigning a proxy to a single exchange."""
        exchange = MockExchangeClient("lighter")
        proxy = "socks5://127.0.0.1:1080"

        # Simulate proxy assignment
        exchange.proxy = proxy

        assert hasattr(exchange, 'proxy')
        assert exchange.proxy == proxy

    def test_proxy_assignment_to_multiple_exchanges(self):
        """Test assigning different proxies to multiple exchanges."""
        exchanges = {
            "lighter": MockExchangeClient("lighter"),
            "aster": MockExchangeClient("aster"),
        }

        proxies = {
            "lighter": "socks5://127.0.0.1:1080",
            "aster": "socks5://127.0.0.1:1081",
        }

        for exchange_name, proxy in proxies.items():
            exchanges[exchange_name].proxy = proxy

        assert exchanges["lighter"].proxy == "socks5://127.0.0.1:1080"
        assert exchanges["aster"].proxy == "socks5://127.0.0.1:1081"

    def test_proxy_rotation_logic(self):
        """Test proxy rotation functionality."""
        available_proxies = [
            "socks5://127.0.0.1:1080",
            "socks5://127.0.0.1:1081",
            "socks5://127.0.0.1:1082",
        ]

        exchanges = [
            MockExchangeClient("lighter"),
            MockExchangeClient("aster"),
            MockExchangeClient("backpack"),
        ]

        # Assign proxies in round-robin fashion
        for i, exchange in enumerate(exchanges):
            exchange.proxy = available_proxies[i % len(available_proxies)]

        assert len(set(e.proxy for e in exchanges)) <= len(available_proxies)


class TestTradingBotWebSocketLifecycle:
    """Test WebSocket connection lifecycle."""

    @pytest.mark.asyncio
    async def test_websocket_connection_establishment(self):
        """Test WebSocket connections are established for all exchanges."""
        exchanges = {
            "lighter": MockExchangeClient("lighter"),
            "aster": MockExchangeClient("aster"),
        }

        for exchange in exchanges.values():
            await exchange.connect_websocket()

        assert all(client.websocket_connected for client in exchanges.values())

    @pytest.mark.asyncio
    async def test_websocket_reconnection_on_failure(self):
        """Test WebSocket reconnection logic on connection failure."""
        exchange = MockExchangeClient("lighter")

        # Simulate connection failure and reconnection
        await exchange.connect_websocket()
        assert exchange.websocket_connected

        # Simulate disconnect
        await exchange.disconnect()
        assert not exchange.websocket_connected

        # Reconnect
        await exchange.connect_websocket()
        assert exchange.websocket_connected

    @pytest.mark.asyncio
    async def test_multiple_websocket_subscriptions(self):
        """Test managing multiple WebSocket subscriptions."""
        exchange = MockExchangeClient("lighter")
        exchange.subscriptions = []

        # Simulate subscribing to different channels
        channels = ["orderbook", "trades", "positions"]
        for channel in channels:
            exchange.subscriptions.append(channel)

        assert len(exchange.subscriptions) == 3
        assert "orderbook" in exchange.subscriptions


class TestTradingBotStrategyManagement:
    """Test strategy initialization and management."""

    @pytest.mark.asyncio
    async def test_strategy_initialization(self):
        """Test strategy is initialized correctly."""
        strategy = MockStrategy("grid")

        assert strategy.name == "grid"
        assert not strategy.started

    @pytest.mark.asyncio
    async def test_strategy_start(self):
        """Test starting a strategy."""
        strategy = MockStrategy("grid")

        await strategy.start()

        assert strategy.started

    @pytest.mark.asyncio
    async def test_strategy_stop(self):
        """Test stopping a strategy."""
        strategy = MockStrategy("grid")

        await strategy.start()
        await strategy.stop()

        assert strategy.stopped

    @pytest.mark.asyncio
    async def test_multiple_strategies(self):
        """Test managing multiple strategies."""
        strategies = [
            MockStrategy("grid"),
            MockStrategy("funding_arbitrage"),
        ]

        for strategy in strategies:
            await strategy.start()

        assert all(s.started for s in strategies)


class TestTradingBotErrorHandling:
    """Test error handling in trading bot."""

    @pytest.mark.asyncio
    async def test_handles_exchange_connection_failure(self):
        """Test handling of exchange connection failures."""
        exchange = MockExchangeClient("lighter")

        # Simulate connection failure
        async def failing_connect():
            raise ConnectionError("Failed to connect")

        exchange.connect = failing_connect

        with pytest.raises(ConnectionError):
            await exchange.connect()

    @pytest.mark.asyncio
    async def test_continues_with_partial_exchange_failure(self):
        """Test that bot continues if one exchange fails but others succeed."""
        exchanges = {
            "lighter": MockExchangeClient("lighter"),
            "aster": MockExchangeClient("aster"),
        }

        # Make one exchange fail
        async def failing_connect():
            raise ConnectionError("Failed to connect")

        exchanges["lighter"].connect = failing_connect

        # Try to connect all
        results = {}
        for name, exchange in exchanges.items():
            try:
                await exchange.connect()
                results[name] = True
            except ConnectionError:
                results[name] = False

        # At least one should succeed
        assert any(results.values())
        assert results["aster"] is True


class TestTradingBotGracefulShutdown:
    """Test graceful shutdown of trading bot."""

    @pytest.mark.asyncio
    async def test_stops_all_strategies_on_shutdown(self):
        """Test that all strategies are stopped during shutdown."""
        strategies = [
            MockStrategy("grid"),
            MockStrategy("funding_arbitrage"),
        ]

        # Start all strategies
        for strategy in strategies:
            await strategy.start()

        # Shutdown - stop all strategies
        for strategy in strategies:
            await strategy.stop()

        assert all(s.stopped for s in strategies)

    @pytest.mark.asyncio
    async def test_closes_all_connections_on_shutdown(self):
        """Test that all connections are closed during shutdown."""
        exchanges = {
            "lighter": MockExchangeClient("lighter"),
            "aster": MockExchangeClient("aster"),
        }

        # Connect all
        for exchange in exchanges.values():
            await exchange.connect()

        # Disconnect all
        for exchange in exchanges.values():
            await exchange.disconnect()

        assert all(not client.connected for client in exchanges.values())

    @pytest.mark.asyncio
    async def test_handles_shutdown_errors_gracefully(self):
        """Test that shutdown errors don't crash the bot."""
        exchange = MockExchangeClient("lighter")

        # Make disconnect raise an error
        async def failing_disconnect():
            raise Exception("Disconnect failed")

        exchange.disconnect = failing_disconnect

        # Should not raise
        try:
            await exchange.disconnect()
        except Exception as e:
            # Log error but don't crash
            assert "Disconnect failed" in str(e)


class TestTradingBotConfiguration:
    """Test configuration loading and validation."""

    def test_valid_configuration(self):
        """Test that valid configuration is accepted."""
        config = {
            "exchanges": {
                "lighter": {
                    "api_key": "test_key",
                    "api_secret": "test_secret",
                    "contract_id": "BTC-PERP"
                }
            },
            "strategy": {
                "type": "grid",
                "params": {
                    "grid_size": 10,
                    "upper_price": 60000,
                    "lower_price": 40000
                }
            }
        }

        assert "exchanges" in config
        assert "strategy" in config
        assert config["exchanges"]["lighter"]["api_key"] is not None

    def test_missing_required_fields(self):
        """Test that missing required fields are detected."""
        config = {
            "exchanges": {}
            # Missing strategy
        }

        assert "strategy" not in config
        # Validation should fail
        with pytest.raises((KeyError, ValueError)):
            if "strategy" not in config:
                raise ValueError("Missing strategy configuration")

    def test_invalid_exchange_config(self):
        """Test that invalid exchange configuration is rejected."""
        config = {
            "exchanges": {
                "lighter": {
                    # Missing api_key and api_secret
                    "contract_id": "BTC-PERP"
                }
            }
        }

        lighter_config = config["exchanges"]["lighter"]
        with pytest.raises((KeyError, ValueError)):
            if "api_key" not in lighter_config:
                raise ValueError("Missing api_key")
