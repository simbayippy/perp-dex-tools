from __future__ import annotations

import json
from typing import Callable, Iterable, List, Optional

from networking.models import ProxyAssignment, ProxyEndpoint


async def load_proxy_assignments(
    db,
    account_id,
    *,
    decrypt: Optional[Callable[[str], str]] = None,
    only_active: bool = True,
) -> List[ProxyAssignment]:
    """
    Fetch proxy assignments for an account.

    Args:
        db: Databases Database instance (or compatible) with `fetch_all`.
        account_id: UUID of the account (str/UUID).
        decrypt: Optional callable to decrypt encrypted credential fields.
        only_active: When True, restrict to active assignments + proxies.

    Returns:
        List of ProxyAssignment instances (may be empty).
    """
    where_clauses: List[str] = ["apa.account_id = :account_id"]
    if only_active:
        where_clauses.append("apa.status = 'active'")
        where_clauses.append("np.is_active = TRUE")

    query = f"""
        SELECT
            apa.id AS assignment_id,
            apa.account_id,
            apa.priority,
            apa.status,
            apa.last_checked_at,
            np.id AS proxy_id,
            np.label,
            np.endpoint_url,
            np.auth_type,
            np.credentials_encrypted,
            np.metadata AS proxy_metadata,
            np.is_active AS proxy_is_active
        FROM account_proxy_assignments apa
        JOIN network_proxies np ON apa.proxy_id = np.id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY apa.priority ASC, np.label ASC
    """

    try:
        rows: Iterable = await db.fetch_all(query, {"account_id": account_id})
    except Exception as exc:  # pragma: no cover - depends on DB backend
        message = str(exc).lower()
        if "network_proxies" in message or "account_proxy_assignments" in message:
            # Tables not provisioned yet.
            return []
        raise

    assignments: List[ProxyAssignment] = []

    for row in rows:
        row_dict = dict(row)
        credentials_payload = _decode_credentials(row_dict.get("credentials_encrypted"), decrypt)

        raw_metadata = row_dict.get("proxy_metadata")
        metadata = {}
        if isinstance(raw_metadata, dict):
            metadata = raw_metadata
        elif isinstance(raw_metadata, str):
            try:
                decoded_meta = json.loads(raw_metadata)
                if isinstance(decoded_meta, dict):
                    metadata = decoded_meta
            except json.JSONDecodeError:
                metadata = {}

        endpoint = ProxyEndpoint(
            id=str(row_dict["proxy_id"]),
            label=row_dict["label"],
            endpoint=row_dict["endpoint_url"],
            auth_type=row_dict["auth_type"],
            username=credentials_payload.get("username"),
            password=credentials_payload.get("password"),
            metadata=metadata,
            credentials=credentials_payload,
            is_active=bool(row_dict["proxy_is_active"]),
        )

        assignment = ProxyAssignment(
            id=str(row_dict["assignment_id"]),
            account_id=str(row_dict["account_id"]),
            proxy=endpoint,
            priority=row_dict["priority"] or 0,
            status=row_dict["status"] or "inactive",
            last_checked_at=row_dict.get("last_checked_at"),
        )

        if only_active and not assignment.is_account_active:
            # Guard in case the DB row slipped through filters.
            continue

        assignments.append(assignment)

    return assignments


def _decode_credentials(raw_payload, decrypt: Optional[Callable[[str], str]]) -> dict:
    if raw_payload is None:
        return {}

    payload = raw_payload
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            # Accept legacy plaintext payloads.
            pass

    if not isinstance(payload, dict):
        return {}

    decoded = {}
    for key, value in payload.items():
        if value is None:
            decoded[key] = None
            continue
        if decrypt:
            try:
                decoded[key] = decrypt(value)
            except Exception:
                # Best-effort: fall back to original value if decrypt fails.
                decoded[key] = value
        else:
            decoded[key] = value
    return decoded


def _coerce_dict(value) -> dict:  # kept for backwards compatibility
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
