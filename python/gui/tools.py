"""Gemini Live function calling -> Golem behaviour."""
from .expressions import LABELS, perform
from .golem import Golem

# Goes into the Gemini setup message as: "tools": [{"functionDeclarations": TOOL_DECLARATIONS}]
TOOL_DECLARATIONS = [
    {
        "name": "set_expression",
        "description": "Show an emotion on the Golem's face and LED. Call it when your mood changes.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"emotion": {"type": "STRING", "enum": LABELS}},
            "required": ["emotion"],
        },
    },
    {"name": "nod", "description": "Nod once, e.g. to agree or acknowledge the visitor."},
    {"name": "gaze_away", "description": "Briefly look away, e.g. while recalling something old."},
]


class GolemTools:
    def __init__(self, golem: Golem):
        self.golem = golem
        self.face = golem.face

    async def handle_tool_call(self, name: str, args: dict) -> dict:
        """Run one tool call. Never raises: errors are returned to the model as a result."""
        try:
            if not self.golem.awake:
                return {"error": "The Golem is dormant."}
            if name == "set_expression":
                return {"result": "ok", "emotion": await perform(self.face, args.get("emotion", ""))}
            if name == "nod":
                await self.face.nod()
                return {"result": "ok"}
            if name == "gaze_away":
                await self.face.gaze_away()
                return {"result": "ok"}
            return {"error": f"Unknown tool: {name}"}
        except Exception as e:
            return {"error": str(e)}

    async def respond(self, tool_call: dict) -> dict:
        """Turn a Gemini "toolCall" payload into the "toolResponse" message to send back."""
        responses = []
        for call in tool_call.get("functionCalls", []):
            result = await self.handle_tool_call(call.get("name", ""), call.get("args") or {})
            responses.append({"id": call.get("id"), "name": call.get("name"), "response": result})
        return {"toolResponse": {"functionResponses": responses}}
