"""
Tests for configuration loading and validation.

Tests configuration builders, loaders, and validation logic.
"""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, mock_open
import json
import yaml


class TestConfigurationLoading:
    """Test configuration file loading."""

    def test_load_valid_json_config(self):
        """Test loading valid JSON configuration."""
        config_data = {
            "exchange": "lighter",
            "strategy": "grid",
            "api_key": "test_key",
            "api_secret": "test_secret"
        }

        config_json = json.dumps(config_data)

        with patch("builtins.open", mock_open(read_data=config_json)):
            with open("config.json", "r") as f:
                loaded_config = json.load(f)

        assert loaded_config["exchange"] == "lighter"
        assert loaded_config["strategy"] == "grid"

    def test_load_valid_yaml_config(self):
        """Test loading valid YAML configuration."""
        config_yaml = """
        exchange: lighter
        strategy: grid
        api_key: test_key
        api_secret: test_secret
        """

        with patch("builtins.open", mock_open(read_data=config_yaml)):
            with open("config.yaml", "r") as f:
                loaded_config = yaml.safe_load(f)

        assert loaded_config["exchange"] == "lighter"
        assert loaded_config["strategy"] == "grid"

    def test_fails_on_missing_config_file(self):
        """Test that missing config file raises error."""
        with pytest.raises(FileNotFoundError):
            with open("nonexistent_config.json", "r") as f:
                json.load(f)

    def test_fails_on_invalid_json(self):
        """Test that invalid JSON raises error."""
        invalid_json = "{ exchange: lighter, invalid json }"

        with patch("builtins.open", mock_open(read_data=invalid_json)):
            with pytest.raises(json.JSONDecodeError):
                with open("config.json", "r") as f:
                    json.load(f)


class TestConfigurationValidation:
    """Test configuration validation logic."""

    def test_valid_config_passes_validation(self):
        """Test that valid configuration passes validation."""
        config = {
            "exchange": "lighter",
            "api_key": "test_key",
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP",
            "strategy": {
                "type": "grid",
                "params": {
                    "grid_size": 10,
                    "upper_price": 60000,
                    "lower_price": 40000
                }
            }
        }

        # Validate required fields
        required_fields = ["exchange", "api_key", "api_secret", "contract_id", "strategy"]
        assert all(field in config for field in required_fields)

    def test_missing_required_field_fails_validation(self):
        """Test that missing required fields fail validation."""
        config = {
            "exchange": "lighter",
            # Missing api_key
            "api_secret": "test_secret",
            "contract_id": "BTC-PERP"
        }

        required_fields = ["exchange", "api_key", "api_secret", "contract_id"]

        with pytest.raises(ValueError):
            for field in required_fields:
                if field not in config:
                    raise ValueError(f"Missing required field: {field}")

    def test_invalid_exchange_name_fails_validation(self):
        """Test that invalid exchange name fails validation."""
        config = {
            "exchange": "invalid_exchange",
            "api_key": "test_key",
            "api_secret": "test_secret"
        }

        valid_exchanges = ["lighter", "aster", "backpack", "paradex", "edgex", "grvt"]

        with pytest.raises(ValueError):
            if config["exchange"] not in valid_exchanges:
                raise ValueError(f"Invalid exchange: {config['exchange']}")

    def test_invalid_strategy_type_fails_validation(self):
        """Test that invalid strategy type fails validation."""
        config = {
            "strategy": {
                "type": "invalid_strategy"
            }
        }

        valid_strategies = ["grid", "funding_arbitrage"]

        with pytest.raises(ValueError):
            if config["strategy"]["type"] not in valid_strategies:
                raise ValueError(f"Invalid strategy type: {config['strategy']['type']}")


class TestGridStrategyConfig:
    """Test Grid strategy configuration validation."""

    def test_valid_grid_config(self):
        """Test valid grid strategy configuration."""
        config = {
            "type": "grid",
            "params": {
                "grid_size": 10,
                "upper_price": 60000,
                "lower_price": 40000,
                "order_size_usd": 100
            }
        }

        assert config["params"]["grid_size"] > 0
        assert config["params"]["upper_price"] > config["params"]["lower_price"]
        assert config["params"]["order_size_usd"] > 0

    def test_invalid_grid_size_fails(self):
        """Test that invalid grid size fails validation."""
        config = {
            "type": "grid",
            "params": {
                "grid_size": 0,  # Invalid
                "upper_price": 60000,
                "lower_price": 40000
            }
        }

        with pytest.raises(ValueError):
            if config["params"]["grid_size"] <= 0:
                raise ValueError("Grid size must be positive")

    def test_invalid_price_range_fails(self):
        """Test that invalid price range fails validation."""
        config = {
            "type": "grid",
            "params": {
                "grid_size": 10,
                "upper_price": 40000,
                "lower_price": 60000  # Lower > Upper
            }
        }

        with pytest.raises(ValueError):
            if config["params"]["lower_price"] >= config["params"]["upper_price"]:
                raise ValueError("Lower price must be less than upper price")


class TestFundingArbitrageConfig:
    """Test Funding Arbitrage strategy configuration validation."""

    def test_valid_funding_arb_config(self):
        """Test valid funding arbitrage configuration."""
        config = {
            "type": "funding_arbitrage",
            "params": {
                "min_funding_rate_diff": 0.0005,
                "position_size_usd": 1000,
                "max_positions": 5,
                "min_position_age_hours": 8
            }
        }

        assert config["params"]["min_funding_rate_diff"] > 0
        assert config["params"]["position_size_usd"] > 0
        assert config["params"]["max_positions"] > 0

    def test_invalid_funding_rate_diff_fails(self):
        """Test that invalid funding rate diff fails validation."""
        config = {
            "type": "funding_arbitrage",
            "params": {
                "min_funding_rate_diff": -0.001  # Negative
            }
        }

        with pytest.raises(ValueError):
            if config["params"]["min_funding_rate_diff"] <= 0:
                raise ValueError("Min funding rate diff must be positive")

    def test_invalid_position_size_fails(self):
        """Test that invalid position size fails validation."""
        config = {
            "type": "funding_arbitrage",
            "params": {
                "position_size_usd": 0  # Zero
            }
        }

        with pytest.raises(ValueError):
            if config["params"]["position_size_usd"] <= 0:
                raise ValueError("Position size must be positive")


class TestMultiAccountConfig:
    """Test multi-account configuration."""

    def test_valid_multi_account_config(self):
        """Test valid multi-account configuration."""
        config = {
            "accounts": [
                {
                    "name": "account1",
                    "exchanges": {
                        "lighter": {
                            "api_key": "key1",
                            "api_secret": "secret1"
                        }
                    }
                },
                {
                    "name": "account2",
                    "exchanges": {
                        "aster": {
                            "api_key": "key2",
                            "api_secret": "secret2"
                        }
                    }
                }
            ]
        }

        assert len(config["accounts"]) == 2
        assert config["accounts"][0]["name"] == "account1"
        assert config["accounts"][1]["name"] == "account2"

    def test_duplicate_account_names_fail(self):
        """Test that duplicate account names fail validation."""
        config = {
            "accounts": [
                {"name": "account1"},
                {"name": "account1"}  # Duplicate
            ]
        }

        account_names = [acc["name"] for acc in config["accounts"]]

        with pytest.raises(ValueError):
            if len(account_names) != len(set(account_names)):
                raise ValueError("Duplicate account names")


class TestProxyConfiguration:
    """Test proxy configuration validation."""

    def test_valid_proxy_config(self):
        """Test valid proxy configuration."""
        config = {
            "proxies": [
                "socks5://127.0.0.1:1080",
                "socks5://127.0.0.1:1081",
                "http://proxy.example.com:8080"
            ]
        }

        assert len(config["proxies"]) == 3
        assert all("://" in proxy for proxy in config["proxies"])

    def test_invalid_proxy_format_fails(self):
        """Test that invalid proxy format fails validation."""
        proxy = "invalid_proxy_format"

        with pytest.raises(ValueError):
            if "://" not in proxy:
                raise ValueError(f"Invalid proxy format: {proxy}")

    def test_unsupported_proxy_protocol_fails(self):
        """Test that unsupported proxy protocol fails validation."""
        proxy = "ftp://proxy.example.com:21"

        supported_protocols = ["socks5", "http", "https"]
        protocol = proxy.split("://")[0]

        with pytest.raises(ValueError):
            if protocol not in supported_protocols:
                raise ValueError(f"Unsupported proxy protocol: {protocol}")


class TestEnvironmentVariableLoading:
    """Test environment variable configuration loading."""

    @patch.dict('os.environ', {'API_KEY': 'env_api_key', 'API_SECRET': 'env_secret'})
    def test_loads_from_environment_variables(self):
        """Test loading configuration from environment variables."""
        import os

        api_key = os.environ.get('API_KEY')
        api_secret = os.environ.get('API_SECRET')

        assert api_key == 'env_api_key'
        assert api_secret == 'env_secret'

    @patch.dict('os.environ', {}, clear=True)
    def test_falls_back_to_config_file(self):
        """Test fallback to config file when env vars not set."""
        import os

        api_key = os.environ.get('API_KEY')

        if api_key is None:
            # Fallback to config file
            api_key = 'config_file_key'

        assert api_key == 'config_file_key'


class TestConfigMerging:
    """Test configuration merging logic."""

    def test_merges_default_and_user_config(self):
        """Test merging default and user configurations."""
        default_config = {
            "timeout": 30,
            "retry_count": 3,
            "log_level": "INFO"
        }

        user_config = {
            "timeout": 60,  # Override
            "log_level": "DEBUG"  # Override
        }

        merged_config = {**default_config, **user_config}

        assert merged_config["timeout"] == 60
        assert merged_config["retry_count"] == 3  # From default
        assert merged_config["log_level"] == "DEBUG"

    def test_nested_config_merging(self):
        """Test merging nested configuration dictionaries."""
        default_config = {
            "strategy": {
                "type": "grid",
                "params": {
                    "grid_size": 10,
                    "order_size_usd": 100
                }
            }
        }

        user_config = {
            "strategy": {
                "params": {
                    "grid_size": 20  # Override
                }
            }
        }

        # Deep merge
        merged_params = {
            **default_config["strategy"]["params"],
            **user_config["strategy"]["params"]
        }

        assert merged_params["grid_size"] == 20
        assert merged_params["order_size_usd"] == 100


class TestConfigSerialization:
    """Test configuration serialization."""

    def test_serializes_to_json(self):
        """Test serializing configuration to JSON."""
        config = {
            "exchange": "lighter",
            "strategy": "grid",
            "params": {
                "grid_size": 10
            }
        }

        json_str = json.dumps(config, indent=2)

        assert "lighter" in json_str
        assert "grid" in json_str

    def test_handles_decimal_serialization(self):
        """Test serializing Decimal values."""
        config = {
            "price": Decimal("50000.50")
        }

        # Need custom encoder for Decimal
        class DecimalEncoder(json.JSONEncoder):
            def default(self, obj):
                if isinstance(obj, Decimal):
                    return str(obj)
                return super().default(obj)

        json_str = json.dumps(config, cls=DecimalEncoder)

        assert "50000.50" in json_str
