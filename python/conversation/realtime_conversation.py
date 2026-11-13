"""Realtime Conversation Module.

Owner: Yuki. Bridges Furhat audio and the Gemini Live API over a websocket,
following the structure of demos/openai_realtime.py. It does not connect to
Furhat or read the camera itself: main.py hands it a connected Furhat client,
and other modules feed it through `send_video_frame`.
"""
import os
import json
import asyncio
import websockets
from furhat_realtime_api import AsyncFurhatClient, Events

from scenario import ScenarioManager


class RealtimeConversation:
    def __init__(self, furhat: AsyncFurhatClient, scenario: ScenarioManager,
                 model: str = "gemini-3.8-live", api_key: str | None = None, debug: bool = False):
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
        self.ws = None
        self.setup_done = False  # nothing but "setup" may be sent before setupComplete
        self.user_turn = False
        self.output_started = False
        self.stop_event = asyncio.Event()
        self.shutting_down = False
        self.furhat.add_handler(Events.response_speak_end, self.furhat_speak_end)
        self.furhat.add_handler(Events.response_audio_data, self.furhat_microphone_data)

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

    # ---- Furhat -> Gemini ----

    async def furhat_speak_end(self, data):
        # This is called when Furhat finishes speaking
        self.user_turn = True
        # Gemini Live expects 16 kHz 16-bit PCM audio input
        await self.furhat.request_audio_start(sample_rate=16000, microphone=True, speaker=False)

    async def furhat_microphone_data(self, data):
        # This is called when Furhat received user audio
        # We only send audio data to Gemini if it's the user's turn and not shutting down
        if self.user_turn and self.ws and self.setup_done and not self.shutting_down:
            await self.ws.send(json.dumps({
                "realtimeInput": {
                    "audio": {
                        "data": data.get("microphone"),
                        "mimeType": "audio/pcm;rate=16000"
                    }
                }
            }))

    async def send_video_frame(self, image_b64: str):
        """Send one camera frame (base64 JPEG) to Gemini. Used as a vision-module listener."""
        if self.ws and self.setup_done and not self.shutting_down:
            if self.debug:
                print("[conversation] sending video frame")
            await self.ws.send(json.dumps({
                "realtimeInput": {
                    "video": {
                        "data": image_b64,
                        "mimeType": "image/jpeg"
                    }
                }
            }))

    # ---- Gemini -> Furhat ----

    async def session_created(self):
        # Gemini has no server "session.created" event: we send the setup message
        # as soon as we connect, and the server answers with "setupComplete".
        await self.ws.send(json.dumps(self.scenario.build_setup_message(self.model)))

    async def setup_complete(self, data):
        # This is called when the Gemini session is ready
        # We ask Gemini to create the initial response
        self.setup_done = True
        await self.furhat.request_attend_user()
        await self.ws.send(json.dumps(self.scenario.build_opening_message()))

    async def response_audio_delta(self, part):
        # This is called when Gemini sends a chunk of audio data
        if not self.output_started:
            # First chunk of the response: stop listening and start speaking
            await self.furhat.request_audio_stop()
            self.user_turn = False
            # Gemini Live output audio is 24 kHz 16-bit PCM
            await self.furhat.request_speak_audio_start(sample_rate=24000, lipsync=True)
            self.output_started = True
        await self.furhat.request_speak_audio_data(part["inlineData"]["data"])

    async def response_audio_done(self, data):
        # This is called when Gemini has finished sending audio data
        if self.output_started:
            await self.furhat.request_speak_audio_end()
            self.output_started = False

    async def server_content(self, content):
        for part in content.get("modelTurn", {}).get("parts", []):
            if "inlineData" in part:
                await self.response_audio_delta(part)
        if content.get("turnComplete") or content.get("interrupted"):
            await self.response_audio_done(content)

    # ---- Main loop ----

    async def run(self):
        """Handle the Realtime connection until stop() is called or the socket closes."""
        async with websockets.connect(self.url) as ws:
            self.ws = ws
            await self.session_created()
            while not self.stop_event.is_set():
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=0.1)
                    data = json.loads(message)
                    if self.debug:
                        print("[conversation] Gemini message keys:", list(data.keys()))
                    if "setupComplete" in data:
                        await self.setup_complete(data)
                    elif "serverContent" in data:
                        await self.server_content(data["serverContent"])
                    elif "goAway" in data:
                        print("Gemini is closing the session soon:", data)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    print("Connection to Gemini closed:", e)
                    break
