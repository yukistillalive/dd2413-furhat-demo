# Furhat Realtime API – Python backend

Install the Furhat Realtime API Client and all dependencies with pip:

```
pip install -r requirements.txt
```

Create a file called `.env` with your API key(s):

```
GEMINI_API_KEY=your_gemini_api_key_here
FURHAT_HOST=192.168.1.114
OPENAI_API_KEY=your_openai_api_key_here   # only for the OpenAI demos
```

`python/.env` is gitignored. `main.py` reads `FURHAT_HOST` from that file, so the host flag can be omitted.

## Running the backend

```
python main.py --host=192.168.0.52 --auth_key=mykey123
```

Useful options: `--no-vision` (no camera), `--scenario default|golem|vision`, `--golem-face FACE_ID`, `--prompt "..."` (custom system prompt), `--model` (default `gemini-3.8-flash-lite`), `--frame_interval`, `--log-db conversations.db`, `--debug-ui`, `--debug`.

Speech is Gemini text spoken by Furhat TTS (`request.speak.text`). The Gemini Live audio path is parked in `conversation/gemini_live_audio.py`.

## Scenario setting

`scenario/` turns the active behavior into a Gemini system prompt and into Furhat WebSocket requests for face, TTS voice, and LED. It does not synthesize speech.

Two behaviors:

| | `default` | `golem` |
|---|---|---|
| Prompt | Short, warm English chat | Slow, solemn, few words |
| Face | The face active at startup | The same face, unless `--golem-face FACE_ID` is set |
| TTS | The robot's original voice | Male, `en-US` |
| LED | Off | Breathing brown from `#D9B270`, dimmed so the ring stays warm |

`--scenario vision` keeps the default body and adds a camera sentence. With the camera enabled, that same sentence is appended to whichever behavior is active.

Switch while `main.py` is running:

- Type `default` or `1`, or `golem` or `2`, in the terminal.
- Or say the whole utterance as exactly one of: `golem mode`, `switch to golem`, `be the golem`, `golem`, `normal mode`, `default mode`, `switch to normal`, `normal`, `default`. These phrases are passed to Furhat's recognizer. A longer sentence that only mentions the word does not switch.

`--golem-face FACE_ID` gives Golem its own installed face. Without it, Golem keeps the startup face. The program does not pick a different face on its own. Startup prints the installed face ids.

## Conversation log

`--log-db conversations.db` stores the transcript in SQLite. Without that flag, nothing is written. The file has three tables: `sessions` (model, behavior, system prompt), `messages` (`user` and `robot` text), and `events` (behavior changes, skipped fragments, rate limits, Gemini errors). Audio and camera frames are not stored. `*.db` is gitignored.

## Debug UI

`--debug-ui` opens `ws://127.0.0.1:8765` and streams the transcript, behavior events, and camera frames. The page is separate:

```
cd debug-ui && npm install && npm run dev
```

The text path still speaks with Furhat TTS. The page only watches that conversation.
Useful options: `--gui` (Golem behaviour layer + debug panel on http://127.0.0.1:8090, see `gui/INTEGRATION.md`), `--no-vision` (no camera), `--scenario default|vision`, `--prompt "..."` (custom system prompt), `--model`, `--frame_interval`, `--debug`.

## Structure

```
python/
├── main.py          Entry point: connects to Furhat and wires the modules together
├── conversation/    Gemini text reply -> Furhat TTS; Live audio code is commented out
├── store/           SQLite transcript used when --log-db is set
├── debug_hub/       Local WebSocket feed for ../debug-ui
├── vision/          Vision Perception: reads Furhat camera frames, passes them to listeners
├── scenario/        Scenario Setting: prompt -> WebSocket requests
├── gui/             Golem behaviour layer: face, expressions, idle, tools, debug panel
└── demos/           The original standalone examples from Furhat
```
