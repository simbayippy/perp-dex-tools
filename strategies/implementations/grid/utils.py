"""
Utility helpers for the grid strategy.
"""

from __future__ import annotations

import hashlib


def client_order_index_from_position(
    position_id: str,
    category: str,
    modulus: int = 1_000_000,
) -> int:
    """Derive a deterministic client-order index from a position identifier.

    The output is constrained by ``modulus`` (default 1,000,000) so it fits the
    more restrictive exchanges such as Lighter that require six-digit client
    order indices.  Different categories (e.g. "entry" vs "close") hash to
    independent values even after applying the modulus.
    """
    key = f"{position_id}:{category}".encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=8).digest()
    value = int.from_bytes(digest, byteorder="big", signed=False)

    if modulus and modulus > 1:
        value %= modulus
        if value == 0:
            value = 1

    return value
