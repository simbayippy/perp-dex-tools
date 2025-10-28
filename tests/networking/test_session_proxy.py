import os
import socket

import pytest
from networking.models import ProxyEndpoint
from networking.session_proxy import SessionProxyManager

socks = pytest.importorskip("socks")

PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")


@pytest.fixture(autouse=True)
def reset_session_proxy():
    saved_env = {key: os.environ.get(key) for key in PROXY_ENV_VARS}
    SessionProxyManager.disable()
    yield
    SessionProxyManager.disable()
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def build_proxy(endpoint: str, username: str | None = None, password: str | None = None) -> ProxyEndpoint:
    return ProxyEndpoint(
        id="proxy-1",
        label="test-proxy",
        endpoint=endpoint,
        auth_type="basic",
        username=username,
        password=password,
    )


def test_enable_sets_env_and_patches_socket():
    proxy = build_proxy("socks5://127.0.0.1:9050", "alice", "secret")

    SessionProxyManager.enable(proxy)

    expected_url = "socks5://alice:secret@127.0.0.1:9050"
    for key in PROXY_ENV_VARS:
        assert os.environ.get(key) == expected_url

    assert SessionProxyManager.is_active() is True
    sock = socket.socket()
    try:
        assert isinstance(sock, socks.socksocket)  # type: ignore
    finally:
        sock.close()

    masked = SessionProxyManager.describe()
    assert masked == "socks5://alice:***@127.0.0.1:9050"


def test_disable_restores_socket_and_env():
    proxy = build_proxy("socks5://127.0.0.1:9050")
    original_socket = SessionProxyManager._original_socket  # type: ignore[attr-defined]

    SessionProxyManager.enable(proxy)
    SessionProxyManager.disable()

    for key in PROXY_ENV_VARS:
        assert key not in os.environ

    assert SessionProxyManager.is_active() is False
    assert socket.socket is original_socket


def test_rotate_switches_to_new_proxy():
    first = build_proxy("socks5://127.0.0.1:9050", "alice", "secret")
    second = build_proxy("http://10.0.0.5:8080", "bob", "open")

    SessionProxyManager.enable(first)
    SessionProxyManager.rotate(second)

    expected_url = "http://bob:open@10.0.0.5:8080"
    assert os.environ["HTTP_PROXY"] == expected_url
    assert SessionProxyManager.describe() == "http://bob:***@10.0.0.5:8080"
    sock = socket.socket()
    try:
        assert isinstance(sock, socks.socksocket)  # type: ignore
    finally:
        sock.close()
