"""Realtime Conversation Module.

Furhat audio <-> Gemini Live websocket, half-duplex (the mic is only open on the
user's turn). Stability features:
  * reconnects automatically (backoff) and resumes the Gemini session
  * never gets stuck waiting: listening is restarted after empty turns, and a
    watchdog restarts it if the robot's turn never finishes
  * interruptions stop the robot's speech immediately
  * every websocket send is guarded against a closed socket
Optional: pass a ConversationLog to store the transcript in SQLite.
"""
import os
import json
import time
import asyncio
import websockets
from furhat_realtime_api import AsyncFurhatClient, Events

from scenario import ScenarioManager

MAX_CONSECUTIVE_FAILURES = 6
WATCHDOG_IDLE_S = 15          # robot's turn produced nothing for this long
WATCHDOG_SPEAKING_S = 45      # audio sent, but Furhat never reported speak.end


class RealtimeConversation:
    def __init__(self, furhat: AsyncFurhatClient, scenario: ScenarioManager,
                 model: str = "gemini-3.8-live", api_key: str | None = None,
                 debug: bool = False, log=None, hub=None):
        api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment or .env")
        self.url = (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            "?key=" + api_key
        )
        self.furhat = furhat
        self.scenario = scenario
        self.model = model
        self.debug = debug
        self.log = log
        self.hub = hub   # optional DebugHub (live view for the debug-ui)
        self.ws = None
        self.resume_handle: str | None = None   # Gemini session resumption handle
        self.stop_event = asyncio.Event()
        self.shutting_down = False
        self._reset_state()
        self.furhat.add_handler(Events.response_speak_end, self.furhat_speak_end)
        self.furhat.add_handler(Events.response_audio_data, self.furhat_microphone_data)

    def _reset_state(self):
        """Per-connection state; called before every (re)connect."""
        self.setup_done = False       # nothing but "setup" may be sent before setupComplete
        self.user_turn = False        # mic audio is forwarded to Gemini
        self.listening = False        # Furhat mic is on
        self.output_started = False   # robot is playing Gemini audio
        self.awaiting_speak_end = False
        self.last_activity = time.monotonic()
        self.user_buf: list[str] = []
        self.robot_buf: list[str] = []

    async def stop(self):
        """Graceful shutdown"""
        if self.shutting_down:
            return
        self.shutting_down = True
        print("Initiating shutdown...")
        try:
            await self.furhat.request_audio_stop()
            await self.furhat.request_speak_stop()
        except Exception as e:
            print(f"Error during Furhat shutdown: {e}")
        self.stop_event.set()

    # ---- helpers ----

    async def _send(self, message: dict) -> bool:
        """Send to Gemini; False (instead of raising) if the socket is gone."""
        if not self.ws:
            return False
        try:
            await self.ws.send(json.dumps(message))
            return True
        except websockets.exceptions.ConnectionClosed:
            return False

    async def _emit(self, **obj):
        if self.hub:
            try:
                await self.hub.emit(obj)
            except Exception as e:
                print("[conversation] debug hub error:", e)

    async def _log_event(self, type_: str, detail: str = ""):
        await self._emit(type="event", name=type_, detail=detail)
        if self.log:
            try:
                await self.log.log_event(type_, detail)
            except Exception as e:
                print("[conversation] log error:", e)

    async def _flush(self, role: str, interrupted: bool = False):
        buf = self.user_buf if role == "user" else self.robot_buf
        text = "".join(buf).strip()
        buf.clear()
        if not text:
            return
        print(f"[{role}] {text}" + (" (interrupted)" if interrupted else ""))
        await self._emit(type="message", role=role, text=text, interrupted=interrupted)
        if self.log:
            try:
                await self.log.log_message(role, text, interrupted)
            except Exception as e:
                print("[conversation] log error:", e)

    async def _start_listening(self):
        self.user_turn = True
        if not self.listening and not self.shutting_down:
            self.listening = True
            await self._emit(type="state", state="listening")
            self.last_activity = time.monotonic()
            # Gemini Live expects 16 kHz 16-bit PCM audio input
            await self.furhat.request_audio_start(sample_rate=16000, microphone=True, speaker=False)

    async def _stop_listening(self):
        self.user_turn = False
        if self.listening:
            self.listening = False
            await self.furhat.request_audio_stop()

    # ---- Furhat -> Gemini ----

    async def furhat_speak_end(self, data):
        # Furhat finished speaking: the user's turn
        self.awaiting_speak_end = False
        if self.setup_done:
            await self._start_listening()

    async def furhat_microphone_data(self, data):
        # Only forward audio on the user's turn
        if self.user_turn and self.setup_done and not self.shutting_down:
            await self._send({
                "realtimeInput": {
                    "audio": {
                        "data": data.get("microphone"),
                        "mimeType": "audio/pcm;rate=16000"
                    }
                }
            })

    async def send_video_frame(self, image_b64: str):
        """Send one camera frame (base64 JPEG) to Gemini. Used as a vision-module listener."""
        if self.setup_done and not self.shutting_down:
            if self.debug:
                print("[conversation] sending video frame")
            await self._send({"realtimeInput": {"video": {"data": image_b64, "mimeType": "image/jpeg"}}})

    # ---- Gemini -> Furhat ----

    async def session_created(self):
        # Gemini has no server "session.created" event: we send the setup message
        # as soon as we connect, and the server answers with "setupComplete".
        await self._send(self.scenario.build_setup_message(self.model, self.resume_handle))

    async def setup_complete(self, data):
        self.setup_done = True
        await self.furhat.request_attend_user()
        if self.resume_handle:
            # Resumed session: Gemini remembers the conversation, just listen again
            await self._log_event("resumed")
            await self._start_listening()
        else:
            # Fresh session: ask Gemini to speak first
            await self._send(self.scenario.build_opening_message())

    async def response_audio_delta(self, part):
        if not self.output_started:
            # First chunk of the response: stop listening and start speaking
            await self._stop_listening()
            # Gemini Live output audio is 24 kHz 16-bit PCM
            await self.furhat.request_speak_audio_start(sample_rate=24000, lipsync=True)
            self.output_started = True
            await self._emit(type="state", state="speaking")
        await self.furhat.request_speak_audio_data(part["inlineData"]["data"])

    async def response_audio_done(self, data=None):
        if self.output_started:
            self.output_started = False
            self.awaiting_speak_end = True   # furhat_speak_end will reopen the mic
            await self.furhat.request_speak_audio_end()

    async def server_content(self, content):
        self.last_activity = time.monotonic()
        if "inputTranscription" in content:
            self.user_buf.append(content["inputTranscription"].get("text", ""))
        if "outputTranscription" in content:
            await self._flush("user")  # the user finished before the robot answers
            self.robot_buf.append(content["outputTranscription"].get("text", ""))

        for part in content.get("modelTurn", {}).get("parts", []):
            if "inlineData" in part:
                await self.response_audio_delta(part)

        if content.get("interrupted"):
            # User barged in: cut the robot off now (queued audio included)
            self.output_started = False
            self.awaiting_speak_end = False
            await self.furhat.request_speak_stop()
            await self._flush("robot", interrupted=True)
            await self._start_listening()
        elif content.get("turnComplete"):
            await self._flush("user")
            await self._flush("robot")
            if self.output_started:
                await self.response_audio_done(content)
            elif not self.awaiting_speak_end:
                # Empty turn (no audio): speak.end will never come, so reopen the mic ourselves
                await self._start_listening()

    async def _watchdog(self):
        """Reopen the mic if the robot's turn never completes."""
        while not self.stop_event.is_set():
            await asyncio.sleep(1)
            if not self.setup_done or self.user_turn or self.output_started:
                continue
            limit = WATCHDOG_SPEAKING_S if self.awaiting_speak_end else WATCHDOG_IDLE_S
            if time.monotonic() - self.last_activity > limit:
                print("[conversation] watchdog: turn stalled, listening again")
                await self._log_event("watchdog", "turn stalled")
                self.awaiting_speak_end = False
                try:
                    await self._start_listening()
                except Exception as e:
                    print("[conversation] watchdog error:", e)

    # ---- Main loop ----

    async def _session(self) -> str:
        """One websocket connection. Returns why it ended: 'stopped' | 'goaway' | 'closed'."""
        async with websockets.connect(self.url) as ws:
            self.ws = ws
            self._reset_state()
            await self.session_created()
            reason = "closed"
            while not self.stop_event.is_set():
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    print("Connection to Gemini closed:", e)
                    await self._log_event("closed", str(e))
                    break
                data = json.loads(message)
                if self.debug:
                    print("[conversation] Gemini message keys:", list(data.keys()))
                if "setupComplete" in data:
                    self.failures = 0
                    await self.setup_complete(data)
                elif "serverContent" in data:
                    await self.server_content(data["serverContent"])
                elif "sessionResumptionUpdate" in data:
                    update = data["sessionResumptionUpdate"]
                    if update.get("resumable") and update.get("newHandle"):
                        self.resume_handle = update["newHandle"]
                elif "goAway" in data:
                    print("Gemini is closing the session soon:", data)
                    await self._log_event("goAway", json.dumps(data["goAway"]))
                    reason = "goaway"
                    break
            else:
                reason = "stopped"
            # Save whatever was said before the connection ended
            await self._flush("user")
            await self._flush("robot", interrupted=True)
            self.ws = None
            await self._emit(type="state", state="reconnecting" if reason != "stopped" else "stopped")
            return reason

    async def run(self):
        """Handle the Gemini connection until stop() is called, reconnecting on drops."""
        self.failures = 0
        if self.log:
            await self.log.start_session(self.model, self.scenario.scenario.name,
                                         self.scenario.scenario.system_instruction)
        watchdog = asyncio.create_task(self._watchdog())
        try:
            while not self.stop_event.is_set():
                try:
                    reason = await self._session()
                except (OSError, websockets.exceptions.WebSocketException) as e:
                    reason = "error"
                    print("Gemini connection error:", e)
                    await self._log_event("error", repr(e))
                if reason == "stopped" or self.stop_event.is_set():
                    break
                if reason != "goaway":
                    self.failures += 1
                if self.failures >= MAX_CONSECUTIVE_FAILURES:
                    print("Giving up after repeated connection failures")
                    await self._log_event("gave_up")
                    break
                delay = 0 if reason == "goaway" else min(2 ** self.failures, 30)
                print(f"Reconnecting to Gemini in {delay}s (resume={'yes' if self.resume_handle else 'no'})...")
                await self._log_event("reconnect", f"reason={reason} delay={delay}")
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=delay or 0.01)
                except asyncio.TimeoutError:
                    pass
        finally:
            watchdog.cancel()
            await self._emit(type="state", state="stopped")
            if self.log:
                await self.log.end_session()
