"""Background behaviour so the Golem never looks frozen: blinks, glances and a thinking mode."""
import asyncio
import logging
import random

from furhat_realtime_api import Events

from .expressions import perform
from .face import GolemFace
from .golem import Golem

log = logging.getLogger(__name__)

SPEAKING_EVENTS = [Events.response_speak_start, Events.response_speak_audio_buffer]


class IdleBehaviour:
    def __init__(self, face: GolemFace, golem: Golem):
        self.face = face
        self.golem = golem
        self.mode = "listening"   # "listening" | "speaking" | "thinking" | "quiet"
        # True while a filler line ("Hmm...") is spoken during the model wait:
        # that speech must not end the thinking mode.
        self.filler_speaking = False
        self._task = None

    def start(self):
        # Furhat reports its own speech, so the speak hooks are called automatically
        # (streamed audio sends no speak.start, only audio.buffer events while playing)
        self.face.furhat.add_handler(SPEAKING_EVENTS, self.on_robot_speak_start)
        self.face.furhat.add_handler(Events.response_speak_end, self.on_robot_speak_end)
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self.face.furhat.remove_handler(SPEAKING_EVENTS, self.on_robot_speak_start)
        self.face.furhat.remove_handler(Events.response_speak_end, self.on_robot_speak_end)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ---- Hooks (safe to call more than once) ----

    async def on_robot_speak_start(self, _event=None):
        self.face.speaking = True
        if self.mode == "thinking" and self.filler_speaking:
            return   # filler line while still waiting for the model: keep thinking
        self.mode = "speaking"
        # Stop "thinking" when the answer starts, unless the model already chose an expression
        if self.face.expression == "thinking" and self.golem.awake:
            await perform(self.face, "neutral")

    async def on_robot_speak_end(self, _event=None):
        self.face.speaking = False
        if self.mode == "thinking":
            return   # the filler line ended; the reply is still on its way
        self.mode = "listening"

    async def on_user_turn_start(self):
        """The visitor spoke: listen to them (and leave the quiet "serene" wait)."""
        self.mode = "listening"
        if self.golem.awake:
            if self.face.expression == "serene":
                await perform(self.face, "neutral")   # releases the held eyelids, gaze to user
            else:
                await self.face.gaze_user()

    async def on_waiting_for_model(self):
        self.mode = "thinking"
        if self.golem.awake:
            await perform(self.face, "thinking")

    async def on_reply_done(self):
        """The reply was spoken (or dropped). Leave thinking if speak events did not already."""
        self.filler_speaking = False
        if self.mode != "thinking":
            return
        self.mode = "listening"
        if self.face.expression == "thinking" and self.golem.awake:
            await perform(self.face, "neutral")

    async def on_quiet_wait(self):
        """Visitor stays silent after a prompt: wait calmly, no glances, until they speak or leave."""
        self.mode = "quiet"
        if self.golem.awake:
            await perform(self.face, "serene")

    # ---- Background loop ----

    async def _run(self):
        while True:
            await asyncio.sleep(random.uniform(3.0, 6.0))
            if not self.golem.awake:
                continue
            try:
                await self.face.blink()
                if self.mode == "listening" and random.random() < 0.3:
                    await self._glance()
                elif self.mode == "thinking":
                    # let the gaze wander upwards while "thinking"
                    await self.face.gaze_location(random.uniform(-0.5, 0.5), 0.4, 1.0)
            except Exception as e:  # keep the loop alive if one request fails
                log.warning("Idle behaviour error: %s", e)

    async def _glance(self):
        """Small gaze shift, then back to the user."""
        await self.face.gaze_location(random.uniform(-0.3, 0.3), random.uniform(-0.1, 0.2), 1.0)
        await asyncio.sleep(random.uniform(0.5, 1.2))
        if self.mode == "listening" and self.golem.awake:
            await self.face.gaze_user()
