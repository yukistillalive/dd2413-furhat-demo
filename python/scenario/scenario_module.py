"""Scenario Setting Module.

Owner: Hengxiao Liu. Turns a named behavior into the system prompt Gemini
uses to write a reply, and into the Furhat requests that change face, LED
and TTS voice. Furhat speaks that text itself.

The previous Gemini Live audio path (setup message, PCM playback, reverb)
is commented out in conversation/gemini_live_audio.py.

Two lab behaviors:
  default  normal short chat, original face and voice, LED off
  golem    calm solemn persona, male TTS voice, the same face, slow gold light
"""
import asyncio
import math
import re
from dataclasses import dataclass, replace

from furhat_realtime_api import AsyncFurhatClient


SPOKEN_RULES = (
    "Speak English. Everything you say is spoken aloud by a robot, so use one or "
    "two short sentences. Do not use markdown, lists, or emojis."
)

VISION_NOTE = (
    " You can see the user through the robot's camera. You may comment briefly "
    "on what is actually visible. Do not invent objects or clothing."
)


@dataclass(frozen=True)
class Scenario:
    name: str
    system_instruction: str
    opening_prompt: str
    # Spoken once when the user switches into this behavior mid-session.
    enter_prompt: str
    # Used only by the parked Gemini Live audio path (gemini_live_audio.py).
    gemini_voice: str
    reverb_mix: float = 0.0
    # None: keep the face that was active when the program started.
    face_id: str | None = None
    # Empty: turn the LED off. Otherwise loop (hex color, seconds).
    led_frames: tuple[tuple[str, float], ...] = ()
    furhat_gender: str | None = None
    microexpressions: bool = True
    head_sway: bool = False


def _srgb_to_linear(channel: int) -> float:
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def breathing_led(hex_color: str, period: float = 6.4, steps: int = 160, floor: float = 0.18) -> tuple[tuple[str, float], ...]:
    """One slow breath of a screen color, remapped for Furhat's LED ring.

    The wire format stays #RRGGBB. A light tint such as #D9B270 still has a
    large blue byte, and this ring's blue is much stronger than a monitor's,
    so those bytes come out cold white. The hue is kept, blue is reduced,
    and the brightest channel stays under 100 so the color does not wash out.
    Brightness follows a cosine in small steps, so the change stays continuous.
    """
    color = hex_color.lstrip("#")
    linear = [_srgb_to_linear(int(color[i:i + 2], 16)) for i in (0, 2, 4)]
    peak = max(linear) or 1.0
    # Blue is scaled down: on this hardware it otherwise pulls the tint cool.
    ratios = (linear[0] / peak, linear[1] / peak, linear[2] / peak * 0.35)
    led_peak = 88
    hold = period / steps
    frames = []
    for step in range(steps):
        wave = 0.5 - 0.5 * math.cos(2 * math.pi * step / steps)
        level = floor + (1.0 - floor) * wave
        rgb = tuple(max(0, min(127, round(led_peak * ratio * level))) for ratio in ratios)
        frames.append(("#{:02X}{:02X}{:02X}".format(*rgb), hold))
    return tuple(frames)


SCENARIOS = {
    "default": Scenario(
        name="default",
        system_instruction=SPOKEN_RULES + " You are a friendly robot looking for a nice little chat. Be warm and casual.",
        opening_prompt="Greet the user briefly in a warm, casual tone. Then wait.",
        enter_prompt="You are back to your normal friendly self. Say one short casual sentence so the user hears that, then wait.",
        gemini_voice="Aoede",
    ),
    "golem": Scenario(
        name="golem",
        system_instruction=(
            SPOKEN_RULES + " You are the Golem, an ancient constructed guardian. "
            "Speak slowly, calmly, and with a solemn, restrained tone. "
            "Do not joke, chatter, or sound like a casual assistant. "
            "Use few words. Answer directly, then wait."
        ),
        opening_prompt="Greet the user as the Golem: slow, calm, very few words, no jokes. Then wait.",
        enter_prompt="You have just taken on the Golem manner. Speak one short, calm sentence so the user hears the change. Then wait.",
        gemini_voice="Charon",
        reverb_mix=0.42,
        led_frames=breathing_led("#D9B270"),
        furhat_gender="male",
        microexpressions=False,
        head_sway=False,
    ),
}

# Same body as default, with the camera sentence baked in for --scenario vision.
SCENARIOS["vision"] = replace(
    SCENARIOS["default"],
    name="vision",
    system_instruction=SCENARIOS["default"].system_instruction + VISION_NOTE,
)

BEHAVIORS = ("default", "golem")

# The whole utterance must match. A longer sentence that merely mentions
# "golem" stays a normal chat turn.
_VOICE_COMMANDS = {
    "golem": "golem",
    "golem mode": "golem",
    "switch to golem": "golem",
    "be the golem": "golem",
    "default": "default",
    "default mode": "default",
    "normal": "default",
    "normal mode": "default",
    "switch to default": "default",
    "switch to normal": "default",
    "be normal": "default",
}


def match_behavior_command(text: str) -> str | None:
    """Return a behavior name when the recognised utterance is only a switch command."""
    cleaned = re.sub(r"[^a-z\s]", "", text.lower())
    cleaned = " ".join(cleaned.split())
    return _VOICE_COMMANDS.get(cleaned)


def behavior_command_phrases() -> list[str]:
    """Phrases passed to Furhat so the recogniser prefers these wordings."""
    return list(_VOICE_COMMANDS)


def _face_entries(face_list) -> list[dict]:
    entries = []
    for item in face_list or []:
        if isinstance(item, str):
            entries.append({"id": item, "label": item})
        elif isinstance(item, dict):
            face_id = item.get("face_id") or item.get("id") or item.get("name")
            label = " ".join(str(item.get(key) or "") for key in ("face_id", "id", "name", "character"))
            if face_id:
                entries.append({"id": str(face_id), "label": label or str(face_id)})
    return entries


class ScenarioManager:
    def __init__(self, scenario: Scenario | str = "default", golem_face_id: str | None = None):
        self.scenario = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
        self.golem_face_id = golem_face_id
        self.use_vision = False
        self._furhat: AsyncFurhatClient | None = None
        self._original_face_id: str | None = None
        self._original_voice_id: str | None = None
        self._led_generation = 0
        self._led_task: asyncio.Task | None = None
        self._led_wake = asyncio.Event()
        self._lock = asyncio.Lock()

    @classmethod
    def from_prompt(cls, system_instruction: str, opening_prompt: str = "Please greet the user."):
        """Free-text prompt on top of the default body. Hardware stays default."""
        base = SCENARIOS["default"]
        custom = replace(
            base,
            name="custom",
            system_instruction=SPOKEN_RULES + " " + system_instruction.strip(),
            opening_prompt=opening_prompt,
            enter_prompt=opening_prompt,
        )
        return cls(custom)

    def bind(self, furhat: AsyncFurhatClient):
        self._furhat = furhat

    async def apply(self):
        """Push this behavior's face, LED and TTS voice to Furhat."""
        if self._furhat is None:
            raise RuntimeError("ScenarioManager.bind() was not called")
        async with self._lock:
            await self._apply_voice()
            await self._apply_face()
            self._restart_led()
            print(f"[scenario] active behavior: {self.scenario.name} (Furhat TTS)")

    def instruction_text(self) -> str:
        """System prompt for a Gemini text reply. Furhat will speak the result."""
        instruction = self.scenario.system_instruction.strip()
        if self.use_vision and "robot's camera" not in instruction and "robot camera" not in instruction:
            instruction += VISION_NOTE
        if not instruction:
            raise ValueError("System instruction is empty")
        return instruction

    def cue_text(self, switched: bool = False) -> str:
        """Instruction that asks Gemini to write the next spoken line."""
        text = self.scenario.enter_prompt if switched else self.scenario.opening_prompt
        if not text or not text.strip():
            raise ValueError("Refusing to send an empty cue")
        return text

    async def switch(self, name: str) -> bool:
        """Change behavior. Returns False if that behavior is already active."""
        if name not in BEHAVIORS:
            raise ValueError(f"Unknown behavior {name!r}. Use one of {BEHAVIORS}.")
        if self.scenario.name == name:
            return False
        self.scenario = SCENARIOS[name]
        await self.apply()
        return True

    async def aclose(self):
        self._led_generation += 1
        self._led_wake.set()
        if self._led_task and not self._led_task.done():
            self._led_task.cancel()
            try:
                await self._led_task
            except asyncio.CancelledError:
                pass
        if self._furhat is not None:
            try:
                await self._furhat.request_led_set("#000000")
            except Exception:
                pass

    # ---- Furhat requests ----

    async def _apply_voice(self):
        try:
            if self._original_voice_id is None:
                status = await self._furhat.request_voice_status()
                self._original_voice_id = status.get("voice_id")
            if self.scenario.furhat_gender:
                await self._furhat.request_voice_config(
                    gender=self.scenario.furhat_gender,
                    language="en-US",
                )
                print(f"[scenario] Furhat TTS gender -> {self.scenario.furhat_gender}")
            elif self._original_voice_id:
                await self._furhat.request_voice_config(voice_id=self._original_voice_id)
                print(f"[scenario] Furhat TTS restored -> {self._original_voice_id}")
        except Exception as e:
            print(f"[scenario] voice config failed: {e}")

    async def _apply_face(self):
        try:
            status = await self._furhat.request_face_status()
        except Exception as e:
            print(f"[scenario] face status failed: {e}")
            return
        if self._original_face_id is None and status.get("face_id"):
            self._original_face_id = status.get("face_id")
            entries = _face_entries(status.get("face_list"))
            print("[scenario] available faces:", ", ".join(item["id"] for item in entries) or "(none)")
        target = self._resolve_face()
        event = {
            "type": "request.face.config",
            "visibility": True,
            "microexpressions": self.scenario.microexpressions,
            "blinking": True,
            "head_sway": self.scenario.head_sway,
        }
        if target:
            event["face_id"] = target
        try:
            await self._furhat.send_event(event)
            print(f"[scenario] face -> {target or self._original_face_id or '(unchanged)'}")
        except Exception as e:
            print(f"[scenario] face config failed: {e}")

    def _resolve_face(self) -> str | None:
        """Use only an explicitly configured face. Otherwise keep the startup face."""
        if self.scenario.name == "golem" and self.golem_face_id:
            return self.golem_face_id
        if self.scenario.face_id:
            return self.scenario.face_id
        return self._original_face_id

    def _restart_led(self):
        self._led_generation += 1
        generation = self._led_generation
        self._led_wake.set()
        frames = self.scenario.led_frames
        self._led_task = asyncio.create_task(self._led_loop(generation, frames))

    async def _led_loop(self, generation: int, frames: tuple[tuple[str, float], ...]):
        try:
            if not frames:
                await self._furhat.request_led_set("#000000")
                print("[scenario] LED off")
                return
            index = 0
            print("[scenario] LED pattern started")
            while self._led_generation == generation:
                color, hold = frames[index % len(frames)]
                await self._furhat.request_led_set(color)
                self._led_wake.clear()
                try:
                    await asyncio.wait_for(self._led_wake.wait(), timeout=hold)
                except asyncio.TimeoutError:
                    pass
                index += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[scenario] LED update failed: {e}")
