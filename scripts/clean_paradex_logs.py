#!/usr/bin/env python3
"""
Clean PARADEX Order Book Logs

Removes verbose PARADEX order book debug logs from log files.
Filters out lines containing:
- [PARADEX] 🔍 Raw order book data keys
- [PARADEX] Order book snapshot received
- [PARADEX] 🔍 Insert item
- [PARADEX] Processed ... inserts
- [PARADEX] Best prices

Usage:
    python clean_paradex_logs.py input_file.md                    # Clean in-place
    python clean_paradex_logs.py input_file.md output_file.md     # Write to new file
    python clean_paradex_logs.py input_file.md --backup           # Create backup before cleaning
"""

import argparse
import re
import shutil
from pathlib import Path
from typing import List


def should_keep_line(line: str) -> bool:
    """
    Determine if a line should be kept (True) or filtered out (False).
    
    Args:
        line: Log line to check
        
    Returns:
        True if line should be kept, False if it should be filtered out
    """
    # Patterns to filter out
    patterns = [
        r'\[PARADEX\].*🔍 Raw order book data keys',
        r'\[PARADEX\].*Order book snapshot received',
        r'\[PARADEX\].*🔍 Insert item',
        r'\[PARADEX\].*Processed \d+ inserts',
        r'\[PARADEX\].*Best prices:',
    ]
    
    # Check if line matches any filter pattern
    for pattern in patterns:
        if re.search(pattern, line):
            return False
    
    return True


def clean_log_file(input_path: Path, output_path: Path) -> tuple[int, int]:
    """
    Clean PARADEX order book logs from a file.
    
    Args:
        input_path: Path to input log file
        output_path: Path to output cleaned file
        
    Returns:
        Tuple of (total_lines, removed_lines)
    """
    total_lines = 0
    removed_lines = 0
    
    with open(input_path, 'r', encoding='utf-8') as infile:
        with open(output_path, 'w', encoding='utf-8') as outfile:
            for line in infile:
                total_lines += 1
                if should_keep_line(line):
                    outfile.write(line)
                else:
                    removed_lines += 1
    
    return total_lines, removed_lines


def main():
    parser = argparse.ArgumentParser(
        description='Clean PARADEX order book debug logs from log files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        'input_file',
        type=str,
        help='Input log file to clean'
    )
    parser.add_argument(
        'output_file',
        type=str,
        nargs='?',
        default=None,
        help='Output file (default: overwrite input file)'
    )
    parser.add_argument(
        '--backup',
        action='store_true',
        help='Create backup of input file before cleaning'
    )
    
    args = parser.parse_args()
    
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: Input file '{input_path}' does not exist")
        return 1
    
    # Determine output path
    if args.output_file:
        output_path = Path(args.output_file)
    else:
        output_path = input_path
    
    # Create backup if requested
    if args.backup and output_path == input_path:
        backup_path = input_path.with_suffix(input_path.suffix + '.bak')
        print(f"Creating backup: {backup_path}")
        shutil.copy2(input_path, backup_path)
    
    # Clean the file
    print(f"Cleaning log file: {input_path}")
    total_lines, removed_lines = clean_log_file(input_path, output_path)
    
    print(f"\nCleaning complete!")
    print(f"  Total lines: {total_lines:,}")
    print(f"  Removed lines: {removed_lines:,}")
    print(f"  Kept lines: {total_lines - removed_lines:,}")
    print(f"  Output file: {output_path}")
    
    return 0


if __name__ == '__main__':
    exit(main())

