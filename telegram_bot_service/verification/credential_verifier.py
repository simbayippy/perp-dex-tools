"""
Credential Verification Module

Verifies exchange credentials before storing them in the database.
Tests API access for each exchange to ensure credentials are valid.
"""

from typing import Dict, Any, Optional, Tuple
from exchange_clients.factory import ExchangeFactory
from exchange_clients.base_models import MissingCredentialsError
from helpers.unified_logger import get_logger


logger = get_logger("core", "credential_verifier")


class CredentialVerifier:
    """Verifies exchange credentials before storage"""
    
    # Exchange-specific credential field mappings
    CREDENTIAL_FIELDS = {
        'lighter': ['private_key', 'account_index', 'api_key_index'],
        'aster': ['api_key', 'secret_key'],
        'backpack': ['public_key', 'secret_key'],
        'paradex': ['l1_address', 'l2_private_key_hex', 'l2_address', 'environment'],
    }
    
    async def verify_exchange_credentials(
        self,
        exchange_name: str,
        credentials: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify exchange credentials by testing API access.
        
        Args:
            exchange_name: Exchange name (lighter, aster, backpack, paradex)
            credentials: Dictionary of credential fields
            
        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        exchange_name = exchange_name.lower()
        
        if exchange_name not in self.CREDENTIAL_FIELDS:
            return False, f"Unsupported exchange: {exchange_name}"
        
        # Check required fields
        required_fields = self.CREDENTIAL_FIELDS[exchange_name]
        missing_fields = [field for field in required_fields if not credentials.get(field)]
        
        if missing_fields:
            return False, f"Missing required fields: {', '.join(missing_fields)}"
        
        # Create minimal config object for testing
        # Exchange clients expect config.ticker (attribute access), not config['ticker']
        from types import SimpleNamespace
        test_config = SimpleNamespace(
            ticker='BTC',  # Dummy ticker for testing
            exchange=exchange_name,
            quantity=None,
            contract_id=None,
            tick_size=None,
            strategy=None,
            order_notional_usd=None,
            target_leverage=None,
            strategy_params={}
        )
        
        try:
            # Create exchange client with test credentials
            client = ExchangeFactory.create_exchange(
                exchange_name=exchange_name,
                config=test_config,
                credentials=credentials
            )
            
            # Test API access by attempting to fetch account balance or market data
            # Each exchange has different methods, so we'll try common ones
            if exchange_name == 'lighter':
                # Lighter: Try to get account info
                if hasattr(client, 'account_manager') and client.account_manager:
                    try:
                        # Try to get account info (this validates credentials)
                        await client.account_manager.get_account_info()
                        return True, None
                    except Exception as e:
                        return False, f"Lighter API error: {str(e)}"
                else:
                    # Fallback: try to connect
                    try:
                        await client.connect()
                        await client.disconnect()
                        return True, None
                    except Exception as e:
                        return False, f"Lighter connection error: {str(e)}"
            
            elif exchange_name == 'aster':
                # Aster: Try to get account balance
                try:
                    await client.connect()
                    balance = await client.get_account_balance()
                    await client.disconnect()
                    if balance is not None:
                        return True, None
                    else:
                        return False, "Failed to fetch account balance"
                except Exception as e:
                    return False, f"Aster API error: {str(e)}"
            
            elif exchange_name == 'backpack':
                # Backpack: Try to get account info
                try:
                    await client.connect()
                    # Try to get account balance or account info
                    if hasattr(client, 'get_account_balance'):
                        balance = await client.get_account_balance()
                    elif hasattr(client, 'get_account_info'):
                        info = await client.get_account_info()
                    await client.disconnect()
                    return True, None
                except Exception as e:
                    return False, f"Backpack API error: {str(e)}"
            
            elif exchange_name == 'paradex':
                # Paradex: Try to get account info
                try:
                    await client.connect()
                    # Try to get account balance
                    if hasattr(client, 'get_account_balance'):
                        balance = await client.get_account_balance()
                    await client.disconnect()
                    return True, None
                except Exception as e:
                    return False, f"Paradex API error: {str(e)}"
            
            else:
                # Generic fallback: try to connect
                try:
                    await client.connect()
                    await client.disconnect()
                    return True, None
                except Exception as e:
                    return False, f"Connection error: {str(e)}"
        
        except MissingCredentialsError as e:
            return False, f"Missing credentials: {str(e)}"
        except ValueError as e:
            return False, f"Invalid credentials format: {str(e)}"
        except Exception as e:
            return False, f"Verification failed: {str(e)}"
    
    def validate_credential_format(
        self,
        exchange_name: str,
        credentials: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate credential format without making API calls.
        
        Args:
            exchange_name: Exchange name
            credentials: Dictionary of credential fields
            
        Returns:
            Tuple of (valid: bool, error_message: Optional[str])
        """
        exchange_name = exchange_name.lower()
        
        if exchange_name not in self.CREDENTIAL_FIELDS:
            return False, f"Unsupported exchange: {exchange_name}"
        
        required_fields = self.CREDENTIAL_FIELDS[exchange_name]
        missing_fields = [field for field in required_fields if not credentials.get(field)]
        
        if missing_fields:
            return False, f"Missing required fields: {', '.join(missing_fields)}"
        
        # Exchange-specific format validation
        if exchange_name == 'paradex':
            # Validate hex format for l2_private_key_hex
            l2_key = credentials.get('l2_private_key_hex', '')
            if l2_key and not l2_key.startswith('0x'):
                # Try to validate it's hex
                try:
                    int(l2_key, 16)
                except ValueError:
                    return False, "l2_private_key_hex must be a valid hex string"
        
        return True, None

