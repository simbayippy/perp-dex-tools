#!/usr/bin/env python3
"""
Admin script to view all running strategy processes.

This script provides multiple ways to view running strategies:
1. From the database (strategy_runs table)
2. From Supervisor (all managed processes)
3. From system processes (ps command)

Usage:
    python scripts/view_all_strategies.py [--format table|json] [--source db|supervisor|all]
"""

import asyncio
import os
import sys
import subprocess
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import argparse
import dotenv

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables from .env file
env_file = project_root / ".env"
if env_file.exists():
    dotenv.load_dotenv(env_file)
else:
    # Try to load from current directory
    dotenv.load_dotenv()

from databases import Database
from helpers.unified_logger import get_logger

logger = get_logger("admin", "view_strategies")


async def get_strategies_from_db(database: Database) -> List[Dict[str, Any]]:
    """Get all strategies from database."""
    query = """
        SELECT 
            sr.id,
            sr.supervisor_program_name,
            sr.status,
            sr.control_api_port,
            sr.started_at,
            sr.last_heartbeat,
            sr.health_status,
            sr.error_count,
            u.username,
            a.account_name,
            sc.config_name,
            sc.strategy_type
        FROM strategy_runs sr
        JOIN users u ON sr.user_id = u.id
        JOIN accounts a ON sr.account_id = a.id
        JOIN strategy_configs sc ON sr.config_id = sc.id
        ORDER BY sr.started_at DESC
    """
    rows = await database.fetch_all(query)
    return [dict(row) for row in rows]


def get_strategies_from_supervisor(supervisor_rpc_url: str = "http://localhost:9001/RPC2") -> List[Dict[str, Any]]:
    """Get all strategy processes from Supervisor."""
    try:
        supervisor = xmlrpc.client.ServerProxy(supervisor_rpc_url)
        all_processes = supervisor.supervisor.getAllProcessInfo()
        
        # Filter for strategy processes
        strategy_processes = [
            p for p in all_processes 
            if p['name'].startswith('strategy')
        ]
        
        return strategy_processes
    except Exception as e:
        logger.error(f"Failed to connect to Supervisor: {e}")
        return []


def format_table_strategies(strategies: List[Dict[str, Any]], source: str) -> str:
    """Format strategies as a table."""
    if not strategies:
        return f"\nNo strategies found (source: {source})\n"
    
    output = []
    output.append(f"\n{'='*100}")
    output.append(f"Strategies from {source.upper()} ({len(strategies)} found)")
    output.append(f"{'='*100}\n")
    
    if source == "db":
        output.append(f"{'Run ID':<12} {'User':<15} {'Account':<15} {'Config':<20} {'Status':<12} {'Port':<6} {'Started':<20}")
        output.append("-" * 100)
        
        for s in strategies:
            run_id = str(s['id'])[:8]
            username = s.get('username', 'N/A')[:14]
            account = s.get('account_name', 'N/A')[:14]
            config = s.get('config_name', 'N/A')[:19]
            status = s.get('status', 'N/A')[:11]
            port = str(s.get('control_api_port', 'N/A'))[:5]
            started = s.get('started_at')
            started_str = started.strftime('%Y-%m-%d %H:%M:%S') if started else 'N/A'
            
            output.append(f"{run_id:<12} {username:<15} {account:<15} {config:<20} {status:<12} {port:<6} {started_str:<20}")
    
    elif source == "supervisor":
        output.append(f"{'Program Name':<30} {'State':<12} {'PID':<8} {'Uptime':<15} {'Log File':<40}")
        output.append("-" * 100)
        
        for p in strategies:
            name = p.get('name', 'N/A')[:29]
            state = p.get('statename', 'N/A')[:11]
            pid = str(p.get('pid', 'N/A'))[:7]
            
            # Calculate uptime
            start_time = p.get('start')
            uptime = 'N/A'
            if start_time:
                try:
                    start_dt = datetime.fromtimestamp(start_time)
                    uptime_delta = datetime.now() - start_dt
                    uptime = str(uptime_delta).split('.')[0]  # Remove microseconds
                except:
                    pass
            
            log_file = p.get('stdout_logfile', 'N/A')[:39]
            output.append(f"{name:<30} {state:<12} {pid:<8} {uptime:<15} {log_file:<40}")
    
    elif source == "ps":
        output.append(f"{'User':<10} {'PID':<8} {'CPU%':<8} {'MEM%':<8} {'Command':<60}")
        output.append("-" * 100)
        
        for p in strategies:
            user = p.get('user', 'N/A')[:9]
            pid = p.get('pid', 'N/A')[:7]
            cpu = p.get('cpu', 'N/A')[:7]
            mem = p.get('mem', 'N/A')[:7]
            cmd = p.get('command', 'N/A')[:59]
            output.append(f"{user:<10} {pid:<8} {cpu:<8} {mem:<8} {cmd:<60}")
    
    output.append("")
    return "\n".join(output)


async def main():
    parser = argparse.ArgumentParser(description="View all running strategy processes")
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)"
    )
    parser.add_argument(
        "--source",
        choices=["db", "supervisor", "all"],
        default="all",
        help="Data source: db (database), supervisor, or all (default: all)"
    )
    
    args = parser.parse_args()
    
    # Load database connection from environment
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable not set")
        sys.exit(1)
    
    database = Database(database_url)
    
    try:
        await database.connect()
        
        results = {}
        
        if args.source in ["db", "all"]:
            logger.info("Fetching strategies from database...")
            db_strategies = await get_strategies_from_db(database)
            results["database"] = db_strategies
            
            if args.format == "table":
                print(format_table_strategies(db_strategies, "db"))
            else:
                print(f"\nDatabase strategies ({len(db_strategies)}):")
                import json
                print(json.dumps(db_strategies, indent=2, default=str))
        
        if args.source in ["supervisor", "all"]:
            logger.info("Fetching strategies from Supervisor...")
            supervisor_strategies = get_strategies_from_supervisor()
            results["supervisor"] = supervisor_strategies
            
            if args.format == "table":
                print(format_table_strategies(supervisor_strategies, "supervisor"))
            else:
                print(f"\nSupervisor strategies ({len(supervisor_strategies)}):")
                import json
                print(json.dumps(supervisor_strategies, indent=2, default=str))
        
        # Summary
        print("\n" + "="*100)
        print("SUMMARY")
        print("="*100)
        if "database" in results:
            print(f"Database: {len(results['database'])} strategy runs")
        if "supervisor" in results:
            print(f"Supervisor: {len(results['supervisor'])} managed processes")
        if "processes" in results:
            print(f"System processes: {len(results['processes'])} matching processes")
        print()
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

