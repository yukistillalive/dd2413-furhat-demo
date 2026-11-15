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

Useful options: `--gui` (Golem behaviour layer + debug panel on http://127.0.0.1:8090, see `gui/INTEGRATION.md`), `--no-vision` (no camera), `--scenario default|vision`, `--prompt "..."` (custom system prompt), `--model`, `--frame_interval`, `--debug`.

## Structure

```
python/
├── main.py          Entry point: connects to Furhat and wires the modules together
├── conversation/    Realtime Conversation: Furhat audio <-> Gemini Live websocket
├── vision/          Vision Perception: reads Furhat camera frames, passes them to listeners
├── scenario/        Scenario Setting: prompt -> WebSocket requests
├── gui/             Golem behaviour layer: face, expressions, idle, tools, debug panel
└── demos/           The original standalone examples from Furhat
```
