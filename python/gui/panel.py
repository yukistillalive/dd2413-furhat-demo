"""Debug control panel: one web page + one websocket on the same port.

GET /            -> panel.html
websocket on /   -> the page sends an action name, we run it and reply with the result
"""
import json
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.server import serve

from .expressions import LABELS, perform

PAGE = (Path(__file__).parent / "panel.html").read_text()


def build_actions(gui) -> dict:
    """Button groups: {group: {button name: async callable without arguments}}."""
    face = gui.face
    return {
        "Primitives": {
            "blink": face.blink, "close_eyes": face.close_eyes, "open_eyes": face.open_eyes,
            "nod": face.nod, "smile": face.smile, "raise_brows": face.raise_brows, "frown": face.frown,
            "gaze_user": face.gaze_user, "gaze_away": face.gaze_away,
            "gaze_left": lambda: face.gaze_location(-0.6, 0.0, 1.0),
            "gaze_right": lambda: face.gaze_location(0.6, 0.0, 1.0),
            "led_off": lambda: face.set_led("#000000"),
        },
        "Expressions": {label: (lambda label=label: perform(face, label)) for label in LABELS},
        "Golem": {
            "awaken": gui.golem.awaken, "sleep": gui.golem.sleep,
            "thinking_mode": gui.idle.on_waiting_for_model, "listening_mode": gui.idle.on_user_turn_start,
        },
    }


class ControlPanel:
    def __init__(self, gui):
        self.gui = gui
        self.groups = build_actions(gui)
        self.actions = {name: fn for group in self.groups.values() for name, fn in group.items()}
        self.server = None

    async def start(self, port: int = 8080):
        self.server = await serve(self._handle, "127.0.0.1", port, process_request=self._http)
        print(f"Golem control panel: http://127.0.0.1:{port}")

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    def _http(self, connection, request):
        # Plain browser request -> serve the page; websocket upgrade -> return None to continue
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return None
        response = connection.respond(HTTPStatus.OK, PAGE)
        del response.headers["Content-Type"]
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response

    def _status(self) -> dict:
        return {"state": self.gui.golem.state, "mode": self.gui.idle.mode}

    async def _handle(self, ws):
        groups = {group: list(actions) for group, actions in self.groups.items()}
        await ws.send(json.dumps({"groups": groups, **self._status()}))
        async for name in ws:
            action = self.actions.get(name)
            if action is None:
                reply = {"error": f"unknown action {name}"}
            else:
                try:
                    await action()
                    reply = {"ok": name}
                except Exception as e:
                    reply = {"error": f"{name}: {e}"}
            await ws.send(json.dumps({**reply, **self._status()}))
