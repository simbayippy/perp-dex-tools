from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from .models import ProxyAssignment, ProxyEndpoint

_STATUS_ORDER = {"active": 0, "standby": 1, "burned": 2}


class ProxySelector:
    """
    Helper to choose proxies for an account.

    It keeps assignments ordered by priority and status, exposes helpers to
    access the current proxy, and supports simple rotation across eligible
    assignments.
    """

    def __init__(self, assignments: Sequence[ProxyAssignment]):
        ordered = sorted(
            assignments,
            key=lambda a: (a.priority, _STATUS_ORDER.get(a.status, 99), a.proxy.label),
        )
        self._assignments: List[ProxyAssignment] = list(ordered)
        self._cursor = 0

    @classmethod
    def from_assignments(cls, assignments: Iterable[ProxyAssignment]) -> "ProxySelector":
        assignments = list(assignments)
        if not assignments:
            raise ValueError("ProxySelector requires at least one assignment")
        return cls(assignments)

    @property
    def assignments(self) -> List[ProxyAssignment]:
        """Return the ordered assignments."""
        return list(self._assignments)

    def current_assignment(self) -> Optional[ProxyAssignment]:
        """Return the active assignment, preferring active proxies."""
        pool = self._active_pool()
        if pool:
            idx = min(self._cursor, len(pool) - 1)
            return pool[idx]

        standby = self._standby_pool()
        if standby:
            idx = min(self._cursor, len(standby) - 1)
            return standby[idx]

        return None

    def current_proxy(self) -> Optional[ProxyEndpoint]:
        assignment = self.current_assignment()
        return assignment.proxy if assignment else None

    def rotate(self) -> Optional[ProxyEndpoint]:
        """
        Advance to the next assignment within the same pool.

        Returns:
            The newly selected proxy endpoint, or None when no assignments
            are available.
        """
        pool = self._active_pool()
        if pool:
            if len(pool) == 1:
                return pool[0].proxy
            self._cursor = (self._cursor + 1) % len(pool)
            return pool[self._cursor].proxy

        standby = self._standby_pool()
        if standby:
            if len(standby) == 1:
                return standby[0].proxy
            self._cursor = (self._cursor + 1) % len(standby)
            return standby[self._cursor].proxy

        return None

    def reset(self) -> None:
        """Reset cursor to the first entry."""
        self._cursor = 0

    def has_active_proxy(self) -> bool:
        return bool(self._active_pool())

    def _active_pool(self) -> List[ProxyAssignment]:
        return [assignment for assignment in self._assignments if assignment.is_account_active]

    def _standby_pool(self) -> List[ProxyAssignment]:
        return [
            assignment
            for assignment in self._assignments
            if assignment.status == "standby" and assignment.proxy.is_active
        ]

    def __len__(self) -> int:
        return len(self._assignments)

    def __bool__(self) -> bool:  # pragma: no cover - defensive
        return bool(self._assignments)
*** End Patch (Note: purposely inserted error to highlight)
