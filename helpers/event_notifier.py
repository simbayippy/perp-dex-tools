"""
Grid strategy event notifier.

Captures structured events emitted by the grid strategy and optionally
forwards them to alerting channels (Telegram) while recording them locally
for post-trade analysis.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from helpers.telegram_bot import TelegramBot


class GridEventNotifier:
    """
    Light-weight alert dispatcher for grid strategy events.

    Responsibilities:
    - Persist every event as JSONL (`logs/grid_events.jsonl`)
    - Forward high-severity events to Telegram if credentials are provided
    """

    def __init__(
        self,
        strategy: str,
        exchange: str,
        ticker: str,
        *,
        history_path: Optional[Path] = None,
    ) -> None:
        self.strategy = strategy
        self.exchange = exchange
        self.ticker = ticker

        logs_dir = Path("logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = history_path or logs_dir / "grid_events.jsonl"

        self._telegram_token = os.getenv("GRID_ALERT_TELEGRAM_TOKEN")
        self._telegram_chat_id = os.getenv("GRID_ALERT_TELEGRAM_CHAT_ID")
        self._telegram_bot: Optional[TelegramBot] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not currently in an asyncio loop (e.g. during unit tests)
            self._loop = None

        if self._telegram_token and self._telegram_chat_id:
            self._telegram_bot = TelegramBot(
                token=self._telegram_token,
                chat_id=self._telegram_chat_id,
            )

    def notify(
        self,
        *,
        event_type: str,
        level: str,
        message: str,
        payload: Dict[str, Any],
    ) -> None:
        """Persist and optionally forward an event."""
        timestamp = datetime.now(timezone.utc).isoformat()
        record = {
            "timestamp": timestamp,
            "strategy": self.strategy,
            "exchange": self.exchange,
            "ticker": self.ticker,
            "level": level,
            "event_type": event_type,
            "message": message,
            "payload": payload,
        }

        self._write_history(record)

        if self._telegram_bot and level in {"WARNING", "ERROR", "CRITICAL"}:
            self._send_telegram(record)

    def _write_history(self, record: Dict[str, Any]) -> None:
        """Append record to JSONL history file."""
        try:
            with self.history_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")
        except Exception as exc:  # pragma: no cover - defensive logging
            # Fallback to stdout to avoid raising inside strategy loop
            print(f"[GridEventNotifier] Failed to write history: {exc}")

    def _send_telegram(self, record: Dict[str, Any]) -> None:
        """Send the alert payload to Telegram in a background thread."""
        if not self._telegram_bot:
            return

        level = record["level"]
        header = f"[GRID {level}] {record['event_type']}"
        details_lines = [
            f"Exchange: {record['exchange']}",
            f"Ticker: {record['ticker']}",
            f"Message: {record['message']}",
        ]

        payload = record.get("payload") or {}
        context_lines = [
            f"{key}: {value}"
            for key, value in sorted(payload.items())
        ]

        body = "\n".join(details_lines + ["", *context_lines])
        text = f"{header}\n{body}".strip()

        def _send() -> None:
            try:
                self._telegram_bot.send_text(text)
            except Exception as exc:  # pragma: no cover - defensive logging
                print(f"[GridEventNotifier] Telegram send failed: {exc}")

        if self._loop and self._loop.is_running():
            self._loop.run_in_executor(None, _send)
        else:
            _send()
