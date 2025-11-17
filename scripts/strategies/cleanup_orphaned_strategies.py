#!/usr/bin/env python3
"""
Cleanup Orphaned Supervisor Strategies

Finds and optionally removes supervisor process groups that don't have
corresponding database entries (orphaned strategies).

Usage:
    python scripts/cleanup_orphaned_strategies.py  # Dry run (shows what would be cleaned)
    python scripts/cleanup_orphaned_strategies.py --execute  # Actually clean up
"""

import asyncio
import sys
import os
import argparse
import tempfile
from pathlib import Path
from typing import List, Dict, Any

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
from databases import Database
from helpers.unified_logger import get_logger
import xmlrpc.client

dotenv.load_dotenv()

logger = get_logger("scripts", "cleanup_orphaned_strategies")


def get_supervisor_client():
    """Get supervisor XML-RPC client."""
    supervisor_rpc_url = os.getenv('SUPERVISOR_RPC_URL', 'http://localhost:9001/RPC2')
    return xmlrpc.client.ServerProxy(supervisor_rpc_url)


async def get_db_strategies(db: Database) -> Dict[str, Dict[str, Any]]:
    """Get all strategies from database."""
    rows = await db.fetch_all(
        """
        SELECT id, supervisor_program_name, status
        FROM strategy_runs
        ORDER BY started_at DESC
        """
    )
    
    strategies = {}
    for row in rows:
        # Convert Row to dict for safe access
        row_dict = dict(row)
        supervisor_name = row_dict.get('supervisor_program_name')
        if supervisor_name:
            strategies[supervisor_name] = {
                'run_id': str(row_dict['id']),
                'status': row_dict.get('status'),
                'supervisor_name': supervisor_name
            }
    
    return strategies


def get_supervisor_strategies() -> List[Dict[str, Any]]:
    """Get all strategies from Supervisor."""
    try:
        supervisor = get_supervisor_client()
        all_processes = supervisor.supervisor.getAllProcessInfo()
        
        strategies = []
        for proc in all_processes:
            # XML-RPC returns dicts directly
            if not isinstance(proc, dict):
                # Try to convert if possible
                try:
                    proc = dict(proc)
                except (TypeError, ValueError):
                    logger.warning(f"Skipping non-dict process info: {type(proc)}")
                    continue
            
            # Only include strategy processes (start with "strategy" or "strategys")
            name = proc.get('name', '')
            if name and name.startswith('strategy'):
                strategies.append({
                    'name': name,
                    'state': proc.get('statename', 'UNKNOWN'),
                    'pid': proc.get('pid', 0),
                    'start': proc.get('start', 0),
                    'stop': proc.get('stop', 0),
                    'exitstatus': proc.get('exitstatus', 0),
                    'spawnerr': proc.get('spawnerr', ''),
                    'stdout_logfile': proc.get('stdout_logfile', ''),
                    'stderr_logfile': proc.get('stderr_logfile', ''),
                })
        
        return strategies
    except Exception as e:
        logger.error(f"Failed to get supervisor strategies: {e}", exc_info=True)
        return []


def find_orphaned_strategies(db_strategies: Dict[str, Dict], supervisor_strategies: List[Dict]) -> List[Dict[str, Any]]:
    """Find strategies that exist in Supervisor but not in database."""
    orphaned = []
    
    for sup_strat in supervisor_strategies:
        supervisor_name = sup_strat['name']
        if supervisor_name not in db_strategies:
            orphaned.append(sup_strat)
    
    return orphaned


def cleanup_strategy(supervisor_name: str, execute: bool = False) -> Dict[str, Any]:
    """
    Clean up a single orphaned strategy.
    
    Returns:
        Dict with cleanup results
    """
    result = {
        'supervisor_name': supervisor_name,
        'stopped': False,
        'removed_from_supervisor': False,
        'config_file_deleted': False,
        'supervisor_config_deleted': False,
        'errors': []
    }
    
    if not execute:
        return result
    
    try:
        supervisor = get_supervisor_client()
        
        # Stop process if running
        try:
            process_info = supervisor.supervisor.getProcessInfo(supervisor_name)
            state = process_info.get('statename', 'UNKNOWN')
            
            if state in ('RUNNING', 'STARTING'):
                supervisor.supervisor.stopProcess(supervisor_name)
                result['stopped'] = True
                logger.info(f"Stopped process: {supervisor_name}")
        except xmlrpc.client.Fault as fault:
            if 'BAD_NAME' not in str(fault):
                result['errors'].append(f"Failed to stop: {fault}")
        except Exception as e:
            result['errors'].append(f"Failed to stop: {e}")
        
        # Remove from supervisor
        try:
            supervisor.supervisor.removeProcessGroup(supervisor_name)
            result['removed_from_supervisor'] = True
            logger.info(f"Removed process group: {supervisor_name}")
        except xmlrpc.client.Fault as fault:
            if 'BAD_NAME' in str(fault) or 'NOT_RUNNING' in str(fault):
                result['removed_from_supervisor'] = True  # Already removed
            else:
                result['errors'].append(f"Failed to remove from supervisor: {fault}")
        except Exception as e:
            result['errors'].append(f"Failed to remove from supervisor: {e}")
        
        # Try to find and delete config file (extract run_id from supervisor_name)
        # Supervisor names are like "strategy{run_id}" or "strategys{run_id}"
        run_id_part = supervisor_name.replace('strategy', '').replace('strategys', '')
        if run_id_part:
            # Try to find the full UUID by searching temp files
            temp_dir = Path(tempfile.gettempdir())
            config_files = list(temp_dir.glob(f"strategy_*{run_id_part}*.yml"))
            for config_file in config_files:
                try:
                    config_file.unlink()
                    result['config_file_deleted'] = True
                    logger.info(f"Deleted config file: {config_file}")
                except Exception as e:
                    result['errors'].append(f"Failed to delete config file {config_file}: {e}")
        
        # Delete supervisor config file
        supervisor_config_file = Path("/etc/supervisor/conf.d") / f"{supervisor_name}.conf"
        if supervisor_config_file.exists():
            try:
                import subprocess
                subprocess.run(["sudo", "rm", str(supervisor_config_file)], check=False)
                result['supervisor_config_deleted'] = True
                logger.info(f"Deleted supervisor config: {supervisor_config_file}")
                
                # Reload supervisor config
                try:
                    supervisor.supervisor.reloadConfig()
                    logger.info("Reloaded supervisor config")
                except Exception as e:
                    result['errors'].append(f"Failed to reload supervisor config: {e}")
            except Exception as e:
                result['errors'].append(f"Failed to delete supervisor config: {e}")
        
    except Exception as e:
        result['errors'].append(f"Cleanup failed: {e}")
        logger.error(f"Failed to cleanup {supervisor_name}: {e}", exc_info=True)
    
    return result


async def main():
    """Main cleanup function."""
    parser = argparse.ArgumentParser(description='Cleanup orphaned supervisor strategies')
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Actually perform cleanup (default is dry-run)'
    )
    args = parser.parse_args()
    
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable is not set")
        sys.exit(1)
    
    db = Database(database_url)
    await db.connect()
    
    try:
        print("\n" + "="*70)
        print("Orphaned Strategy Cleanup")
        print("="*70)
        
        if not args.execute:
            print("\n⚠️  DRY RUN MODE - No changes will be made")
            print("   Use --execute to actually perform cleanup\n")
        
        # Get strategies from both sources
        print("Fetching strategies from database...")
        db_strategies = await get_db_strategies(db)
        print(f"  Found {len(db_strategies)} strategies in database")
        
        print("\nFetching strategies from Supervisor...")
        supervisor_strategies = get_supervisor_strategies()
        print(f"  Found {len(supervisor_strategies)} strategies in Supervisor")
        
        # Find orphaned strategies
        orphaned = find_orphaned_strategies(db_strategies, supervisor_strategies)
        
        if not orphaned:
            print("\n✅ No orphaned strategies found!")
            return
        
        print(f"\n⚠️  Found {len(orphaned)} orphaned strategy(ies):")
        print("-"*70)
        
        for strat in orphaned:
            state = strat['state']
            state_emoji = {
                'RUNNING': '🟢',
                'STOPPED': '⚫',
                'FATAL': '🔴',
                'STARTING': '🟡',
            }.get(state, '⚪')
            
            print(f"{state_emoji} {strat['name']:<30} State: {state}")
            if strat.get('spawnerr'):
                print(f"   Error: {strat['spawnerr']}")
        
        if not args.execute:
            print("\n" + "="*70)
            print("To actually clean up these strategies, run:")
            print(f"  python {sys.argv[0]} --execute")
            print("="*70 + "\n")
            return
        
        # Perform cleanup
        print("\n" + "="*70)
        print("Cleaning up orphaned strategies...")
        print("="*70)
        
        results = []
        for strat in orphaned:
            supervisor_name = strat['name']
            print(f"\nCleaning up: {supervisor_name}")
            result = cleanup_strategy(supervisor_name, execute=True)
            results.append(result)
            
            if result['errors']:
                print(f"  ⚠️  Errors: {', '.join(result['errors'])}")
            else:
                actions = []
                if result['stopped']:
                    actions.append("stopped")
                if result['removed_from_supervisor']:
                    actions.append("removed from supervisor")
                if result['config_file_deleted']:
                    actions.append("deleted config file")
                if result['supervisor_config_deleted']:
                    actions.append("deleted supervisor config")
                
                if actions:
                    print(f"  ✅ {', '.join(actions)}")
                else:
                    print(f"  ℹ️  Already cleaned up")
        
        # Summary
        print("\n" + "="*70)
        print("Cleanup Summary")
        print("="*70)
        
        successful = sum(1 for r in results if not r['errors'])
        failed = len(results) - successful
        
        print(f"Total processed: {len(results)}")
        print(f"✅ Successful: {successful}")
        if failed > 0:
            print(f"❌ Failed: {failed}")
        
        for result in results:
            if result['errors']:
                print(f"\n❌ {result['supervisor_name']}:")
                for error in result['errors']:
                    print(f"   - {error}")
        
        print("\n" + "="*70 + "\n")
        
    except Exception as e:
        logger.error(f"Cleanup error: {e}", exc_info=True)
        raise
    finally:
        await db.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to run cleanup script: {e}")
        sys.exit(1)

