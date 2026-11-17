"""Offline tests. From python/: python -m unittest discover -s interaction -p 'test_*.py' -t ."""
import asyncio
import unittest
from unittest import mock

from furhat_realtime_api import Events

import conversation.realtime_conversation as rc
from gui import GolemGUI
from interaction import replay
from interaction.controller import FALLBACK_GREETING
from scenario import ScenarioManager, parse_model_reply


class ReplayTests(unittest.TestCase):
    def test_all_segments(self):
        results = asyncio.run(replay.run_all(out=lambda *_: None))
        self.assertIn("recording", results)
        problems = [p for name, r in results.items() for p in replay.check(name, r)]
        self.assertEqual(problems, [])

    def test_visitor_close_signal_follows_the_zone(self):
        r = asyncio.run(replay.run_all(only="approach", out=lambda *_: None))["approach"]
        awake_at = next(t for t, a, b in r["transition_times"] if b == "AWAKE")
        asleep_at = next(t for t, a, b in r["transition_times"] if a == "AWAKE")
        values = [c for _, c in r["close"]]
        self.assertEqual(values[-2:], [True, False])
        on, off = r["close"][-2][0], r["close"][-1][0]
        self.assertLessEqual(on, awake_at, "close before the greeting")
        self.assertLess(off, asleep_at, "not close during the person_left grace")

    def test_recording_never_awake_at_1_2_m(self):
        results = asyncio.run(replay.run_all(only="recording", out=lambda *_: None))
        self.assertNotIn("AWAKE", {b for _, b in results["recording"]["transitions"]})

    def test_recording_reaches_awake_with_wider_zone(self):
        # The recording's closest point is ~1.44 m, so a 1.6 m enter distance wakes the Golem.
        results = asyncio.run(replay.run_all(only="recording", enter=1.6, exit_=1.9, out=lambda *_: None))
        self.assertIn("AWAKE", {b for _, b in results["recording"]["transitions"]})


class ReplyFormatTests(unittest.TestCase):
    def test_emotion_labels(self):
        self.assertEqual(parse_model_reply('{"say":"Hi.","emotion":"warm"}').emotion, "warm")
        self.assertEqual(parse_model_reply('{"say":"Hi."}').emotion, "neutral")
        self.assertEqual(parse_model_reply('{"say":"Hi.","emotion":"furious"}').emotion, "neutral")

    def test_raw_requests_are_not_spoken(self):
        self.assertEqual(parse_model_reply('{"type":"request.led.set","color":"#FF0000"}').say, "")
        self.assertEqual(parse_model_reply('{"say":"request.led.set red"}').say, "")


class ListeningFurhat(replay.FakeFurhat):
    """FakeFurhat whose listen ends after a short silence, like a quiet room."""

    async def request_listen_config(self, **kw):
        self._record("listen_config")

    async def request_listen_start(self, **kw):
        self._record("listen_start")
        asyncio.get_running_loop().call_later(
            0.02, lambda: asyncio.ensure_future(self._dispatch(Events.response_listen_end)))


class ConversationSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.furhat = ListeningFurhat(lambda: 0.0, lambda *_: None)
        self.scenario = ScenarioManager("golem")
        self.scenario.use_vision = True
        self.scenario.bind(self.furhat)
        self.gui = GolemGUI(self.furhat)
        self.gui.face.led_allowed = lambda: not self.scenario.owns_led()
        self.conv = rc.RealtimeConversation(self.furhat, self.scenario, api_key="offline-test",
                                            gui=self.gui, wait_for_session=True)
        self.prompts = []
        self.reply = '{"say":"Welcome. Your green scarf is bright.","emotion":"warm"}'

        async def fake_generate(contents, remember, use_json=True):
            self.prompts.append(contents)
            if isinstance(self.reply, Exception):
                raise self.reply
            if self.reply == "slow":
                await asyncio.sleep(5)
            return self.reply

        self.conv._generate = fake_generate
        self.task = asyncio.create_task(self.conv.run())

    async def asyncTearDown(self):
        await self.conv.stop()
        await asyncio.wait({self.task}, timeout=2)

    def spoken(self):
        return [kw["text"] for _, call, kw in self.furhat.calls if call == "speak"]

    def count(self, call):
        return sum(1 for _, c, _ in self.furhat.calls if c == call)

    async def test_silent_until_session_starts(self):
        await asyncio.sleep(0.2)
        self.assertEqual(self.spoken(), [])
        self.assertEqual(self.count("listen_start"), 0)
        self.assertEqual(self.count("attend_user"), 0)

    async def test_opening_uses_frame_then_listens(self):
        self.conv.latest_frame = "aGVsbG8="   # any base64
        self.conv.start_session("cue", FALLBACK_GREETING)
        await asyncio.sleep(0.2)
        self.assertEqual(self.spoken()[0], "Welcome. Your green scarf is bright.")
        parts = self.prompts[0][0].parts
        self.assertTrue(any(p.inline_data for p in parts), "frame must be sent with the opening cue")
        self.assertGreater(self.count("listen_start"), 0)

    async def test_fallback_without_frame(self):
        self.conv.start_session("cue", FALLBACK_GREETING)
        await asyncio.sleep(0.1)
        self.assertEqual(self.spoken()[0], FALLBACK_GREETING)
        self.assertEqual(self.prompts, [], "no model call without a frame")

    async def test_fallback_on_timeout_and_error(self):
        self.conv.latest_frame = "aGVsbG8="
        self.reply = "slow"
        with mock.patch.object(rc, "OPENING_TIMEOUT_S", 0.1):
            self.conv.start_session("cue", FALLBACK_GREETING)
            await asyncio.sleep(0.3)
        self.assertEqual(self.spoken()[0], FALLBACK_GREETING)
        await self.conv.end_session()
        self.reply = ""   # what _generate returns after an API error
        self.conv.start_session("cue", FALLBACK_GREETING)
        await asyncio.sleep(0.1)
        self.assertEqual(self.spoken()[-1], FALLBACK_GREETING)

    async def test_end_session_stops_listening_and_resume_skips_greeting(self):
        self.conv.start_session("cue", FALLBACK_GREETING)
        await asyncio.sleep(0.1)
        await self.conv.end_session()
        await asyncio.sleep(0.05)
        listens = self.count("listen_start")
        await asyncio.sleep(0.2)
        self.assertEqual(self.count("listen_start"), listens, "no listening after end_session")
        self.conv.start_session("cue", FALLBACK_GREETING, resume=True)
        await asyncio.sleep(0.1)
        self.assertEqual(self.spoken(), [FALLBACK_GREETING], "resume must not greet again")
        self.assertGreater(self.count("listen_start"), listens)


class ScriptedFurhat(replay.FakeFurhat):
    """Listens follow a script of (heard text, seconds); each one moves the fake clock.

    Records (call, text/heard, idle mode, face expression) so tests can see what
    the face looked like at every listen and after every spoken line.
    """

    def __init__(self, script):
        self.now = 0.0
        super().__init__(lambda: self.now, lambda *_: None)
        self.script = list(script)
        self.gui = None
        self.trace = []

    def snapshot(self, call, text):
        self.trace.append((call, text, self.gui.idle.mode, self.gui.face.expression))

    async def request_listen_config(self, **kw):
        self._record("listen_config")

    async def request_listen_start(self, **kw):
        self._record("listen_start")
        self.snapshot("listen", None)
        if not self.script:
            return   # nothing more to hear: the listen stays open until the test stops
        text, seconds = self.script.pop(0)
        self.now += seconds

        async def finish():
            await asyncio.sleep(0.01)
            if text:
                await self._dispatch(Events.response_hear_end, {"text": text})
            await self._dispatch(Events.response_listen_end)
        asyncio.ensure_future(finish())

    async def request_speak_text(self, text, abort=False, wait=False, monitor_words=False):
        await super().request_speak_text(text, abort=abort, wait=wait)
        self.snapshot("said", text)


class TurnTakingTests(unittest.IsolatedAsyncioTestCase):
    """Thinking face + filler while Gemini works, and the silence prompt in a session."""

    REPLY = '{"say":"I guard the old words.","emotion":"solemn"}'

    async def start(self, script, delay=0.0, session=True):
        self.furhat = ScriptedFurhat(script)
        scenario = ScenarioManager("golem")
        scenario.bind(self.furhat)
        self.gui = GolemGUI(self.furhat)
        self.gui.face.led_allowed = lambda: not scenario.owns_led()
        self.furhat.gui = self.gui
        await self.gui.golem.awaken()
        self.gui.idle.start()
        self.conv = rc.RealtimeConversation(self.furhat, scenario, api_key="offline-test",
                                            gui=self.gui, wait_for_session=session)
        self.conv.clock = lambda: self.furhat.now
        self.conv.filler_delay_s = 0.1
        self.at_generate = []

        async def fake_generate(contents, remember, use_json=True):
            self.at_generate.append((self.gui.idle.mode, self.gui.face.expression))
            await asyncio.sleep(delay)
            return self.REPLY

        self.conv._generate = fake_generate
        self.task = asyncio.create_task(self.conv.run())
        if session:
            self.conv.start_session(None, FALLBACK_GREETING)
        await asyncio.sleep(0.3 + 2 * delay)

    async def asyncTearDown(self):
        await self.conv.stop()
        await asyncio.wait({self.task}, timeout=2)
        await self.gui.idle.stop()

    def spoken(self):
        return [kw["text"] for _, call, kw in self.furhat.calls if call == "speak"]

    def said(self, text):
        return next((mode, expr) for call, t, mode, expr in self.furhat.trace if call == "said" and t == text)

    async def test_fast_reply_thinks_without_filler(self):
        await self.start([("What are you guarding here?", 1)], delay=0.0)
        self.assertEqual(self.at_generate, [("thinking", "thinking")])
        self.assertEqual(self.spoken(), [FALLBACK_GREETING, "I guard the old words."])
        self.assertEqual(self.gui.face.expression, "solemn", "the reply's emotion replaces thinking")
        self.assertEqual(self.gui.idle.mode, "listening")

    async def test_slow_reply_says_filler_then_reply(self):
        await self.start([("What are you guarding here?", 1)], delay=0.3)
        self.assertEqual(self.spoken(), [FALLBACK_GREETING, rc.FILLER_LINE, "I guard the old words."])
        self.assertEqual(self.said(rc.FILLER_LINE), ("thinking", "thinking"),
                         "the filler must not end the thinking mode")
        self.assertEqual(self.said("I guard the old words."), ("listening", "solemn"))

    async def test_reply_before_filler_delay_cancels_filler(self):
        await self.start([("What are you guarding here?", 1)], delay=0.05)
        await asyncio.sleep(0.2)
        self.assertNotIn(rc.FILLER_LINE, self.spoken())

    async def test_silence_prompts_once_then_waits_serene(self):
        silence = [("", rc.NO_SPEECH_TIMEOUT_S)]
        await self.start(silence * 6 + [("Who are you, golem?", 1)])
        spoken = self.spoken()
        self.assertEqual(spoken, [FALLBACK_GREETING, rc.SILENCE_PROMPT, "I guard the old words."])
        listens = [(mode, expr) for call, _, mode, expr in self.furhat.trace if call == "listen"]
        # listens 1-2: silent -> prompt; 3-4: silent -> serene; 5-7 stay quiet until the visitor speaks
        self.assertNotEqual(listens[3][1], "serene")
        self.assertEqual(listens[4:7], [("quiet", "serene")] * 3)
        self.assertEqual(self.at_generate, [("thinking", "thinking")], "speaking ends the serene wait")
        self.assertEqual(self.gui.face._held, {}, "serene eyelids released")

    async def test_speech_restarts_the_silence(self):
        silence = ("", rc.NO_SPEECH_TIMEOUT_S)
        await self.start([silence, ("Hello there, friend", 1), silence])
        self.assertNotIn(rc.SILENCE_PROMPT, self.spoken())

    async def test_no_silence_prompt_without_sessions(self):
        await self.start([("", rc.NO_SPEECH_TIMEOUT_S)] * 6, session=False)
        self.assertNotIn(rc.SILENCE_PROMPT, self.spoken())

    async def test_no_silence_prompt_while_visitor_is_not_close(self):
        silence = ("", rc.NO_SPEECH_TIMEOUT_S)
        await self.start([])
        self.conv.set_visitor_close(False)   # walking off / person_left grace
        self.furhat.script = [silence] * 4
        await self.conv._stop_robot_io()     # end the open listen so the new script is used
        await asyncio.sleep(0.3)
        self.assertNotIn(rc.SILENCE_PROMPT, self.spoken())
        self.conv.set_visitor_close(True)    # back in the zone: silence counts again from now
        self.furhat.script = [silence] * 2
        await self.conv._stop_robot_io()
        await asyncio.sleep(0.3)
        self.assertEqual(self.spoken().count(rc.SILENCE_PROMPT), 1)

    async def test_new_session_resets_silence_and_quiet_mode(self):
        await self.start([("", rc.NO_SPEECH_TIMEOUT_S)] * 4)
        self.assertEqual(self.gui.idle.mode, "quiet")
        await self.conv.end_session()
        self.furhat.script = [("", rc.NO_SPEECH_TIMEOUT_S)] * 2
        self.conv.start_session(None, FALLBACK_GREETING)
        await asyncio.sleep(0.3)
        self.assertEqual(self.spoken().count(rc.SILENCE_PROMPT), 2, "a new visitor gets their own prompt")


class LedOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_gui_leaves_led_alone_in_golem_mode(self):
        furhat = replay.FakeFurhat(lambda: 0.0, lambda *_: None)
        scenario = ScenarioManager("golem")
        gui = GolemGUI(furhat)
        gui.face.led_allowed = lambda: not scenario.owns_led()
        await gui.face.set_led("#ff0000")
        self.assertEqual([c for c in furhat.calls if c[1] == "led"], [])
        scenario.scenario = ScenarioManager("default").scenario
        await gui.face.set_led("#ff0000")
        self.assertEqual(len([c for c in furhat.calls if c[1] == "led"]), 1)


if __name__ == "__main__":
    unittest.main()
