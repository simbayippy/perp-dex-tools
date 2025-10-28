"""Custom exceptions for proxy and networking utilities."""


class ProxyError(Exception):
    """Base class for proxy-related errors."""


class ProxyUnavailableError(ProxyError):
    """Raised when no suitable proxy endpoint can be selected or reached."""


class ProxyConfigurationError(ProxyError):
    """Raised when proxy configuration is invalid or incomplete."""
