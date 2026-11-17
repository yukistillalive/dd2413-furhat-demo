"""Replay Furhat user data into the InteractionController without a robot.

From the python/ folder:
    python -m interaction.replay                       # recording + all synthetic segments
    python -m interaction.replay --only walk_in        # one segment
    python -m interaction.replay --file other.jsonl    # another recording
    python -m interaction.replay --check               # exit 1 if a segment's transitions are wrong

What is real here: PerceptionTracker (vision/), InteractionController,
GolemGUI face/golem code and ScenarioManager LED logic. What is fake: the
Furhat client (it prints what the robot would do), the conversation (it
prints start/end), and time (a simulated clock, so a 40 s segment runs at once).
"""
import argparse
import asyncio
import json
from pathlib import Path

from furhat_realtime_api import Events

from gui import GolemGUI
from interaction.controller import AWAKE, DORMANT, SUMMONING, InteractionController
from scenario import SCENARIOS, ScenarioManager
from vision.perception_state import PerceptionConfig, PerceptionTracker

RECORDING = Path(__file__).resolve().parent.parent / "users_recording.jsonl"
POLL_S = 0.5     # VisionModule.poll_interval
MONITOR_S = 0.2  # VisionModule._monitor period
BREATHING = {color for color, _ in SCENARIOS["golem"].led_frames}


class SimClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeFurhat:
    """Records requests and prints the ones you would see on the robot."""

    def __init__(self, clock, out):
        self.clock = clock
        self.out = out
        self.calls = []
        self.handlers = {}
        self._breathing = False

    def _say(self, text):
        self.out(f"  {self.clock():6.2f}s   robot: {text}")

    def _record(self, call, **kw):
        self.calls.append((self.clock(), call, kw))

    def add_handler(self, event_type, handler):
        for t in event_type if isinstance(event_type, list) else [event_type]:
            self.handlers.setdefault(t, []).append(handler)

    def remove_handler(self, event_type, handler):
        for t in event_type if isinstance(event_type, list) else [event_type]:
            if handler in self.handlers.get(t, []):
                self.handlers[t].remove(handler)

    async def _dispatch(self, event_type, data=None):
        for handler in list(self.handlers.get(event_type, [])):
            await handler(data or {})

    async def request_speak_text(self, text, abort=False, wait=False, monitor_words=False):
        self._record("speak", text=text)
        self._say(f'SAY "{text}"')
        if wait:
            await self._dispatch(Events.response_speak_start)
            await self._dispatch(Events.response_speak_end)

    async def request_speak_stop(self):
        self._record("speak_stop")

    async def request_listen_stop(self):
        self._record("listen_stop")

    async def request_gesture_start(self, name, intensity=1.0, duration=1.0, wait=False):
        self._record("gesture", name=name)
        self._say(f"gesture {name}")

    async def request_face_params(self, params):
        self._record("face_params", params=params)

    async def request_attend_user(self, user_id="closest"):
        self._record("attend_user")
        self._say("gaze -> user")

    async def request_attend_location(self, x, y, z):
        self._record("attend_location", x=x, y=y, z=z)
        self._say(f"gaze -> ({x:+.1f}, {y:+.1f}, {z:.1f})")

    async def request_led_set(self, color):
        self._record("led", color=color)
        if color.upper() in BREATHING:
            if not self._breathing:
                self._say("LED breathing gold (scenario pattern)")
            self._breathing = True
            return
        self._breathing = False
        self._say(f"LED {color}")

    async def request_voice_status(self):
        return {"voice_id": "fake-voice"}

    async def request_voice_config(self, **kw):
        self._record("voice_config", **kw)

    async def request_face_status(self):
        return {"face_id": "fake-face", "face_list": ["fake-face"]}

    async def request_face_config(self, **kw):
        self._record("face_config", **kw)

    async def send_event(self, event):
        self._record("event", **event)


class FakeConversation:
    def __init__(self, clock, out):
        self.clock = clock
        self.out = out
        self.active = False
        self.starts = []   # resume flags, one per start_session
        self.ends = 0
        self.latest_frame = None

    def start_session(self, opening_cue, fallback_line, resume=False):
        self.active = True
        self.starts.append(resume)
        what = "resume, no greeting" if resume else "opening line (frame + clothing cue), then Q&A"
        self.out(f"  {self.clock():6.2f}s   conversation: START ({what})")

    async def end_session(self):
        if self.active:
            self.ends += 1
            self.out(f"  {self.clock():6.2f}s   conversation: END")
        self.active = False


# ---- data ----

def load_recording(path: Path):
    samples = []
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            samples.append((float(row["t"]), row["data"]))
    return samples


def _users(user_id, distance):
    if distance is None:
        return {"users": []}
    return {"users": [{"id": user_id, "distance": distance}]}


def _segment(*parts):
    """parts: (start, end, user_id, distance or callable(t) or None). Sampled every POLL_S."""
    samples = []
    t = 0.0
    end = max(p[1] for p in parts)
    while t <= end + 1e-9:
        data = {"users": []}
        for start, stop, user_id, distance in parts:
            if start <= t < stop:
                d = distance(t) if callable(distance) else distance
                data = _users(user_id, None if d is None else round(d, 3))
        samples.append((round(t, 2), data))
        t += POLL_S
    return samples


def _walk(t0, t1, d0, d1):
    return lambda t: d0 + (d1 - d0) * min(1.0, max(0.0, (t - t0) / (t1 - t0)))


def synthetic_segments():
    return {
        # far 2.5 m long enough for both summons, walk to 1.0 m, stay, frozen distance, gone
        "approach": _segment(
            (2.0, 17.0, "user-7", 2.5),
            (17.0, 20.0, "user-7", _walk(17.0, 20.0, 2.5, 1.0)),
            (20.0, 28.0, "user-7", 1.0),        # last 2.5 s = frozen value after leaving
            (28.0, 40.0, None, None),
        ),
        # straight in: 2.2 -> 1.6 -> 1.1 -> 1.0, never 1.5 s in the far zone
        "walk_in": _segment(
            (2.0, 2.5, "user-9", 2.2), (2.5, 3.0, "user-9", 1.6), (3.0, 3.5, "user-9", 1.1),
            (3.5, 12.0, "user-9", 1.0),
            (12.0, 20.0, None, None),
        ),
        # leaves, id changes, back within 5 s -> same visitor; later a new visitor
        "same_visitor": _segment(
            (2.0, 10.0, "user-1", 1.0),
            (10.0, 11.0, None, None),           # 1 s gap
            (11.0, 18.0, "user-3", 1.0),        # same person, new id
            (18.0, 30.0, None, None),           # really gone (> 5 s)
            (30.0, 38.0, "user-4", 1.0),        # new visitor
            (38.0, 48.0, None, None),
        ),
        # perception errors while AWAKE must not change the state
        "stale": _segment((2.0, 30.0, "user-5", 1.0), (30.0, 40.0, None, None)),
    }


# expected (from, to) transitions, summon lines and opening greetings per segment
EXPECTED = {
    # user-0 -> user-2 id change is inside the 5 s grace: SUMMONING continues, no flicker
    "recording": dict(transitions=[(DORMANT, SUMMONING), (SUMMONING, DORMANT)], summons=2, greetings=0),
    "approach": dict(transitions=[(DORMANT, SUMMONING), (SUMMONING, AWAKE), (AWAKE, DORMANT)],
                     summons=2, greetings=1),
    "walk_in": dict(transitions=[(DORMANT, AWAKE), (AWAKE, DORMANT)], summons=0, greetings=1),
    "same_visitor": dict(transitions=[(DORMANT, AWAKE), (AWAKE, DORMANT), (DORMANT, AWAKE), (AWAKE, DORMANT)],
                         summons=0, greetings=2, resumes=0, sleeps=3),
    "stale": dict(transitions=[(DORMANT, AWAKE), (AWAKE, DORMANT)], summons=0, greetings=1),
}


# ---- replay ----

async def replay(name, samples, enter=1.2, exit_=1.5, out=print, error_window=None):
    """Feed samples through tracker + controller. Returns a result dict."""
    clock = SimClock()
    furhat = FakeFurhat(clock, out)
    conversation = FakeConversation(clock, out)
    scenario = ScenarioManager("golem")
    scenario.bind(furhat)
    scenario.builtin_blinking = False
    gui = GolemGUI(furhat)
    gui.face.led_allowed = lambda: not scenario.owns_led()
    log = lambda text: out(f"  {clock():6.2f}s {text}")
    controller = InteractionController(furhat, gui, conversation, scenario, clock=clock, log=log)
    tracker = PerceptionTracker(PerceptionConfig(enter_distance_m=enter, exit_distance_m=exit_,
                                                 distance_unit="m"), clock=clock)

    out(f"\n=== {name} (enter {enter} m, exit {exit_} m) ===")
    await scenario.apply()
    await controller.start()
    await _settle(controller)

    index, end = 0, samples[-1][0] + 1.0
    step = 0
    while step * MONITOR_S <= end:
        clock.now = round(step * MONITOR_S, 2)
        while index < len(samples) and samples[index][0] <= clock.now + 1e-9:
            data = samples[index][1]
            if error_window and error_window[0] <= samples[index][0] < error_window[1]:
                tracker.mark_error("simulated request failure")
            else:
                before = tracker.snapshot()
                tracker.update(data)
                after = tracker.snapshot()
                if before["zone"] != after["zone"] and after["zone"] in ("far", "interaction"):
                    out(f"  {clock():6.2f}s perception: {after['target_user_id']} zone={after['zone']}"
                        f" ({after['smoothed_distance_m']:.2f} m)")
            index += 1
        state = tracker.tick()
        state["events"] = list(tracker.events)
        await controller.on_state(state)
        await _settle(controller)
        step += 1

    await controller.stop()
    await scenario.aclose()
    spoken = [kw["text"] for _, call, kw in furhat.calls if call == "speak"]
    return dict(
        transitions=[(a, b) for _, a, b, _ in controller.transitions],
        summons=len(spoken),
        greetings=conversation.starts.count(False),
        resumes=conversation.starts.count(True),
        final=controller.state,
        sleeps=sum(1 for _, call, kw in furhat.calls if call == "gesture" and kw.get("name") == "CloseEyes"),
    )


async def _settle(controller):
    for _ in range(3):
        task = controller._task
        if task and not task.done():
            await asyncio.wait({task}, timeout=1.0)
        await asyncio.sleep(0)


def check(name, result) -> list[str]:
    problems = []
    for key, want in EXPECTED.get(name, {}).items():
        if result.get(key) != want:
            problems.append(f"{name}: {key} = {result.get(key)}, expected {want}")
    if result["final"] != DORMANT:
        problems.append(f"{name}: ended in {result['final']}, expected DORMANT")
    return problems


async def run_all(only=None, file=RECORDING, enter=1.2, exit_=1.5, out=print):
    segments = {}
    if file and Path(file).exists():
        segments["recording"] = (load_recording(Path(file)), None)
    for name, samples in synthetic_segments().items():
        segments[name] = (samples, (12.0, 18.0) if name == "stale" else None)
    results = {}
    for name, (samples, error_window) in segments.items():
        if only and name != only:
            continue
        results[name] = await replay(name, samples, enter, exit_, out, error_window)
        r = results[name]
        out(f"  -> transitions: {' | '.join(f'{a}->{b}' for a, b in r['transitions']) or 'none'}")
        out(f"  -> summon lines: {r['summons']}, greetings: {r['greetings']}, resumes: {r['resumes']}")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=str(RECORDING), help="users_recording.jsonl to replay")
    parser.add_argument("--only", default=None, help="recording, approach, walk_in, same_visitor or stale")
    parser.add_argument("--enter-distance", type=float, default=1.2)
    parser.add_argument("--exit-distance", type=float, default=1.5)
    parser.add_argument("--check", action="store_true", help="compare with the expected transitions")
    args = parser.parse_args()
    results = asyncio.run(run_all(args.only, args.file, args.enter_distance, args.exit_distance))
    if args.check:
        problems = [p for name, r in results.items() for p in check(name, r)]
        print("\nCHECK:", "all segments as expected" if not problems else "\n  " + "\n  ".join(problems))
        raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
