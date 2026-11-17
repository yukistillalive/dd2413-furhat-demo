# Integrating the Golem behaviour layer

The `gui` package is the "Act" part: it turns model output into face, gaze and LED
behaviour. `main.py --gui` creates a `GolemGUI` object (`gui`) that holds:

| attribute   | what it is |
|-------------|------------|
| `gui.tools` | `GolemTools`: `handle_tool_call(name, args)` and `respond(tool_call)` |
| `gui.idle`  | `IdleBehaviour`: turn-taking hooks |
| `gui.golem` | `Golem`: `awaken()`, `sleep()`, `state` |

Nothing below is wired in yet: `conversation/` and `scenario/` are owned by the
other group members.

## 1. Scenario owner (`scenario/scenario_module.py`)

Add the tool declarations to the setup message in `build_setup_message`:

```python
from gui.tools import TOOL_DECLARATIONS

"setup": {
    "model": "models/" + model,
    "generationConfig": {"responseModalities": ["AUDIO"]},
    "systemInstruction": {"parts": [{"text": self.scenario.system_instruction}]},
    "tools": [{"functionDeclarations": TOOL_DECLARATIONS}],   # <- new
}
```

The system instruction should also tell the model to use the tools, for example:
*"You are an ancient Golem. Use set_expression when your mood changes, nod to
agree, and gaze_away when recalling something."*

## 2. Conversation owner (`conversation/realtime_conversation.py`)

1. Accept the GUI object: `def __init__(..., gui=None)` and store `self.gui = gui`.
2. In `run()`, forward tool calls and send the answer back:

   ```python
   elif "toolCall" in data:
       if self.gui:
           await self.ws.send(json.dumps(await self.gui.tools.respond(data["toolCall"])))
   ```

   `respond()` turns `{"functionCalls": [{"id", "name", "args"}]}` into
   `{"toolResponse": {"functionResponses": [{"id", "name", "response"}]}}`.
   It never raises: errors come back as `{"error": "..."}` so the model can carry on.
   Every call must be answered, or Gemini waits before it continues speaking.

3. Optional turn-taking hooks (all of them are async and safe to call twice):

   | where | call |
   |-------|------|
   | `furhat_speak_end` (the user's turn starts) | `await self.gui.idle.on_user_turn_start()` |
   | the user stops talking, e.g. `serverContent.inputTranscription` finished, or a `toolCall` arrives | `await self.gui.idle.on_waiting_for_model()` |
   | first audio chunk in `response_audio_delta` | `await self.gui.idle.on_robot_speak_start()` |

   `on_robot_speak_start` and `on_robot_speak_end` are already triggered by Furhat's
   events. Calling them yourself just makes the switch happen sooner. Note (tested on
   the virtual Furhat): streamed audio (`request_speak_audio_start/data/end`) sends
   **no** `response.speak.start`, only `response.speak.audio.buffer` while playing and
   `response.speak.end` at the end. The gui layer listens to `audio.buffer` for that reason.
   `request_speak_text` only sends speak events when called with `wait=True`.

## 3. `main.py` (once step 2 is done)

```python
conversation = RealtimeConversation(furhat, scenario, model=args.model, debug=args.debug, gui=gui)
```

## Rule for anyone sending face parameters

While the robot speaks, lipsync controls the mouth. Never send `MOUTH_*` or `JAW_*`
params during speech. `GolemFace.smile()` already follows this rule.

## LED owner

While the active behavior has an LED pattern (golem), `ScenarioManager` owns the LED and
`GolemFace.set_led()` does nothing (`main.py` sets `face.led_allowed`). See
`interaction/README.md` for how sleep/awake change the light in that case.
