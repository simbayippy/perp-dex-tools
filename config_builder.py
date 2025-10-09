"""
Interactive Configuration Builder

Hummingbot-style interactive configuration system for trading strategies.
Uses questionary for beautiful prompts and validation.

Usage:
    python config_builder.py
    
Or import and use programmatically:
    from config_builder import InteractiveConfigBuilder
    builder = InteractiveConfigBuilder()
    config = builder.build_config()
"""

import sys
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from decimal import Decimal

try:
    import questionary
    from questionary import Style
except ImportError:
    print("Error: questionary library not found.")
    print("Please install it: pip install questionary")
    sys.exit(1)

from strategies.base_schema import StrategySchema, ParameterSchema, ParameterType
from strategies.implementations.funding_arbitrage.schema import get_funding_arb_schema
from strategies.implementations.grid.schema import get_grid_schema


# ============================================================================
# Custom Styling
# ============================================================================

CUSTOM_STYLE = Style([
    ('qmark', 'fg:#673ab7 bold'),       # Question mark
    ('question', 'bold'),                # Question text
    ('answer', 'fg:#f44336 bold'),      # User's answer
    ('pointer', 'fg:#673ab7 bold'),     # Pointer for selections
    ('highlighted', 'fg:#673ab7 bold'), # Highlighted choice
    ('selected', 'fg:#cc5454'),         # Selected choice
    ('separator', 'fg:#cc5454'),        # Separator
    ('instruction', ''),                 # Instructions
    ('text', ''),                        # Plain text
    ('disabled', 'fg:#858585 italic')   # Disabled choices
])


# ============================================================================
# Interactive Config Builder
# ============================================================================

class InteractiveConfigBuilder:
    """
    Interactive configuration builder for trading strategies.
    
    Guides users through step-by-step configuration with validation,
    help text, and beautiful prompts.
    """
    
    def __init__(self):
        """Initialize the config builder."""
        self.available_strategies = {
            "grid": get_grid_schema(),
            "funding_arbitrage": get_funding_arb_schema()
        }
    
    def build_config(self) -> Optional[Dict[str, Any]]:
        """
        Main entry point - build a configuration interactively.
        
        Returns:
            Dictionary with configuration, or None if user cancels
        """
        self._print_header()
        
        # Step 1: Choose strategy
        strategy_name = self._select_strategy()
        if strategy_name is None:
            return None
        
        schema = self.available_strategies[strategy_name]
        
        # Step 2: Configure parameters
        config = self._configure_strategy(schema)
        if config is None:
            return None
        
        # Step 3: Show summary and confirm
        if not self._show_summary_and_confirm(schema, config):
            return None
        
        # Step 4: Save config
        config_file = self._save_config(strategy_name, config)
        
        # Step 5: Ask if user wants to start bot
        if self._ask_start_bot():
            return {
                "strategy": strategy_name,
                "config": config,
                "config_file": config_file,
                "start_bot": True
            }
        else:
            return {
                "strategy": strategy_name,
                "config": config,
                "config_file": config_file,
                "start_bot": False
            }
    
    def _print_header(self):
        """Print welcome header."""
        print("\n" + "="*70)
        print("  Trading Bot - Interactive Configuration Wizard")
        print("="*70 + "\n")
    
    def _select_strategy(self) -> Optional[str]:
        """
        Let user select a strategy.
        
        Returns:
            Strategy name, or None if cancelled
        """
        choices = []
        for name, schema in self.available_strategies.items():
            choices.append({
                "name": f"{schema.display_name} - {schema.description[:60]}...",
                "value": name
            })
        
        try:
            strategy = questionary.select(
                "Which strategy would you like to run?",
                choices=choices,
                style=CUSTOM_STYLE
            ).ask()
            
            return strategy
        except KeyboardInterrupt:
            print("\n\nConfiguration cancelled.")
            return None
    
    def _configure_strategy(self, schema: StrategySchema) -> Optional[Dict[str, Any]]:
        """
        Configure all parameters for a strategy.
        
        Args:
            schema: Strategy schema
            
        Returns:
            Configuration dictionary, or None if cancelled
        """
        print(f"\n{'='*70}")
        print(f"  {schema.display_name} - Configuration")
        print(f"{'='*70}\n")
        
        config = {}
        total_params = len(schema.parameters)
        
        for idx, param in enumerate(schema.parameters, 1):
            print(f"\n[{idx}/{total_params}] ", end="")
            
            value = self._prompt_parameter(param)
            if value is None and param.required:
                # User cancelled
                print("\n\nConfiguration cancelled.")
                return None
            
            config[param.key] = value
            
            # Show confirmation
            if value is not None:
                display_value = self._format_value_display(value)
                print(f"✓ {param.key}: {display_value}")
        
        return config
    
    def _prompt_parameter(self, param: ParameterSchema) -> Any:
        """
        Prompt user for a single parameter.
        
        Args:
            param: Parameter schema
            
        Returns:
            User's input value (parsed and validated)
        """
        # Build prompt text
        prompt_text = param.prompt
        
        # Add default to prompt if configured
        if param.show_default_in_prompt and param.default is not None:
            default_str = self._format_value_display(param.default)
            prompt_text += f" [default: {default_str}]"
        
        # Add help text
        if param.help_text:
            print(f"  ❓ {param.help_text}")
        
        # Prompt based on type
        try:
            if param.param_type == ParameterType.CHOICE:
                return self._prompt_choice(prompt_text, param)
            elif param.param_type == ParameterType.MULTI_CHOICE:
                return self._prompt_multi_choice(prompt_text, param)
            elif param.param_type == ParameterType.BOOLEAN:
                return self._prompt_boolean(prompt_text, param)
            else:
                return self._prompt_text(prompt_text, param)
        except KeyboardInterrupt:
            return None
    
    def _prompt_choice(self, prompt: str, param: ParameterSchema) -> Optional[str]:
        """Prompt for single choice."""
        return questionary.select(
            prompt,
            choices=param.choices,
            default=param.default if param.default else None,
            style=CUSTOM_STYLE
        ).ask()
    
    def _prompt_multi_choice(self, prompt: str, param: ParameterSchema) -> Optional[list]:
        """Prompt for multiple choices."""
        default_list = param.default if isinstance(param.default, list) else \
                      [x.strip() for x in str(param.default).split(',')] if param.default else []
        
        result = questionary.checkbox(
            prompt,
            choices=param.choices,
            default=default_list,
            style=CUSTOM_STYLE
        ).ask()
        
        return result if result else default_list
    
    def _prompt_boolean(self, prompt: str, param: ParameterSchema) -> Optional[bool]:
        """Prompt for boolean."""
        return questionary.confirm(
            prompt,
            default=param.default if param.default is not None else True,
            style=CUSTOM_STYLE
        ).ask()
    
    def _prompt_text(self, prompt: str, param: ParameterSchema) -> Optional[Any]:
        """Prompt for text/number input with validation."""
        while True:
            # Get input
            result = questionary.text(
                prompt,
                default=str(param.default) if param.default is not None else "",
                style=CUSTOM_STYLE
            ).ask()
            
            if result is None:
                # User cancelled
                return None
            
            # Use default if empty and not required
            if result == "" and not param.required and param.default is not None:
                return param.default
            
            # Validate
            is_valid, error_msg = param.validate(result)
            if is_valid:
                return param.parse_value(result)
            else:
                print(f"  ❌ {error_msg}. Please try again.")
    
    def _format_value_display(self, value: Any) -> str:
        """Format a value for display."""
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            return "Yes" if value else "No"
        elif isinstance(value, Decimal):
            return str(value)
        else:
            return str(value)
    
    def _show_summary_and_confirm(self, schema: StrategySchema, config: Dict) -> bool:
        """
        Show configuration summary and ask for confirmation.
        
        Returns:
            True if user confirms, False otherwise
        """
        print(f"\n{'='*70}")
        print("  Configuration Summary")
        print(f"{'='*70}\n")
        
        print(f"Strategy: {schema.display_name}")
        print()
        
        # Show parameters by category if available
        if schema.categories:
            for category_name, param_keys in schema.categories.items():
                print(f"{category_name}:")
                for key in param_keys:
                    if key in config:
                        value_display = self._format_value_display(config[key])
                        print(f"  • {key}: {value_display}")
                print()
        else:
            # Show all parameters
            for key, value in config.items():
                value_display = self._format_value_display(value)
                print(f"  • {key}: {value_display}")
        
        print(f"{'='*70}\n")
        
        try:
            return questionary.confirm(
                "Does this configuration look correct?",
                default=True,
                style=CUSTOM_STYLE
            ).ask()
        except KeyboardInterrupt:
            return False
    
    def _save_config(self, strategy_name: str, config: Dict) -> Optional[str]:
        """
        Ask user if they want to save config and save it.
        
        Returns:
            Path to saved config file, or None if not saved
        """
        try:
            should_save = questionary.confirm(
                "💾 Would you like to save this configuration?",
                default=True,
                style=CUSTOM_STYLE
            ).ask()
            
            if not should_save:
                return None
            
            # Generate default filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            default_name = f"{strategy_name}_{timestamp}.yml"
            
            filename = questionary.text(
                "Configuration filename:",
                default=default_name,
                style=CUSTOM_STYLE
            ).ask()
            
            if not filename:
                return None
            
            # Ensure configs directory exists
            configs_dir = Path("configs")
            configs_dir.mkdir(exist_ok=True)
            
            # Full path
            config_path = configs_dir / filename
            
            # Save using YAML helper (will create in next task)
            from config_yaml import save_config_to_yaml
            save_config_to_yaml(strategy_name, config, config_path)
            
            print(f"✓ Configuration saved to: {config_path}")
            return str(config_path)
            
        except KeyboardInterrupt:
            return None
        except Exception as e:
            print(f"Warning: Could not save config: {e}")
            return None
    
    def _ask_start_bot(self) -> bool:
        """Ask if user wants to start the bot now."""
        try:
            return questionary.confirm(
                "🚀 Start trading bot now?",
                default=True,
                style=CUSTOM_STYLE
            ).ask()
        except KeyboardInterrupt:
            return False


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point for interactive config builder."""
    builder = InteractiveConfigBuilder()
    result = builder.build_config()
    
    if result is None:
        print("\nGoodbye!")
        sys.exit(0)
    
    if result["start_bot"]:
        print("\nStarting bot with your configuration...")
        print(f"Strategy: {result['strategy']}")
        if result["config_file"]:
            print(f"Config file: {result['config_file']}")
        
        # TODO: Launch bot here
        print("\n(Bot launch will be implemented in runbot.py integration)")
    else:
        print("\nConfiguration complete!")
        if result["config_file"]:
            print(f"\nTo use this configuration, run:")
            print(f"  python runbot.py --config {result['config_file']}")
        print("\nGoodbye!")


if __name__ == "__main__":
    main()

