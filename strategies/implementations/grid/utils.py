"""
Utility helpers for the grid strategy.
"""

from __future__ import annotations

import hashlib


def client_order_index_from_position(position_id: str, category: str) -> int:
    """
    Derive a deterministic client-order index from a position identifier.

    The result fits within 63 bits (signed 64-bit positive) to satisfy exchanges
    such as Lighter that expect ``int64`` client order indices.
    """
    key = f"{position_id}:{category}".encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=8).digest()
    value = int.from_bytes(digest, byteorder="big", signed=False)
    # Ensure positive 63-bit value
    mask = (1 << 63) - 1
    value &= mask
    if value == 0:
        value = 1
    return value

