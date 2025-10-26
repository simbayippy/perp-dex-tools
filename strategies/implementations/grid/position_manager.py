"""
Grid Strategy Position Manager.

Lightweight in-memory tracking tailored for the grid strategy. Keeps all
position data in process memory (no persistence) while offering small
helpers for recovery workflows.
"""

from __future__ import annotations

from typing import Iterable, List, Set

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
        self._state.tracked_positions.append(tracked_position)

    def extend(self, tracked_positions: Iterable[TrackedPosition]) -> None:
        """Bulk register positions."""
        self._state.tracked_positions.extend(tracked_positions)

    def clear(self) -> None:
        """Remove all tracked positions."""
        self._state.tracked_positions = []

    # ------------------------------------------------------------------ #
    # Query helpers
    # ------------------------------------------------------------------ #
    def all(self) -> List[TrackedPosition]:
        """Return a copy of all tracked positions."""
        return list(self._state.tracked_positions)

    def count(self) -> int:
        """Return the number of tracked positions."""
        return len(self._state.tracked_positions)

    # ------------------------------------------------------------------ #
    # Maintenance helpers
    # ------------------------------------------------------------------ #
    def prune_by_active_orders(self, active_order_ids: Set[str]) -> None:
        """
        Remove positions whose close orders are no longer active.

        Mirrors the legacy behaviour previously embedded in ``GridStrategy``.
        """
        remaining: List[TrackedPosition] = []
        for tracked in self._state.tracked_positions:
            if tracked.hedged:
                continue
            if not tracked.close_order_ids:
                continue
            if any(order_id in active_order_ids for order_id in tracked.close_order_ids):
                remaining.append(tracked)

        self._state.tracked_positions = remaining

    def replace(self, positions: List[TrackedPosition]) -> None:
        """Replace the tracked positions list."""
        self._state.tracked_positions = list(positions)
