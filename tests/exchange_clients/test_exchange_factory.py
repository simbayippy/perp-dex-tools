"""
Tests for exchange client factory.

Tests that the factory correctly creates and initializes
exchange clients for all supported DEXes.
"""

import pytest
from unittest.mock import MagicMock, patch

from exchange_clients.factory import ExchangeFactory


class TestExchangeClientFactory:
    """Test exchange client factory functionality."""

    def test_factory_creates_lighter_client(self):
        """Test that factory creates Lighter client correctly."""
        config = {
            "exchange": "lighter",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP"
        }

        client = ExchangeFactory.create(config)

        assert client is not None
        assert client.get_exchange_name() == "lighter"

    def test_factory_creates_aster_client(self):
        """Test that factory creates Aster client correctly."""
        config = {
            "exchange": "aster",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP"
        }

        client = ExchangeFactory.create(config)

        assert client is not None
        assert client.get_exchange_name() == "aster"

    def test_factory_creates_backpack_client(self):
        """Test that factory creates Backpack client correctly."""
        config = {
            "exchange": "backpack",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP"
        }

        client = ExchangeFactory.create(config)

        assert client is not None
        assert client.get_exchange_name() == "backpack"

    def test_factory_creates_paradex_client(self):
        """Test that factory creates Paradex client correctly."""
        config = {
            "exchange": "paradex",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP"
        }

        client = ExchangeFactory.create(config)

        assert client is not None
        assert client.get_exchange_name() == "paradex"

    def test_factory_creates_edgex_client(self):
        """Test that factory creates EdgeX client correctly."""
        config = {
            "exchange": "edgex",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP"
        }

        client = ExchangeFactory.create(config)

        assert client is not None
        assert client.get_exchange_name() == "edgex"

    def test_factory_creates_grvt_client(self):
        """Test that factory creates GRVT client correctly."""
        config = {
            "exchange": "grvt",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP"
        }

        client = ExchangeFactory.create(config)

        assert client is not None
        assert client.get_exchange_name() == "grvt"

    def test_factory_raises_error_for_unknown_exchange(self):
        """Test that factory raises error for unknown exchange."""
        config = {
            "exchange": "unknown_exchange",
            "api_key": "test_key",
            "api_secret": "test_secret"
        }

        with pytest.raises((ValueError, KeyError, Exception)):
            ExchangeFactory.create(config)

    def test_factory_handles_missing_config_fields(self):
        """Test that factory handles missing configuration fields."""
        config = {
            "exchange": "lighter"
            # Missing api_key and api_secret
        }

        with pytest.raises((ValueError, KeyError, Exception)):
            ExchangeFactory.create(config)

    def test_factory_creates_multiple_clients(self):
        """Test that factory can create multiple different clients."""
        configs = [
            {"exchange": "lighter", "api_key": "key1", "api_secret": "secret1", "contract_id": "BTC-PERP"},
            {"exchange": "aster", "api_key": "key2", "api_secret": "secret2", "contract_id": "ETH-PERP"},
        ]

        clients = [ExchangeFactory.create(config) for config in configs]

        assert len(clients) == 2
        assert clients[0].get_exchange_name() == "lighter"
        assert clients[1].get_exchange_name() == "aster"
        assert clients[0] is not clients[1]

    def test_factory_creates_clients_with_proxy_config(self):
        """Test that factory correctly passes proxy configuration."""
        config = {
            "exchange": "lighter",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP",
            "proxy": "socks5://127.0.0.1:1080"
        }

        client = ExchangeFactory.create(config)

        assert client is not None
        # Proxy configuration should be stored in client
        assert hasattr(client, 'config') or hasattr(client, 'proxy')


class TestExchangeClientInterface:
    """Test that all exchange clients implement the required interface."""

    @pytest.mark.parametrize("exchange_name", [
        "lighter",
        "aster",
        "backpack",
        "paradex",
        "edgex",
        "grvt"
    ])
    def test_client_implements_required_methods(self, exchange_name):
        """Test that each exchange client implements all required methods."""
        config = {
            "exchange": exchange_name,
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP"
        }

        client = ExchangeFactory.create(config)

        # Check for required methods
        required_methods = [
            'get_exchange_name',
            'place_market_order',
            'place_limit_order',
            'cancel_order',
            'get_position_snapshot',
        ]

        for method_name in required_methods:
            assert hasattr(client, method_name), \
                f"{exchange_name} client missing method: {method_name}"
            assert callable(getattr(client, method_name)), \
                f"{exchange_name} {method_name} is not callable"

    @pytest.mark.parametrize("exchange_name", [
        "lighter",
        "aster",
        "backpack",
        "paradex",
        "edgex",
        "grvt"
    ])
    def test_client_has_correct_exchange_name(self, exchange_name):
        """Test that each client returns the correct exchange name."""
        config = {
            "exchange": exchange_name,
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP"
        }

        client = ExchangeFactory.create(config)

        assert client.get_exchange_name() == exchange_name.lower()
