"""Build a test database with synthetic visits, written through the real ConversationLog.

    cd python
    python -m store.make_test_db                       # -> store/testdata/test_conversations.db
    python -m store.make_test_db --out /tmp/t.db --force

All data is invented (fake visitors, fake sentences, fake distances). It is deterministic,
so the same command always gives the same file. Use it to try the example queries in
README.md, or to point other tools at a database with realistic content.

Visits it contains:
    1  approach    far -> summoned twice -> comes close -> 3 turns (one slow, with filler) -> silence prompt -> leaves
    2  walk_in     walks straight in -> 2 turns, one Gemini error, one skipped utterance -> leaves
    3  passed_by   detected for a few seconds, leaves before the Golem reacts
    4  same_visitor  leaves, tracking lost, comes back inside the 5 s grace window: still one visit
    5  mode_switch   golem -> normal mode by voice, then leaves; perception goes stale during the visit
"""
import argparse
import asyncio
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from store import conversation_log
from store.conversation_log import ConversationLog

DEFAULT_OUT = Path(__file__).parent / "testdata" / "test_conversations.db"
START = datetime(2026, 11, 17, 10, 0, 0, tzinfo=timezone.utc)


class Timeline:
    """One fake clock for both the monotonic clock and the UTC timestamps."""

    def __init__(self):
        self.t = 0.0

    def mono(self) -> float:
        return self.t

    def wall(self) -> str:
        return (START + timedelta(seconds=self.t)).isoformat(timespec="milliseconds")

    def advance(self, seconds: float):
        self.t += seconds


def snapshot(tl, rng, zone, distance, *, user="user-7", status="ok", tracking="tracking", visible=True,
             count=1):
    """A perception snapshot like VisionModule.latest_state, with a little noise on the distance."""
    d = None if distance is None else round(distance + rng.uniform(-0.03, 0.03), 2)
    return dict(status=status, tracking=tracking, user_count=count, target_user_id=user,
                target_visible=visible, distance_raw=d, distance_m=d, smoothed_distance_m=d,
                zone=zone, data_age_s=round(rng.uniform(0.0, 0.4), 2))


def zone_for(distance):
    return "interaction" if distance <= 1.2 else "far"


async def walk(log, tl, rng, robot_state, start, end, seconds, *, user="user-7", step=0.5):
    """Perception samples for a person moving from `start` to `end` metres over `seconds`."""
    n = max(1, int(seconds / step))
    for i in range(n):
        d = start + (end - start) * (i + 1) / n
        log.log_perception(snapshot(tl, rng, zone_for(d), d, user=user), robot_state())
        tl.advance(step)


async def say(log, tl, mode, user_text, robot_text, *, think_s=0.9, emotion="warm", raw=None,
              extra_robot=None):
    """One visitor utterance followed by the robot's reply, with the measured response time."""
    await log.log_message("user", user_text, source="asr", mode=mode)
    tl.advance(think_s)
    await log.log_message("robot", robot_text, source="reply", mode=mode, emotion=emotion,
                          latency_ms=round(think_s * 1000),
                          raw_text=raw or f'{{"say": "{robot_text}", "emotion": "{emotion}"}}')
    tl.advance(3.0)


async def build(path: Path):
    tl = Timeline()
    rng = random.Random(7)
    conversation_log._now = tl.wall          # fake UTC timestamps (module-level helper)
    log = ConversationLog(str(path), clock=tl.mono)
    await log.start_session("gemini-fake-model", "golem", "You are the Golem. (synthetic test data)")
    state = {"now": "DORMANT"}

    def go(new, reason):
        """Like InteractionController._go: log the transition; going to sleep also ends the visit."""
        log.log_transition(state["now"], new, reason)
        state["now"] = new
        if new == "DORMANT":
            log.end_visit("left")

    robot_state = lambda: state["now"]

    # ---- 1. approach: far, summoned, comes close, conversation, silence, leaves ----
    tl.advance(30)
    log.start_visit("user-7", "golem")
    await walk(log, tl, rng, robot_state, 3.0, 2.5, 1.5)
    go("SUMMONING", "far for 1.6 s")
    await log.log_message("robot", "Traveller... come closer. The Golem stirs.", source="summon", mode="golem")
    await walk(log, tl, rng, robot_state, 2.5, 2.4, 10)
    await log.log_message("robot", "Do not be afraid... Step nearer, and I shall wake.", source="summon", mode="golem")
    await walk(log, tl, rng, robot_state, 2.4, 1.0, 4)
    go("AWAKE", "came close")
    await log.log_event("session", "start")
    await log.log_message("robot", "I am awake... welcome, traveller in the blue jacket. Ask, and I shall answer.",
                          source="opening", mode="golem", emotion="warm", latency_ms=None,
                          raw_text='{"say": "I am awake... welcome, traveller in the blue jacket. Ask, and I shall answer.", "emotion": "warm"}')
    tl.advance(6)
    await say(log, tl, "golem", "Who made you?", "Clay and a word, long ago.", think_s=0.8, emotion="solemn")
    await say(log, tl, "golem", "How old are you really?", "Older than your oldest story.", think_s=1.1, emotion="serene")
    # a slow reply: the filler line is spoken after 3 s
    await log.log_message("user", "Can you tell me what you guard here?", source="asr", mode="golem")
    tl.advance(3.0)
    await log.log_event("filler", "Hmm... let me consider.")
    await log.log_message("robot", "Hmm... let me consider.", source="filler", mode="golem")
    tl.advance(2.5)
    await log.log_message("robot", "The door behind you, and the words behind the door.", source="reply",
                          mode="golem", emotion="thinking", latency_ms=5600,
                          raw_text='{"say": "The door behind you, and the words behind the door.", "emotion": "thinking"}')
    tl.advance(4)
    await log.log_event("silence", "prompt")                  # visitor silent for ~10 s
    await log.log_message("robot", "Do you have a question for me, traveller?", source="silence_prompt", mode="golem")
    await walk(log, tl, rng, robot_state, 1.0, 1.0, 6)
    # the visitor walks away: the last distance stays frozen for a while, then they are lost
    await walk(log, tl, rng, robot_state, 1.0, 1.0, 2)
    for _ in range(6):
        log.log_perception(snapshot(tl, rng, "unknown", None, tracking="temporarily_lost", visible=False), robot_state())
        tl.advance(1)
    go("DORMANT", "nobody back 5 s after person_left")
    tl.advance(0.2)

    # ---- 2. walk_in: straight to the robot, one Gemini error, one skipped utterance ----
    tl.advance(40)
    log.start_visit("user-9", "golem")
    await walk(log, tl, rng, robot_state, 2.2, 1.0, 1.5, user="user-9")
    go("AWAKE", "walked straight in")
    await log.log_event("session", "start")
    await log.log_message("robot", "I am awake... Welcome, traveller. Ask, and I shall answer.", source="opening",
                          mode="golem", emotion="warm")
    tl.advance(5)
    await say(log, tl, "golem", "Hello, are you real?", "As real as stone and patience.", think_s=0.7, emotion="solemn")
    await log.log_message("user", "um yeah", source="asr", mode="golem")
    await log.log_event("skipped", "um yeah")                  # not a complete question: no Gemini call
    tl.advance(4)
    await log.log_message("user", "What do you think of people?", source="asr", mode="golem")
    await log.log_event("gemini_error", "503 UNAVAILABLE: model overloaded")
    tl.advance(2.0)
    await log.log_message("robot", "Sorry, I didn't catch that. Could you say it again?", source="reply",
                          mode="golem", emotion="neutral", latency_ms=2000)
    tl.advance(4)
    await walk(log, tl, rng, robot_state, 1.0, 1.0, 3, user="user-9")
    for _ in range(3):
        log.log_perception(snapshot(tl, rng, "unknown", None, tracking="temporarily_lost", visible=False, user="user-9"),
                           robot_state())
        tl.advance(1)
    go("DORMANT", "nobody back 5 s after person_left")
    tl.advance(1)

    # ---- 3. passed_by: seen for a few seconds, never reaches the robot ----
    tl.advance(25)
    log.start_visit("user-11", "golem")
    await walk(log, tl, rng, robot_state, 3.4, 2.8, 3, user="user-11")
    log.log_perception(snapshot(tl, rng, "unknown", None, tracking="temporarily_lost", visible=False, user="user-11"),
                       robot_state())
    tl.advance(1)
    log.end_visit("passed_by")
    tl.advance(1)

    # ---- 4. same_visitor: loses tracking, comes back within the grace window ----
    tl.advance(30)
    log.start_visit("user-12", "golem")
    await walk(log, tl, rng, robot_state, 1.8, 1.0, 2, user="user-12")
    go("AWAKE", "walked straight in")
    await log.log_message("robot", "I am awake... Welcome, traveller. Ask, and I shall answer.", source="opening",
                          mode="golem", emotion="warm")
    tl.advance(6)
    await say(log, tl, "golem", "What is your name?", "I am called the Golem.", think_s=0.9, emotion="neutral")
    for _ in range(3):                                         # steps out of view for 3 s
        log.log_perception(snapshot(tl, rng, "unknown", None, tracking="temporarily_lost", visible=False, user="user-12"),
                           robot_state())
        tl.advance(1)
    await log.log_event("same_visitor", "back after 3.0 s (user-13)")
    await walk(log, tl, rng, robot_state, 1.1, 1.0, 2, user="user-13")   # new robot-side id, same visit
    await say(log, tl, "golem", "Sorry, I stepped away. Where were we?", "At the beginning, as always.", think_s=1.0,
              emotion="warm")
    await walk(log, tl, rng, robot_state, 1.0, 1.0, 2, user="user-13")
    for _ in range(6):
        log.log_perception(snapshot(tl, rng, "unknown", None, tracking="temporarily_lost", visible=False, user="user-13"),
                           robot_state())
        tl.advance(1)
    go("DORMANT", "nobody back 5 s after person_left")
    tl.advance(1)

    # ---- 5. mode_switch: voice switch from golem to normal, perception goes stale ----
    tl.advance(35)
    log.start_visit("user-14", "golem")
    await walk(log, tl, rng, robot_state, 1.6, 1.0, 1.5, user="user-14")
    go("AWAKE", "walked straight in")
    await log.log_message("robot", "I am awake... Welcome, traveller. Ask, and I shall answer.", source="opening",
                          mode="golem", emotion="warm")
    tl.advance(5)
    await log.log_message("user", "normal mode", source="asr", mode="golem")
    await log.log_event("behavior", "default")
    tl.advance(0.8)
    await log.log_message("robot", "I am back to my normal self. What shall we talk about?", source="cue",
                          mode="default", emotion="warm", latency_ms=800)
    tl.advance(4)
    await say(log, tl, "default", "Tell me a joke", "Why did the robot cross the room? To meet you.", think_s=1.2,
              emotion="warm")
    log.log_perception(snapshot(tl, rng, "unknown", None, status="stale", tracking="unknown", visible=None, user="user-14"),
                       robot_state())
    await log.log_event("perception_stale", "no user data for 3 s")
    tl.advance(4)
    await walk(log, tl, rng, robot_state, 1.0, 1.0, 2, user="user-14")
    for _ in range(6):
        log.log_perception(snapshot(tl, rng, "unknown", None, tracking="temporarily_lost", visible=False, user="user-14"),
                           robot_state())
        tl.advance(1)
    go("DORMANT", "nobody back 5 s after person_left")

    await log.end_session()
    log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"database to create (default {DEFAULT_OUT})")
    parser.add_argument("--force", action="store_true", help="overwrite the file if it already exists")
    args = parser.parse_args()
    if args.out.exists():
        if not args.force:
            parser.error(f"{args.out} already exists; use --force to replace it")
        for suffix in ("", "-wal", "-shm"):
            Path(str(args.out) + suffix).unlink(missing_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(build(args.out))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
