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
        self.expression = "neutral"  # last label run by expressions.perform()
        self._tasks = set()        # keep references to delayed tasks so they are not garbage-collected
        self._held = {}            # face params kept active by hold_params()
        self._hold_task = None
        # LED owner check. main.py sets this to "scenario has no LED pattern":
        # in golem mode the scenario's breathing LED owns the LED and set_led() does nothing.
        self.led_allowed = lambda: True
        # Streamed audio sends no speak.start, only audio.buffer events while playing
        furhat.add_handler([Events.response_speak_start, Events.response_speak_audio_buffer], self._on_speak_start)
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

    async def blink(self, duration: float = 1.0):
        if not self.eyes_closed:
            await self.furhat.request_gesture_start(BLINK, duration=duration)

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

    # ---- Held face parameters (eyes/brows only - never MOUTH_* or JAW_*) ----

    async def hold_params(self, params: dict, refresh: float = 0.0):
        """Set face params and keep them: re-send every `refresh` s (0 = send once)."""
        await self.release_params()
        self._held = params
        await self.furhat.request_face_params(params)
        if refresh > 0:
            self._hold_task = asyncio.create_task(self._refresh_held(refresh))

    async def _refresh_held(self, interval: float):
        while True:
            await asyncio.sleep(interval)
            await self.furhat.request_face_params(self._held)

    async def release_params(self):
        """Stop refreshing held params and set them back to 0."""
        if self._hold_task:
            self._hold_task.cancel()
            self._hold_task = None
        if self._held:
            await self.furhat.request_face_params({p: 0.0 for p in self._held})
            self._held = {}

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
        if not self.led_allowed():
            return
        await self.furhat.request_led_set(color)  # "#rrggbb", "#000000" = off
