#!/usr/bin/env python3
"""
Delete proxies and their assignments by label pattern.

Usage:
    python database/scripts/delete_proxies.py --pattern "primed_sg_%"
    python database/scripts/delete_proxies.py --label "primed_sg_1"
    python database/scripts/delete_proxies.py --pattern "luna_isp_%" --account acc1
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
from databases import Database

dotenv.load_dotenv()


async def delete_by_pattern(db: Database, pattern: str, account_name: str = None) -> None:
    """Delete proxies matching a label pattern."""
    
    # First, show what will be deleted
    if account_name:
        query = """
            SELECT np.id, np.label, np.endpoint_url
            FROM network_proxies np
            JOIN account_proxy_assignments apa ON np.id = apa.proxy_id
            JOIN accounts a ON apa.account_id = a.id
            WHERE np.label LIKE :pattern AND a.account_name = :account
            ORDER BY np.label
        """
        rows = await db.fetch_all(query, {"pattern": pattern, "account": account_name})
    else:
        query = """
            SELECT id, label, endpoint_url
            FROM network_proxies
            WHERE label LIKE :pattern
            ORDER BY label
        """
        rows = await db.fetch_all(query, {"pattern": pattern})
    
    if not rows:
        print(f"No proxies found matching pattern: {pattern}")
        return
    
    print(f"\nFound {len(rows)} proxy(ies) to delete:")
    for row in rows:
        print(f"  • {row['label']} ({row['endpoint_url']})")
    
    # Confirm deletion
    response = input(f"\nDelete these {len(rows)} proxy(ies)? [y/N]: ").strip().lower()
    if response != 'y':
        print("Cancelled.")
        return
    
    # Delete assignments first
    if account_name:
        assignments_query = """
            DELETE FROM account_proxy_assignments
            WHERE proxy_id IN (
                SELECT np.id FROM network_proxies np
                JOIN account_proxy_assignments apa ON np.id = apa.proxy_id
                JOIN accounts a ON apa.account_id = a.id
                WHERE np.label LIKE :pattern AND a.account_name = :account
            )
        """
        assignments_deleted = await db.execute(
            assignments_query, {"pattern": pattern, "account": account_name}
        )
    else:
        assignments_query = """
            DELETE FROM account_proxy_assignments
            WHERE proxy_id IN (
                SELECT id FROM network_proxies WHERE label LIKE :pattern
            )
        """
        assignments_deleted = await db.execute(assignments_query, {"pattern": pattern})
    
    print(f"✓ Deleted {assignments_deleted} proxy assignment(s)")
    
    # Delete proxies
    proxies_query = "DELETE FROM network_proxies WHERE label LIKE :pattern"
    proxies_deleted = await db.execute(proxies_query, {"pattern": pattern})
    print(f"✓ Deleted {proxies_deleted} proxy(ies)")


async def delete_by_label(db: Database, label: str) -> None:
    """Delete a specific proxy by exact label."""
    
    # Check if exists
    row = await db.fetch_one(
        "SELECT id, label, endpoint_url FROM network_proxies WHERE label = :label",
        {"label": label}
    )
    
    if not row:
        print(f"Proxy not found: {label}")
        return
    
    print(f"\nFound proxy to delete:")
    print(f"  • {row['label']} ({row['endpoint_url']})")
    
    # Confirm deletion
    response = input(f"\nDelete this proxy? [y/N]: ").strip().lower()
    if response != 'y':
        print("Cancelled.")
        return
    
    # Delete assignments
    assignments_deleted = await db.execute(
        "DELETE FROM account_proxy_assignments WHERE proxy_id = :id",
        {"id": row['id']}
    )
    print(f"✓ Deleted {assignments_deleted} proxy assignment(s)")
    
    # Delete proxy
    await db.execute("DELETE FROM network_proxies WHERE id = :id", {"id": row['id']})
    print(f"✓ Deleted proxy '{label}'")


async def main(args: argparse.Namespace) -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set in environment")
    
    async with Database(db_url) as db:
        if args.pattern:
            await delete_by_pattern(db, args.pattern, args.account)
        elif args.label:
            await delete_by_label(db, args.label)
        else:
            print("Error: Must specify either --pattern or --label")
            return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete proxies by label pattern or exact label"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--pattern",
        help="SQL LIKE pattern to match proxy labels (e.g., 'primed_sg_%%' or 'luna_isp_%%')"
    )
    group.add_argument(
        "--label",
        help="Exact proxy label to delete"
    )
    parser.add_argument(
        "--account",
        help="Only delete proxies assigned to this account (optional, only works with --pattern)"
    )
    return parser


if __name__ == "__main__":
    parser = build_parser()
    cli_args = parser.parse_args()
    asyncio.run(main(cli_args))

