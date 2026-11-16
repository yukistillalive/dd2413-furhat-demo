"""PARKED. Gemini generates audio; Furhat only plays the PCM.

Not imported by main.py. The running path is text in, Furhat TTS out
(realtime_conversation.py). To restore this path later, remove the leading
"# " from the code below and construct GeminiLiveAudioConversation instead
of RealtimeConversation. Scenario.gemini_voice and Scenario.reverb_mix are
still on the behavior presets for this path.
"""

# import array
# import asyncio
# import base64
# import json
# import os
#
# import websockets
# from furhat_realtime_api import AsyncFurhatClient, Events
#
# from scenario import ScenarioManager
#
#
# class HallReverb:
#     """Two-tap echo applied to Gemini's 24 kHz PCM before Furhat plays it."""
#
#     def __init__(self):
#         self._key = None
#         self._buffers = []
#         self._positions = []
#
#     def reset(self):
#         self._key = None
#         self._buffers = []
#         self._positions = []
#
#     def process(self, audio_b64, mix, sample_rate=24000):
#         if mix <= 0 or not audio_b64:
#             return audio_b64
#         raw = base64.b64decode(audio_b64)
#         if len(raw) < 4:
#             return audio_b64
#         if len(raw) % 2:
#             raw = raw[:-1]
#         samples = array.array("h")
#         samples.frombytes(raw)
#         delays = (int(sample_rate * 0.08), int(sample_rate * 0.14))
#         decays = (0.48, 0.30)
#         if self._key != delays:
#             self._buffers = [array.array("h", [0]) * n for n in delays]
#             self._positions = [0 for _ in delays]
#             self._key = delays
#         out = array.array("h", samples)
#         for i, sample in enumerate(samples):
#             echo = 0.0
#             for index, decay in enumerate(decays):
#                 echo += self._buffers[index][self._positions[index]] * decay
#             mixed = int(sample * (1.0 - mix) + (sample + echo) * mix)
#             mixed = max(-32768, min(32767, mixed))
#             out[i] = mixed
#             for index in range(len(delays)):
#                 pos = self._positions[index]
#                 self._buffers[index][pos] = sample
#                 self._positions[index] = (pos + 1) % len(self._buffers[index])
#         return base64.b64encode(out.tobytes()).decode("ascii")
#
#
# def build_live_setup_message(scenario, model):
#     """First Gemini Live message: audio out, Gemini voice, system prompt."""
#     instruction = scenario.instruction_text()
#     model_name = model if model.startswith("models/") else "models/" + model
#     return {
#         "setup": {
#             "model": model_name,
#             "generationConfig": {
#                 "responseModalities": ["AUDIO"],
#                 "speechConfig": {
#                     "voiceConfig": {
#                         "prebuiltVoiceConfig": {"voiceName": scenario.scenario.gemini_voice}
#                     }
#                 },
#             },
#             "systemInstruction": {"parts": [{"text": instruction}]},
#         }
#     }
#
#
# class GeminiLiveAudioConversation:
#     """Furhat mic PCM -> Gemini Live, Gemini audio PCM -> Furhat speaker."""
#
#     def __init__(self, furhat: AsyncFurhatClient, scenario: ScenarioManager,
#                  model="gemini-3.8-live", api_key=None, debug=False):
#         api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
#         if not api_key:
#             raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment or .env")
#         self.url = (
#             "wss://generativelanguage.googleapis.com/ws/"
#             "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
#             "?key=" + api_key
#         )
#         self.furhat = furhat
#         self.scenario = scenario
#         self.model = model
#         self.debug = debug
#         self.ws = None
#         self.setup_done = False
#         self.user_turn = False
#         self.output_started = False
#         self.stop_event = asyncio.Event()
#         self.reconnect_event = asyncio.Event()
#         self.switched = False
#         self.shutting_down = False
#         self._reverb = HallReverb()
#         self.furhat.add_handler(Events.response_speak_end, self.furhat_speak_end)
#         self.furhat.add_handler(Events.response_audio_data, self.furhat_microphone_data)
#
#     async def stop(self):
#         if self.shutting_down:
#             return
#         self.shutting_down = True
#         try:
#             await self.furhat.request_audio_stop()
#             await self.furhat.request_speak_stop()
#         except Exception as e:
#             print(f"Error during Furhat shutdown: {e}")
#         self.stop_event.set()
#         self.reconnect_event.set()
#
#     def request_reconnect(self):
#         self.switched = True
#         self.reconnect_event.set()
#         self._reverb.reset()
#
#     async def furhat_speak_end(self, data):
#         self.user_turn = True
#         await self.furhat.request_audio_start(sample_rate=16000, microphone=True, speaker=False)
#
#     async def furhat_microphone_data(self, data):
#         if self.user_turn and self.ws and self.setup_done and not self.shutting_down:
#             await self.ws.send(json.dumps({
#                 "realtimeInput": {
#                     "audio": {
#                         "data": data.get("microphone"),
#                         "mimeType": "audio/pcm;rate=16000",
#                     }
#                 }
#             }))
#
#     async def send_video_frame(self, image_b64):
#         if self.ws and self.setup_done and not self.shutting_down:
#             await self.ws.send(json.dumps({
#                 "realtimeInput": {
#                     "video": {"data": image_b64, "mimeType": "image/jpeg"}
#                 }
#             }))
#
#     async def session_created(self):
#         await self.ws.send(json.dumps(build_live_setup_message(self.scenario, self.model)))
#
#     async def setup_complete(self, data):
#         self.setup_done = True
#         await self.furhat.request_attend_user()
#         cue = self.scenario.cue_text(switched=self.switched)
#         self.switched = False
#         await self.ws.send(json.dumps({"realtimeInput": {"text": cue}}))
#
#     async def response_audio_delta(self, part):
#         if not self.output_started:
#             await self.furhat.request_audio_stop()
#             self.user_turn = False
#             await self.furhat.request_speak_audio_start(sample_rate=24000, lipsync=True)
#             self.output_started = True
#         audio = self._reverb.process(part["inlineData"]["data"], self.scenario.scenario.reverb_mix)
#         await self.furhat.request_speak_audio_data(audio)
#
#     async def response_audio_done(self, data):
#         if self.output_started:
#             await self.furhat.request_speak_audio_end()
#             self.output_started = False
#
#     async def server_content(self, content):
#         for part in content.get("modelTurn", {}).get("parts", []):
#             if "inlineData" in part:
#                 await self.response_audio_delta(part)
#         if content.get("turnComplete") or content.get("interrupted"):
#             await self.response_audio_done(content)
#
#     async def _pump(self):
#         while not self.stop_event.is_set():
#             if self.reconnect_event.is_set():
#                 return "reconnect"
#             try:
#                 message = await asyncio.wait_for(self.ws.recv(), timeout=0.1)
#             except asyncio.TimeoutError:
#                 continue
#             except websockets.exceptions.ConnectionClosed as e:
#                 print("Connection to Gemini closed:", e)
#                 return "closed"
#             data = json.loads(message)
#             if "setupComplete" in data:
#                 await self.setup_complete(data)
#             elif "serverContent" in data:
#                 await self.server_content(data["serverContent"])
#             elif "goAway" in data:
#                 print("Gemini is closing the session soon:", data)
#             elif "error" in data:
#                 print("Gemini error:", data)
#         return "stop"
#
#     async def run(self):
#         while not self.stop_event.is_set():
#             self.reconnect_event.clear()
#             self.setup_done = False
#             self.output_started = False
#             self.user_turn = False
#             try:
#                 async with websockets.connect(self.url) as ws:
#                     self.ws = ws
#                     await self.session_created()
#                     reason = await self._pump()
#             except Exception as e:
#                 if self.stop_event.is_set():
#                     break
#                 print(f"Gemini connection error: {e}")
#                 await asyncio.sleep(1)
#                 continue
#             if reason == "reconnect" and not self.stop_event.is_set():
#                 try:
#                     await self.furhat.request_audio_stop()
#                     await self.furhat.request_speak_stop()
#                 except Exception as e:
#                     print(f"[conversation] could not stop Furhat audio: {e}")
#                 continue
#             break
