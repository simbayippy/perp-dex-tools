# Proxies Implementation Roadmap

## Objective
Ensure each trading account process transparently routes all outbound traffic (HTTP, WebSocket, DNS) through its assigned proxy, with monitoring and rotation support, while leaving non-trading services unproxied.

## 1. Session Proxy Infrastructure
- [x] Add `PySocks` to the runtime dependencies (`requirements.txt` / `setup.cfg`) and document the install step (`pip install PySocks`).
- [x] Implement `networking/session_proxy.py` with a `SessionProxyManager` that:
  - [x] Normalizes proxy definitions from the DB model (`ProxyEndpoint`).
  - [x] Applies environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, lowercase variants).
  - [x] Monkey-patches `socket.socket` using `socks.socksocket` for SOCKS proxies.
  - [x] Skip socket patching for HTTP proxies (env-vars only) to keep tunnels stable.
  - [x] Provides `enable()`, `disable()`, `rotate()`, and `is_active()` helpers.
  - [x] Restores the original socket implementation and clears env vars on disable.

## 2. Process Bootstrap & Scoping
- [x] Extend the account launch path (`runbot.py` / CLI wrappers) to fetch the active proxy assignment for the selected account before any network clients initialize.
- [x] Invoke `SessionProxyManager.enable()` once per process, log the proxy label + endpoint, and expose a CLI flag/env override to skip proxy usage when needed.
- [ ] Ensure data-only services (`funding_rate_service`, collectors) bypass the proxy manager by default.
- [x] Add graceful handling for missing/disabled proxies (warn and continue without patching).

## 3. Exchange Client Integration
- [x] Update `exchange_clients/lighter/client.py` to rely on the session proxy (remove manual proxy wiring in `_initialize_api_client`, nonce patching, signer config).
- [ ] Apply the same pattern to Backpack, Aster, and any other SDK wrappers, replacing per-client proxy config with a simple readiness check (`SessionProxyManager.is_active()`).
- [ ] Audit for direct `requests`/`httpx` usage elsewhere and delete redundant proxy plumbing.
- [ ] Ensure WebSocket managers (Lighter, Backpack, etc.) honour the proxy (switch to proxy-aware client or tunnel helper).

## 4. Monitoring & Rotation
- [ ] Implement a startup check that resolves the visible IP via the proxy (`helpers/networking.py::detect_egress_ip`) and log the result.
- [ ] Emit structured logs with proxy label, endpoint, and detected IP at process start (and on rotation).
- [ ] Wire rotation logic to `SessionProxyManager.rotate()` when priority fallbacks are attempted or health checks fail.
- [ ] Capture proxy health metrics/events for operations dashboards.

## 5. Testing & Rollout
- [x] Add unit tests for `SessionProxyManager` (env var application, socket patch, disable/rotate behavior) under `tests/networking/test_session_proxy.py`.
- [ ] Write integration smoke scripts that launch a bot process against a test proxy and confirm egress IP change.
- [ ] Document manual verification steps (`curl ifconfig.me`, WebSocket handshake) before production rollout.

## 6. Documentation & Playbooks
- [ ] Update `docs/networking.md` (or new `docs/proxies.md`) with setup instructions, provider notes, and troubleshooting tips.
- [ ] Add runbook entries for proxy rotation, failure recovery, and cost monitoring (including provider shortlist mentioned in strategy docs).
- [ ] Capture per-account proxy configuration expectations in internal docs (`docs-internal/`).
