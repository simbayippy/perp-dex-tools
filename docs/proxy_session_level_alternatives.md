# Session-Level Proxy Configuration Alternatives

## Problem Statement

Currently, we manually configure proxies at multiple levels in our exchange clients:
- Monkey-patching SDK nonce managers
- Configuring API client instances
- Ensuring signer clients use the proxy
- Re-configuring on proxy rotation

This is complex, error-prone, and requires exchange-specific implementations for each SDK.

## Alternative Approaches

This document outlines cleaner alternatives that enable session-level or process-level proxy configuration, so **all network requests automatically route through the proxy** without manual configuration.

---

## Approach 1: Socket-Level Monkey Patching (Most Comprehensive)

**Coverage:** ALL network connections (HTTP, HTTPS, WebSocket, DNS)  
**Scope:** Process-wide or context-scoped  
**Dependencies:** `pip install PySocks`

This intercepts network connections at the lowest level by replacing `socket.socket` globally.

### Implementation

```python
# networking/socket_proxy.py
import socket
import socks  # pip install PySocks
from typing import Optional

class GlobalProxyManager:
    """Monkey-patch socket.socket to force all connections through proxy."""
    
    _original_socket = socket.socket
    _proxy_enabled = False
    
    @classmethod
    def enable_proxy(cls, proxy_host: str, proxy_port: int, 
                    username: Optional[str] = None, password: Optional[str] = None):
        """Replace socket.socket globally with SOCKS proxy version."""
        if cls._proxy_enabled:
            return
            
        # Configure SOCKS default proxy
        socks.set_default_proxy(
            socks.SOCKS5,  # or SOCKS4/HTTP depending on your proxy
            proxy_host, 
            proxy_port,
            username=username,
            password=password
        )
        
        # Replace socket.socket with proxy-aware version
        socket.socket = socks.socksocket
        cls._proxy_enabled = True
        
    @classmethod
    def disable_proxy(cls):
        """Restore original socket implementation."""
        if not cls._proxy_enabled:
            return
            
        socket.socket = cls._original_socket
        socks.set_default_proxy(None)
        cls._proxy_enabled = False
```

### Usage in Exchange Client

```python
# In LighterClient.__init__ or connect()
if self.current_proxy():
    proxy = self.current_proxy()
    GlobalProxyManager.enable_proxy(
        proxy.host, 
        proxy.port,
        proxy.username,
        proxy.password
    )
```

### Pros
- ✅ Works with **any** HTTP library (requests, httpx, aiohttp, urllib3)
- ✅ Covers WebSocket connections
- ✅ Covers DNS queries
- ✅ No need to modify SDK code or configure individual clients
- ✅ Single point of configuration

### Cons
- ⚠️ Affects the **entire Python process** (all threads/tasks)
- ⚠️ Requires SOCKS proxy support (may need conversion from HTTP proxy)
- ⚠️ Global state can be tricky with multiple accounts in same process

---

## Approach 2: Environment Variable Configuration (Simplest)

**Coverage:** Most HTTP libraries (requests, httpx, aiohttp, curl, etc.)  
**Scope:** Process-wide  
**Dependencies:** None (standard library)

Many libraries automatically check environment variables for proxy configuration.

### Implementation

```python
# networking/env_proxy.py
import os
from typing import Optional
from .models import ProxyEndpoint

class EnvironmentProxyManager:
    """Set process-wide proxy via environment variables."""
    
    @staticmethod
    def set_proxy(proxy: Optional[ProxyEndpoint]):
        """Configure environment for all HTTP libraries."""
        if proxy:
            proxy_url = proxy.url_with_auth()
            os.environ['HTTP_PROXY'] = proxy_url
            os.environ['HTTPS_PROXY'] = proxy_url
            os.environ['http_proxy'] = proxy_url  # Some libs check lowercase
            os.environ['https_proxy'] = proxy_url
            
            # For async libraries
            os.environ['ALL_PROXY'] = proxy_url
            os.environ['all_proxy'] = proxy_url
        else:
            # Clear proxy settings
            for var in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 
                       'https_proxy', 'ALL_PROXY', 'all_proxy']:
                os.environ.pop(var, None)
    
    @staticmethod
    def clear_proxy():
        """Remove all proxy environment variables."""
        EnvironmentProxyManager.set_proxy(None)
```

### Usage

```python
# In connect() or __init__
proxy = self.current_proxy()
EnvironmentProxyManager.set_proxy(proxy)
```

### Pros
- ✅ Extremely simple to implement
- ✅ Works with most HTTP libraries out of the box
- ✅ Standard approach recognized by curl, wget, etc.
- ✅ No external dependencies

### Cons
- ⚠️ Some libraries may ignore environment variables if explicitly configured
- ⚠️ Process-wide scope (all threads/tasks)
- ⚠️ May not cover all edge cases (some SDKs ignore env vars)

---

## Approach 3: Transport-Level Interception (For httpx/aiohttp)

**Coverage:** httpx and aiohttp clients  
**Scope:** Per-client instance  
**Dependencies:** `httpx` or `aiohttp`

Intercept at the HTTP transport layer for async libraries.

### Implementation for httpx

```python
# networking/transport_proxy.py
import httpcore
from typing import Optional

class ProxiedTransport(httpcore.AsyncHTTPTransport):
    """Custom transport that forces all connections through proxy."""
    
    def __init__(self, proxy_url: str):
        self.proxy = httpcore.AsyncHTTPProxy(proxy_url)
        
    async def handle_async_request(self, request):
        # Route all requests through proxy
        return await self.proxy.handle_async_request(request)
```

### Usage with httpx

```python
import httpx

proxy_url = "http://user:pass@proxy:8080"
transport = ProxiedTransport(proxy_url)
client = httpx.AsyncClient(transport=transport)
# Now ALL requests from this client use the proxy
```

### Pros
- ✅ Clean, library-native approach
- ✅ Per-client configuration (not process-wide)
- ✅ Works well with async code
- ✅ No monkey patching

### Cons
- ⚠️ Only works with specific HTTP libraries (httpx, aiohttp)
- ⚠️ Need to create custom clients for each library
- ⚠️ Doesn't cover SDKs that use requests or other libraries

---

## Approach 4: urllib3 Connection Pool Override

**Coverage:** requests, urllib3-based libraries  
**Scope:** Process-wide  
**Dependencies:** `urllib3`

Override urllib3's connection creation to route through proxy.

### Implementation

```python
# networking/urllib3_proxy.py
import urllib3
from urllib3.util import connection

class ProxyConnectionPool:
    """Override urllib3's connection creation."""
    
    @staticmethod
    def patch_urllib3(proxy_host: str, proxy_port: int):
        """Force all urllib3 connections through proxy."""
        
        original_create_connection = connection.create_connection
        
        def proxy_create_connection(address, *args, **kwargs):
            # Intercept and route through proxy
            # Connect to proxy first, then tunnel to target
            conn = original_create_connection((proxy_host, proxy_port), *args, **kwargs)
            # Send CONNECT request for HTTPS tunneling
            # ... implementation details ...
            return conn
            
        connection.create_connection = proxy_create_connection
```

### Pros
- ✅ Covers all requests/urllib3-based libraries
- ✅ Single configuration point

### Cons
- ⚠️ Complex CONNECT tunnel implementation needed
- ⚠️ Doesn't cover httpx/aiohttp
- ⚠️ Process-wide monkey patching

---

## Approach 5: Context Manager Pattern (Cleanest for Multi-Account)

**Coverage:** All approaches above, but scoped  
**Scope:** Async context-scoped (per task/coroutine)  
**Dependencies:** Depends on chosen approach

Use Python's context managers to scope proxy settings per async task.

### Implementation

```python
# networking/proxy_context.py
import contextvars
from contextlib import contextmanager
from typing import Optional

# Thread-local storage for current proxy
_proxy_context: contextvars.ContextVar[Optional[ProxyEndpoint]] = contextvars.ContextVar('proxy', default=None)

@contextmanager
def proxy_context(proxy: Optional[ProxyEndpoint]):
    """Context manager to set proxy for current async context."""
    token = _proxy_context.set(proxy)
    try:
        if proxy:
            # Set environment variables
            EnvironmentProxyManager.set_proxy(proxy)
            # Enable socket patching
            GlobalProxyManager.enable_proxy(proxy.host, proxy.port, proxy.username, proxy.password)
        yield
    finally:
        _proxy_context.reset(token)
        # Clean up
        EnvironmentProxyManager.set_proxy(None)
        GlobalProxyManager.disable_proxy()

def get_current_proxy() -> Optional[ProxyEndpoint]:
    """Get proxy for current async context."""
    return _proxy_context.get()
```

### Usage in Trading Bot

```python
async def run_strategy():
    proxy = proxy_selector.current()
    with proxy_context(proxy):
        # ALL network calls in this context use the proxy
        client = LighterClient(config)
        await client.connect()
        # ... trading logic ...
```

### Pros
- ✅ Clean scoping per account/strategy
- ✅ Supports multiple accounts with different proxies in same process
- ✅ Automatic cleanup on context exit
- ✅ Works well with async code

### Cons
- ⚠️ Requires careful context management
- ⚠️ May still have edge cases with nested contexts

---

## Recommended Hybrid Approach

**Combine multiple techniques for comprehensive coverage and clean scoping:**

### Step 1: Add Dependencies

```bash
pip install PySocks
```

### Step 2: Create Unified Proxy Manager

```python
# networking/session_proxy.py
import os
import socket
import socks
from typing import Optional
from .models import ProxyEndpoint

class SessionProxyManager:
    """Unified proxy manager combining multiple approaches."""
    
    _active_proxy: Optional[ProxyEndpoint] = None
    _original_socket = socket.socket
    
    @classmethod
    def enable(cls, proxy: ProxyEndpoint):
        """Enable session-wide proxy using all available methods."""
        if cls._active_proxy == proxy:
            return  # Already enabled
            
        proxy_url = proxy.url_with_auth()
        
        # Method 1: Environment variables (for libraries that check them)
        os.environ['HTTP_PROXY'] = proxy_url
        os.environ['HTTPS_PROXY'] = proxy_url
        os.environ['http_proxy'] = proxy_url
        os.environ['https_proxy'] = proxy_url
        os.environ['ALL_PROXY'] = proxy_url
        os.environ['all_proxy'] = proxy_url
        
        # Method 2: Socket-level patching (for comprehensive coverage)
        if proxy.protocol == 'socks5':
            socks.set_default_proxy(
                socks.SOCKS5,
                proxy.host,
                proxy.port,
                username=proxy.username,
                password=proxy.password
            )
            socket.socket = socks.socksocket
        elif proxy.protocol == 'http':
            # For HTTP proxies, environment variables are usually sufficient
            # Some libraries like httpx will pick them up
            pass
        
        cls._active_proxy = proxy
    
    @classmethod
    def disable(cls):
        """Restore original networking configuration."""
        # Clear environment variables
        for var in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 
                   'https_proxy', 'ALL_PROXY', 'all_proxy']:
            os.environ.pop(var, None)
        
        # Restore socket
        socket.socket = cls._original_socket
        socks.set_default_proxy(None)
        
        cls._active_proxy = None
    
    @classmethod
    def rotate(cls, new_proxy: ProxyEndpoint):
        """Switch to a different proxy."""
        cls.disable()
        cls.enable(new_proxy)
```

### Step 3: Integrate into Exchange Client

```python
# exchange_clients/lighter/client.py
from networking.session_proxy import SessionProxyManager

class LighterClient(BaseExchangeClient):
    async def connect(self) -> None:
        """Connect with automatic proxy configuration."""
        proxy = self.current_proxy()
        
        if proxy:
            # Enable session-wide proxy
            SessionProxyManager.enable(proxy)
            self.logger.info(f"[LIGHTER] Session proxy enabled: {proxy.label}")
        
        # Now ALL SDK calls automatically use the proxy!
        # No need for manual configuration
        await self._initialize_lighter_client()
        self.account_api = lighter.AccountApi(self.api_client)
        # ... rest of initialization
        
    async def disconnect(self) -> None:
        """Disconnect and clean up proxy."""
        # ... existing disconnect logic ...
        
        # Optionally disable proxy (if not shared across clients)
        # SessionProxyManager.disable()
```

### Step 4: Handle Proxy Rotation

```python
def _initialize_api_client(self, rotate: bool = False, rebuild_dependents: bool = False) -> None:
    """Simplified - proxy is already configured at session level."""
    
    if rotate:
        new_proxy = self.current_proxy(rotate_if_needed=True)
        if new_proxy:
            SessionProxyManager.rotate(new_proxy)
            self.logger.debug(f"[LIGHTER] Rotated to proxy: {new_proxy.label}")
    
    # Create API client - it will automatically use session proxy
    configuration = Configuration(host=self.base_url)
    self.api_client = ApiClient(configuration=configuration)
    
    # No need to manually configure proxy on api_client!
```

---

## Benefits Summary

### Before (Current Approach)
- ❌ Manual proxy configuration in 3-4 different places
- ❌ Exchange-specific monkey patches
- ❌ Complex rotation logic scattered across methods
- ❌ Error-prone (easy to miss a spot)
- ❌ 200+ lines of proxy-specific code in client.py

### After (Session-Level Approach)
- ✅ Single `SessionProxyManager.enable()` call
- ✅ Works with **any** SDK or HTTP library
- ✅ Clean rotation: just call `SessionProxyManager.rotate()`
- ✅ Removes ~150 lines of proxy configuration code from client
- ✅ Much easier to maintain and debug

---

## Migration Path

1. **Phase 1:** Implement `SessionProxyManager` in `networking/session_proxy.py`
2. **Phase 2:** Update `LighterClient.connect()` to use `SessionProxyManager.enable()`
3. **Phase 3:** Remove manual proxy configuration from:
   - `_initialize_api_client()`
   - `_ensure_nonce_proxy_patch()`
   - `_configure_signer_client_proxy()`
4. **Phase 4:** Test with live trading on VPS
5. **Phase 5:** Apply same pattern to other exchange clients (Backpack, Aster, etc.)
6. **Phase 6:** Remove deprecated proxy configuration methods

---

## Caveats & Considerations

### Multiple Accounts in Same Process
If running multiple accounts with different proxies in the same process, you'll need either:
- Separate processes per account (simplest)
- Context-scoped proxies using `contextvars` (complex but possible)
- Thread-local storage (if using threads)

### Proxy Protocol Support
- **SOCKS5**: Best for comprehensive coverage (socket-level patching works)
- **HTTP/HTTPS**: Relies on environment variables (most libraries support this)
- **SOCKS4**: Similar to SOCKS5 but less common

### WebSocket Considerations
- Socket-level patching covers WebSocket connections ✅
- Environment variables may not be checked by all WebSocket libraries ⚠️
- Test WebSocket connections after implementing to ensure proxy routing

### Data Transfer Costs
With proxies charging per GB:
- Session-level approach doesn't change data usage
- Still ~1.5 GB/day per bot (~43 GB/month)
- Ensure **only trading bots** use proxies, not data collectors

---

## Testing Checklist

Before deploying to production:

- [ ] Test REST API calls route through proxy
- [ ] Test WebSocket connections route through proxy
- [ ] Test nonce fetching routes through proxy
- [ ] Test order placement/cancellation
- [ ] Test proxy rotation on failure
- [ ] Verify egress IP matches proxy IP (`curl ifconfig.io` via proxy)
- [ ] Test with multiple concurrent strategies
- [ ] Monitor data usage (nethogs/iftop)
- [ ] Test graceful degradation when proxy fails
- [ ] Verify logs show correct proxy label

---

## Further Reading

- [PySocks Documentation](https://github.com/Anorov/PySocks)
- [Python Requests Proxy Guide](https://docs.python-requests.org/en/latest/user/advanced/#proxies)
- [httpx Proxy Configuration](https://www.python-httpx.org/advanced/#http-proxying)
- [SOCKS Protocol Overview](https://en.wikipedia.org/wiki/SOCKS)
- [Environment Variable Proxy Standards](https://www.gnu.org/software/wget/manual/html_node/Proxies.html)

---

## Conclusion

Session-level proxy configuration is **significantly cleaner** than manual configuration at multiple levels. The hybrid approach (environment variables + socket patching) provides comprehensive coverage while maintaining simplicity.

**Recommended Action:** Implement `SessionProxyManager` and migrate `LighterClient` as a proof of concept, then expand to other exchange clients once validated.

