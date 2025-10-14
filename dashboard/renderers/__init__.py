"""
Terminal renderer implementations for dashboard snapshots.
"""

from .plain_renderer import PlainTextDashboardRenderer
from .rich_renderer import RichDashboardRenderer

__all__ = ["RichDashboardRenderer", "PlainTextDashboardRenderer"]
