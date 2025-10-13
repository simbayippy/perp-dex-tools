#!/usr/bin/env python3
"""Entry point for the Textual dashboard application."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tui.dashboard_app import run_dashboard_app


if __name__ == "__main__":
    run_dashboard_app()
