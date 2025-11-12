"""
YAML Configuration File Support

Handles loading and saving trading strategy configurations to/from YAML files.

Features:
- Load config from YAML
- Save config to YAML
- Validation against strategy schema
- Decimal/datetime serialization
- Config merging (file + CLI overrides)
"""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime


# ============================================================================
# YAML Custom Representers (for Decimal serialization)
# ============================================================================

def decimal_representer(dumper, data):
    """Custom representer for Decimal type."""
    return dumper.represent_scalar('tag:yaml.org,2002:float', str(data))

def decimal_constructor(loader, node):
    """Custom constructor for Decimal type."""
    value = loader.construct_scalar(node)
    return Decimal(value)

# Register custom handlers
yaml.add_representer(Decimal, decimal_representer)
yaml.add_constructor('tag:yaml.org,2002:float', decimal_constructor)


# ============================================================================
# YAML Config Operations
# ============================================================================

def save_config_to_yaml(strategy_name: str, config: Dict[str, Any], file_path: Path) -> None:
    """
    Save configuration to YAML file.
    
    Args:
        strategy_name: Name of the strategy
        config: Configuration dictionary
        file_path: Path to save to
    """
    # Build complete config structure
    full_config = {
        "strategy": strategy_name,
        "created_at": datetime.now().isoformat(),
        "version": "1.0",
        "config": config
    }
    
    # Write to YAML
    with open(file_path, 'w') as f:
        yaml.dump(
            full_config,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            indent=2
        )


def load_config_from_yaml(file_path: Path) -> Dict[str, Any]:
    """
    Load configuration from YAML file.
    
    Args:
        file_path: Path to config file
        
    Returns:
        Dictionary with 'strategy' and 'config' keys
        
    Raises:
        FileNotFoundError: If file doesn't exist
        yaml.YAMLError: If YAML is invalid
        ValueError: If config structure is invalid
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Config file not found: {file_path}")
    
    with open(file_path, 'r') as f:
        full_config = yaml.safe_load(f)
    
    # Validate structure
    if not isinstance(full_config, dict):
        raise ValueError("Invalid config file: must be a YAML dictionary")
    
    if "strategy" not in full_config:
        raise ValueError("Invalid config file: missing 'strategy' field")
    
    if "config" not in full_config:
        raise ValueError("Invalid config file: missing 'config' field")
    
    return {
        "strategy": full_config["strategy"],
        "config": full_config["config"],
        "metadata": {
            "created_at": full_config.get("created_at"),
            "version": full_config.get("version", "1.0")
        }
    }


def validate_config_file(file_path: Path) -> tuple[bool, Optional[str]]:
    """
    Validate a config file against its strategy schema.
    
    Args:
        file_path: Path to config file
        
    Returns:
        (is_valid, error_message)
    """
    try:
        # Load config
        loaded = load_config_from_yaml(file_path)
        strategy_name = loaded["strategy"]
        config = loaded["config"]

        if "primary_exchange" in config and "mandatory_exchange" not in config:
            config["mandatory_exchange"] = config.pop("primary_exchange")

        if not config.get("mandatory_exchange"):
            config["mandatory_exchange"] = None
            config["max_oi_usd"] = None
        config.pop("primary_exchange", None)
        
        # Get schema
        from strategies.implementations.funding_arbitrage.config_builder import get_funding_arb_schema
        from strategies.implementations.grid.config_builder import get_grid_schema
        
        schemas = {
            "funding_arbitrage": get_funding_arb_schema(),
            "grid": get_grid_schema()
        }
        
        if strategy_name not in schemas:
            return False, f"Unknown strategy: {strategy_name}"
        
        schema = schemas[strategy_name]
        
        # Validate
        is_valid, errors = schema.validate_config(config)
        if not is_valid:
            return False, "\n".join(errors)
        
        return True, None
        
    except Exception as e:
        return False, str(e)


def merge_configs(base_config: Dict, overrides: Dict) -> Dict:
    """
    Merge two configurations (for CLI override support).
    
    Args:
        base_config: Base configuration (from file)
        overrides: Override values (from CLI args)
        
    Returns:
        Merged configuration
    """
    merged = base_config.copy()
    
    for key, value in overrides.items():
        if value is not None:  # Only override if value is provided
            merged[key] = value
    
    return merged


def create_example_configs():
    """
    Create example configuration files for each strategy.
    
    Useful for users to see the format and get started quickly.
    """
    configs_dir = Path("configs")
    configs_dir.mkdir(exist_ok=True)
    
    # ========================================================================
    # Funding Arbitrage Example
    # ========================================================================
    funding_arb_config = {
        "strategy": "funding_arbitrage",
        "created_at": datetime.now().isoformat(),
        "version": "1.0",
        "config": {
            "mandatory_exchange": None,
            "scan_exchanges": ["lighter", "grvt", "backpack", "edgex"],
            "target_exposure": Decimal("100"),
            "max_positions": 5,
            "max_total_exposure_usd": Decimal("1000"),
            "min_profit_rate": Decimal("0.0001"),
            "max_oi_usd": None,
            "risk_strategy": "combined",
            "profit_erosion_threshold": Decimal("0.5"),
            "max_position_age_hours": 168,
            "max_new_positions_per_cycle": 2,
            "check_interval_seconds": 60,
            "dry_run": True,
            "min_volume_24h": Decimal("350000"),
            "min_oi_usd": Decimal("100000")
        }
    }
    
    funding_path = configs_dir / "example_funding_arbitrage.yml"
    with open(funding_path, 'w') as f:
        yaml.dump(funding_arb_config, f, default_flow_style=False, sort_keys=False, indent=2)
    
    print(f"Created: {funding_path}")
    
    # ========================================================================
    # Grid Strategy Example
    # ========================================================================
    grid_config = {
        "strategy": "grid",
        "created_at": datetime.now().isoformat(),
        "version": "1.0",
        "config": {
            "exchange": "lighter",
            "ticker": "BTC",
            "direction": "buy",
            "order_notional_usd": Decimal("100"),
            "target_leverage": Decimal("10"),
            "take_profit": Decimal("0.008"),
            "grid_step": Decimal("0.002"),
            "max_orders": 25,
            "wait_time": 10,
            "max_margin_usd": Decimal("5000"),
            "stop_loss_enabled": True,
            "stop_loss_percentage": Decimal("2.0"),
            "position_timeout_minutes": 60,
            "recovery_mode": "aggressive",
            "stop_price": None,
            "pause_price": None
        }
    }
    
    grid_path = configs_dir / "example_grid.yml"
    with open(grid_path, 'w') as f:
        yaml.dump(grid_config, f, default_flow_style=False, sort_keys=False, indent=2)
    
    print(f"Created: {grid_path}")


# ============================================================================
# Main Entry Point (for testing/example generation)
# ============================================================================

if __name__ == "__main__":
    print("Creating example configuration files...\n")
    create_example_configs()
    print("\n✓ Example configs created in ./configs/")
    print("\nYou can use these as templates:")
    print("  python runbot.py --config configs/example_funding_arbitrage.yml")
    print("  python runbot.py --config configs/example_grid.yml")
