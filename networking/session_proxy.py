from __future__ import annotations

import os
import socket
import threading
from typing import Optional

import socks  
from .models import ProxyEndpoint


class SessionProxyManager:
    """
    Process-wide proxy manager combining environment variables and socket patching.

    Usage:
        SessionProxyManager.enable(proxy_endpoint)
        ...
        SessionProxyManager.disable()
    """

    _lock = threading.RLock()
    _original_socket = socket.socket
    _active_proxy: Optional[ProxyEndpoint] = None
    _socket_patched = False

    @classmethod
    def enable(cls, proxy: ProxyEndpoint) -> None:
        """
        Enable the session proxy.

        Calling this multiple times with the same proxy is idempotent.
        """
        if proxy is None:
            raise ValueError("ProxyEndpoint is required")

        with cls._lock:
            if cls._active_proxy and cls._proxies_equivalent(cls._active_proxy, proxy):
                return

            cls._apply_environment(proxy)
            cls._apply_socket_patch(proxy)
            cls._active_proxy = proxy

    @classmethod
    def rotate(cls, proxy: ProxyEndpoint) -> None:
        """Swap to a different proxy endpoint."""
        with cls._lock:
            cls.disable()
            cls.enable(proxy)

    @classmethod
    def disable(cls) -> None:
        """Restore original networking configuration."""
        with cls._lock:
            cls._clear_environment()
            cls._restore_socket()
            cls._active_proxy = None

    @classmethod
    def is_active(cls) -> bool:
        return cls._active_proxy is not None

    @classmethod
    def describe(cls, *, mask_password: bool = True) -> Optional[str]:
        proxy = cls._active_proxy
        if not proxy:
            return None
        return proxy.url_with_auth(mask_password=mask_password)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _apply_environment(cls, proxy: ProxyEndpoint) -> None:
        cls._clear_environment()

        proxy_url = proxy.url_with_auth()
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url
        os.environ["ALL_PROXY"] = proxy_url
        os.environ["all_proxy"] = proxy_url

    @classmethod
    def _clear_environment(cls) -> None:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            os.environ.pop(key, None)

    @classmethod
    def _apply_socket_patch(cls, proxy: ProxyEndpoint) -> None:
        cls._restore_socket()
        cls._socket_patched = False

        proxy_type = _protocol_to_socks_type(proxy.protocol)
        if proxy_type is None:
            if proxy.protocol.lower().startswith("socks"):
                raise RuntimeError(
                    "PySocks is required for socket-level proxying. Install with 'pip install PySocks'."
                ) from _SOCKS_IMPORT_ERROR
            # HTTP/HTTPS proxies rely on environment variables; socket patching is skipped.
            return

        socks.set_default_proxy(
            proxy_type,
            proxy.host,
            proxy.port,
            username=proxy.username,
            password=proxy.password,
        )
        socket.socket = socks.socksocket
        cls._socket_patched = True

    @classmethod
    def _restore_socket(cls) -> None:
        if cls._socket_patched:
            socket.socket = cls._original_socket
            if socks is not None:  # pragma: no branch - defensive
                socks.set_default_proxy(None)
            cls._socket_patched = False

    @staticmethod
    def _proxies_equivalent(a: ProxyEndpoint, b: ProxyEndpoint) -> bool:
        return (
            a.endpoint == b.endpoint
            and a.username == b.username
            and a.password == b.password
        )


def _protocol_to_socks_type(protocol: str):
    if socks is None:
        return None
    protocol = protocol.lower()
    if protocol in {"socks5", "socks5h"}:
        return socks.SOCKS5
    if protocol in {"socks4", "socks4a"}:
        return socks.SOCKS4
    return None
