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

    def build_setup_message(self, model: str) -> dict:
        """First message of a Gemini Live session (model, output type, system prompt)."""
        return {
            "setup": {
                "model": "models/" + model,
                "generationConfig": {"responseModalities": ["AUDIO"]},
                "systemInstruction": {"parts": [{"text": self.scenario.system_instruction}]},
            }
        }

    def build_opening_message(self) -> dict:
        """Message that makes the robot speak first."""
        return self.build_text_message(self.scenario.opening_prompt)

    @staticmethod
    def build_text_message(text: str) -> dict:
        """A realtime text turn."""
        return {"realtimeInput": {"text": text}}
