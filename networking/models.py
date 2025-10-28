from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from uuid import UUID


@dataclass(frozen=True)
class ProxyCredential:
    """Authentication material for a proxy endpoint."""

    username: Optional[str] = None
    password: Optional[str] = None

    def is_empty(self) -> bool:
        return not self.username and not self.password


@dataclass(frozen=True)
class ProxyEndpoint:
    """
    Represents a proxy endpoint with optional authentication and metadata.

    The `endpoint_url` should be the base URL understood by HTTP/WebSocket
    clients (e.g. ``http://1.2.3.4:8080`` or ``socks5://proxy.example.com:1080``).
    Authentication may either be embedded in the URL or provided separately
    via ``credentials``.
    """

    id: Optional[UUID]
    label: str
    endpoint_url: str
    auth_type: str = "none"
    credentials: Optional[ProxyCredential] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True

    def url_with_auth(self) -> str:
        """
        Return a URL that includes authentication details when the proxy uses
        basic auth credentials.

        For SOCKS proxies or endpoints that already contain credentials, the
        original ``endpoint_url`` is returned unchanged.
        """
        if self.credentials is None or self.credentials.is_empty():
            return self.endpoint_url

        # Only embed credentials for HTTP/HTTPS proxies unless already present.
        if "://" not in self.endpoint_url:
            return self.endpoint_url

        scheme, rest = self.endpoint_url.split("://", 1)
        if "@" in rest:
            return self.endpoint_url  # Credentials already embedded

        username = self.credentials.username or ""
        password = self.credentials.password or ""
        cred_part = f"{username}:{password}" if password else username
        return f"{scheme}://{cred_part}@{rest}"


@dataclass(frozen=True)
class ProxyAssignment:
    """
    Maps a proxy endpoint to an account with rotation metadata.
    """

    account_id: UUID
    proxy: ProxyEndpoint
    priority: int = 0
    status: str = "active"
    last_checked_at: Optional[Any] = None  # Timestamp (datetime) stored without imposing dependency

    def is_active(self) -> bool:
        return self.status.lower() == "active" and self.proxy.is_active
