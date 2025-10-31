"""
Networking utilities for proxy validation and monitoring.

Provides helpers to verify the externally visible IP address when a proxy
is enabled and to monitor proxy health during long-running trading sessions.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable, Optional, Sequence
import inspect

import aiohttp

DEFAULT_EGRESS_SERVICES: Sequence[str] = (
    "https://ifconfig.io/ip",
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
)


@dataclass(slots=True)
class ProxyEgressResult:
    """Container for proxy egress detection results."""

    address: Optional[str]
    source: Optional[str]
    error: Optional[str]
    timestamp: float


async def detect_egress_ip(
    *,
    session: Optional[aiohttp.ClientSession] = None,
    services: Sequence[str] = DEFAULT_EGRESS_SERVICES,
    timeout: float = 10.0,
) -> ProxyEgressResult:
    """
    Probe public egress IP using one of the provided services.

    Args:
        session: Optional aiohttp.ClientSession to reuse.
        services: List of URL endpoints returning the caller's IP as plain text.
        timeout: Overall timeout per request in seconds.

    Returns:
        ProxyEgressResult describing the detected IP (or any failure encountered).
    """
    if not services:
        raise ValueError("At least one egress detection service must be provided")

    errors: list[tuple[str, Exception]] = []
    client = session
    close_client = False

    if client is None:
        client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            trust_env=True,
        )
        close_client = True

    try:
        for url in services:
            try:
                async with client.get(url) as response:
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}")
                    body = (await response.text()).strip()
                    if not body:
                        raise RuntimeError("Empty response body")
                    return ProxyEgressResult(
                        address=body,
                        source=url,
                        error=None,
                        timestamp=time.time(),
                    )
            except Exception as exc:  # pragma: no cover - network variability
                errors.append((url, exc))
                continue

        joined_error = ", ".join(f"{url}: {exc}" for url, exc in errors) if errors else None
        return ProxyEgressResult(address=None, source=None, error=joined_error, timestamp=time.time())
    finally:
        if close_client:
            await client.close()


UnhealthyCallback = Callable[[int], Optional[Awaitable[None]]]


class ProxyHealthMonitor:
    """
    Periodically validates the proxy egress IP to ensure the proxy remains active.
    """

    def __init__(
        self,
        *,
        logger,
        interval_seconds: float = 1800.0,
        timeout: float = 10.0,
        services: Iterable[str] = DEFAULT_EGRESS_SERVICES,
        on_unhealthy: Optional[UnhealthyCallback] = None,
        failure_threshold: int = 3,
    ) -> None:
        self._logger = logger
        self._interval = interval_seconds
        self._timeout = timeout
        self._services = tuple(services)
        self._task: Optional[asyncio.Task] = None
        self._last_ip: Optional[str] = None
        self._consecutive_failures = 0
        self._on_unhealthy = on_unhealthy
        self._failure_threshold = max(1, int(failure_threshold))
        self._failure_notified = False

    def start(self) -> None:
        """Begin monitoring in the background."""
        if self._task and not self._task.done():
            return
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run(), name="proxy-health-monitor")

    async def stop(self) -> None:
        """Stop monitoring if running."""
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:  # pragma: no cover - expected on shutdown
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        first_iteration = True
        while True:
            try:
                result = await detect_egress_ip(
                    services=self._services,
                    timeout=self._timeout,
                )
                if result.address:
                    if first_iteration:
                        self._logger.info(
                            f"Proxy egress IP confirmed: {result.address} (via {result.source})"
                        )
                    elif result.address != self._last_ip:
                        self._logger.info(
                            f"Proxy egress IP changed: {result.address} (via {result.source})"
                        )
                    else:
                        self._logger.debug(
                            f"Proxy egress IP unchanged: {result.address} (checked via {result.source})"
                        )
                    self._last_ip = result.address
                    self._consecutive_failures = 0
                    self._failure_notified = False
                else:
                    self._consecutive_failures += 1
                    log_method = self._logger.warning
                    if self._consecutive_failures >= 3:
                        log_method = self._logger.error
                    message = "Proxy egress IP check failed"
                    if result.error:
                        message = f"{message}: {result.error}"
                    log_method(message)
                    await self._maybe_notify_unhealthy()
                first_iteration = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                self._consecutive_failures += 1
                self._logger.error(f"Proxy health monitor error: {exc}")
                await self._maybe_notify_unhealthy()
            await asyncio.sleep(self._interval)

    async def _maybe_notify_unhealthy(self) -> None:
        if (
            self._on_unhealthy
            and not self._failure_notified
            and self._consecutive_failures >= self._failure_threshold
        ):
            try:
                result = self._on_unhealthy(self._consecutive_failures)
                if inspect.isawaitable(result):
                    await result  # type: ignore[arg-type]
            except Exception as exc:  # pragma: no cover - defensive logging
                self._logger.error(f"Proxy rotation callback failed: {exc}")
            finally:
                self._failure_notified = True


__all__ = [
    "DEFAULT_EGRESS_SERVICES",
    "ProxyEgressResult",
    "ProxyHealthMonitor",
    "detect_egress_ip",
]
