"""
Process Manager for managing strategy processes via Supervisor

Uses Supervisor XML-RPC API to spawn, stop, and monitor strategy processes.
Each strategy runs as an independent Supervisor program.
"""

import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from databases import Database
import xmlrpc.client
import json

from helpers.unified_logger import get_logger
from telegram_bot_service.managers.port_manager import PortManager


logger = get_logger("core", "process_manager")


class StrategyProcessManager:
    """Manages strategy processes using Supervisor"""
    
    # Resource limits
    RESOURCE_LIMITS = {
        'max_strategies_total': 15,
        'max_strategies_per_user': 3,
        'min_free_memory_mb': 500,
        'strategy_memory_estimate_mb': 100
    }
    
    def __init__(
        self,
        database: Database,
        supervisor_rpc_url: str = "http://localhost:9001/RPC2",
        supervisor_conf_dir: str = "/etc/supervisor/conf.d",
        project_root: Optional[str] = None,
        venv_path: Optional[str] = None,
        vps_user: Optional[str] = None
    ):
        """
        Initialize StrategyProcessManager.
        
        Args:
            database: Database connection instance
            supervisor_rpc_url: Supervisor XML-RPC URL (default: http://localhost:9001/RPC2)
            supervisor_conf_dir: Directory for Supervisor config files (default: /etc/supervisor/conf.d)
            project_root: Project root directory (default: auto-detect)
            venv_path: Path to virtual environment (default: auto-detect)
            vps_user: VPS user to run processes as (default: current user)
        """
        self.database = database
        self.supervisor_rpc_url = supervisor_rpc_url
        self.supervisor_conf_dir = Path(supervisor_conf_dir)
        self.port_manager = PortManager(database)
        
        # Auto-detect paths if not provided
        if project_root is None:
            # Auto-detect project root
            # process_manager.py is at: telegram_bot_service/managers/process_manager.py
            # So we need to go up 3 levels to get to project root
            project_root = Path(__file__).resolve().parent.parent.parent
        self.project_root = Path(project_root)
        
        if venv_path is None:
            # Look for venv in project root
            venv_candidates = [
                self.project_root / "venv",
                self.project_root / ".venv",
            ]
            venv_path = None
            for candidate in venv_candidates:
                if candidate.exists():
                    venv_path = candidate
                    break
            if venv_path is None:
                # Fallback: use system Python
                venv_path = None
        
        self.venv_path = Path(venv_path) if venv_path else None
        
        if vps_user is None:
            vps_user = os.getenv("USER", "root")
        self.vps_user = vps_user
        
        # Supervisor XML-RPC client (lazy initialization)
        self._supervisor_client = None
        
        logger.info(f"ProcessManager initialized:")
        logger.info(f"  Supervisor RPC: {self.supervisor_rpc_url}")
        logger.info(f"  Config dir: {self.supervisor_conf_dir}")
        logger.info(f"  Project root: {self.project_root}")
        logger.info(f"  Venv: {self.venv_path}")
        logger.info(f"  User: {self.vps_user}")
    
    def _get_supervisor_client(self) -> xmlrpc.client.ServerProxy:
        """Get or create Supervisor XML-RPC client."""
        if self._supervisor_client is None:
            self._supervisor_client = xmlrpc.client.ServerProxy(self.supervisor_rpc_url)
        return self._supervisor_client
    
    async def spawn_strategy(
        self,
        user_id: str,
        account_id: str,
        account_name: str,
        config_id: str,
        config_data: Dict[str, Any],
        is_admin: bool = False
    ) -> Dict[str, Any]:
        """
        Spawn a new strategy process via Supervisor.
        
        Args:
            user_id: User UUID
            account_id: Account UUID
            account_name: Account name
            config_id: Config UUID
            config_data: Config data (JSONB from database)
            is_admin: Whether user is an admin (admins can run without proxy)
            
        Returns:
            Dict with run_id, supervisor_program_name, port, status
        """
        # Generate run_id
        run_id = str(uuid.uuid4())
        # Supervisor program names: use only alphanumeric (no hyphens or underscores)
        # Remove hyphens from UUID and take first 8 chars
        run_id_clean = run_id.replace('-', '')[:8]
        # Ensure it starts with a letter (Supervisor requirement)
        if run_id_clean[0].isdigit():
            # Prefix with 's' if starts with number
            supervisor_program_name = f"strategys{run_id_clean}"
        else:
            supervisor_program_name = f"strategy{run_id_clean}"
        
        # Allocate port
        port = await self.port_manager.allocate_port()
        if port is None:
            raise RuntimeError("No available ports for control API")
        
        # Write config to temp file
        config_file = Path(tempfile.gettempdir()) / f"strategy_{run_id}.yml"
        try:
            import yaml
            from decimal import Decimal
            from datetime import datetime
            
            # Register Decimal representer for YAML
            def decimal_representer(dumper, data):
                return dumper.represent_scalar('tag:yaml.org,2002:float', str(data))
            yaml.add_representer(Decimal, decimal_representer)
            
            # Ensure config_data has the correct structure
            # config_data should be the full config dict with 'strategy' and 'config' keys
            if isinstance(config_data, dict):
                # If config_data is already the full structure, use it
                if 'strategy' in config_data and 'config' in config_data:
                    full_config = config_data
                else:
                    # Otherwise, wrap it (assume it's the config dict)
                    # Get strategy_type from database
                    config_row = await self.database.fetch_one(
                        "SELECT strategy_type FROM strategy_configs WHERE id = :id",
                        {"id": config_id}
                    )
                    strategy_type = config_row['strategy_type'] if config_row else 'funding_arbitrage'
                    
                    full_config = {
                        "strategy": strategy_type,
                        "created_at": datetime.now().isoformat(),
                        "version": "1.0",
                        "config": config_data
                    }
            else:
                raise ValueError("config_data must be a dictionary")
            
            with open(config_file, 'w') as f:
                yaml.dump(
                    full_config,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                    indent=2
                )
        except Exception as e:
            raise RuntimeError(f"Failed to write config file: {e}")
        
        # Determine Python executable
        if self.venv_path:
            python_exe = self.venv_path / "bin" / "python"
        else:
            python_exe = Path("python3")  # Use system Python
        
        # Build command
        runbot_path = self.project_root / "runbot.py"
        command_parts = [
            f"nice -n 10 {python_exe} {runbot_path}",
            f"--config {config_file}",
            f"--account {account_name}",
            f"--enable-control-api",
            f"--control-api-port {port}"
        ]
        
        # Only add --enable-proxy if not admin (admins can run on VPS IP)
        if not is_admin:
            command_parts.append("--enable-proxy")
            logger.info(f"Non-admin user: proxy enabled for account {account_name}")
        else:
            logger.info(f"Admin user: proxy disabled, running on VPS IP for account {account_name}")
        
        command = " ".join(command_parts)
        
        # Log file paths
        logs_dir = self.project_root / "logs"
        logs_dir.mkdir(exist_ok=True)
        stdout_log = logs_dir / f"strategy_{run_id}.out.log"
        stderr_log = logs_dir / f"strategy_{run_id}.err.log"
        
        # Create Supervisor config
        supervisor_config = f"""[program:{supervisor_program_name}]
command={command}
directory={self.project_root}
user={self.vps_user}
autostart=true
autorestart=unexpected
priority=999
stopwaitsecs=30
stderr_logfile={stderr_log}
stdout_logfile={stdout_log}
"""
        
        # Log the config content for debugging
        logger.debug(f"Supervisor config content:\n{supervisor_config}")
        logger.info(f"Supervisor program name: {supervisor_program_name}")
        
        # Write Supervisor config file (requires sudo)
        config_file_path = self.supervisor_conf_dir / f"{supervisor_program_name}.conf"
        try:
            # Use sudo to write config file
            subprocess.run(
                ["sudo", "tee", str(config_file_path)],
                input=supervisor_config.encode(),
                check=True,
                capture_output=True
            )
            logger.info(f"Created Supervisor config: {config_file_path}")
            
            # Verify the file was written correctly
            try:
                verify_result = subprocess.run(
                    ["sudo", "cat", str(config_file_path)],
                    capture_output=True,
                    text=True,
                    check=True
                )
                logger.debug(f"Verified config file content:\n{verify_result.stdout}")
            except Exception as verify_error:
                logger.warning(f"Could not verify config file: {verify_error}")
            
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else "Unknown error"
            logger.error(f"Failed to create Supervisor config. stderr: {stderr_msg}, stdout: {e.stdout.decode() if e.stdout else 'None'}")
            raise RuntimeError(f"Failed to create Supervisor config: {stderr_msg}")
        
        # Reload Supervisor and verify
        try:
            supervisor = self._get_supervisor_client()
            
            # Check Supervisor logs for errors before reload
            logger.debug("Reloading Supervisor config...")
            try:
                # First, reread configs to ensure Supervisor sees the new file
                try:
                    reread_result = supervisor.supervisor.reloadConfig()
                    logger.info(f"Supervisor reloadConfig result: {reread_result}")
                except xmlrpc.client.Fault as reread_fault:
                    fault_msg = reread_fault.faultString if hasattr(reread_fault, 'faultString') else str(reread_fault)
                    logger.warning(f"Supervisor reloadConfig returned fault (might be OK): {fault_msg}")
                
                # After reloadConfig, we need to add the process group
                # The reloadConfig returns [[added], [modified], [removed]]
                # We need to actually add the process group to Supervisor
                try:
                    add_result = supervisor.supervisor.addProcessGroup(supervisor_program_name)
                    logger.info(f"Supervisor addProcessGroup result: {add_result}")
                except xmlrpc.client.Fault as add_fault:
                    fault_code = add_fault.faultCode if hasattr(add_fault, 'faultCode') else 'Unknown'
                    fault_msg = add_fault.faultString if hasattr(add_fault, 'faultString') else str(add_fault)
                    # If process group already exists, that's OK
                    if 'ALREADY_ADDED' in fault_msg or fault_code == 60:
                        logger.info(f"Process group already exists (OK): {fault_msg}")
                    else:
                        logger.error(f"Supervisor addProcessGroup failed: Code={fault_code}, Message={fault_msg}")
                        raise RuntimeError(f"Supervisor failed to add process group: {fault_msg}")
            except RuntimeError:
                raise
            except xmlrpc.client.Fault as reload_fault:
                # Supervisor might return a fault if config has syntax errors
                fault_msg = reload_fault.faultString if hasattr(reload_fault, 'faultString') else str(reload_fault)
                logger.error(f"Supervisor reloadConfig failed with fault: {fault_msg}")
                # Try to read the config file to show what was written
                try:
                    verify_result = subprocess.run(
                        ["sudo", "cat", str(config_file_path)],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    logger.error(f"Config file that caused error:\n{verify_result.stdout}")
                except:
                    pass
                raise RuntimeError(f"Supervisor rejected config file: {fault_msg}")
            
            # Verify the program was added by checking if it exists
            # Note: getAllProcessInfo() might return empty if programs haven't been started yet
            # So we'll try getProcessInfo() directly
            try:
                info = supervisor.supervisor.getProcessInfo(supervisor_program_name)
                logger.info(f"Program '{supervisor_program_name}' successfully registered. State: {info.get('statename', 'UNKNOWN')}")
            except xmlrpc.client.Fault as fault_error:
                fault_code = fault_error.faultCode if hasattr(fault_error, 'faultCode') else 'Unknown'
                fault_msg = fault_error.faultString if hasattr(fault_error, 'faultString') else str(fault_error)
                
                # Get all process info to see what Supervisor knows about
                try:
                    all_processes = supervisor.supervisor.getAllProcessInfo()
                    program_names = [p['name'] for p in all_processes]
                    logger.debug(f"Supervisor knows about {len(program_names)} programs: {program_names[:10]}")
                except Exception as e:
                    logger.warning(f"Could not get all process info: {e}")
                    program_names = []
                
                # Check Supervisor logs for why it wasn't loaded
                # Try to read the config file to see if there's a syntax error
                try:
                    verify_result = subprocess.run(
                        ["sudo", "cat", str(config_file_path)],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    logger.error(f"Supervisor config file content:\n{verify_result.stdout}")
                except Exception as read_error:
                    logger.error(f"Could not read config file: {read_error}")
                
                # Check Supervisor logs for more details
                try:
                    log_result = subprocess.run(
                        ["sudo", "tail", "-20", "/var/log/supervisor/supervisord.log"],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    logger.error(f"Supervisor logs (last 20 lines):\n{log_result.stdout}")
                except Exception as log_error:
                    logger.warning(f"Could not read Supervisor logs: {log_error}")
                
                raise RuntimeError(
                    f"Program '{supervisor_program_name}' not found after addProcessGroup. "
                    f"Supervisor error (Code {fault_code}): {fault_msg}. "
                    f"Config file: {config_file_path}. "
                    f"Available programs: {program_names[:5]}"
                )
        except RuntimeError:
            # Re-raise RuntimeErrors as-is
            raise
        except Exception as e:
            logger.error(f"Failed to reload Supervisor config: {e}", exc_info=True)
            raise RuntimeError(f"Failed to reload Supervisor config: {e}")
        
        # Start program (if not already started)
        # Note: With autostart=true, Supervisor may have already started it
        try:
            supervisor = self._get_supervisor_client()
            
            # Check current state first
            try:
                info = supervisor.supervisor.getProcessInfo(supervisor_program_name)
                current_state = info.get('statename', 'UNKNOWN')
                logger.info(f"Program '{supervisor_program_name}' current state: {current_state}")
                
                # If already running or starting, that's success
                if current_state in ['RUNNING', 'STARTING']:
                    logger.info(f"Program '{supervisor_program_name}' is already {current_state.lower()}, skipping start")
                    result = True
                else:
                    # Try to start it
                    result = supervisor.supervisor.startProcess(supervisor_program_name)
                    logger.info(f"Started Supervisor program: {result}")
            except xmlrpc.client.Fault as state_fault:
                # If we can't get state, try to start anyway
                fault_code = state_fault.faultCode if hasattr(state_fault, 'faultCode') else 'Unknown'
                fault_msg = state_fault.faultString if hasattr(state_fault, 'faultString') else str(state_fault)
                
                # If it's ALREADY_STARTED, that's actually success
                if fault_code == 60 or 'ALREADY_STARTED' in fault_msg:
                    logger.info(f"Program '{supervisor_program_name}' is already started (this is OK)")
                    result = True
                else:
                    # Try to start it
                    result = supervisor.supervisor.startProcess(supervisor_program_name)
                    logger.info(f"Started Supervisor program: {result}")
        except xmlrpc.client.Fault as e:
            fault_code = e.faultCode if hasattr(e, 'faultCode') else 'Unknown'
            error_msg = e.faultString if hasattr(e, 'faultString') else str(e)
            
            # ALREADY_STARTED is actually success - program is running
            if fault_code == 60 or 'ALREADY_STARTED' in error_msg:
                logger.info(f"Program '{supervisor_program_name}' is already started (this is OK)")
                result = True
            else:
                raise RuntimeError(f"Failed to start Supervisor program '{supervisor_program_name}': {error_msg}")
        except Exception as e:
            raise RuntimeError(f"Failed to start Supervisor program: {e}")
        
        # Create database entry
        query = """
            INSERT INTO strategy_runs (
                id, user_id, account_id, config_id,
                supervisor_program_name, status, control_api_port,
                log_file_path, started_at
            )
            VALUES (
                :id, :user_id, :account_id, :config_id,
                :supervisor_program_name, :status, :control_api_port,
                :log_file_path, :started_at
            )
        """
        await self.database.execute(
            query,
            {
                "id": run_id,
                "user_id": user_id,
                "account_id": account_id,
                "config_id": config_id,
                "supervisor_program_name": supervisor_program_name,
                "status": "starting",
                "control_api_port": port,
                "log_file_path": str(stdout_log),
                "started_at": datetime.now()
            }
        )
        
        return {
            "run_id": run_id,
            "supervisor_program_name": supervisor_program_name,
            "port": port,
            "status": "starting",
            "log_file": str(stdout_log)
        }
    
    async def stop_strategy(self, run_id: str) -> bool:
        """
        Stop a running strategy via Supervisor.
        
        Args:
            run_id: Strategy run UUID
            
        Returns:
            True if stopped successfully, False otherwise
        """
        # Get supervisor program name from database
        query = """
            SELECT supervisor_program_name, status
            FROM strategy_runs
            WHERE id = :run_id
        """
        row = await self.database.fetch_one(query, {"run_id": run_id})
        
        if not row:
            logger.warning(f"Strategy run not found: {run_id}")
            return False
        
        supervisor_program_name = row["supervisor_program_name"]
        current_status = row["status"]
        
        if current_status in ("stopped", "error"):
            logger.info(f"Strategy already stopped: {run_id}")
            return True
        
        # Stop via Supervisor
        try:
            supervisor = self._get_supervisor_client()
            result = supervisor.supervisor.stopProcess(supervisor_program_name)
            if not result:
                logger.warning(f"Supervisor failed to stop process: {supervisor_program_name}")
                return False
            logger.info(f"Stopped Supervisor program: {supervisor_program_name}")
        except Exception as e:
            logger.error(f"Error stopping Supervisor program: {e}")
            return False
        
        # Update database
        query = """
            UPDATE strategy_runs
            SET status = 'stopped', stopped_at = :stopped_at
            WHERE id = :run_id
        """
        await self.database.execute(
            query,
            {"run_id": run_id, "stopped_at": datetime.now()}
        )
        
        # Release port
        port = row.get("control_api_port")
        if port:
            await self.port_manager.release_port(port)
        
        return True
    
    async def get_running_strategies(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get list of running strategies.
        
        Args:
            user_id: Optional user ID to filter by
            
        Returns:
            List of strategy run dictionaries
        """
        if user_id:
            query = """
                SELECT id, user_id, account_id, config_id,
                       supervisor_program_name, status, control_api_port,
                       log_file_path, started_at, last_heartbeat, health_status
                FROM strategy_runs
                WHERE user_id = :user_id
                ORDER BY started_at DESC
            """
            rows = await self.database.fetch_all(query, {"user_id": user_id})
        else:
            query = """
                SELECT id, user_id, account_id, config_id,
                       supervisor_program_name, status, control_api_port,
                       log_file_path, started_at, last_heartbeat, health_status
                FROM strategy_runs
                ORDER BY started_at DESC
            """
            rows = await self.database.fetch_all(query)
        
        return [dict(row) for row in rows]
    
    async def get_strategy_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Get strategy status from Supervisor.
        
        Args:
            run_id: Strategy run UUID
            
        Returns:
            Status dict or None if not found
        """
        query = """
            SELECT supervisor_program_name
            FROM strategy_runs
            WHERE id = :run_id
        """
        row = await self.database.fetch_one(query, {"run_id": run_id})
        
        if not row:
            return None
        
        supervisor_program_name = row["supervisor_program_name"]
        
        try:
            supervisor = self._get_supervisor_client()
            info = supervisor.supervisor.getProcessInfo(supervisor_program_name)
            return {
                "name": info.get("name"),
                "state": info.get("statename"),  # RUNNING, STOPPED, etc.
                "pid": info.get("pid"),
                "start": info.get("start"),
                "stop": info.get("stop"),
                "exitstatus": info.get("exitstatus"),
                "spawnerr": info.get("spawnerr"),
                "stdout_logfile": info.get("stdout_logfile"),
                "stderr_logfile": info.get("stderr_logfile"),
            }
        except Exception as e:
            logger.error(f"Error getting Supervisor status: {e}")
            return None
    
    async def recover_processes(self) -> Dict[str, int]:
        """
        Recover processes on bot restart - sync DB with Supervisor state.
        
        Returns:
            Dict with recovery statistics
        """
        stats = {
            "db_running": 0,
            "supervisor_running": 0,
            "marked_stopped": 0,
            "orphaned_stopped": 0,
            "errors_found": 0
        }
        
        try:
            # Get all 'running' strategies from DB
            query = """
                SELECT id, supervisor_program_name, status
                FROM strategy_runs
                WHERE status IN ('starting', 'running', 'paused')
            """
            db_runs = await self.database.fetch_all(query)
            stats["db_running"] = len(db_runs)
            
            # Get all running processes from Supervisor
            supervisor = self._get_supervisor_client()
            all_processes = supervisor.supervisor.getAllProcessInfo()
            
            # Filter to our strategy programs
            supervisor_strategies = {
                proc["name"]: proc
                for proc in all_processes
                if proc["name"].startswith("strategy_")
            }
            stats["supervisor_running"] = len(supervisor_strategies)
            
            # Reconcile differences
            db_program_names = {row["supervisor_program_name"] for row in db_runs}
            
            # Mark DB entries as stopped if Supervisor doesn't have them
            for row in db_runs:
                program_name = row["supervisor_program_name"]
                if program_name not in supervisor_strategies:
                    await self.database.execute(
                        """
                        UPDATE strategy_runs
                        SET status = 'stopped', stopped_at = :stopped_at
                        WHERE id = :id
                        """,
                        {"id": row["id"], "stopped_at": datetime.now()}
                    )
                    stats["marked_stopped"] += 1
            
            # Stop orphaned Supervisor processes (not in DB)
            for program_name, proc_info in supervisor_strategies.items():
                if program_name not in db_program_names:
                    try:
                        supervisor.supervisor.stopProcess(program_name)
                        stats["orphaned_stopped"] += 1
                        logger.warning(f"Stopped orphaned Supervisor process: {program_name}")
                    except Exception as e:
                        logger.error(f"Error stopping orphaned process {program_name}: {e}")
                
                # Check for error states
                if proc_info.get("statename") == "FATAL":
                    # Find corresponding DB entry and mark as error
                    query = """
                        UPDATE strategy_runs
                        SET status = 'error', error_message = :error_msg
                        WHERE supervisor_program_name = :program_name
                    """
                    await self.database.execute(
                        query,
                        {
                            "program_name": program_name,
                            "error_msg": proc_info.get("spawnerr", "Supervisor FATAL state")
                        }
                    )
                    stats["errors_found"] += 1
            
            logger.info(f"Process recovery completed: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"Error during process recovery: {e}")
            return stats
    
    async def get_log_file(self, run_id: str) -> Optional[str]:
        """
        Get log file path for a strategy run.
        
        Args:
            run_id: Strategy run UUID
            
        Returns:
            Log file path or None if not found
        """
        query = """
            SELECT log_file_path
            FROM strategy_runs
            WHERE id = :run_id
        """
        row = await self.database.fetch_one(query, {"run_id": run_id})
        
        if row and row["log_file_path"]:
            log_path = Path(row["log_file_path"])
            if log_path.exists():
                return str(log_path)
        
        return None

