"""
Tests for exchange client factory.

NOTE: These tests are skipped because they require external exchange SDKs
(lighter, bpx, edgex_sdk, pysdk, paradex-py) which are not installed.

To run these tests, install the required SDKs first.
"""

import pytest
from exchange_clients.factory import ExchangeFactory


pytestmark = pytest.mark.skip(reason="Requires external exchange SDKs (lighter, bpx, edgex_sdk, pysdk, paradex-py)")


class TestExchangeClientFactory:
    """Test exchange client factory functionality."""

    def test_get_supported_exchanges(self):
        """Test that factory returns list of supported exchanges."""
        supported = ExchangeFactory.get_supported_exchanges()

        assert isinstance(supported, list)
        assert len(supported) == 6
        assert "lighter" in supported
        assert "aster" in supported
        assert "backpack" in supported
        assert "paradex" in supported
        assert "edgex" in supported
        assert "grvt" in supported

    def test_factory_raises_error_for_unknown_exchange(self):
        """Test that factory raises error for unknown exchange."""
        config = {"contract_id": "BTC-PERP"}

        with pytest.raises(ValueError, match="Unsupported exchange"):
            ExchangeFactory.create_exchange("unknown_exchange", config)
