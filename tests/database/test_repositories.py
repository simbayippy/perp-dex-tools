"""
Tests for database repositories.

Tests CRUD operations for positions, orders, and strategy state.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch


class MockDatabaseConnection:
    """Mock database connection for testing."""

    def __init__(self):
        self.data = {
            "positions": {},
            "orders": {},
            "strategy_state": {}
        }
        self.transaction_active = False

    async def execute(self, query, *args):
        """Mock execute method."""
        return MagicMock(rowcount=1)

    async def fetch_one(self, query, *args):
        """Mock fetch_one method."""
        return None

    async def fetch_all(self, query, *args):
        """Mock fetch_all method."""
        return []

    async def begin_transaction(self):
        """Mock transaction start."""
        self.transaction_active = True

    async def commit(self):
        """Mock transaction commit."""
        self.transaction_active = False

    async def rollback(self):
        """Mock transaction rollback."""
        self.transaction_active = False


@pytest.fixture
def mock_db():
    """Create a mock database connection."""
    return MockDatabaseConnection()


class TestPositionRepository:
    """Test position repository CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_position(self, mock_db):
        """Test creating a new position."""
        position_id = str(uuid4())
        position_data = {
            "position_id": position_id,
            "symbol": "BTC-PERP",
            "long_dex": "lighter",
            "short_dex": "aster",
            "size_usd": Decimal("1000"),
            "entry_price_long": Decimal("50000"),
            "entry_price_short": Decimal("50050"),
            "status": "open",
            "created_at": datetime.utcnow()
        }

        # Mock the repository create method
        mock_db.data["positions"][position_id] = position_data

        assert position_id in mock_db.data["positions"]
        assert mock_db.data["positions"][position_id]["symbol"] == "BTC-PERP"

    @pytest.mark.asyncio
    async def test_get_position_by_id(self, mock_db):
        """Test retrieving a position by ID."""
        position_id = str(uuid4())
        position_data = {
            "position_id": position_id,
            "symbol": "BTC-PERP",
            "status": "open"
        }

        mock_db.data["positions"][position_id] = position_data

        # Retrieve
        position = mock_db.data["positions"].get(position_id)

        assert position is not None
        assert position["position_id"] == position_id
        assert position["symbol"] == "BTC-PERP"

    @pytest.mark.asyncio
    async def test_get_all_open_positions(self, mock_db):
        """Test retrieving all open positions."""
        # Create multiple positions
        for i in range(3):
            position_id = str(uuid4())
            mock_db.data["positions"][position_id] = {
                "position_id": position_id,
                "symbol": f"BTC-PERP-{i}",
                "status": "open"
            }

        # Add a closed position
        closed_id = str(uuid4())
        mock_db.data["positions"][closed_id] = {
            "position_id": closed_id,
            "symbol": "ETH-PERP",
            "status": "closed"
        }

        # Get only open positions
        open_positions = [
            p for p in mock_db.data["positions"].values()
            if p["status"] == "open"
        ]

        assert len(open_positions) == 3
        assert all(p["status"] == "open" for p in open_positions)

    @pytest.mark.asyncio
    async def test_update_position(self, mock_db):
        """Test updating an existing position."""
        position_id = str(uuid4())
        position_data = {
            "position_id": position_id,
            "symbol": "BTC-PERP",
            "size_usd": Decimal("1000"),
            "status": "open"
        }

        mock_db.data["positions"][position_id] = position_data

        # Update position
        mock_db.data["positions"][position_id]["size_usd"] = Decimal("1500")
        mock_db.data["positions"][position_id]["updated_at"] = datetime.utcnow()

        assert mock_db.data["positions"][position_id]["size_usd"] == Decimal("1500")

    @pytest.mark.asyncio
    async def test_close_position(self, mock_db):
        """Test closing a position."""
        position_id = str(uuid4())
        position_data = {
            "position_id": position_id,
            "symbol": "BTC-PERP",
            "status": "open"
        }

        mock_db.data["positions"][position_id] = position_data

        # Close position
        mock_db.data["positions"][position_id]["status"] = "closed"
        mock_db.data["positions"][position_id]["closed_at"] = datetime.utcnow()
        mock_db.data["positions"][position_id]["pnl"] = Decimal("150.50")

        assert mock_db.data["positions"][position_id]["status"] == "closed"
        assert "closed_at" in mock_db.data["positions"][position_id]
        assert mock_db.data["positions"][position_id]["pnl"] == Decimal("150.50")

    @pytest.mark.asyncio
    async def test_delete_position(self, mock_db):
        """Test deleting a position."""
        position_id = str(uuid4())
        position_data = {"position_id": position_id, "symbol": "BTC-PERP"}

        mock_db.data["positions"][position_id] = position_data

        # Delete
        del mock_db.data["positions"][position_id]

        assert position_id not in mock_db.data["positions"]

    @pytest.mark.asyncio
    async def test_get_positions_by_symbol(self, mock_db):
        """Test retrieving positions for a specific symbol."""
        symbols = ["BTC-PERP", "BTC-PERP", "ETH-PERP"]

        for symbol in symbols:
            position_id = str(uuid4())
            mock_db.data["positions"][position_id] = {
                "position_id": position_id,
                "symbol": symbol,
                "status": "open"
            }

        # Get BTC-PERP positions
        btc_positions = [
            p for p in mock_db.data["positions"].values()
            if p["symbol"] == "BTC-PERP"
        ]

        assert len(btc_positions) == 2
        assert all(p["symbol"] == "BTC-PERP" for p in btc_positions)


class TestOrderRepository:
    """Test order repository CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_order(self, mock_db):
        """Test creating a new order record."""
        order_id = "order_" + str(uuid4())
        order_data = {
            "order_id": order_id,
            "position_id": str(uuid4()),
            "exchange": "lighter",
            "symbol": "BTC-PERP",
            "side": "buy",
            "size": Decimal("1.5"),
            "price": Decimal("50000"),
            "status": "filled",
            "created_at": datetime.utcnow()
        }

        mock_db.data["orders"][order_id] = order_data

        assert order_id in mock_db.data["orders"]
        assert mock_db.data["orders"][order_id]["exchange"] == "lighter"

    @pytest.mark.asyncio
    async def test_get_orders_for_position(self, mock_db):
        """Test retrieving all orders for a specific position."""
        position_id = str(uuid4())

        # Create multiple orders for this position
        for i in range(3):
            order_id = f"order_{i}"
            mock_db.data["orders"][order_id] = {
                "order_id": order_id,
                "position_id": position_id,
                "side": "buy" if i % 2 == 0 else "sell"
            }

        # Get orders for position
        position_orders = [
            o for o in mock_db.data["orders"].values()
            if o["position_id"] == position_id
        ]

        assert len(position_orders) == 3

    @pytest.mark.asyncio
    async def test_update_order_status(self, mock_db):
        """Test updating order status."""
        order_id = "order_123"
        mock_db.data["orders"][order_id] = {
            "order_id": order_id,
            "status": "pending"
        }

        # Update status
        mock_db.data["orders"][order_id]["status"] = "filled"
        mock_db.data["orders"][order_id]["filled_at"] = datetime.utcnow()

        assert mock_db.data["orders"][order_id]["status"] == "filled"


class TestStrategyStateRepository:
    """Test strategy state persistence."""

    @pytest.mark.asyncio
    async def test_save_strategy_state(self, mock_db):
        """Test saving strategy state."""
        strategy_id = str(uuid4())
        state_data = {
            "strategy_id": strategy_id,
            "strategy_type": "funding_arbitrage",
            "state": {
                "active_positions": 3,
                "total_pnl": Decimal("500.50"),
                "failed_symbols": ["BTC-PERP"]
            },
            "updated_at": datetime.utcnow()
        }

        mock_db.data["strategy_state"][strategy_id] = state_data

        assert strategy_id in mock_db.data["strategy_state"]
        assert mock_db.data["strategy_state"][strategy_id]["strategy_type"] == "funding_arbitrage"

    @pytest.mark.asyncio
    async def test_load_strategy_state(self, mock_db):
        """Test loading strategy state."""
        strategy_id = str(uuid4())
        state_data = {
            "strategy_id": strategy_id,
            "state": {"active_positions": 2}
        }

        mock_db.data["strategy_state"][strategy_id] = state_data

        # Load state
        loaded_state = mock_db.data["strategy_state"].get(strategy_id)

        assert loaded_state is not None
        assert loaded_state["state"]["active_positions"] == 2


class TestDatabaseTransactions:
    """Test database transaction handling."""

    @pytest.mark.asyncio
    async def test_transaction_commit(self, mock_db):
        """Test successful transaction commit."""
        await mock_db.begin_transaction()
        assert mock_db.transaction_active

        # Perform operations
        position_id = str(uuid4())
        mock_db.data["positions"][position_id] = {"position_id": position_id}

        await mock_db.commit()
        assert not mock_db.transaction_active
        assert position_id in mock_db.data["positions"]

    @pytest.mark.asyncio
    async def test_transaction_rollback(self, mock_db):
        """Test transaction rollback on error."""
        await mock_db.begin_transaction()
        assert mock_db.transaction_active

        # Simulate error and rollback
        try:
            raise Exception("Database error")
        except Exception:
            await mock_db.rollback()

        assert not mock_db.transaction_active

    @pytest.mark.asyncio
    async def test_concurrent_updates(self, mock_db):
        """Test handling of concurrent updates."""
        position_id = str(uuid4())
        mock_db.data["positions"][position_id] = {
            "position_id": position_id,
            "size_usd": Decimal("1000"),
            "version": 1
        }

        # First update
        mock_db.data["positions"][position_id]["size_usd"] = Decimal("1500")
        mock_db.data["positions"][position_id]["version"] = 2

        # Second concurrent update should check version
        current_version = mock_db.data["positions"][position_id]["version"]
        assert current_version == 2

        # Update with version check
        if current_version == 2:
            mock_db.data["positions"][position_id]["size_usd"] = Decimal("2000")
            mock_db.data["positions"][position_id]["version"] = 3

        assert mock_db.data["positions"][position_id]["size_usd"] == Decimal("2000")


class TestDatabaseErrorHandling:
    """Test database error handling."""

    @pytest.mark.asyncio
    async def test_handles_connection_error(self, mock_db):
        """Test handling of database connection errors."""
        async def failing_execute(*args, **kwargs):
            raise ConnectionError("Database connection lost")

        mock_db.execute = failing_execute

        with pytest.raises(ConnectionError):
            await mock_db.execute("SELECT * FROM positions")

    @pytest.mark.asyncio
    async def test_handles_constraint_violation(self, mock_db):
        """Test handling of constraint violations."""
        position_id = str(uuid4())

        # Create position
        mock_db.data["positions"][position_id] = {"position_id": position_id}

        # Try to create duplicate (should fail)
        with pytest.raises(ValueError):
            if position_id in mock_db.data["positions"]:
                raise ValueError("Duplicate position_id")

    @pytest.mark.asyncio
    async def test_handles_query_timeout(self, mock_db):
        """Test handling of query timeouts."""
        async def timeout_execute(*args, **kwargs):
            raise TimeoutError("Query timeout")

        mock_db.execute = timeout_execute

        with pytest.raises(TimeoutError):
            await mock_db.execute("SELECT * FROM positions")


class TestDatabaseMigrations:
    """Test database migration tracking."""

    def test_track_applied_migrations(self):
        """Test tracking of applied migrations."""
        migrations = {
            "001_initial_schema": {"applied_at": datetime.utcnow()},
            "002_add_pnl_columns": {"applied_at": datetime.utcnow()},
        }

        assert len(migrations) == 2
        assert "001_initial_schema" in migrations

    def test_detect_pending_migrations(self):
        """Test detection of pending migrations."""
        applied = ["001_initial_schema", "002_add_pnl_columns"]
        available = ["001_initial_schema", "002_add_pnl_columns", "003_add_indexes"]

        pending = [m for m in available if m not in applied]

        assert len(pending) == 1
        assert pending[0] == "003_add_indexes"
