"""Vision Perception Module.

Owner: Yakun. Reads camera frames from Furhat and hands them to whoever
registered as a listener. It knows nothing about Gemini: main.py connects
it to the conversation module. A debugging frontend can register its own
listener (or poll `latest_frame`) to display what the robot sees.
"""
import time
from typing import Awaitable, Callable, Optional

from furhat_realtime_api import AsyncFurhatClient, Events

# async def listener(image_b64: str) -> None
FrameListener = Callable[[str], Awaitable[None]]


class VisionModule:
    def __init__(self, furhat: AsyncFurhatClient, frame_interval: float = 1.0, debug: bool = False):
        self.furhat = furhat
        self.frame_interval = frame_interval  # minimum seconds between frames given to listeners
        self.debug = debug
        self.latest_frame: Optional[str] = None  # newest base64 image from the robot
        self.frames_received = 0
        self.frames_forwarded = 0
        self._listeners: list[FrameListener] = []
        self._last_forward = 0.0
        self.furhat.add_handler(Events.response_camera_data, self._on_camera_data)

    def add_frame_listener(self, listener: FrameListener):
        """Register an async callable that receives a base64 image (throttled)."""
        self._listeners.append(listener)

    async def start(self):
        await self.furhat.request_camera_start()

    async def stop(self):
        await self.furhat.request_camera_stop()

    async def _on_camera_data(self, data):
        image = data.get("image")
        self.frames_received += 1
        if self.debug and self.frames_received == 1:
            print("[vision] first frame received, keys:", list(data.keys()),
                  "image length:", len(image or ""))
        if not image:
            return
        self.latest_frame = image

        now = time.monotonic()
        if now - self._last_forward < self.frame_interval:
            return
        self._last_forward = now
        self.frames_forwarded += 1
        if self.debug:
            print(f"[vision] forwarding frame {self.frames_forwarded}")
        for listener in self._listeners:
            await listener(image)
