#!/usr/bin/env python3
"""
Clean up incorrectly parsed proxies from the database.
"""

import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
from databases import Database

dotenv.load_dotenv()


async def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set in environment")

    async with Database(db_url) as db:
        # Delete the bad proxy assignments first (foreign key constraint)
        assignments_deleted = await db.execute(
            """
            DELETE FROM account_proxy_assignments 
            WHERE proxy_id IN (
                SELECT id FROM network_proxies WHERE label LIKE 'luna_isp_%'
            )
            """
        )
        print(f"✓ Deleted {assignments_deleted} proxy assignments")

        # Delete the bad proxies
        proxies_deleted = await db.execute(
            "DELETE FROM network_proxies WHERE label LIKE 'luna_isp_%'"
        )
        print(f"✓ Deleted {proxies_deleted} proxies")
        print("\nYou can now re-run the add_proxy.py script with your proxies.txt file")


if __name__ == "__main__":
    asyncio.run(main())

