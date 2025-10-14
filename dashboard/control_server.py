"""
Dashboard control API and event streaming server.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from aiohttp import web

from dashboard.event_bus import event_bus
from dashboard.state import dashboard_state

JsonDict = Dict[str, Any]


class DashboardControlServer:
    """Expose snapshot and event stream endpoints over HTTP/WebSocket."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        self._host = host
        self._port = port
        self._app = web.Application()
        self._app.add_routes(
            [
                web.get("/snapshot", self._handle_snapshot),
                web.get("/stream", self._handle_stream),
            ]
        )
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    async def start(self) -> None:
        if self._runner is not None:
            return
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def _handle_snapshot(self, request: web.Request) -> web.Response:
        state = await dashboard_state.get_state()
        return web.json_response(_serialize_state(state))

    async def _handle_stream(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        queue = await event_bus.subscribe()
        try:
            state = await dashboard_state.get_state()
            await ws.send_json({"type": "snapshot", "payload": _serialize_state(state)})
            while True:
                message = await queue.get()
                await ws.send_json(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await ws.send_json({"type": "error", "payload": str(exc)})
        finally:
            await event_bus.unsubscribe(queue)
            await ws.close()
        return ws


def _serialize_state(state: JsonDict) -> JsonDict:
    serialized: JsonDict = {}
    for key, value in state.items():
        if value is None:
            serialized[key] = None
        elif key == "snapshot":
            serialized[key] = value.model_dump(mode="json")
        elif key == "session":
            serialized[key] = value.model_dump(mode="json")
        elif key == "events":
            serialized[key] = [event.model_dump(mode="json") for event in value]
        elif isinstance(value, datetime):
            serialized[key] = value.isoformat()
        else:
            serialized[key] = value
    return serialized


control_server = DashboardControlServer()
