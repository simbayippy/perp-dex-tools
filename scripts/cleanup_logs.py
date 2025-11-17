#!/usr/bin/env python3
"""
Clean up log files to make them more human-readable.

Removes:
- ANSI escape codes (color formatting)
- Timestamps
- Log levels (INFO, WARNING, ERROR, etc.)

Usage:
    python scripts/logs/cleanup_logs.py <log_file>              # Clean single file
    python scripts/logs/cleanup_logs.py <log_file> -o <output>  # Specify output file
    python scripts/logs/cleanup_logs.py <log_file> --in-place   # Modify file in place
"""

import re
import sys
import argparse
from pathlib import Path
from typing import Optional, Tuple


def remove_ansi_codes(text: str) -> str:
    """Remove ANSI escape codes from text."""
    # Pattern matches ANSI escape sequences like [32m, [0m, [1m, etc.
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def parse_log_line(line: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a log line and extract module info and message.
    
    Handles two formats:
    1. Original format with ANSI codes: [32m2025-11-16 23:06:43[0m | [1mINFO[0m | [36mmodule:func:line[0m | [1mmsg[0m
    2. Already cleaned format: module:func:line | message
    
    Returns:
        Tuple of (module_info, message) or (None, None) if line doesn't match format
    """
    # First remove ANSI codes
    line = remove_ansi_codes(line)
    
    # Split by pipe separator
    parts = [p.strip() for p in line.split('|')]
    
    if len(parts) == 4:
        # Original format: timestamp | log_level | module_info | message
        # parts[0] = timestamp (remove)
        # parts[1] = log level (remove)
        # parts[2] = module:function:line (keep)
        # parts[3] = message (keep)
        module_info = parts[2].strip()
        message = parts[3].strip()
        return module_info, message
    elif len(parts) == 2:
        # Already cleaned format: module_info | message
        module_info = parts[0].strip()
        message = parts[1].strip()
        # Check if first part looks like module:function:line format
        if ':' in module_info and module_info.split(':')[0]:
            return module_info, message
    
    return None, None


def clean_log_file(input_path: Path, output_path: Optional[Path] = None) -> None:
    """
    Clean a log file and write to output with fixed-width module info.
    
    Args:
        input_path: Path to input log file
        output_path: Path to output file (if None, prints to stdout)
    """
    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    # First pass: parse all lines and find max width
    parsed_lines = []
    max_module_width = 0
    
    with open(input_path, 'r', encoding='utf-8') as f:
        for line in f:
            module_info, message = parse_log_line(line)
            if module_info is not None and message is not None:
                parsed_lines.append((module_info, message))
                max_module_width = max(max_module_width, len(module_info))
            else:
                # Line doesn't match format - keep cleaned line as-is
                cleaned_line = remove_ansi_codes(line).strip()
                if cleaned_line:
                    parsed_lines.append((None, cleaned_line))
    
    # Second pass: format with fixed width
    cleaned_lines = []
    for module_info, message in parsed_lines:
        if module_info is not None:
            # Format with fixed width, right-aligned module info
            formatted_line = f"{module_info:<{max_module_width}} | {message}"
            cleaned_lines.append(formatted_line)
        else:
            # Line that didn't match format - keep as is
            cleaned_lines.append(message)
    
    output_text = '\n'.join(cleaned_lines)
    
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_text)
        print(f"Cleaned log written to: {output_path}")
    else:
        print(output_text)


def main():
    parser = argparse.ArgumentParser(
        description='Clean up log files by removing ANSI codes, timestamps, and log levels'
    )
    parser.add_argument(
        'input_file',
        type=Path,
        help='Input log file to clean'
    )
    parser.add_argument(
        '-o', '--output',
        type=Path,
        default=None,
        help='Output file path (default: stdout)'
    )
    parser.add_argument(
        '--in-place',
        action='store_true',
        help='Modify the input file in place'
    )
    
    args = parser.parse_args()
    
    if args.in_place:
        if args.output:
            print("Error: Cannot use --in-place with --output", file=sys.stderr)
            sys.exit(1)
        # Create temporary output, then replace input
        temp_output = args.input_file.with_suffix(args.input_file.suffix + '.tmp')
        clean_log_file(args.input_file, temp_output)
        temp_output.replace(args.input_file)
        print(f"Cleaned log file in place: {args.input_file}")
    else:
        clean_log_file(args.input_file, args.output)


if __name__ == '__main__':
    main()

