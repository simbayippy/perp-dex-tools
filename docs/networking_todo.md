# Networking Implementation To‑Do

## Goal
Associate each trading account with a pool of static proxies, use them for all HTTP/WebSocket traffic, and support automatic rotation when an IP is burned.

## Database & Secrets
- [x] Create migration adding `network_proxies` (id, label, endpoint_url, auth_type, credentials_encrypted, metadata JSONB, is_active) and `account_proxy_assignments` (account_id FK, proxy_id FK, priority INT, status ENUM, last_checked_at TIMESTAMP).
- [ ] Extend the Fernet helper in `database/scripts/add_account.py` to encrypt proxy credentials and add CLI flags `--proxy-label`, `--proxy-endpoint`, `--proxy-user`, `--proxy-pass`, `--proxy-priority`.
- [ ] Seed initial proxies for the existing account and document required `.env` variables for the proxy encryption key.

## Networking Package
- [x] Add top-level `networking/` package with modules:
  - `models.py` – dataclasses `ProxyEndpoint`, `ProxyCredential`, `ProxyAssignment`.
  - `repository.py` – DB queries returning proxies for an account, respecting active status and priority ordering.
  - `selector.py` – rotation policies (round-robin with health checks, demote burned proxies).
  - `http.py` / `websocket.py` – factory helpers returning proxied `httpx.AsyncClient` and websocket connectors.
  - `exceptions.py` – `ProxyUnavailableError`, `ProxyAuthError`, etc.
- [ ] Add unit tests covering selection logic and helper factories (use dummy proxies/mocks).

## Client & Strategy Wiring
- [x] Update `exchange_clients/base_client.py` to accept an optional `ProxyEndpoint` or selector and expose helper methods (`_create_http_client`, `_connect_websocket`) that apply the proxy.
- [ ] Ensure all concrete exchange clients use the helpers when instantiating SDK sessions or websockets; for SDKs that accept transport overrides, pass the proxied client, otherwise inject via environment/session config.
- [x] Modify strategy bootstrap (`runbot.py`, `trading_bot.py`) to load proxy assignments per account (DB + optional config override) and pass them into every exchange client instance.

## Operations & Monitoring
- [ ] Implement a health check coroutine that verifies egress IP per proxy (`https://ifconfig.io` via proxy) and marks failing proxies inactive.
- [ ] Emit structured logs (`account`, `proxy_label`, `egress_ip`) when a client connects or rotates proxies.
- [ ] Document proxy provisioning in `docs/networking.md` (providers, setup, rotation policy, troubleshooting). Includes vendor shortlist (1Proxy, NetNut, Smartproxy fixed residential, Bright Data static residential, Oxylabs static residential).
