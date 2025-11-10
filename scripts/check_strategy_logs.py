#!/usr/bin/env python3
"""
Quick script to check strategy error logs.

Usage:
    python scripts/check_strategy_logs.py <run_id>
    python scripts/check_strategy_logs.py --all  # Check all FATAL strategies
"""

import sys
import subprocess
import xmlrpc.client
from pathlib import Path
import argparse

def get_fatal_strategies():
    """Get all FATAL strategies from Supervisor."""
    try:
        supervisor = xmlrpc.client.ServerProxy('http://localhost:9001/RPC2')
        all_processes = supervisor.supervisor.getAllProcessInfo()
        
        fatal = [
            p for p in all_processes 
            if p['name'].startswith('strategy') and p['statename'] == 'FATAL'
        ]
        return fatal
    except Exception as e:
        print(f"Error connecting to Supervisor: {e}")
        return []

def check_logs(run_id: str, project_root: Path):
    """Check logs for a specific run_id (can be partial or full UUID)."""
    logs_dir = project_root / "logs"
    
    # Try to find the full log file by matching the run_id prefix
    stdout_log = None
    stderr_log = None
    
    if logs_dir.exists():
        # Look for log files matching the run_id
        for log_file in logs_dir.glob("strategy_*.out.log"):
            # Extract UUID from filename: strategy_<uuid>.out.log
            file_uuid = log_file.stem.replace('strategy_', '').replace('.out', '')
            if file_uuid.startswith(run_id) or run_id.startswith(file_uuid[:8]):
                stdout_log = log_file
                # Find corresponding stderr log
                stderr_log = logs_dir / f"strategy_{file_uuid}.err.log"
                run_id = file_uuid  # Use full UUID for display
                break
    
    # Fallback: try direct path if not found
    if stdout_log is None:
        stdout_log = logs_dir / f"strategy_{run_id}.out.log"
        stderr_log = logs_dir / f"strategy_{run_id}.err.log"
    
    print(f"\n{'='*80}")
    print(f"Checking logs for run_id: {run_id}")
    print(f"{'='*80}\n")
    
    # Check stderr first (errors)
    if stderr_log and stderr_log.exists():
        print(f"📋 STDERR LOG ({stderr_log}):")
        print("-" * 80)
        try:
            with open(stderr_log, 'r') as f:
                content = f.read()
                if content.strip():
                    print(content)
                else:
                    print("(empty)")
        except Exception as e:
            print(f"Error reading stderr log: {e}")
    else:
        print(f"⚠️  STDERR log not found: {stderr_log}")
    
    print("\n")
    
    # Check stdout
    if stdout_log and stdout_log.exists():
        print(f"📋 STDOUT LOG ({stdout_log}):")
        print("-" * 80)
        try:
            with open(stdout_log, 'r') as f:
                content = f.read()
                if content.strip():
                    # Show last 50 lines
                    lines = content.strip().split('\n')
                    if len(lines) > 50:
                        print("... (showing last 50 lines) ...\n")
                        print('\n'.join(lines[-50:]))
                    else:
                        print(content)
                else:
                    print("(empty)")
        except Exception as e:
            print(f"Error reading stdout log: {e}")
    else:
        print(f"⚠️  STDOUT log not found: {stdout_log}")
    
    print("\n")

def main():
    parser = argparse.ArgumentParser(description="Check strategy error logs")
    parser.add_argument("run_id", nargs="?", help="Run ID to check (first 8 chars)")
    parser.add_argument("--all", action="store_true", help="Check all FATAL strategies")
    
    args = parser.parse_args()
    
    project_root = Path(__file__).parent.parent
    
    if args.all:
        fatal_strategies = get_fatal_strategies()
        if not fatal_strategies:
            print("✅ No FATAL strategies found")
            return
        
        print(f"Found {len(fatal_strategies)} FATAL strategy(ies):\n")
        for strategy in fatal_strategies:
            name = strategy['name']
            # Extract run_id from supervisor program name
            # Format: strategys629bf736 -> 629bf736
            if name.startswith('strategy'):
                run_id = name.replace('strategy', '').replace('strategys', '').replace('strategyd', '')
                # Need full UUID - check database or logs directory
                logs_dir = project_root / "logs"
                # Try to find matching log file
                matching_logs = list(logs_dir.glob("strategy_*.out.log"))
                found = False
                for log_file in matching_logs:
                    # Extract UUID from filename: strategy_<uuid>.out.log
                    log_run_id = log_file.stem.replace('strategy_', '').replace('.out', '')
                    # Match by first 8 characters
                    if len(run_id) >= 8 and log_run_id.startswith(run_id[:8]):
                        check_logs(log_run_id, project_root)
                        found = True
                        break
                    elif len(log_run_id) >= 8 and run_id.startswith(log_run_id[:8]):
                        check_logs(log_run_id, project_root)
                        found = True
                        break
                
                if not found:
                    print(f"⚠️  Could not find logs for {name} (run_id prefix: {run_id})")
                    print(f"   Supervisor info: {strategy.get('spawnerr', 'No error message')}")
    elif args.run_id:
        # Try to find full UUID from partial run_id
        logs_dir = project_root / "logs"
        matching_logs = list(logs_dir.glob("strategy_*.out.log"))
        
        found = False
        for log_file in matching_logs:
            log_run_id = log_file.stem.replace('strategy_', '').replace('.out', '')
            # Match by first 8 characters or exact match
            if log_run_id.startswith(args.run_id) or args.run_id.startswith(log_run_id[:8]):
                check_logs(log_run_id, project_root)
                found = True
                break
        
        if not found:
            print(f"❌ No logs found for run_id starting with: {args.run_id}")
            print(f"   Searched in: {logs_dir}")
            if logs_dir.exists():
                print(f"   Available log files:")
                for log_file in sorted(logs_dir.glob("strategy_*.out.log"))[:10]:
                    uuid_part = log_file.stem.replace('strategy_', '').replace('.out', '')
                    print(f"     - {uuid_part[:8]}... ({log_file.name})")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

