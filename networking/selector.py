from __future__ import annotations

import random
import threading
from typing import Iterable, List, Optional, Sequence

from .exceptions import ProxyUnavailableError
from .models import ProxyEndpoint


class ProxySelector:
    """
    Rotation helper for a set of proxy endpoints.

    The selector keeps track of the current index and supports simple
    round-robin iteration as well as marking proxies unhealthy so they are
    temporarily skipped.
    """

    def __init__(
        self,
        endpoints: Sequence[ProxyEndpoint],
        *,
        shuffle: bool = False,
    ) -> None:
        if not endpoints:
            raise ProxyUnavailableError("No proxy endpoints provided")

        active = [endpoint for endpoint in endpoints if endpoint.is_active]
        if not active:
            raise ProxyUnavailableError("No active proxy endpoints provided")

        self._endpoints: List[ProxyEndpoint] = list(active)
        if shuffle:
            random.shuffle(self._endpoints)

        self._lock = threading.Lock()
        self._index = 0
        self._unhealthy: set[ProxyEndpoint] = set()

    @classmethod
    def from_assignments(cls, assignments: Iterable) -> "ProxySelector":
        endpoints = [assignment.proxy for assignment in assignments if assignment.is_active()]
        if not endpoints:
            raise ProxyUnavailableError("No active proxy assignments available")
        return cls(endpoints, shuffle=False)

    def current(self) -> ProxyEndpoint:
        with self._lock:
            endpoint = self._endpoints[self._index % len(self._endpoints)]
            if endpoint in self._unhealthy:
                endpoint = self._next_locked()
            return endpoint

    def rotate(self) -> ProxyEndpoint:
        with self._lock:
            return self._next_locked()

    def mark_unhealthy(self, endpoint: ProxyEndpoint) -> None:
        with self._lock:
            self._unhealthy.add(endpoint)
            # Ensure current pointer moves away from unhealthy proxy
            if self._endpoints[self._index % len(self._endpoints)] in self._unhealthy:
                self._next_locked()

    def reset_health(self, endpoint: Optional[ProxyEndpoint] = None) -> None:
        with self._lock:
            if endpoint is None:
                self._unhealthy.clear()
            else:
                self._unhealthy.discard(endpoint)

    def _next_locked(self) -> ProxyEndpoint:
        if len(self._unhealthy) >= len(self._endpoints):
            raise ProxyUnavailableError("All proxies are marked unhealthy")

        attempts = 0
        while attempts < len(self._endpoints):
            self._index = (self._index + 1) % len(self._endpoints)
            candidate = self._endpoints[self._index]
            if candidate not in self._unhealthy:
                return candidate
            attempts += 1

        raise ProxyUnavailableError("Failed to select a healthy proxy endpoint")
