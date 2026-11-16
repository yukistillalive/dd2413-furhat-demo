"""Vision Perception Module: camera frames and stable user states.

Owner: Yakun. main.py can keep using the original frame listener interface.
No speech, movement, microphone or cloud model calls are made here.
"""
import asyncio
from contextlib import suppress
import logging
import time

from .perception_state import PerceptionTracker

logger = logging.getLogger(__name__)


class VisionModule:
    def __init__(self, furhat, frame_interval=1.0, debug=False, *,
                 config=None, camera_enabled=True, poll_interval=0.5, request_timeout=2.0):
        from furhat_realtime_api import Events
        if min(frame_interval, poll_interval, request_timeout) <= 0:
            raise ValueError("Intervals must be positive")
        self.furhat = furhat
        self.frame_interval = frame_interval
        self.debug = debug
        self.camera_enabled = camera_enabled
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self.tracker = PerceptionTracker(config)
        self.latest_frame = None
        self.last_frame_at = None
        self.camera_error = None
        self.frames_received = 0
        self.frames_forwarded = 0
        self._listeners = []
        self._state_listeners = []
        self._last_forward = None
        self._running = False
        self._tasks = []
        self._frames = asyncio.Queue(maxsize=1)
        self._states = asyncio.Queue(maxsize=1)
        self._camera_event = Events.response_camera_data

    @property
    def latest_state(self):
        return self.tracker.snapshot()

    def add_frame_listener(self, listener):
        """保留原接口：async listener(image_b64)。"""
        self._listeners.append(listener)

    def add_state_listener(self, listener):
        """新增接口：async listener(state)，含最近的 events。"""
        self._state_listeners.append(listener)

    @staticmethod
    def _put_latest(queue, item):
        # 只保留最新数据，慢消费者不会让旧画面无限积累。
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(item)

    async def start(self):
        if self._running:
            return
        self.tracker = PerceptionTracker(self.tracker.config)
        self._running = True
        self._last_forward = None
        self.latest_frame = None
        self.last_frame_at = None
        self.camera_error = None
        for queue in (self._frames, self._states):
            while not queue.empty():
                queue.get_nowait()
        self.furhat.add_handler(self._camera_event, self._on_camera_data)
        self._tasks = [asyncio.create_task(self._poll_users()),
                       asyncio.create_task(self._monitor()),
                       asyncio.create_task(self._deliver(self._frames, self._listeners)),
                       asyncio.create_task(self._deliver(self._states, self._state_listeners))]
        if self.camera_enabled:
            try:
                await asyncio.wait_for(self.furhat.request_camera_start(), self.request_timeout)
            except Exception as exc:
                self.camera_error = f"Camera start failed: {type(exc).__name__}"
                logger.warning(self.camera_error)

    async def stop(self):
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self.furhat.remove_handler(self._camera_event, self._on_camera_data)
        if self.camera_enabled:
            with suppress(Exception):
                await asyncio.wait_for(self.furhat.request_camera_stop(), self.request_timeout)
        self.tracker.status = "stopped"

    async def _poll_users(self):
        while self._running:
            try:
                # 定期单次读取，避免启停其他模块可能共用的 users stream。
                response = await asyncio.wait_for(
                    self.furhat.request_users_once(), self.request_timeout)
                self.tracker.update(response)
            except Exception as exc:
                self.tracker.mark_error(f"User request failed: {type(exc).__name__}")
            await asyncio.sleep(self.poll_interval)

    async def _monitor(self):
        while self._running:
            state = self.tracker.tick()
            state["events"] = list(self.tracker.events)
            self._put_latest(self._states, state)
            await asyncio.sleep(0.2)

    async def _deliver(self, queue, listeners):
        while self._running:
            item = await queue.get()
            for listener in list(listeners):
                try:
                    await asyncio.wait_for(listener(item), timeout=2.0)
                except Exception:
                    logger.warning("Vision listener failed or timed out", exc_info=self.debug)

    async def _on_camera_data(self, data):
        # 回调快速返回；慢速图像消费者在单独任务里运行。
        if not self._running:
            return
        image = data.get("image")
        self.frames_received += 1
        if not isinstance(image, str) or not image:
            return
        self.latest_frame = image
        self.last_frame_at = time.monotonic()
        if self._last_forward is not None and self.last_frame_at - self._last_forward < self.frame_interval:
            return
        self._last_forward = self.last_frame_at
        self.frames_forwarded += 1
        self._put_latest(self._frames, image)
