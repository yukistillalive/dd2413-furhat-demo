"""Scenario Setting Module.

Owner: Hengxiao Liu. Translates a scenario prompt into the WebSocket requests
that configure and start a Gemini Live session.

The conversation module never builds these messages itself: it asks the
ScenarioManager for them. To change how a prompt becomes a request, edit
this file only.
"""
from dataclasses import dataclass


@dataclass
class Scenario:
    name: str
    system_instruction: str
    # Text sent as the first user turn so the robot speaks first
    opening_prompt: str = "Please greet the user."
    language_code: str = "en-US"
    voice_name: str | None = None  # e.g. "Puck"; None = model default


# Appended to every system prompt so the robot stays in English.
ENGLISH_RULE = (
    " Always speak English only, even if the user speaks another language or the audio is "
    "unclear: in that case politely ask them, in English, to repeat. Keep replies short "
    "and conversational (one to three sentences)."
)


SCENARIOS = {
    "default": Scenario(
        name="default",
        system_instruction="You are a friendly robot speaking English, looking for a nice little chat.",
    ),
    "vision": Scenario(
        name="vision",
        system_instruction=(
            "You are a friendly robot speaking English, looking for a nice little chat. "
            "You can see the user through your camera and may comment on what you see."
        ),
    ),
}


class ScenarioManager:
    def __init__(self, scenario: Scenario | str = "default"):
        self.scenario = SCENARIOS[scenario] if isinstance(scenario, str) else scenario

    @classmethod
    def from_prompt(cls, system_instruction: str, opening_prompt: str = "Please greet the user."):
        """Build a scenario from a free-text prompt."""
        return cls(Scenario("custom", system_instruction, opening_prompt))

    # ---- Prompt -> WebSocket request translation ----

    def build_setup_message(self, model: str, resume_handle: str | None = None) -> dict:
        """First message of a Gemini Live session.

        Sets model, audio output, English speech, turn detection, transcription
        (for logging), session resumption and context compression (long chats).
        """
        speech = {"languageCode": self.scenario.language_code}
        if self.scenario.voice_name:
            speech["voiceConfig"] = {"prebuiltVoiceConfig": {"voiceName": self.scenario.voice_name}}
        return {
            "setup": {
                "model": "models/" + model,
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": speech},
                "systemInstruction": {"parts": [{"text": self.scenario.system_instruction + ENGLISH_RULE}]},
                "realtimeInputConfig": {
                    "automaticActivityDetection": {
                        "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
                        "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
                        "silenceDurationMs": 700,
                    }
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
                "sessionResumption": {"handle": resume_handle} if resume_handle else {},
                "contextWindowCompression": {"slidingWindow": {}},
            }
        }

    def build_opening_message(self) -> dict:
        """Message that makes the robot speak first."""
        return self.build_text_message(self.scenario.opening_prompt)

    @staticmethod
    def build_text_message(text: str) -> dict:
        """A realtime text turn."""
        return {"realtimeInput": {"text": text}}
