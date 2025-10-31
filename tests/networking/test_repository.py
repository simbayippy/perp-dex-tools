import asyncio
import json

from networking.repository import load_proxy_assignments


class FakeDatabase:
    def __init__(self, rows=None, exception: Exception | None = None):
        self._rows = rows or []
        self._exception = exception
        self.last_query = None
        self.last_params = None

    async def fetch_all(self, query, params):
        if self._exception:
            raise self._exception
        self.last_query = query
        self.last_params = params
        return self._rows


def _row(
    *,
    assignment_id="assign-1",
    account_id="acc",
    priority=0,
    status="active",
    endpoint="socks5://1.2.3.4:1080",
    proxy_is_active=True,
    credentials=None,
    metadata=None,
):
    return {
        "assignment_id": assignment_id,
        "account_id": account_id,
        "priority": priority,
        "status": status,
        "last_checked_at": None,
        "proxy_id": "proxy-1",
        "label": "primary",
        "endpoint_url": endpoint,
        "auth_type": "basic",
        "credentials_encrypted": json.dumps(credentials) if credentials is not None else None,
        "proxy_metadata": json.dumps(metadata) if metadata is not None else None,
        "proxy_is_active": proxy_is_active,
    }


def test_load_proxy_assignments_decodes_credentials():
    creds = {"username": "enc_user", "password": "enc_pass"}
    db = FakeDatabase([_row(credentials=creds, metadata={"region": "sg"})])

    def decrypt(value: str) -> str:
        return value.replace("enc_", "dec_")

    assignments = asyncio.run(
        load_proxy_assignments(db, "acc", decrypt=decrypt, only_active=False)
    )

    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment.proxy.username == "dec_user"
    assert assignment.proxy.password == "dec_pass"
    assert assignment.proxy.metadata["region"] == "sg"
    assert assignment.proxy.credentials["username"] == "dec_user"
    assert assignment.is_account_active


def test_only_active_filters_standby():
    db = FakeDatabase([_row(status="standby")])
    assignments = asyncio.run(load_proxy_assignments(db, "acc", decrypt=None, only_active=True))
    assert assignments == []


def test_missing_tables_returns_empty():
    db = FakeDatabase(exception=Exception('relation "network_proxies" does not exist'))
    assignments = asyncio.run(load_proxy_assignments(db, "acc", decrypt=None, only_active=True))
    assert assignments == []
