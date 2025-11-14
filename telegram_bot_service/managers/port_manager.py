"""
Port Manager for allocating control API ports to strategies

Manages port allocation in the range 8767-8799 (32 ports available).
Port 8766 is reserved for the standalone control API server (start_control_api.py).
Ports are allocated from the database to ensure no conflicts.
"""

from typing import Optional, List
from databases import Database
from helpers.unified_logger import get_logger


logger = get_logger("core", "port_manager")


class PortManager:
    """Manages port allocation for strategy control APIs"""
    
    # Port 8766 is reserved for standalone control API server
    STANDALONE_PORT = 8766
    PORT_RANGE_START = 8767  # Strategies start from 8767
    PORT_RANGE_END = 8799
    PORT_COUNT = PORT_RANGE_END - PORT_RANGE_START + 1
    
    def __init__(self, database: Database):
        """
        Initialize PortManager.
        
        Args:
            database: Database connection instance
        """
        self.database = database
        logger.info(
            f"PortManager initialized: "
            f"standalone port {self.STANDALONE_PORT} (reserved), "
            f"strategy ports {self.PORT_RANGE_START}-{self.PORT_RANGE_END}"
        )
    
    async def allocate_port(self) -> Optional[int]:
        """
        Allocate next available port for a strategy.
        
        Note: Port 8766 is reserved for the standalone control API server
        and will never be allocated to strategies.
        
        Returns:
            Port number if available, None if all ports are in use
        """
        used_ports = await self.get_used_ports_from_db()
        
        # Find first available port (starting from 8767, skipping reserved 8766)
        for port in range(self.PORT_RANGE_START, self.PORT_RANGE_END + 1):
            if port not in used_ports:
                logger.info(f"Allocated port {port} for strategy")
                return port
        
        logger.warning(f"No available ports in range {self.PORT_RANGE_START}-{self.PORT_RANGE_END}")
        return None
    
    async def release_port(self, port: int) -> None:
        """
        Release a port (mark as available).
        
        Note: Ports are automatically released when strategy_runs entry is deleted
        or when status changes to 'stopped'. This method is mainly for logging.
        
        Args:
            port: Port number to release
        """
        logger.info(f"Port {port} released (strategy stopped)")
    
    async def get_used_ports_from_db(self) -> List[int]:
        """
        Get list of currently used ports from database.
        
        Returns:
            List of port numbers currently in use
        """
        query = """
            SELECT DISTINCT control_api_port
            FROM strategy_runs
            WHERE status IN ('starting', 'running', 'paused')
            AND control_api_port IS NOT NULL
        """
        
        try:
            rows = await self.database.fetch_all(query)
            used_ports = [row['control_api_port'] for row in rows]
            logger.debug(f"Found {len(used_ports)} used ports: {used_ports}")
            return used_ports
        except Exception as e:
            logger.error(f"Error fetching used ports: {e}")
            return []
    
    async def is_port_available(self, port: int) -> bool:
        """
        Check if a specific port is available.
        
        Args:
            port: Port number to check
            
        Returns:
            True if port is available, False otherwise
        """
        if port < self.PORT_RANGE_START or port > self.PORT_RANGE_END:
            return False
        
        used_ports = await self.get_used_ports_from_db()
        return port not in used_ports
    
    async def get_available_port_count(self) -> int:
        """
        Get count of available ports.
        
        Returns:
            Number of available ports
        """
        used_ports = await self.get_used_ports_from_db()
        return self.PORT_COUNT - len(used_ports)

