from __future__ import annotations

import json
import logging
from typing import Callable, Iterable, List, Optional, Sequence
from uuid import UUID

from databases import Database

from .models import ProxyAssignment, ProxyCredential, ProxyEndpoint

logger = logging.getLogger(__name__)


async def load_proxy_assignments(
    db: Database,
    account_id: UUID,
    *,
    decrypt: Optional[Callable[[str], str]] = None,
    only_active: bool = True,
) -> List[ProxyAssignment]:
    """
    Load proxy assignments for an account.

    Args:
        db: Connected Database instance.
        account_id: Account identifier.
        decrypt: Optional callable used to decrypt stored credential values.
        only_active: Filter out inactive assignments if True.

    Returns:
        List of ProxyAssignment objects. Returns an empty list when the
        networking tables are not yet provisioned.
    """
    where_clause = "apa.status IN ('active', 'pending')" if only_active else "TRUE"

    query = f"""
        SELECT
            apa.account_id,
            apa.proxy_id,
            apa.priority,
            apa.status,
            apa.last_checked_at,
            np.label,
            np.endpoint_url,
            np.auth_type,
            np.credentials_encrypted,
            np.metadata,
            np.is_active AS proxy_is_active
        FROM account_proxy_assignments AS apa
        JOIN network_proxies AS np
          ON apa.proxy_id = np.id
        WHERE apa.account_id = :account_id
          AND {where_clause}
        ORDER BY apa.priority ASC, np.label ASC
    """

    try:
        rows = await db.fetch_all(query, {"account_id": account_id})
    except Exception as exc:  # pragma: no cover - defensive fallback for missing tables
        error_msg = str(exc).lower()
        if "account_proxy_assignments" in error_msg or "network_proxies" in error_msg:
            logger.debug("Proxy tables not available yet: %s", exc)
            return []
        raise

    assignments: List[ProxyAssignment] = []
    for row in rows:
        try:
            credentials = _decrypt_credentials(row["credentials_encrypted"], decrypt)
            metadata = _parse_metadata(row["metadata"])
            endpoint = ProxyEndpoint(
                id=row["proxy_id"],
                label=row["label"],
                endpoint_url=row["endpoint_url"],
                auth_type=row["auth_type"] or "none",
                credentials=credentials,
                metadata=metadata,
                is_active=bool(row["proxy_is_active"]),
            )
            assignment = ProxyAssignment(
                account_id=row["account_id"],
                proxy=endpoint,
                priority=row["priority"] or 0,
                status=row["status"] or "unknown",
                last_checked_at=row["last_checked_at"],
            )
            assignments.append(assignment)
        except Exception as exc:
            logger.warning("Failed to load proxy assignment (%s): %s", row.get("label"), exc)
            continue

    return assignments


def _decrypt_credentials(
    raw_value: Optional[str],
    decrypt: Optional[Callable[[str], str]],
) -> Optional[ProxyCredential]:
    if not raw_value:
        return None

    try:
        payload = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except json.JSONDecodeError:
        # Data is stored as plaintext or single encrypted blob
        payload = raw_value

    if isinstance(payload, dict):
        username_enc = payload.get("username")
        password_enc = payload.get("password")
        username = decrypt(username_enc) if decrypt and username_enc else username_enc
        password = decrypt(password_enc) if decrypt and password_enc else password_enc
        return ProxyCredential(username=username, password=password)

    if decrypt:
        try:
            decrypted = decrypt(payload)
            data = json.loads(decrypted)
            return ProxyCredential(
                username=data.get("username"),
                password=data.get("password"),
            )
        except Exception as exc:
            logger.warning("Unable to decrypt proxy credential payload: %s", exc)

    return None


def _parse_metadata(raw_value) -> dict:
    if raw_value is None:
        return {}
    if isinstance(raw_value, dict):
        return raw_value
    try:
        return json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return {}
