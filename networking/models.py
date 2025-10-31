from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

_DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
    "socks5": 1080,
    "socks4": 1080,
    "socks4a": 1080,
}


@dataclass(slots=True)
class ProxyEndpoint:
    """
    Represents a network proxy endpoint with optional credentials.

    Attributes:
        id: Proxy identifier (UUID as string).
        label: Human-friendly label for logging/selection.
        endpoint: Raw endpoint string as stored in the database.
        auth_type: Authentication flavour (none/basic/token/custom).
        username: Optional username (decrypted).
        password: Optional password (decrypted).
        metadata: Additional JSON payload from the database.
        is_active: Whether the proxy itself is active.
    """

    id: str
    label: str
    endpoint: str
    auth_type: str
    username: Optional[str] = None
    password: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    credentials: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True

    protocol: str = field(init=False)
    host: str = field(init=False)
    port: int = field(init=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if not parsed.scheme:
            # Default to http when scheme omitted.
            parsed = urlparse(f"http://{self.endpoint}")

        self.protocol = parsed.scheme.lower()
        self.host = parsed.hostname or ""
        port = parsed.port or _DEFAULT_PORTS.get(self.protocol)
        if port is None:
            raise ValueError(f"Proxy endpoint '{self.endpoint}' is missing a port")
        self.port = port

        # Normalise endpoint to include explicit port.
        netloc = parsed.netloc
        if "@" in netloc:
            # Strip embedded credentials; we control authentication via username/password.
            netloc = netloc.split("@", 1)[1]
        if ":" not in netloc:
            netloc = f"{self.host}:{self.port}"

        self.endpoint = urlunparse(
            (self.protocol, netloc, parsed.path or "", parsed.params, parsed.query, parsed.fragment)
        )

    def url_with_auth(self, *, mask_password: bool = False) -> str:
        """
        Build a proxy URL with credentials embedded.

        Args:
            mask_password: Replace the password with "***" for logging.
        """
        parsed = urlparse(self.endpoint)
        netloc = parsed.netloc

        if self.username:
            password = "***" if mask_password and self.password else self.password or ""
            auth_segment = self.username if not password else f"{self.username}:{password}"
            netloc = f"{auth_segment}@{parsed.hostname}:{parsed.port}"

        return urlunparse(
            (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
        )

    def masked_label(self) -> str:
        """Return label with endpoint info while masking credentials."""
        return f"{self.label} ({self.url_with_auth(mask_password=True)})"


@dataclass(slots=True)
class ProxyAssignment:
    """
    Represents an account-to-proxy mapping with rotation metadata.
    """

    id: str
    account_id: str
    proxy: ProxyEndpoint
    priority: int
    status: str
    last_checked_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        self.status = self.status.lower()

    @property
    def is_account_active(self) -> bool:
        """True when both assignment and proxy are active."""
        return self.status == "active" and self.proxy.is_active

    def to_dict(self) -> Dict[str, Any]:
        """Serialize assignment for debugging/logging."""
        return {
            "assignment_id": self.id,
            "account_id": self.account_id,
            "priority": self.priority,
            "status": self.status,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "proxy": {
                "id": self.proxy.id,
                "label": self.proxy.label,
                "endpoint": self.proxy.endpoint,
                "auth_type": self.proxy.auth_type,
                "protocol": self.proxy.protocol,
                "username": self.proxy.username,
                "has_password": bool(self.proxy.password),
                "metadata": self.proxy.metadata,
                "credentials": {k: "***" if k in {"password", "token"} and v else v for k, v in self.proxy.credentials.items()},
                "is_active": self.proxy.is_active,
            },
        }
