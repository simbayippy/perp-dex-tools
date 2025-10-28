from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from .models import ProxyEndpoint


def create_httpx_client(
    proxy: Optional[ProxyEndpoint],
    *,
    timeout: Optional[httpx.Timeout] = None,
    **kwargs: Any,
) -> httpx.AsyncClient:
    """
    Return an AsyncClient configured to route requests through the proxy.

    Args:
        proxy: Proxy endpoint definition. When ``None``, a standard client is returned.
        timeout: Optional explicit timeout. If omitted, httpx defaults are used.
        **kwargs: Additional parameters forwarded to ``httpx.AsyncClient``.
    """
    client_kwargs: Dict[str, Any] = dict(kwargs)

    if timeout is not None:
        client_kwargs["timeout"] = timeout

    if proxy:
        proxy_url = proxy.url_with_auth()
        client_kwargs.setdefault("proxies", {"http://": proxy_url, "https://": proxy_url})

    return httpx.AsyncClient(**client_kwargs)
