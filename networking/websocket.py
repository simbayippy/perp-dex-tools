from __future__ import annotations

from typing import Any, Dict, Optional

from websockets.client import connect

try:  # websockets >= 11 provides proxy helpers
    from websockets.proxy import proxy_connect
except ImportError:  # pragma: no cover - optional dependency
    proxy_connect = None

from .exceptions import ProxyUnavailableError
from .models import ProxyEndpoint


async def connect_via_proxy(
    uri: str,
    proxy: ProxyEndpoint,
    *,
    connect_kwargs: Optional[Dict[str, Any]] = None,
):
    """
    Establish a WebSocket connection through the given proxy.

    Falls back to a direct connection when proxy utilities are not available.
    """
    params = dict(connect_kwargs or {})

    if proxy is None:
        return await connect(uri, **params)

    proxy_uri = proxy.url_with_auth()
    if proxy_connect is None:
        raise ProxyUnavailableError(
            "websockets proxy support not available. Install websockets>=11.0 or provide a custom connector."
        )

    return await proxy_connect(proxy_uri, uri, **params)
