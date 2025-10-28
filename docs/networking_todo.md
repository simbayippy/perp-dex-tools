# Networking Implementation To‑Do

## Goal
Associate each trading account with a pool of static proxies, use them for all HTTP/WebSocket traffic, and support automatic rotation when an IP is burned.

## Database & Secrets
- [x] Create migration adding `network_proxies` (id, label, endpoint_url, auth_type, credentials_encrypted, metadata JSONB, is_active) and `account_proxy_assignments` (account_id FK, proxy_id FK, priority INT, status ENUM, last_checked_at TIMESTAMP).
- [x] Extend the Fernet helper in `database/scripts/add_account.py` to encrypt proxy credentials and add CLI flags `--proxy-label`, `--proxy-endpoint`, `--proxy-user`, `--proxy-pass`, `--proxy-priority`.
- [x] Seed initial proxies for the existing account and document required `.env` variables for the proxy encryption key.

## Networking Package
- [x] Add top-level `networking/` package with modules:
  - `models.py` – dataclasses `ProxyEndpoint`, `ProxyCredential`, `ProxyAssignment`.
  - `repository.py` – DB queries returning proxies for an account, respecting active status and priority ordering.
  - `selector.py` – rotation policies (round-robin with health checks, demote burned proxies).
  - `http.py` / `websocket.py` – factory helpers returning proxied `httpx.AsyncClient` and websocket connectors.
  - `exceptions.py` – `ProxyUnavailableError`, `ProxyAuthError`, etc.
- [ ] Add unit tests covering selection logic and helper factories (use dummy proxies/mocks).

## Runtime Proxy Strategy
- [ ] Implement per-process proxy enablement (e.g., socket-level patch or env vars) so each bot process routes all outbound traffic through its assigned proxy.
- [ ] Provide a CLI hook or wrapper that reads the account’s proxy configuration and enables the proxy before instantiating exchange clients.
- [ ] Document how to run one account per process (screen/tmux session) and verify the proxy in use.
- [ ] Ensure data-only collectors (funding_rate_service, etc.) skip proxy enablement.

## Operations & Monitoring
- [ ] Add a simple “detect external IP” check in each bot process to confirm the configured proxy is active.
- [ ] Emit logs showing proxy label and detected IP at startup.
- [ ] Document the per-process proxy bootstrap flow in `docs/networking.md` (providers, setup, verification, troubleshooting). Includes vendor shortlist (1Proxy, NetNut, Smartproxy fixed residential, Bright Data static residential, Oxylabs static residential).
