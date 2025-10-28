"""
Networking utilities for proxy-aware exchange operations.

This package centralizes proxy models, selection logic, repository helpers,
and protocol-specific client factories so exchange integrations can remain
agnostic about the underlying proxy implementation.
"""

from .models import ProxyEndpoint, ProxyCredential, ProxyAssignment
from .selector import ProxySelector
from .exceptions import ProxyUnavailableError, ProxyConfigurationError

__all__ = [
    "ProxyEndpoint",
    "ProxyCredential",
    "ProxyAssignment",
    "ProxySelector",
    "ProxyUnavailableError",
    "ProxyConfigurationError",
]
