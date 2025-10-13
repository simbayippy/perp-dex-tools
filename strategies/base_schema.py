"""
Base Parameter Schema System for Interactive Configuration

Provides a structured way to define strategy parameters with validation,
help text, defaults, and types. Used by the interactive config builder.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional, Union, Callable
from decimal import Decimal
from enum import Enum


class ParameterType(Enum):
    """Parameter data types."""
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    CHOICE = "choice"              # Single choice from list
    MULTI_CHOICE = "multi_choice"  # Multiple choices from list
    
    
@dataclass
class ParameterSchema:
    """
    Schema definition for a single strategy parameter.
    
    Defines how to prompt the user, validate input, and store the value.
    """
    # Basic info
    key: str
    prompt: str
    param_type: ParameterType
    
    # Help and description
    help_text: Optional[str] = None
    description: Optional[str] = None
    
    # Validation
    required: bool = True
    default: Optional[Any] = None
    
    # Type-specific constraints
    choices: Optional[List[str]] = None  # For CHOICE/MULTI_CHOICE
    min_value: Optional[Union[int, Decimal]] = None  # For INTEGER/DECIMAL
    max_value: Optional[Union[int, Decimal]] = None  # For INTEGER/DECIMAL
    min_length: Optional[int] = None  # For STRING
    max_length: Optional[int] = None  # For STRING
    
    # Advanced validation
    validator: Optional[Callable[[Any], bool]] = None
    validator_error_msg: Optional[str] = None
    
    # UI hints
    placeholder: Optional[str] = None
    show_default_in_prompt: bool = True
    
    def validate(self, value: Any) -> tuple[bool, Optional[str]]:
        """
        Validate a value against this schema.
        
        Returns:
            (is_valid, error_message)
        """
        # Required check
        if self.required and (value is None or value == ""):
            return False, f"{self.key} is required"
        
        # If not required and value is empty, it's valid
        if not self.required and (value is None or value == ""):
            return True, None
        
        # Type-specific validation
        if self.param_type == ParameterType.INTEGER:
            try:
                int_val = int(value)
                if self.min_value is not None and int_val < self.min_value:
                    return False, f"{self.key} must be >= {self.min_value}"
                if self.max_value is not None and int_val > self.max_value:
                    return False, f"{self.key} must be <= {self.max_value}"
            except (ValueError, TypeError):
                return False, f"{self.key} must be an integer"
        
        elif self.param_type == ParameterType.DECIMAL:
            try:
                dec_val = Decimal(str(value))
                if self.min_value is not None and dec_val < Decimal(str(self.min_value)):
                    return False, f"{self.key} must be >= {self.min_value}"
                if self.max_value is not None and dec_val > Decimal(str(self.max_value)):
                    return False, f"{self.key} must be <= {self.max_value}"
            except (ValueError, TypeError):
                return False, f"{self.key} must be a number"
        
        elif self.param_type == ParameterType.BOOLEAN:
            if not isinstance(value, bool) and str(value).lower() not in ['true', 'false', 'yes', 'no', '1', '0']:
                return False, f"{self.key} must be a boolean (true/false)"
        
        elif self.param_type == ParameterType.CHOICE:
            if self.choices and value not in self.choices:
                return False, f"{self.key} must be one of: {', '.join(self.choices)}"
        
        elif self.param_type == ParameterType.MULTI_CHOICE:
            if self.choices:
                values = value if isinstance(value, list) else [v.strip() for v in str(value).split(',')]
                invalid = [v for v in values if v not in self.choices]
                if invalid:
                    return False, f"Invalid choices: {', '.join(invalid)}. Must be from: {', '.join(self.choices)}"
        
        elif self.param_type == ParameterType.STRING:
            str_val = str(value)
            if self.min_length is not None and len(str_val) < self.min_length:
                return False, f"{self.key} must be at least {self.min_length} characters"
            if self.max_length is not None and len(str_val) > self.max_length:
                return False, f"{self.key} must be at most {self.max_length} characters"
        
        # Custom validator
        if self.validator is not None:
            try:
                if not self.validator(value):
                    return False, self.validator_error_msg or f"{self.key} validation failed"
            except Exception as e:
                return False, f"Validation error: {e}"
        
        return True, None
    
    def parse_value(self, value: Any) -> Any:
        """
        Parse and convert value to appropriate type.
        
        Returns:
            Parsed value in correct type
        """
        if value is None or value == "":
            return self.default
        
        # Convert based on type
        if self.param_type == ParameterType.INTEGER:
            return int(value)
        elif self.param_type == ParameterType.DECIMAL:
            return Decimal(str(value))
        elif self.param_type == ParameterType.BOOLEAN:
            if isinstance(value, bool):
                return value
            return str(value).lower() in ['true', 'yes', '1']
        elif self.param_type == ParameterType.MULTI_CHOICE:
            if isinstance(value, list):
                return value
            return [v.strip() for v in str(value).split(',')]
        else:
            return str(value)


@dataclass
class StrategySchema:
    """
    Complete schema for a trading strategy.
    
    Defines all parameters needed to configure the strategy.
    """
    name: str
    display_name: str
    description: str
    parameters: List[ParameterSchema]
    
    # Category grouping (optional)
    categories: Optional[dict[str, List[str]]] = None  # {category_name: [param_keys]}
    
    def get_parameter(self, key: str) -> Optional[ParameterSchema]:
        """Get parameter schema by key."""
        for param in self.parameters:
            if param.key == key:
                return param
        return None
    
    def get_optional_parameters(self) -> List[ParameterSchema]:
        """Get all optional parameters."""
        return [p for p in self.parameters if not p.required]
    
    def validate_config(self, config: dict) -> tuple[bool, List[str]]:
        """
        Validate a complete configuration.
        
        Returns:
            (is_valid, list_of_errors)
        """
        errors = []
        
        for param in self.parameters:
            value = config.get(param.key)
            is_valid, error = param.validate(value)
            if not is_valid:
                errors.append(error)
        
        return len(errors) == 0, errors
    
    def parse_config(self, raw_config: dict) -> dict:
        """
        Parse and convert all values in config to appropriate types.
        
        Returns:
            Parsed config with correct types
        """
        parsed = {}
        
        for param in self.parameters:
            raw_value = raw_config.get(param.key)
            parsed[param.key] = param.parse_value(raw_value)
        
        return parsed


# ============================================================================
# Helper Functions
# ============================================================================

def create_exchange_choice_parameter(
    key: str = "primary_exchange",
    prompt: str = "Which exchange should be your PRIMARY exchange?",
    required: bool = True,
    help_text: str = "This exchange will be used for the main connection"
) -> ParameterSchema:
    """Helper to create a standard exchange choice parameter."""
    from exchange_clients.factory import ExchangeFactory
    
    return ParameterSchema(
        key=key,
        prompt=prompt,
        param_type=ParameterType.CHOICE,
        choices=ExchangeFactory.get_supported_exchanges(),
        required=required,
        help_text=help_text
    )


def create_exchange_multi_choice_parameter(
    key: str = "scan_exchanges",
    prompt: str = "Which exchanges should we scan for opportunities? (comma-separated)",
    default: str = "lighter,grvt,backpack",
    required: bool = True,
    help_text: str = "We'll look for opportunities across these exchanges"
) -> ParameterSchema:
    """Helper to create a standard multi-exchange selection parameter."""
    from exchange_clients.factory import ExchangeFactory
    
    return ParameterSchema(
        key=key,
        prompt=prompt,
        param_type=ParameterType.MULTI_CHOICE,
        choices=ExchangeFactory.get_supported_exchanges(),
        default=default,
        required=required,
        help_text=help_text,
        show_default_in_prompt=True
    )


def create_decimal_parameter(
    key: str,
    prompt: str,
    default: Optional[Decimal] = None,
    min_value: Optional[Decimal] = None,
    max_value: Optional[Decimal] = None,
    required: bool = True,
    help_text: Optional[str] = None
) -> ParameterSchema:
    """Helper to create a decimal parameter."""
    return ParameterSchema(
        key=key,
        prompt=prompt,
        param_type=ParameterType.DECIMAL,
        default=default,
        min_value=min_value,
        max_value=max_value,
        required=required,
        help_text=help_text,
        show_default_in_prompt=True
    )


def create_boolean_parameter(
    key: str,
    prompt: str,
    default: bool = True,
    required: bool = False,
    help_text: Optional[str] = None
) -> ParameterSchema:
    """Helper to create a boolean parameter."""
    return ParameterSchema(
        key=key,
        prompt=prompt,
        param_type=ParameterType.BOOLEAN,
        default=default,
        required=required,
        help_text=help_text,
        show_default_in_prompt=True
    )

