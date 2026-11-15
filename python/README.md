# Furhat Realtime API – Python backend

Install the Furhat Realtime API Client and all dependencies with pip:

```
pip install -r requirements.txt
```

Create a file called `.env` with your API key(s):

```
GEMINI_API_KEY=your_gemini_api_key_here
OPENAI_API_KEY=your_openai_api_key_here   # only for the OpenAI demos
```

## Running the backend

```
python main.py --host=192.168.0.52 --auth_key=mykey123
```

Useful options: `--no-vision` (no camera), `--scenario default|golem|vision`, `--golem-face FACE_ID`, `--prompt "..."` (custom system prompt), `--model`, `--frame_interval`, `--debug`.

Speech is Gemini text spoken by Furhat TTS (`request.speak.text`). The Gemini Live audio path is parked in `conversation/gemini_live_audio.py`.

## Scenario setting

`scenario/` turns the active behavior into a Gemini system prompt and into Furhat WebSocket requests for face, TTS voice, and LED. It does not synthesize speech.

Two behaviors:

| | `default` | `golem` |
|---|---|---|
| Prompt | Short, warm English chat | Slow, solemn, few words |
| Face | The face active at startup | A face whose id matches `golem`, `stone`, or `mask`; otherwise another installed face |
| TTS | The robot's original voice | Male, `en-US` |
| LED | Off | Slow amber pulse |

`--scenario vision` keeps the default body and adds a camera sentence. With the camera enabled, that same sentence is appended to whichever behavior is active.

Switch while `main.py` is running:

- Type `default` or `1`, or `golem` or `2`, in the terminal.
- Or say the whole utterance as exactly one of: `golem mode`, `switch to golem`, `be the golem`, `golem`, `normal mode`, `default mode`, `switch to normal`, `normal`, `default`. These phrases are passed to Furhat's recognizer. A longer sentence that only mentions the word does not switch.

`--golem-face FACE_ID` selects the Golem face directly. Startup prints the installed face ids.

## Structure

```
python/
├── main.py          Entry point: connects to Furhat and wires the modules together
├── conversation/    Gemini text reply -> Furhat TTS; Live audio code is commented out
├── vision/          Vision Perception: reads Furhat camera frames, passes them to listeners
├── scenario/        Scenario Setting: behavior -> prompt and Furhat face/voice/LED requests
├── gui/             GUI component
└── demos/           The original standalone examples from Furhat
```
