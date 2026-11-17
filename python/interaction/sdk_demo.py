"""Watch one visitor on the Furhat SDK virtual robot, in real time.

From the python/ folder, with the Furhat SDK running locally:
    python -m interaction.sdk_demo
    python -m interaction.sdk_demo --host 127.0.0.1 --scripted   # no Gemini, fixed replies

Scripted visitor: nobody -> stands far away (2.5 m) -> walks to 1.0 m and stops ->
says a few lines -> walks away and disappears.

Real code: AsyncFurhatClient (the SDK virtual robot), ScenarioManager (golem),
GolemGUI, PerceptionTracker, InteractionController, RealtimeConversation (Gemini
replies, emotion on the face, Furhat TTS). Fake: the user list (the SDK has no
camera/person), and the visitor's speech (typed lines instead of the microphone).
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from furhat_realtime_api import AsyncFurhatClient

from conversation import RealtimeConversation
from gui import GolemGUI
from interaction.controller import InteractionController
from scenario import SCENARIOS, ScenarioManager
from vision.perception_state import PerceptionConfig, PerceptionTracker

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

VISITOR_LINES = [
    "Hello. Who are you?",
    "What are you guarding here?",
    "Thank you. Goodbye!",
]
SCRIPTED_REPLIES = [
    {"say": "I am the Golem... shaped from river clay, long ago.", "emotion": "solemn"},
    {"say": "I guard the old words... and those who seek them.", "emotion": "serene"},
    {"say": "Go well, traveller.", "emotion": "warm"},
]
T0 = time.monotonic()


def log(text):
    print(f"{time.monotonic() - T0:6.1f}s  {text}", flush=True)


def trace_robot(furhat):
    """Print the robot requests worth seeing; everything still goes to the SDK."""
    def wrap(name, fmt):
        original = getattr(furhat, name)

        async def traced(*a, **kw):
            log(f"   robot  {fmt(*a, **kw)}")
            return await original(*a, **kw)
        setattr(furhat, name, traced)

    wrap("request_speak_text", lambda text, *a, **kw: f'SAY "{text}"')
    wrap("request_gesture_start", lambda name, *a, **kw: f"gesture {name}")
    wrap("request_attend_user", lambda *a, **kw: "gaze -> user")
    wrap("request_attend_location", lambda x, y, z, *a, **kw: f"gaze -> ({x:+.1f}, {y:+.1f}, {z:.1f})")
    breathing = {c.upper() for c, _ in SCENARIOS["golem"].led_frames}
    last = {"c": None}
    original_led = furhat.request_led_set

    async def led(color, *a, **kw):
        shown = "breathing gold (scenario)" if color.upper() in breathing else color
        if shown != last["c"]:
            log(f"   robot  LED {shown}")
        last["c"] = shown
        return await original_led(color, *a, **kw)
    furhat.request_led_set = led


class Visitor:
    """Where the fake person is. distance None = not seen by the robot."""

    def __init__(self):
        self.distance = None
        self.user_id = "user-1"
        self.lines = list(VISITOR_LINES)
        self.done_talking = asyncio.Event()

    def users(self):
        if self.distance is None:
            return {"users": []}
        return {"users": [{"id": self.user_id, "distance": round(self.distance, 2)}]}

    async def walk(self, d0, d1, seconds):
        steps = int(seconds / 0.25)
        for i in range(1, steps + 1):
            self.distance = d0 + (d1 - d0) * i / steps
            await asyncio.sleep(0.25)

    async def script(self):
        log("visitor: nobody there")
        await asyncio.sleep(4)
        log("visitor: appears far away, 2.5 m, and waits")
        self.distance = 2.5
        await asyncio.sleep(14)
        log("visitor: walks closer 2.5 m -> 1.0 m")
        await self.walk(2.5, 1.0, 3)
        log("visitor: stops at 1.0 m")
        await self.done_talking.wait()
        await asyncio.sleep(2)
        log("visitor: walks away 1.0 m -> 2.5 m")
        await self.walk(1.0, 2.5, 3)
        log("visitor: out of sight (on the real robot the last distance stays frozen ~2 s first)")
        self.distance = None


class DemoConversation(RealtimeConversation):
    """Real conversation; only the microphone is replaced by the visitor's lines."""

    def __init__(self, *a, visitor: Visitor, scripted: bool, **kw):
        super().__init__(*a, **kw)
        self.visitor = visitor
        self.scripted = list(SCRIPTED_REPLIES) if scripted else None

    async def _listen_once(self) -> str:
        await asyncio.sleep(1.5)
        if self.visitor.lines:
            line = self.visitor.lines.pop(0)
            log(f"visitor says: \"{line}\"")
            return line
        self.visitor.done_talking.set()
        return ""

    async def _generate(self, contents, remember, use_json=True):
        if self.scripted is None:
            return await super()._generate(contents, remember, use_json)
        return json.dumps(self.scripted.pop(0) if self.scripted else {"say": "", "emotion": "neutral"})


async def poll_users(visitor: Visitor, tracker: PerceptionTracker):
    while True:   # VisionModule polls every 0.5 s
        tracker.update(visitor.users())
        await asyncio.sleep(0.5)


async def monitor(tracker: PerceptionTracker, controller: InteractionController):
    zone = None
    while True:   # VisionModule._monitor period
        state = tracker.tick()
        state["events"] = list(tracker.events)
        if state.get("zone") != zone:
            zone = state.get("zone")
            dist = state.get("smoothed_distance_m")
            log(f"perception: zone={zone}" + (f" ({dist:.2f} m)" if dist is not None else ""))
        await controller.on_state(state)
        await asyncio.sleep(0.2)


async def main(args):
    furhat = AsyncFurhatClient(args.host)
    await furhat.connect()
    trace_robot(furhat)

    scenario = ScenarioManager("golem")
    scenario.use_vision = False   # no camera in the SDK: the opening line is the fixed fallback
    scenario.bind(furhat)
    gui = GolemGUI(furhat)
    gui.face.led_allowed = lambda: not scenario.owns_led()
    scenario.builtin_blinking = False
    await scenario.apply()
    await gui.start(panel_port=None, awake=False)

    visitor = Visitor()
    conversation = DemoConversation(furhat, scenario, gui=gui, wait_for_session=True,
                                    visitor=visitor, scripted=args.scripted)
    controller = InteractionController(furhat, gui, conversation, scenario,
                                       log=lambda text: log(text))
    tracker = PerceptionTracker(PerceptionConfig(enter_distance_m=1.2, exit_distance_m=1.5,
                                                 distance_unit="m"))
    await controller.start()

    tasks = [asyncio.create_task(c) for c in
             (conversation.run(), poll_users(visitor, tracker), monitor(tracker, controller))]
    try:
        await visitor.script()
        # person_left (~2 s) + same-visitor grace (5 s) + sleep animation
        await asyncio.sleep(10)
        log(f"done, final state {controller.state}")
    finally:
        await conversation.stop()
        await controller.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await scenario.aclose()
        await gui.stop()
        await furhat.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="Furhat SDK / robot address")
    parser.add_argument("--scripted", action="store_true", help="fixed replies instead of Gemini")
    asyncio.run(main(parser.parse_args()))
