"""
Exchange factory for creating exchange clients dynamically.
"""

from typing import Dict, Any, Type
from exchange_clients.base import BaseExchangeClient, MissingCredentialsError


class ExchangeFactory:
    """Factory class for creating exchange clients."""

    _registered_exchanges = {
        'edgex': 'exchange_clients.edgex.EdgeXClient',
        'backpack': 'exchange_clients.backpack.BackpackClient',
        'paradex': 'exchange_clients.paradex.ParadexClient',
        'aster': 'exchange_clients.aster.AsterClient',
        'lighter': 'exchange_clients.lighter.LighterClient',
        'grvt': 'exchange_clients.grvt.GrvtClient',
    }

    @classmethod
    def create_exchange(cls, exchange_name: str, config: Dict[str, Any]) -> BaseExchangeClient:
        """Create an exchange client instance.

        Args:
            exchange_name: Name of the exchange (e.g., 'edgex')
            config: Configuration dictionary for the exchange

        Returns:
            Exchange client instance

        Raises:
            ValueError: If the exchange is not supported
        """
        exchange_name = exchange_name.lower()

        if exchange_name not in cls._registered_exchanges:
            available_exchanges = ', '.join(cls._registered_exchanges.keys())
            raise ValueError(f"Unsupported exchange: {exchange_name}. Available exchanges: {available_exchanges}")

        # Dynamically import the exchange class only when needed
        exchange_class_path = cls._registered_exchanges[exchange_name]
        exchange_class = cls._import_exchange_class(exchange_class_path)
        return exchange_class(config)

    @classmethod
    def _import_exchange_class(cls, class_path: str) -> Type[BaseExchangeClient]:
        """Dynamically import an exchange class.

        Args:
            class_path: Full module path to the exchange class (e.g., 'exchanges.edgex.EdgeXClient')

        Returns:
            The exchange class

        Raises:
            ImportError: If the class cannot be imported
            ValueError: If the class does not inherit from BaseExchangeClient
        """
        try:
            module_path, class_name = class_path.rsplit('.', 1)
            module = __import__(module_path, fromlist=[class_name])
            exchange_class = getattr(module, class_name)
            
            if not issubclass(exchange_class, BaseExchangeClient):
                raise ValueError(f"Exchange class {class_name} must inherit from BaseExchangeClient")
            
            return exchange_class
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Failed to import exchange class {class_path}: {e}")

    @classmethod
    def create_multiple_exchanges(
        cls, 
        exchange_names: list[str], 
        config: Dict[str, Any],
        primary_exchange: str = None,
        skip_unavailable: bool = False
    ) -> Dict[str, BaseExchangeClient]:
        """
        Create multiple exchange client instances.
        
        Useful for multi-DEX strategies like funding arbitrage where you need
        to trade on multiple exchanges simultaneously.
        
        Args:
            exchange_names: List of exchange names (e.g., ['lighter', 'grvt', 'backpack'])
            config: Base configuration dictionary (will be used for all exchanges)
            primary_exchange: Which exchange is the primary one (optional, defaults to first)
            skip_unavailable: If True, skip exchanges that fail to instantiate instead of raising error (default: False)
        
        Returns:
            Dictionary mapping exchange names to client instances
            e.g., {'lighter': LighterClient, 'grvt': GrvtClient}
        
        Raises:
            ValueError: If any exchange is not supported (and skip_unavailable=False)
        
        Example:
            >>> clients = ExchangeFactory.create_multiple_exchanges(
            ...     exchange_names=['lighter', 'grvt'],
            ...     config={'ticker': 'BTC', 'quantity': 100}
            ... )
            >>> lighter_client = clients['lighter']
            >>> grvt_client = clients['grvt']
        """
        if not exchange_names:
            raise ValueError("exchange_names list cannot be empty")
        
        # Validate primary exchange
        if primary_exchange and primary_exchange not in exchange_names:
            raise ValueError(f"Primary exchange '{primary_exchange}' not in exchange_names list")
        
        # Set primary to first exchange if not specified
        if not primary_exchange:
            primary_exchange = exchange_names[0]
        
        clients = {}
        failed_exchanges = []
        
        for exchange_name in exchange_names:
            # Create a config for each exchange
            # If config is a TradingConfig object, recreate it with the new exchange
            if hasattr(config, '__class__') and hasattr(config, 'exchange'):
                # It's a TradingConfig-like object, create a new instance
                from dataclasses import replace
                exchange_config = replace(config, exchange=exchange_name)
            elif isinstance(config, dict):
                # It's a dict, make a copy and update exchange
                exchange_config = config.copy()
                exchange_config['exchange'] = exchange_name
            else:
                # Fallback: try to use as-is
                exchange_config = config
            
            try:
                client = cls.create_exchange(exchange_name, exchange_config)
                clients[exchange_name] = client
            except MissingCredentialsError as e:
                # Skip exchanges with missing credentials (always, even if primary)
                failed_exchanges.append((exchange_name, f"Missing credentials"))
                print(f"⚠️  Skipping {exchange_name}: Missing or invalid API credentials")
                continue
            except Exception as e:
                # Track failed exchanges
                failed_exchanges.append((exchange_name, str(e)))
                
                # If this is the primary exchange, we must fail
                if exchange_name == primary_exchange:
                    # Clean up already created clients
                    for created_client in clients.values():
                        try:
                            if hasattr(created_client, 'disconnect'):
                                import asyncio
                                asyncio.create_task(created_client.disconnect())
                        except:
                            pass
                    raise ValueError(f"Failed to create PRIMARY exchange client for {exchange_name}: {e}")
                
                # If skip_unavailable is False, fail on any error
                if not skip_unavailable:
                    # Clean up already created clients
                    for created_client in clients.values():
                        try:
                            if hasattr(created_client, 'disconnect'):
                                import asyncio
                                asyncio.create_task(created_client.disconnect())
                        except:
                            pass
                    raise ValueError(f"Failed to create exchange client for {exchange_name}: {e}")
                
                # Otherwise, just log and continue
                print(f"⚠️  Skipping {exchange_name}: {str(e)[:100]}")
        
        # Warn if we failed to create any clients
        if failed_exchanges:
            print(f"\n⚠️  Warning: {len(failed_exchanges)} exchange(s) unavailable for trading:")
            for ex_name, error in failed_exchanges:
                # Show shortened error for abstract class issues
                if "abstract" in error.lower():
                    print(f"   • {ex_name}: Trading not yet implemented (funding data only)")
                else:
                    print(f"   • {ex_name}: {error[:80]}")
            print(f"\n✅ Successfully created {len(clients)} trading client(s): {list(clients.keys())}\n")
        
        # Ensure we have at least the primary exchange
        if not clients:
            raise ValueError(f"Failed to create any exchange clients. Primary exchange '{primary_exchange}' is required.")
        
        return clients

    @classmethod
    def get_supported_exchanges(cls) -> list:
        """Get list of supported exchanges.

        Returns:
            List of supported exchange names
        """
        return list(cls._registered_exchanges.keys())

    @classmethod
    def register_exchange(cls, name: str, exchange_class: type) -> None:
        """Register a new exchange client.

        Args:
            name: Exchange name
            exchange_class: Exchange client class that inherits from BaseExchangeClient
        """
        if not issubclass(exchange_class, BaseExchangeClient):
            raise ValueError("Exchange class must inherit from BaseExchangeClient")

        # Convert class to module path for lazy loading
        module_name = exchange_class.__module__
        class_name = exchange_class.__name__
        class_path = f"{module_name}.{class_name}"
        
        cls._registered_exchanges[name.lower()] = class_path
