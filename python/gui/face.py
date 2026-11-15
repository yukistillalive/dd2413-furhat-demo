"""GolemFace: thin, named wrappers around the Furhat Realtime API face/gaze/LED calls."""
import asyncio
import random

from furhat_realtime_api import AsyncFurhatClient, Events

# Built-in Furhat gesture names (validated by the robot, not by the Python client)
BLINK, NOD, SMILE = "Blink", "Nod", "Smile"
CLOSE_EYES, OPEN_EYES = "CloseEyes", "OpenEyes"
BROW_RAISE, BROW_FROWN = "BrowRaise", "BrowFrown"

# Non-mouth part of a smile (cheeks + eyes), safe to send while lipsync drives the mouth
SMILE_EYES_PARAMS = ["CHEEK_SQUINT_LEFT", "CHEEK_SQUINT_RIGHT", "EYE_SQUINT_LEFT", "EYE_SQUINT_RIGHT"]


class GolemFace:
    def __init__(self, furhat: AsyncFurhatClient):
        self.furhat = furhat
        self.speaking = False      # True while the robot talks (lipsync owns the mouth)
        self.eyes_closed = False
        self._tasks = set()        # keep references to delayed tasks so they are not garbage-collected
        furhat.add_handler(Events.response_speak_start, self._on_speak_start)
        furhat.add_handler(Events.response_speak_end, self._on_speak_end)

    async def _on_speak_start(self, _):
        self.speaking = True

    async def _on_speak_end(self, _):
        self.speaking = False

    def _later(self, delay: float, coro_fn, *args):
        """Run coro_fn(*args) after `delay` seconds without blocking the caller."""
        async def run():
            await asyncio.sleep(delay)
            await coro_fn(*args)
        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- Eyes ----

    async def blink(self):
        if not self.eyes_closed:
            await self.furhat.request_gesture_start(BLINK)

    async def close_eyes(self):
        self.eyes_closed = True
        await self.furhat.request_gesture_start(CLOSE_EYES)

    async def open_eyes(self):
        self.eyes_closed = False
        await self.furhat.request_gesture_start(OPEN_EYES)

    # ---- Head, brows, mouth ----

    async def nod(self):
        await self.furhat.request_gesture_start(NOD)

    async def raise_brows(self, intensity: float = 0.7):
        await self.furhat.request_gesture_start(BROW_RAISE, intensity=intensity)

    async def frown(self, intensity: float = 0.5):
        await self.furhat.request_gesture_start(BROW_FROWN, intensity=intensity)

    async def smile(self, intensity: float = 0.5, duration: float = 2.0):
        if not self.speaking:
            # Full smile gesture (moves the mouth) - only when lipsync is not running
            await self.furhat.request_gesture_start(SMILE, intensity=intensity, duration=duration)
            return
        # While speaking: smile with cheeks and eyes only, then release
        await self.furhat.request_face_params({p: intensity for p in SMILE_EYES_PARAMS})
        self._later(duration, self.furhat.request_face_params, {p: 0.0 for p in SMILE_EYES_PARAMS})

    # ---- Gaze (metres relative to the robot: x right, y up, z forward) ----

    async def gaze_user(self, user_id: str = "closest"):
        await self.furhat.request_attend_user(user_id)

    async def gaze_location(self, x: float, y: float, z: float):
        await self.furhat.request_attend_location(x, y, z)

    async def gaze_away(self, duration: float = 2.0):
        """Look to one side, then return to the user after `duration` seconds."""
        await self.gaze_location(random.choice([-0.6, 0.6]), 0.1, 1.0)
        self._later(duration, self.gaze_user)

    # ---- LED ----

    async def set_led(self, color: str):
        await self.furhat.request_led_set(color)  # "#rrggbb", "#000000" = off
