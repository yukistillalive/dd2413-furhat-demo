"""InteractionController: the "decide" step between vision and behaviour.

Perception (vision/) says WHO is there and HOW FAR (zone "far" / "interaction",
events person_entered / person_left). This class turns that into one of three
states and tells the behaviour layers what to do:

    DORMANT    nobody there. Eyes closed, LED dim, conversation silent.
    SUMMONING  someone waited >= 1.5 s in the far zone. Half-awake ("serene"),
               one slow line asking them to come closer (max 2, 10 s apart).
    AWAKE      someone is in the interaction zone. awaken(), "warm", an opening
               line about one visible clothing item, then normal conversation.

    DORMANT --far >= 1.5 s--> SUMMONING --interaction zone--> AWAKE
    DORMANT --interaction zone (no far wait)----------------> AWAKE
    SUMMONING / AWAKE --person_left--> DORMANT

Same-visitor rule: if someone is confirmed within 5 s after person_left, they
are the same visitor (the robot's user id can change when a person moves).
They get back the state they had, without a new summon line or greeting.

on_state() only decides; it never waits for the robot. Robot actions run in
one background task at a time, and a newer transition cancels an older one.
"""
import asyncio
from dataclasses import dataclass
import time

from gui.expressions import perform

DORMANT, SUMMONING, AWAKE = "DORMANT", "SUMMONING", "AWAKE"

SUMMON_LINES = (
    "Traveller... come closer. The Golem stirs.",
    "Do not be afraid... Step nearer, and I shall wake.",
)
OPENING_CUE = (
    "A visitor has just stepped close to you, and you are waking up. "
    "Greet them in one or two short sentences, in your own manner. "
    "Mention exactly one clothing item you can clearly see in the image, "
    "neutrally, by its colour or type only, for example 'your blue jacket'. "
    "Never comment on their body, face, looks, age, gender, or ethnicity. "
    "If no clothing item is clearly visible, do not mention clothing. "
    "End by inviting them to ask you something."
)
FALLBACK_GREETING = "I am awake... Welcome, traveller. Ask, and I shall answer."
DORMANT_LED = "#140802"   # very dim ember while asleep or half-awake


@dataclass
class InteractionConfig:
    far_dwell_s: float = 1.5      # far-zone time before SUMMONING
    summon_repeat_s: float = 10.0  # gap between summon lines
    max_summons: int = 2
    same_visitor_s: float = 5.0    # reappearing within this after person_left = same visitor


@dataclass
class Visitor:
    summons: int = 0
    last_summon_at: float | None = None
    greeted: bool = False


class InteractionController:
    def __init__(self, furhat, gui, conversation, scenario, config: InteractionConfig | None = None,
                 clock=time.monotonic, log=print):
        self.furhat = furhat
        self.gui = gui
        self.conversation = conversation
        self.scenario = scenario
        self.config = config or InteractionConfig()
        self.clock = clock
        self.log = log
        self.state = DORMANT
        self.transitions: list[tuple[float, str, str, str]] = []   # (time, from, to, reason)
        self.visitor: Visitor | None = None
        self._last_seq = 0
        self._far_since: float | None = None
        self._left_at: float | None = None
        self._left_visitor: Visitor | None = None
        self._left_state = DORMANT
        self._task: asyncio.Task | None = None

    # ---- lifecycle ----

    async def start(self):
        """Put the robot in DORMANT. Call once before vision starts."""
        self.log("[interaction] DORMANT (start)")
        await self._act_dormant()

    async def stop(self):
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            await asyncio.wait({task})

    # ---- perception input (VisionModule.add_state_listener) ----

    async def on_state(self, state: dict):
        try:
            self.decide(state)
        except Exception as e:   # never let a bad snapshot kill the listener
            self.log(f"[interaction] decide failed, keeping {self.state}: {e}")

    def decide(self, state: dict):
        now = self.clock()
        self._handle_events(state.get("events") or [], now)
        if state.get("status") != "ok":
            # stale or error: keep the current state, just do not start new timers
            self._far_since = None
            return
        visitor = self.visitor
        zone = state.get("zone")
        if visitor is None:
            self._far_since = None
            return
        if not state.get("target_visible") or zone not in ("far", "interaction"):
            return

        if self.state == DORMANT:
            if visitor.greeted:
                self._go(AWAKE, "same visitor back", now)
            elif zone == "interaction":
                # Reached before 1.5 s in the far zone, otherwise we would be SUMMONING.
                self._go(AWAKE, "walked straight in", now)
            elif visitor.summons > 0:
                self._go(SUMMONING, "same visitor back", now)
            else:  # far: wait for the dwell time
                if self._far_since is None:
                    self._far_since = now
                if now - self._far_since >= self.config.far_dwell_s:
                    self._go(SUMMONING, f"far for {now - self._far_since:.1f} s", now)
        elif self.state == SUMMONING:
            if zone == "interaction":
                self._go(AWAKE, "came close", now)
            elif (visitor.summons < self.config.max_summons and visitor.last_summon_at is not None
                  and now - visitor.last_summon_at >= self.config.summon_repeat_s):
                index = visitor.summons
                visitor.summons += 1
                visitor.last_summon_at = now
                self.log(f"[interaction] summon {index + 1}/{self.config.max_summons}")
                self._launch(self._act_summon_line, index)
        # AWAKE: only person_left ends it (handled in _handle_events)

    def _handle_events(self, events: list, now: float):
        if events and max(e.get("sequence", 0) for e in events) < self._last_seq:
            self._last_seq = 0   # VisionModule was restarted and numbering began again
        for event in events:
            seq = event.get("sequence", 0)
            if seq <= self._last_seq:
                continue
            self._last_seq = seq
            name = event.get("name")
            if name == "person_entered":
                self._on_person_entered(now, event.get("user_id"))
            elif name == "person_left":
                self._on_person_left(now, event.get("user_id"))

    def _on_person_entered(self, now: float, user_id):
        self._far_since = None
        if self._left_visitor is not None and now - self._left_at <= self.config.same_visitor_s:
            self.visitor = self._left_visitor
            self.log(f"[interaction] {user_id} within {now - self._left_at:.1f} s of leaving: same visitor"
                     f" (was {self._left_state})")
        else:
            self.visitor = Visitor()
            self.log(f"[interaction] new visitor {user_id}")
        self._left_visitor = None

    def _on_person_left(self, now: float, user_id):
        self._far_since = None
        if self.visitor is None:
            return
        self._left_at = now
        self._left_visitor = self.visitor
        self._left_state = self.state
        self.visitor = None
        if self.state != DORMANT:
            self._go(DORMANT, f"person_left {user_id}", now)

    # ---- transitions ----

    def _go(self, new_state: str, reason: str, now: float):
        old = self.state
        if old == new_state:
            return
        self.state = new_state
        self._far_since = None
        self.transitions.append((now, old, new_state, reason))
        self.log(f"[interaction] {old} -> {new_state} ({reason})")
        visitor = self.visitor
        if new_state == DORMANT:
            self._launch(self._act_dormant)
        elif new_state == SUMMONING:
            speak = visitor.summons == 0
            if speak:
                visitor.summons = 1
                visitor.last_summon_at = now
            self._launch(self._act_summoning, speak)
        elif new_state == AWAKE:
            resume = visitor.greeted
            visitor.greeted = True
            self._launch(self._act_awake, resume)

    def _launch(self, action, *args):
        """Run one robot action after cancelling the previous one."""
        previous = self._task

        async def runner():
            if previous and not previous.done():
                previous.cancel()
                await asyncio.wait({previous})
            try:
                await action(*args)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log(f"[interaction] action {action.__name__} failed: {e}")

        self._task = asyncio.create_task(runner())

    # ---- behaviour (what each state looks like on the robot) ----

    async def _act_dormant(self):
        await self.conversation.end_session()
        await self._quietly(self.furhat.request_speak_stop())
        await self.gui.golem.sleep()
        await self.scenario.hold_led(DORMANT_LED)

    async def _act_summoning(self, speak: bool):
        # Half-awake: not awaken(). Eyes open just enough for the "serene" half-closed lids.
        await self.gui.face.open_eyes()
        await perform(self.gui.face, "serene")
        if speak:
            await self._act_summon_line(0)

    async def _act_summon_line(self, index: int):
        line = SUMMON_LINES[index % len(SUMMON_LINES)]
        self.log(f"Robot (summon): {line}")
        await self.furhat.request_speak_text(line, abort=True, wait=True)

    async def _act_awake(self, resume: bool):
        await self._quietly(self.furhat.request_speak_stop())
        await self.scenario.release_led()
        await self.gui.golem.awaken()
        await self.gui.face.gaze_user()
        await perform(self.gui.face, "warm")
        self.conversation.start_session(OPENING_CUE, FALLBACK_GREETING, resume=resume)

    async def _quietly(self, coro):
        try:
            await coro
        except Exception as e:
            self.log(f"[interaction] robot request failed: {e}")
