"""WebSocket endpoint for real-time streaming of metrics, alerts, and predictions."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("api-gateway.ws")

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    @property
    def active_count(self) -> int:
        """Return the number of active WebSocket connections."""
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and register it."""
        await websocket.accept()
        self._connections.append(websocket)
        logger.info(
            "WebSocket client connected (%d active)", len(self._connections)
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active list."""
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info(
            "WebSocket client disconnected (%d active)", len(self._connections)
        )

    async def broadcast(self, message: str) -> None:
        """Send a text message to all connected WebSocket clients.

        Silently removes clients that have disconnected.
        """
        stale: list[WebSocket] = []
        for connection in self._connections:
            try:
                await connection.send_text(message)
            except Exception:
                stale.append(connection)
        for conn in stale:
            self.disconnect(conn)

    async def send_personal(self, websocket: WebSocket, message: str) -> None:
        """Send a text message to a specific WebSocket client."""
        try:
            await websocket.send_text(message)
        except Exception:
            self.disconnect(websocket)


@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time data streaming.

    Clients connect to receive JSON messages in the format::

        {"type": "metric|alert|prediction", "data": {...}}

    Messages are broadcast by the Kafka consumer background tasks
    defined in ``app.main``.  This handler keeps the connection
    alive and listens for client pings / close frames.
    """
    # Import the shared manager instance from main
    from app.main import ws_manager

    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive by reading client messages (pings, etc.)
            # The actual data push happens via ws_manager.broadcast()
            data = await websocket.receive_text()
            # Clients can send a JSON message; for now we just acknowledge.
            # Future: support subscription filters.
            logger.debug("Received from client: %s", data)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)
