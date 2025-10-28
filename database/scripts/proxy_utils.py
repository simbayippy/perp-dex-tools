from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple


def parse_proxy_line(raw: str, *, scheme: str = "http") -> Tuple[str, Optional[str], Optional[str]]:
    """
    Parse a proxy definition string into endpoint URL and optional credentials.

    Expected format:
        host:port[:username[:password]]

    Args:
        raw: Raw proxy string.
        scheme: URL scheme to prepend when not provided (default: http).

    Returns:
        Tuple of (endpoint_url, username, password).

    Raises:
        ValueError: When the proxy string is malformed.
    """
    tokens = [token.strip() for token in raw.split(":") if token.strip()]
    if len(tokens) < 2:
        raise ValueError(f"Invalid proxy definition (expected host:port[:user:pass]): '{raw}'")

    host, port = tokens[0], tokens[1]
    endpoint_url = f"{scheme}://{host}:{port}"

    username = tokens[2] if len(tokens) >= 3 else None
    password = ":".join(tokens[3:]) if len(tokens) >= 4 else None

    return endpoint_url, username, password


async def upsert_proxy(
    db,
    *,
    label: str,
    endpoint_url: str,
    auth_type: str,
    encrypted_credentials: Optional[str],
) -> str:
    """
    Insert or update a proxy entry and return its ID.
    """
    query = """
        INSERT INTO network_proxies (label, endpoint_url, auth_type, credentials_encrypted, is_active)
        VALUES (:label, :endpoint, :auth_type, CAST(:creds AS jsonb), TRUE)
        ON CONFLICT (label) DO UPDATE
        SET endpoint_url = EXCLUDED.endpoint_url,
            auth_type = EXCLUDED.auth_type,
            credentials_encrypted = EXCLUDED.credentials_encrypted,
            is_active = TRUE,
            updated_at = NOW()
        RETURNING id
    """
    row = await db.fetch_one(
        query,
        {
            "label": label,
            "endpoint": endpoint_url,
            "auth_type": auth_type,
            "creds": encrypted_credentials,
        },
    )
    return str(row["id"])


async def assign_proxy(
    db,
    *,
    account_name: str,
    proxy_id: str,
    priority: int,
    status: str = "active",
) -> None:
    """
    Attach a proxy to an account (upserts on existing mapping).
    """
    account_row = await db.fetch_one(
        "SELECT id FROM accounts WHERE account_name = :name",
        {"name": account_name},
    )
    if not account_row:
        raise ValueError(f"Account '{account_name}' not found")

    account_id = account_row["id"]
    query = """
        INSERT INTO account_proxy_assignments (account_id, proxy_id, priority, status)
        VALUES (:account_id, :proxy_id, :priority, :status)
        ON CONFLICT (account_id, proxy_id) DO UPDATE
        SET priority = EXCLUDED.priority,
            status = EXCLUDED.status,
            updated_at = NOW()
    """
    await db.execute(
        query,
        {
            "account_id": account_id,
            "proxy_id": proxy_id,
            "priority": priority,
            "status": status,
        },
    )
