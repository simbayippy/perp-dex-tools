"""
Grid Strategy Position Manager.

Lightweight in-memory tracking tailored for the grid strategy. Keeps all
position data in process memory (no persistence) while offering small
helpers for recovery workflows.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Set

from .models import GridState, TrackedPosition


class GridPositionManager:
    """Manage in-memory grid positions backed by ``GridState``."""

    def __init__(self, grid_state: GridState) -> None:
        self._state = grid_state

    # ------------------------------------------------------------------ #
    # Tracking helpers
    # ------------------------------------------------------------------ #
    def track(self, tracked_position: TrackedPosition) -> None:
        """Register a position for monitoring."""
        if not tracked_position.position_id:
            tracked_position.position_id = self.next_position_id()
        self._state.tracked_positions.append(tracked_position)

    def extend(self, tracked_positions: Iterable[TrackedPosition]) -> None:
        """Bulk register positions."""
        enriched: List[TrackedPosition] = []
        for tracked in tracked_positions:
            if not tracked.position_id:
                tracked.position_id = self.next_position_id()
            enriched.append(tracked)
        self._state.tracked_positions.extend(enriched)

    def clear(self) -> None:
        """Remove all tracked positions."""
        self._state.tracked_positions = []

    def next_position_id(self) -> str:
        """Generate a new position identifier."""
        return self._state.allocate_position_id()

    # ------------------------------------------------------------------ #
    # Query helpers
    # ------------------------------------------------------------------ #
    def all(self) -> List[TrackedPosition]:
        """Return a copy of all tracked positions."""
        return list(self._state.tracked_positions)

    def count(self) -> int:
        """Return the number of tracked positions."""
        return len(self._state.tracked_positions)

    def get(self, position_id: str) -> Optional[TrackedPosition]:
        """Lookup a tracked position by its identifier."""
        for tracked in self._state.tracked_positions:
            if tracked.position_id == position_id:
                return tracked
        return None

    # ------------------------------------------------------------------ #
    # Maintenance helpers
    # ------------------------------------------------------------------ #
    def prune_by_active_orders(self, active_order_ids: Set[str]) -> List[TrackedPosition]:
        """
        Remove positions whose close orders are no longer active.

        Mirrors the legacy behaviour previously embedded in ``GridStrategy``.
        
        Returns:
            List of pruned (completed) positions
        """
        remaining: List[TrackedPosition] = []
        pruned: List[TrackedPosition] = []
        
        for tracked in self._state.tracked_positions:
            if tracked.hedged:
                continue
            if not tracked.close_order_ids:
                continue
            if any(order_id in active_order_ids for order_id in tracked.close_order_ids):
                remaining.append(tracked)
            else:
                pruned.append(tracked)

        self._state.tracked_positions = remaining
        return pruned

    def replace(self, positions: List[TrackedPosition]) -> None:
        """Replace the tracked positions list."""
        self._state.tracked_positions = list(positions)

    def remove(self, position_id: str) -> Optional[TrackedPosition]:
        """Remove a single tracked position by id, returning it if present."""
        remaining: List[TrackedPosition] = []
        removed: Optional[TrackedPosition] = None
        for tracked in self._state.tracked_positions:
            if tracked.position_id == position_id and removed is None:
                removed = tracked
                continue
            remaining.append(tracked)
        self._state.tracked_positions = remaining
        return removed
