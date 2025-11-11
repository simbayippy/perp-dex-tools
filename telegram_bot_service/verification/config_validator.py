"""
Config Validator Module

Validates strategy configurations before running.
"""

from typing import Dict, Any, List, Tuple
from databases import Database
from helpers.unified_logger import get_logger


logger = get_logger("core", "config_validator")


class ConfigValidator:
    """Validates configs before running strategies"""
    
    def __init__(self, database: Database):
        """
        Initialize ConfigValidator.
        
        Args:
            database: Database connection instance
        """
        self.database = database
    
    async def validate_before_run(
        self,
        config: Dict[str, Any],
        account_id: str,
        is_admin: bool = False
    ) -> Tuple[bool, List[str]]:
        """
        Comprehensive validation before running a strategy.
        
        Args:
            config: Config data (may be full structure with 'strategy'/'config' keys, or just config dict)
            account_id: Account UUID
            is_admin: Whether user is an admin (admins bypass proxy requirement)
            
        Returns:
            Tuple of (valid: bool, errors: List[str])
        """
        errors = []
        
        # Extract strategy_type and config_dict
        if isinstance(config, dict):
            if 'strategy' in config and 'config' in config:
                strategy_type = config['strategy']
                config_dict = config['config']
            else:
                # Assume it's just the config dict, need to get strategy_type from elsewhere
                # For now, try to infer from config structure
                strategy_type = config.get('strategy', 'funding_arbitrage')
                config_dict = config
        else:
            errors.append("Config must be a dictionary")
            return False, errors
        
        # Check account has required exchange credentials
        account_ok, account_errors = await self._validate_account_credentials(
            config_dict, account_id, strategy_type
        )
        if not account_ok:
            errors.extend(account_errors)
        
        # Check proxy is configured (skip for admins)
        if not is_admin:
            proxy_ok, proxy_error = await self._validate_proxy(account_id)
            if not proxy_ok:
                errors.append(proxy_error)
        else:
            logger.info(f"Admin user bypassing proxy requirement for account {account_id}")
        
        # Validate config parameters
        config_ok, config_errors = self._validate_config_params(config_dict, strategy_type)
        if not config_ok:
            errors.extend(config_errors)
        
        return len(errors) == 0, errors
    
    async def _validate_account_credentials(
        self,
        config: Dict[str, Any],
        account_id: str,
        strategy_type: str
    ) -> Tuple[bool, List[str]]:
        """Check account has required exchange credentials."""
        errors = []
        
        # Determine required exchanges from config
        if strategy_type == "funding_arbitrage":
            # Funding arb needs at least 2 exchanges
            exchanges = config.get("exchanges", []) or config.get("scan_exchanges", [])
            if len(exchanges) < 2:
                errors.append("Funding arbitrage requires at least 2 exchanges")
        else:
            # Other strategies need at least 1 exchange
            exchange = config.get("exchange")
            if not exchange:
                errors.append("Config missing exchange")
        
        # Check account has credentials for required exchanges
        query = """
            SELECT dex.name
            FROM account_exchange_credentials aec
            JOIN dexes dex ON aec.exchange_id = dex.id
            WHERE aec.account_id = :account_id
            AND aec.is_active = TRUE
        """
        rows = await self.database.fetch_all(query, {"account_id": account_id})
        available_exchanges = {row["name"].lower() for row in rows}
        
        if strategy_type == "funding_arbitrage":
            required_exchanges = [ex.lower() for ex in exchanges]
            missing = [ex for ex in required_exchanges if ex not in available_exchanges]
            if missing:
                errors.append(f"Account missing credentials for exchanges: {', '.join(missing)}")
        else:
            exchange = config.get("exchange", "").lower()
            if exchange and exchange not in available_exchanges:
                errors.append(f"Account missing credentials for exchange: {exchange}")
        
        return len(errors) == 0, errors
    
    async def _validate_proxy(self, account_id: str) -> Tuple[bool, str]:
        """Check proxy is configured for account."""
        query = """
            SELECT COUNT(*) as count
            FROM account_proxy_assignments apa
            JOIN network_proxies np ON apa.proxy_id = np.id
            WHERE apa.account_id = :account_id
            AND apa.status = 'active'
            AND np.is_active = TRUE
        """
        row = await self.database.fetch_one(query, {"account_id": account_id})
        proxy_count = row["count"] if row else 0
        
        if proxy_count == 0:
            return False, "Account must have at least one active proxy configured"
        
        return True, ""
    
    def _validate_config_params(self, config: Dict[str, Any], strategy_type: str) -> Tuple[bool, List[str]]:
        """Validate config parameters."""
        errors = []
        
        if strategy_type == "funding_arbitrage":
            # Validate funding arb specific params
            min_profit = config.get("min_profit_percent") or config.get("min_profit_rate")
            if min_profit is None or (isinstance(min_profit, (int, float)) and min_profit <= 0):
                errors.append("min_profit_percent/min_profit_rate must be positive")
            
            # Check for target_margin (new) or target_exposure (backward compatibility)
            target_margin = config.get("target_margin")
            target_exposure = config.get("target_exposure")
            
            if target_margin is None and target_exposure is None:
                errors.append("target_margin must be specified")
            elif target_margin is not None and (isinstance(target_margin, (int, float)) and target_margin <= 0):
                errors.append("target_margin must be positive")
            elif target_exposure is not None and (isinstance(target_exposure, (int, float)) and target_exposure <= 0):
                # Backward compatibility: warn but don't fail
                logger.warning("Config uses deprecated 'target_exposure', will be converted to target_margin")
        
        elif strategy_type == "grid":
            # Validate grid specific params
            grid_levels = config.get("grid_levels")
            if grid_levels is None or grid_levels < 2:
                errors.append("grid_levels must be at least 2")
        
        return len(errors) == 0, errors

