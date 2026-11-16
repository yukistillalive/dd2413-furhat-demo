"""离线验证：从 python/ 运行 python -m unittest discover -s vision -p 'test_*.py'。"""
import asyncio
import unittest

from vision import VisionModule
from vision.perception_state import PerceptionConfig, PerceptionTracker


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.tracker = PerceptionTracker(PerceptionConfig(distance_unit="m"), self.clock)

    def feed(self, when, users):
        self.clock.now = when
        return self.tracker.update({"users": users})

    def establish(self, distance=1.0):
        user = [{"id": "a", "distance": distance}]
        self.feed(0, user)
        return self.feed(1, user)

    def names(self):
        return [e["name"] for e in self.tracker.events]

    def test_confirm_and_only_one_enter(self):
        self.assertEqual(self.feed(0, [{"id": "a", "distance": 1}])["tracking"], "confirming")
        self.feed(1, [{"id": "a", "distance": 1}])
        self.feed(2, [{"id": "a", "distance": 1}])
        self.assertEqual(self.names().count("person_entered"), 1)

    def test_empty_is_different_from_missing_data(self):
        self.assertFalse(self.feed(0, [])["person_present"])
        self.clock.now = 4
        state = self.tracker.tick()
        self.assertEqual(state["status"], "stale")
        self.assertIsNone(state["person_present"])

    def test_brief_occlusion_and_real_departure(self):
        self.establish()
        self.assertEqual(self.feed(1.2, [])["tracking"], "temporarily_lost")
        self.feed(2, [{"id": "a", "distance": 1}])
        self.assertNotIn("person_left", self.names())
        self.feed(2.2, [])
        self.feed(3.2, [])
        state = self.feed(4.3, [])
        self.assertFalse(state["person_present"])
        self.assertEqual(self.names().count("person_left"), 1)

    def test_hysteresis_and_outlier(self):
        self.establish()
        for when in (1.2, 1.4, 1.6):
            self.assertEqual(self.feed(when, [{"id": "a", "distance": 1.35}])["zone"], "interaction")
        self.feed(1.8, [{"id": "a", "distance": 1.8}])
        self.assertEqual(self.tracker.snapshot()["zone"], "interaction")
        self.assertEqual(self.feed(2, [{"id": "a", "distance": 1.8}])["zone"], "far")

    def test_does_not_switch_to_closer_second_person(self):
        self.establish()
        state = self.feed(2, [{"id": "b", "distance": 0.5}, {"id": "a", "distance": 1.1}])
        self.assertEqual(state["user_count"], 2)
        self.assertEqual(state["target_user_id"], "a")

    def test_units_missing_invalid_and_unknown(self):
        self.tracker.config.distance_unit = "cm"
        self.assertEqual(self.establish(100)["distance_m"], 1)
        state = self.feed(2, [{"id": "a", "distance": float("nan")}])
        self.assertIsNone(state["distance_m"])
        self.assertEqual(state["zone"], "unknown")
        self.tracker.config.distance_unit = "unknown"
        state = self.feed(2.2, [{"id": "a", "distance": 100}])
        self.assertEqual(state["distance_raw"], 100)
        self.assertIsNone(state["distance_m"])

    def test_bad_payload_is_not_departure(self):
        self.establish()
        for data in (None, {}, {"users": "bad"}, {"users": [None]}):
            self.assertEqual(self.tracker.update(data)["status"], "error")
        self.assertNotIn("person_left", self.names())

    def test_recovery_does_not_repeat_greeting_or_count_outage_as_departure(self):
        self.establish()
        self.clock.now = 10
        self.tracker.tick()
        self.feed(10.1, [{"id": "a", "distance": 1}])
        self.assertEqual(self.names().count("person_entered"), 1)
        self.assertIn("perception_recovered", self.names())
        self.clock.now = 20
        self.tracker.tick()
        self.assertEqual(self.feed(20.1, [])["tracking"], "temporarily_lost")
        self.assertNotIn("person_left", self.names())


class FakeFurhat:
    def __init__(self):
        self.handlers = {}
        self.calls = []

    def add_handler(self, event, callback):
        self.handlers[event] = callback

    def remove_handler(self, event, callback):
        self.handlers.pop(event)

    async def request_users_once(self):
        self.calls.append("users_once")
        return {"users": [{"id": "a", "distance": 1}]}

    async def request_camera_start(self):
        self.calls.append("camera_start")

    async def request_camera_stop(self):
        self.calls.append("camera_stop")


class ModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_with_local_fake_robot(self):
        # 验证官方 SDK 的真实消息格式；这个服务器不连接实体机器人。
        import json
        import websockets
        from furhat_realtime_api import AsyncFurhatClient

        requests = []

        async def fake_robot(socket):
            async for raw in socket:
                request = json.loads(raw)
                requests.append(request["type"])
                if request["type"] == "request.auth":
                    response = dict(type="response.auth", access=True, scope="local_network")
                else:
                    response = dict(type="response.users.data", users=[{"id": "wire-user", "distance": 1.0}])
                response["request_id"] = request["request_id"]
                await socket.send(json.dumps(response))

        async with websockets.serve(fake_robot, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = AsyncFurhatClient("127.0.0.1")
            client.ws_url = f"ws://127.0.0.1:{port}/v1/events"
            module = VisionModule(client, camera_enabled=False, poll_interval=0.02,
                                  config=PerceptionConfig(distance_unit="m", confirm_seconds=0.02))
            try:
                await client.connect()
                await module.start()
                for _ in range(50):
                    if module.latest_state["target_user_id"] is not None:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(module.latest_state["target_user_id"], "wire-user")
                self.assertEqual(module.latest_state["zone"], "interaction")
                self.assertEqual(set(requests), {"request.auth", "request.users.once"})
            finally:
                await module.stop()
                await client.disconnect()

    async def test_start_frames_poll_and_cleanup(self):
        robot = FakeFurhat()
        module = VisionModule(robot, config=PerceptionConfig(distance_unit="m"))
        frames = []
        states = []

        async def frame(image):
            frames.append(image)

        async def state(data):
            states.append(data)

        module.add_frame_listener(frame)
        module.add_state_listener(state)
        await module.start()
        await module.start()  # 幂等：不重复创建读取任务。
        await module._on_camera_data({"image": "example-jpeg"})
        await asyncio.sleep(0.05)
        self.assertEqual(frames, ["example-jpeg"])
        self.assertEqual(module.latest_state["user_count"], 1)
        self.assertTrue(states)
        await module.stop()
        self.assertFalse(robot.handlers)
        self.assertEqual(robot.calls.count("camera_start"), 1)
        self.assertEqual(robot.calls.count("camera_stop"), 1)
        self.assertEqual(module.latest_state["status"], "stopped")
        self.assertFalse(module._tasks)


if __name__ == "__main__":
    unittest.main()
