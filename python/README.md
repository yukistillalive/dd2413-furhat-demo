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

## Vision prototype (independent debugger)

From the repository root, run `.venv/bin/python python/vision_debug.py --simulate`
and open http://127.0.0.1:8765. This uses simulated data; it does not start the conversation.
For a robot, use `--host ROBOT_IP` and optionally `--camera`. Distance classification
is disabled until the actual units are confirmed and `--distance-unit m` or `cm` is supplied.
Install `requirements-vision.txt` for this debugger, or `requirements.txt`
for the full backend.

## Running the conversation backend

```
python main.py --host=192.168.0.52 --auth_key=mykey123
```

Useful options: `--no-vision` (no camera), `--scenario default|vision`, `--prompt "..."` (custom system prompt), `--model`, `--frame_interval`, `--debug`.

## Structure

```
python/
├── main.py          Entry point: connects to Furhat and wires the modules together
├── conversation/    Realtime Conversation: Furhat audio <-> Gemini Live websocket
├── vision/          Vision Perception: reads Furhat camera frames, passes them to listeners
├── scenario/        Scenario Setting: prompt -> WebSocket requests
├── gui/             GUI component
└── demos/           The original standalone examples from Furhat
```
