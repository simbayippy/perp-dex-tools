"""
Strategy Factory
Creates strategy instances based on configuration.
"""

from types import SimpleNamespace
from typing import Dict, Type, List

from .base_strategy import BaseStrategy
from .implementations.grid import GridStrategy
from .implementations.grid.config import GridConfig
from .implementations.funding_arbitrage import FundingArbitrageStrategy


class StrategyFactory:
    """Factory for creating trading strategy instances."""
    
    # Registry of available strategies
    _strategies: Dict[str, Type[BaseStrategy]] = {
        'grid': GridStrategy,
        'funding_arbitrage': FundingArbitrageStrategy,
    }
    
    @classmethod
    def create_strategy(
        cls, 
        strategy_name: str, 
        config, 
        exchange_client=None, 
        exchange_clients=None
    ) -> BaseStrategy:
        """Create a strategy instance.
        
        Args:
            strategy_name: Name of the strategy to create
            config: Trading configuration object
            exchange_client: Single exchange client instance (for single-exchange strategies)
            exchange_clients: Dict of exchange clients (for multi-exchange strategies)
            
        Returns:
            BaseStrategy: Strategy instance
            
        Raises:
            ValueError: If strategy name is not supported
            
        Note:
            - Single-exchange strategies (e.g., grid) use `exchange_client`
            - Multi-exchange strategies (e.g., funding_arbitrage) use `exchange_clients`
            - Multi-exchange strategies can also receive `exchange_client` for backward compatibility
        """
        strategy_name = strategy_name.lower()
        
        if strategy_name not in cls._strategies:
            available = ', '.join(cls._strategies.keys())
            raise ValueError(f"Unsupported strategy: {strategy_name}. Available: {available}")
        
        strategy_class = cls._strategies[strategy_name]
        prepared_config = config

        if strategy_name == 'grid' and not hasattr(config, 'take_profit'):
            raw_params = dict(getattr(config, 'strategy_params', {}) or {})
            grid_fields = set(GridConfig.model_fields.keys())
            grid_params = {key: value for key, value in raw_params.items() if key in grid_fields}
            grid_config = GridConfig(**grid_params)
            merged = SimpleNamespace(**grid_config.model_dump())
            for attr in ('exchange', 'ticker', 'contract_id', 'quantity', 'tick_size'):
                if hasattr(config, attr):
                    setattr(merged, attr, getattr(config, attr))
            prepared_config = merged
        
        # Multi-exchange strategies receive exchange_clients dict
        multi_exchange_strategies = ['funding_arbitrage']
        
        if strategy_name in multi_exchange_strategies:
            if exchange_clients is None:
                raise ValueError(f"Strategy '{strategy_name}' requires exchange_clients parameter")
            # Pass exchange_clients dict to multi-exchange strategies
            return strategy_class(prepared_config, exchange_clients)
        else:
            # Single-exchange strategies receive single exchange_client
            if exchange_client is None:
                raise ValueError(f"Strategy '{strategy_name}' requires exchange_client parameter")
            return strategy_class(prepared_config, exchange_client)
    
    @classmethod
    def register_strategy(cls, name: str, strategy_class: Type[BaseStrategy]):
        """Register a new strategy.
        
        Args:
            name: Strategy name
            strategy_class: Strategy class that inherits from BaseStrategy
        """
        if not issubclass(strategy_class, BaseStrategy):
            raise ValueError(f"Strategy class {strategy_class.__name__} must inherit from BaseStrategy")
        
        cls._strategies[name.lower()] = strategy_class
    
    @classmethod
    def get_supported_strategies(cls) -> List[str]:
        """Get list of supported strategy names."""
        return list(cls._strategies.keys())
    
    @classmethod
    def get_strategy_info(cls, strategy_name: str) -> Dict[str, any]:
        """Get information about a strategy.
        
        Args:
            strategy_name: Name of the strategy
            
        Returns:
            Dict containing strategy information
        """
        strategy_name = strategy_name.lower()
        
        if strategy_name not in cls._strategies:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        
        strategy_class = cls._strategies[strategy_name]
        
        # Create a temporary instance to get required parameters
        # Note: This is a bit hacky, but needed for parameter discovery
        try:
            temp_config = type('TempConfig', (), {
                'exchange': 'temp',
                'ticker': 'TEMP',
                'strategy_params': {}
            })()
            temp_instance = strategy_class(temp_config, None)
            required_params = temp_instance.get_required_parameters()
        except Exception:
            required_params = []
        
        return {
            'name': strategy_name,
            'class': strategy_class.__name__,
            'description': strategy_class.__doc__ or "No description available",
            'required_parameters': required_params
        }
