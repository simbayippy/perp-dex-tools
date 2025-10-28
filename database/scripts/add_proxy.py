#!/usr/bin/env python3
"""
Register or update proxy endpoints and optionally assign them to trading accounts.

Usage examples:
    python database/scripts/add_proxy.py \\
        --label primed_sg_1 \\
        --endpoint http://proxyas.primedproxies.com:8888 \\
        --username PRIM_USER \\
        --password SECRET \\
        --account acc1 \\
        --priority 0
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional, Tuple

# Ensure project root on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
from databases import Database

from database.scripts.add_account import CredentialEncryptor  # Reuse helper

dotenv.load_dotenv()


async def upsert_proxy(
    db: Database,
    label: str,
    endpoint_url: str,
    auth_type: str,
    encrypted_credentials: Optional[str],
) -> str:
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
    db: Database,
    account_name: str,
    proxy_id: str,
    priority: int,
    status: str = "active",
) -> None:
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


def parse_proxy_line(
    raw: str,
    *,
    scheme: str,
) -> Tuple[str, Optional[str], Optional[str]]:
    tokens = [token.strip() for token in raw.split(":") if token.strip()]
    if len(tokens) < 2:
        raise ValueError(f"Invalid proxy line (need host:port[:user:pass]): '{raw}'")

    host = tokens[0]
    port = tokens[1]
    endpoint_url = f"{scheme}://{host}:{port}"

    username = tokens[2] if len(tokens) >= 3 else None
    password = ":".join(tokens[3:]) if len(tokens) >= 4 else None

    return endpoint_url, username, password


async def main(args: argparse.Namespace) -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not set in environment")

    encryptor = CredentialEncryptor()

    async with Database(database_url) as db:
        if args.batch_file:
            batch = Path(args.batch_file)
            if not batch.exists():
                raise FileNotFoundError(f"Proxy list file not found: {batch}")

            lines = [
                line.strip()
                for line in batch.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            if not lines:
                raise ValueError("Proxy list file is empty")

            start_index = args.start_index or 1
            successes = 0

            for idx, line in enumerate(lines, start=start_index):
                try:
                    endpoint_url, username, password = parse_proxy_line(
                        line, scheme=args.scheme
                    )
                except ValueError as exc:
                    print(f"⚠️  Skipping invalid line {idx - start_index + 1}: {exc}")
                    continue

                label = f"{args.label_prefix}_{idx}"

                creds_payload = None
                if username or password:
                    payload = {
                        "username": encryptor.encrypt(username) if username else None,
                        "password": encryptor.encrypt(password) if password else None,
                    }
                    creds_payload = json.dumps(payload)

                proxy_id = await upsert_proxy(
                    db=db,
                    label=label,
                    endpoint_url=endpoint_url,
                    auth_type=args.auth_type,
                    encrypted_credentials=creds_payload,
                )
                print(f"✓ Proxy '{label}' registered (id: {proxy_id})")

                if args.account:
                    await assign_proxy(
                        db=db,
                        account_name=args.account,
                        proxy_id=proxy_id,
                        priority=args.priority + (idx - start_index),
                        status=args.status,
                    )
                    print(
                        f"  ↳ Assigned to account '{args.account}' "
                        f"(priority {args.priority + (idx - start_index)})"
                    )

                successes += 1

            print(f"\nSummary: {successes}/{len(lines)} proxies processed successfully.")
            return

        # Single proxy mode
        if not args.label or not args.endpoint:
            raise ValueError("Single proxy mode requires --label and --endpoint")

        creds_payload = None
        if args.username or args.password:
            payload = {
                "username": encryptor.encrypt(args.username) if args.username else None,
                "password": encryptor.encrypt(args.password) if args.password else None,
            }
            creds_payload = json.dumps(payload)

        proxy_id = await upsert_proxy(
            db=db,
            label=args.label,
            endpoint_url=args.endpoint,
            auth_type=args.auth_type,
            encrypted_credentials=creds_payload,
        )
        print(f"✓ Proxy '{args.label}' registered (id: {proxy_id})")

        if args.account:
            await assign_proxy(
                db=db,
                account_name=args.account,
                proxy_id=proxy_id,
                priority=args.priority,
                status=args.status,
            )
            print(
                f"✓ Assigned proxy '{args.label}' to account '{args.account}' "
                f"(priority={args.priority}, status={args.status})"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Add or update proxy endpoints")
    parser.add_argument("--label", help="Unique label for the proxy (required for single mode)")
    parser.add_argument("--endpoint", help="Proxy endpoint URL (http://host:port or socks5://...)")
    parser.add_argument(
        "--auth-type",
        default="basic",
        choices=["none", "basic", "token", "custom"],
        help="Authentication type used by the proxy (default: basic)",
    )
    parser.add_argument("--username", help="Proxy username (if applicable)")
    parser.add_argument("--password", help="Proxy password (if applicable)")
    parser.add_argument(
        "--batch-file",
        help="Path to newline-delimited proxy list (host:port[:user:pass]); overrides single mode",
    )
    parser.add_argument(
        "--label-prefix",
        default="proxy",
        help="Label prefix when using --batch-file (default: proxy)",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="Starting index appended to label prefix in batch mode (default: 1)",
    )
    parser.add_argument(
        "--scheme",
        default="http",
        choices=["http", "https", "socks5"],
        help="Scheme to prepend when parsing host:port entries (default: http)",
    )
    parser.add_argument("--account", help="Account name to assign the proxy to (optional)")
    parser.add_argument(
        "--priority",
        type=int,
        default=0,
        help="Priority when assigning to an account (lower numbers preferred)",
    )
    parser.add_argument(
        "--status",
        default="active",
        choices=["active", "standby", "burned"],
        help="Assignment status (default: active)",
    )
    return parser


if __name__ == "__main__":
    parser = build_parser()
    cli_args = parser.parse_args()
    asyncio.run(main(cli_args))
