"""Debug hub: pushes live camera frames, transcript and state to the debug-ui frontend.

A small WebSocket server (default ws://127.0.0.1:8765). Bound to localhost
only: it streams camera images and transcripts of real people.

Messages sent to the browser (JSON):
    {"type": "hello", "state", "history": [...], "frame": b64|null}   on connect
    {"type": "frame",   "image": b64}
    {"type": "message", "role": "user"|"robot", "text", "interrupted", "ts"}
    {"type": "event",   "name", "detail", "ts"}
    {"type": "state",   "state": "connecting|listening|speaking|reconnecting|stopped"}
"""
import json
from collections import deque
from datetime import datetime, timezone

from websockets.asyncio.server import serve, broadcast


DEFAULT_PORT = 8765   # the debug-ui frontend defaults to the same port (debug-ui/src/main.jsx)


class DebugHub:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, history: int = 300):
        self.host, self.port = host, port
        self.clients = set()
        self.history = deque(maxlen=history)   # recent messages/events for late joiners
        self.latest_frame: str | None = None
        self.state = "connecting"
        self._server = None

    async def start(self):
        self._server = await serve(self._handler, self.host, self.port, max_size=None)
        print(f"[debug-ui] streaming on ws://{self.host}:{self.port}")

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(self, ws):
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "hello", "state": self.state,
                                      "history": list(self.history), "frame": self.latest_frame}))
            async for _ in ws:   # browser sends nothing; just keep the socket open
                pass
        finally:
            self.clients.discard(ws)

    def _broadcast(self, obj: dict):
        if self.clients:
            broadcast(self.clients, json.dumps(obj))

    async def emit(self, obj: dict):
        obj.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
        if obj["type"] in ("message", "event"):
            self.history.append(obj)
        elif obj["type"] == "state":
            self.state = obj["state"]
        self._broadcast(obj)

    async def send_frame(self, image_b64: str):
        """Vision-module frame listener."""
        self.latest_frame = image_b64
        self._broadcast({"type": "frame", "image": image_b64})
