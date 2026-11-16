"""Conversation module.

Gemini returns text. Furhat's own TTS speaks it with request.speak.text.
Furhat's speech recognizer turns the user's voice into text first.

The Gemini Live path, where the model streams audio and Furhat only plays it,
is not used. That code is commented out in gemini_live_audio.py.
"""
import asyncio
import base64
import json
import os
import re
import time

from google import genai
from google.genai import errors, types
from furhat_realtime_api import AsyncFurhatClient, Events

from scenario import ScenarioManager, behavior_command_phrases, match_behavior_command, parse_model_reply

# gofast model that is still on the Gemini API. Override with --model.
TEXT_MODEL = "gemini-3.8-flash-lite"
# This key's 404 tells us to call gemini-3.5-flash-lite.
FALLBACK_MODEL = "gemini-3.5-flash-lite"
FILLER_WORDS = {
    "um", "uh", "uhh", "hmm", "hm", "ah", "er", "yeah", "yep", "yes", "no", "nah",
    "ok", "okay", "for", "during", "the", "a", "an", "and", "to", "of", "it", "i",
    "oh", "well", "like", "so", "just", "that",
}
GREETINGS = {"hello", "hi", "hey", "help", "thanks", "thank"}


def is_valid_user_turn(text: str) -> bool:
    """True when the utterance is enough of a question or statement to spend an API call."""
    words = re.findall(r"[a-zA-Z']+", text.lower())
    content = [word for word in words if word not in FILLER_WORDS]
    if not content:
        return False
    if len(content) >= 3:
        return True
    if len(content) >= 2 and sum(len(word) for word in content) >= 8:
        return True
    return len(content) == 1 and content[0] in GREETINGS


class RealtimeConversation:
    def __init__(self, furhat: AsyncFurhatClient, scenario: ScenarioManager,
                 model: str = TEXT_MODEL, api_key: str | None = None, debug: bool = False,
                 log=None, hub=None):
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
        self.log = log
        self.hub = hub
        self.client = genai.Client(api_key=api_key)
        self.history: list[types.Content] = []
        self.latest_frame: str | None = None
        self.stop_event = asyncio.Event()
        self.behavior_changed = asyncio.Event()
        self.shutting_down = False
        self._listen_future: asyncio.Future | None = None
        self._backoff_until = 0.0
        print(f"[conversation] Gemini model: {self.model}")

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
        if self.log:
            await self.log.start_session(
                self.model, self.scenario.scenario.name, self.scenario.instruction_text())
        try:
            await self.furhat.request_attend_user()
            await self.furhat.request_listen_config(
                languages=["en-US"],
                phrases=behavior_command_phrases(),
            )
            await self._speak_cue(switched=False)
            await self._set_state("listening")
            while not self.stop_event.is_set():
                if self.behavior_changed.is_set():
                    self.behavior_changed.clear()
                    await self._log_event("behavior", self.scenario.scenario.name)
                    await self._speak_cue(switched=True)
                    continue
                heard = await self._listen_once()
                if self.stop_event.is_set():
                    break
                if self.behavior_changed.is_set() or not heard.strip():
                    continue
                print("User:", heard)
                await self._log_message("user", heard)
                command = match_behavior_command(heard)
                if command:
                    await self._switch_from_voice(command)
                    continue
                if not is_valid_user_turn(heard):
                    print("[conversation] skipped Gemini call: no complete question")
                    await self._log_event("skipped", heard)
                    continue
                if time.monotonic() < self._backoff_until:
                    print("[conversation] skipped Gemini call: rate-limit pause")
                    await self._log_event("rate_limit", "paused")
                    continue
                reply = await self._complete(heard)
                if reply and not self.behavior_changed.is_set():
                    await self._speak(reply)
        finally:
            await self._set_state("stopped")
            if self.log:
                await self.log.end_session()

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
        raw = await self._generate([self._user_content(cue)], remember=False)
        if not raw or self.stop_event.is_set():
            return
        say = await self._perform_reply(raw)
        if not say or self.stop_event.is_set():
            return
        self.history.append(types.Content(role="model", parts=[types.Part(text=say)]))
        self._trim_history()
        await self._speak(say)

    async def _complete(self, heard: str) -> str:
        user = self._user_content(heard, image_b64=self.latest_frame if self.scenario.use_vision else None)
        raw = await self._generate(self.history + [user], remember=False)
        if not raw:
            return ""
        say = await self._perform_reply(raw)
        self.history.append(user)
        if say:
            self.history.append(types.Content(role="model", parts=[types.Part(text=say)]))
        self._trim_history()
        return say

    async def _perform_reply(self, raw: str) -> str:
        """Run appearance requests. Return only the sentence that should be spoken."""
        reply = parse_model_reply(raw)
        for event in reply.requests:
            if self.stop_event.is_set():
                break
            await self._send_appearance(event)
        if not reply.say and not reply.requests and re.search(r"request\.|^\s*[\[{]", raw):
            print("[conversation] withheld reply that looked like a request")
            await self._log_event("appearance", "reply withheld: invalid request")
        return reply.say

    async def _send_appearance(self, event: dict):
        try:
            if event["type"] == "request.led.set":
                await self.scenario.stop_led_loop()
            await self.furhat.send_event(event)
            print("[conversation] appearance:", event["type"], {k: v for k, v in event.items() if k != "type"})
            await self._log_event("appearance", json.dumps(event, ensure_ascii=False))
        except Exception as e:
            print(f"[conversation] appearance request failed: {e}")
            await self._log_event("appearance_failed", str(e))

    async def _generate(self, contents: list[types.Content], remember: bool, use_json: bool = True) -> str:
        del remember
        if time.monotonic() < self._backoff_until:
            return ""
        config = types.GenerateContentConfig(
            system_instruction=self.scenario.instruction_text(),
            max_output_tokens=240,
        )
        if use_json:
            config.response_mime_type = "application/json"
        if self.model.startswith("gemini-2.5"):
            config.thinking_config = types.ThinkingConfig(thinking_budget=0)
        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=contents,
                config=config,
            )
        except errors.APIError as e:
            message = str(e)
            if use_json and "mime" in message.lower():
                print("[conversation] JSON reply mode unavailable, asking for plain JSON text")
                return await self._generate(contents, False, use_json=False)
            normalized = message.lower().replace("_", " ")
            if self.model != FALLBACK_MODEL and any(
                token in normalized for token in ("not found", "not supported", "no longer available", "unavailable")
            ):
                print(f"[conversation] {self.model} unavailable, switching to {FALLBACK_MODEL}")
                self.model = FALLBACK_MODEL
                await self._log_event("model", self.model)
                return await self._generate(contents, False)
            if "429" in message or "TooManyRequests" in message:
                self._backoff_until = time.monotonic() + 30
                print("[conversation] Gemini rate limit. No further requests for 30 seconds.")
                await self._log_event("rate_limit", message)
                return ""
            print(f"[conversation] Gemini error: {e}")
            await self._log_event("gemini_error", message)
            return ""
        text = (getattr(response, "text", None) or "").strip()
        if self.debug:
            print("[conversation] Gemini text:", text)
        if not text:
            return "Sorry, I didn't catch that. Could you say it again?"
        return text

    async def _speak(self, text: str):
        print("Robot:", text)
        await self._set_state("speaking")
        try:
            await self.furhat.request_speak_text(text, abort=True, wait=True)
        except Exception as e:
            await self._log_event("tts_failed", str(e))
            await self._set_state("listening")
            if not self.shutting_down:
                print(f"[conversation] Furhat TTS failed: {e}")
            return
        await self._log_message("robot", text)
        await self._set_state("listening")

    async def _emit(self, **obj):
        if not self.hub:
            return
        try:
            await self.hub.emit(obj)
        except Exception as e:
            print("[conversation] debug hub error:", e)

    async def _set_state(self, state: str):
        await self._emit(type="state", state=state)

    async def _log_message(self, role: str, text: str, interrupted: bool = False):
        text = (text or "").strip()
        if not text:
            return
        await self._emit(type="message", role=role, text=text, interrupted=interrupted)
        if not self.log:
            return
        try:
            await self.log.log_message(role, text, interrupted)
        except Exception as e:
            print("[conversation] log error:", e)

    async def _log_event(self, type_: str, detail: str = ""):
        await self._emit(type="event", name=type_, detail=detail)
        if not self.log:
            return
        try:
            await self.log.log_event(type_, detail)
        except Exception as e:
            print("[conversation] log error:", e)

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
                end_speech_timeout=1.8,
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
