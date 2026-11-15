"""Conversation module.

Gemini returns text. Furhat's own TTS speaks it with request.speak.text.
Furhat's speech recognizer turns the user's voice into text first.

The Gemini Live path, where the model streams audio and Furhat only plays it,
is not used. That code is commented out in gemini_live_audio.py.
"""
import asyncio
import base64
import os

from google import genai
from google.genai import errors, types
from furhat_realtime_api import AsyncFurhatClient, Events

from scenario import ScenarioManager, behavior_command_phrases, match_behavior_command

TEXT_MODEL = "gemini-3.5-flash"


class RealtimeConversation:
    def __init__(self, furhat: AsyncFurhatClient, scenario: ScenarioManager,
                 model: str = TEXT_MODEL, api_key: str | None = None, debug: bool = False):
        api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment or .env")
        self.furhat = furhat
        self.scenario = scenario
        self.model = model
        if "live" in self.model:
            print(f"[conversation] {self.model} generates audio. Text route uses {TEXT_MODEL}.")
            self.model = TEXT_MODEL
        self.debug = debug
        self.client = genai.Client(api_key=api_key)
        self.history: list[types.Content] = []
        self.latest_frame: str | None = None
        self.stop_event = asyncio.Event()
        self.behavior_changed = asyncio.Event()
        self.shutting_down = False
        self._listen_future: asyncio.Future | None = None

    async def stop(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        print("Initiating shutdown...")
        self.stop_event.set()
        self.behavior_changed.set()
        await self._stop_robot_io()

    def request_reconnect(self):
        """Behavior switch hook. No Gemini socket to reconnect; Furhat TTS changes immediately."""
        self.behavior_changed.set()
        asyncio.get_running_loop().create_task(self._stop_robot_io())

    async def send_video_frame(self, image_b64: str):
        """Keep the newest camera frame for the next text reply. Nothing is streamed live."""
        self.latest_frame = image_b64
        if self.debug:
            print("[conversation] stored camera frame, length", len(image_b64 or ""))

    async def run(self):
        await self.furhat.request_attend_user()
        await self.furhat.request_listen_config(
            languages=["en-US"],
            phrases=behavior_command_phrases(),
        )
        await self._speak_cue(switched=False)
        while not self.stop_event.is_set():
            if self.behavior_changed.is_set():
                self.behavior_changed.clear()
                await self._speak_cue(switched=True)
                continue
            heard = await self._listen_once()
            if self.stop_event.is_set():
                break
            if self.behavior_changed.is_set() or not heard.strip():
                continue
            print("User:", heard)
            command = match_behavior_command(heard)
            if command:
                await self._switch_from_voice(command)
                continue
            reply = await self._complete(heard)
            if reply and not self.behavior_changed.is_set():
                await self._speak(reply)

    async def _switch_from_voice(self, name: str):
        try:
            changed = await self.scenario.switch(name)
        except Exception as e:
            print(f"[conversation] voice switch failed: {e}")
            await self._speak("I could not change mode.")
            return
        if not changed:
            await self._speak("I am already in that mode.")
            return
        print(f"[conversation] voice command switched behavior to {name}")
        self.behavior_changed.set()

    async def _speak_cue(self, switched: bool):
        cue = self.scenario.cue_text(switched=switched)
        reply = await self._generate([self._user_content(cue)], remember=False)
        if not reply or self.stop_event.is_set():
            return
        self.history.append(types.Content(role="model", parts=[types.Part(text=reply)]))
        self._trim_history()
        await self._speak(reply)

    async def _complete(self, heard: str) -> str:
        user = self._user_content(heard, image_b64=self.latest_frame if self.scenario.use_vision else None)
        reply = await self._generate(self.history + [user], remember=False)
        if not reply:
            return ""
        self.history.append(user)
        self.history.append(types.Content(role="model", parts=[types.Part(text=reply)]))
        self._trim_history()
        return reply

    async def _generate(self, contents: list[types.Content], remember: bool) -> str:
        del remember
        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=self.scenario.instruction_text(),
                    max_output_tokens=256,
                ),
            )
        except errors.APIError as e:
            print(f"[conversation] Gemini error: {e}")
            return "Sorry, I had trouble thinking just now. Could you repeat that?"
        text = (getattr(response, "text", None) or "").strip()
        if self.debug:
            print("[conversation] Gemini text:", text)
        if not text:
            return "Sorry, I didn't catch that. Could you say it again?"
        return " ".join(text.split())

    async def _speak(self, text: str):
        print("Robot:", text)
        try:
            await self.furhat.request_speak_text(text, abort=True, wait=True)
        except Exception as e:
            if not self.shutting_down:
                print(f"[conversation] Furhat TTS failed: {e}")

    async def _listen_once(self) -> str:
        loop = asyncio.get_running_loop()
        heard = {"text": ""}
        future = loop.create_future()
        self._listen_future = future

        async def on_hear(data):
            heard["text"] = data.get("text") or heard["text"]

        async def on_listen_end(data):
            if not future.done():
                future.set_result(heard["text"])

        self.furhat.add_handler(Events.response_hear_end, on_hear)
        self.furhat.add_handler(Events.response_listen_end, on_listen_end)
        try:
            await self.furhat.request_listen_start(
                partial=False,
                stop_no_speech=True,
                stop_robot_start=True,
                stop_user_end=True,
                no_speech_timeout=8.0,
                end_speech_timeout=1.0,
            )
            return await asyncio.wait_for(future, timeout=40.0)
        except asyncio.TimeoutError:
            await self.furhat.request_listen_stop()
            return heard["text"]
        finally:
            self.furhat.remove_handler(Events.response_hear_end, on_hear)
            self.furhat.remove_handler(Events.response_listen_end, on_listen_end)
            self._listen_future = None

    def _user_content(self, text: str, image_b64: str | None = None) -> types.Content:
        parts = [types.Part(text=text)]
        if image_b64:
            try:
                parts.append(types.Part(
                    inline_data=types.Blob(mime_type="image/jpeg", data=base64.b64decode(image_b64))
                ))
            except Exception as e:
                print(f"[conversation] skipped camera frame: {e}")
        return types.Content(role="user", parts=parts)

    def _trim_history(self):
        if len(self.history) > 24:
            self.history = self.history[-24:]

    async def _stop_robot_io(self):
        try:
            await self.furhat.request_speak_stop()
            await self.furhat.request_listen_stop()
        except Exception as e:
            if not self.shutting_down:
                print(f"[conversation] could not stop Furhat speech: {e}")
        future = self._listen_future
        if future and not future.done():
            future.set_result("")
