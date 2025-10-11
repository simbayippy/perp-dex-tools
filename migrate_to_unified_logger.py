#!/usr/bin/env python3
"""
Script to migrate exchange client files from TradingLogger to UnifiedLogger.

This script will:
1. Update import statements
2. Replace TradingLogger initialization with get_exchange_logger
3. Convert .log() method calls to specific level methods (info, warning, error, etc.)
4. Preserve the original formatting and structure
"""

import os
import re
import glob
from pathlib import Path


def update_imports(content: str) -> str:
    """Update import statements to use unified logger."""
    # Replace the import statement
    content = re.sub(
        r'from helpers\.logger import TradingLogger',
        'from helpers.unified_logger import get_exchange_logger',
        content
    )
    return content


def update_logger_initialization(content: str) -> str:
    """Update logger initialization from TradingLogger to get_exchange_logger."""
    # Pattern to match TradingLogger initialization
    # Matches: self.logger = TradingLogger(exchange="name", ticker=..., log_to_console=...)
    pattern = r'self\.logger = TradingLogger\(\s*exchange=(["\'])([^"\']+)\1\s*,\s*ticker=([^,]+)(?:\s*,\s*log_to_console=[^)]+)?\s*\)'
    
    def replacement(match):
        exchange_name = match.group(2)
        ticker_param = match.group(3)
        return f'self.logger = get_exchange_logger("{exchange_name}", {ticker_param})'
    
    content = re.sub(pattern, replacement, content)
    return content


def update_log_method_calls(content: str) -> str:
    """Convert .log() method calls to specific level methods."""
    
    # Pattern to match self.logger.log("message", "LEVEL") calls
    # This handles both single and multi-line log calls
    def replace_log_call(match):
        indent = match.group(1)
        message_part = match.group(2)
        level = match.group(3).upper()
        
        # Map log levels to method names
        level_map = {
            'DEBUG': 'debug',
            'INFO': 'info', 
            'WARNING': 'warning',
            'WARN': 'warning',
            'ERROR': 'error',
            'CRITICAL': 'critical'
        }
        
        method_name = level_map.get(level, 'info')
        return f'{indent}self.logger.{method_name}({message_part})'
    
    # Pattern for single-line log calls
    pattern1 = r'^(\s*)self\.logger\.log\(([^,]+),\s*["\']([^"\']+)["\']\s*\)'
    content = re.sub(pattern1, replace_log_call, content, flags=re.MULTILINE)
    
    # Pattern for multi-line log calls (more complex)
    # This handles cases where the message spans multiple lines
    def replace_multiline_log(match):
        indent = match.group(1)
        full_content = match.group(2)
        
        # Extract the level from the end of the call
        level_match = re.search(r',\s*["\']([^"\']+)["\']\s*\)$', full_content)
        if level_match:
            level = level_match.group(1).upper()
            # Remove the level parameter from the content
            message_content = re.sub(r',\s*["\']([^"\']+)["\']\s*\)$', ')', full_content)
            
            level_map = {
                'DEBUG': 'debug',
                'INFO': 'info',
                'WARNING': 'warning', 
                'WARN': 'warning',
                'ERROR': 'error',
                'CRITICAL': 'critical'
            }
            
            method_name = level_map.get(level, 'info')
            return f'{indent}self.logger.{method_name}({message_content}'
        
        return match.group(0)  # Return unchanged if pattern doesn't match expected format
    
    # Handle multi-line log calls
    pattern2 = r'^(\s*)self\.logger\.log\(\s*\n?(.*?)\n?\s*\)$'
    content = re.sub(pattern2, replace_multiline_log, content, flags=re.MULTILINE | re.DOTALL)
    
    return content


def process_file(file_path: Path) -> bool:
    """Process a single file to migrate to unified logger."""
    try:
        print(f"Processing {file_path}...")
        
        # Read the file
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if file uses TradingLogger
        if 'TradingLogger' not in content:
            print(f"  Skipping {file_path} - no TradingLogger found")
            return False
        
        # Apply transformations
        original_content = content
        content = update_imports(content)
        content = update_logger_initialization(content)
        content = update_log_method_calls(content)
        
        # Check if any changes were made
        if content == original_content:
            print(f"  No changes needed for {file_path}")
            return False
        
        # Write back the updated content
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"  ✅ Updated {file_path}")
        return True
        
    except Exception as e:
        print(f"  ❌ Error processing {file_path}: {e}")
        return False


def find_exchange_client_files() -> list[Path]:
    """Find all exchange client Python files."""
    exchange_clients_dir = Path("exchange_clients")
    
    if not exchange_clients_dir.exists():
        print("Error: exchange_clients directory not found!")
        return []
    
    # Find all Python files in exchange_clients subdirectories
    client_files = []
    for pattern in ["*/client.py", "*/clients.py", "*/*client*.py"]:
        client_files.extend(exchange_clients_dir.glob(pattern))
    
    # Also check for any Python files that might contain TradingLogger
    for py_file in exchange_clients_dir.rglob("*.py"):
        if py_file not in client_files:
            with open(py_file, 'r', encoding='utf-8') as f:
                try:
                    if 'TradingLogger' in f.read():
                        client_files.append(py_file)
                except UnicodeDecodeError:
                    continue
    
    return sorted(set(client_files))


def main():
    """Main function to process all exchange client files."""
    print("🔄 Migrating exchange clients to unified logger...")
    print("=" * 60)
    
    # Find all relevant files
    client_files = find_exchange_client_files()
    
    if not client_files:
        print("No exchange client files found!")
        return
    
    print(f"Found {len(client_files)} files to check:")
    for file_path in client_files:
        print(f"  - {file_path}")
    print()
    
    # Process each file
    updated_count = 0
    for file_path in client_files:
        if process_file(file_path):
            updated_count += 1
    
    print()
    print("=" * 60)
    print(f"✅ Migration complete!")
    print(f"   Updated {updated_count} out of {len(client_files)} files")
    
    if updated_count > 0:
        print("\n📋 Next steps:")
        print("1. Review the changes in your IDE")
        print("2. Test the updated exchange clients")
        print("3. Run any linting/formatting tools if needed")
        print("4. Consider removing the old helpers/logger.py file if no longer used")


if __name__ == "__main__":
    main()
