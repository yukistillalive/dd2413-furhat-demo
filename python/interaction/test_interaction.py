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
