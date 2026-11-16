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

Useful options: `--no-vision` (no camera), `--scenario default|vision`, `--model`, `--frame_interval`, `--log-db conversations.db` (save the transcript to SQLite), `--debug`.

## Structure

```
python/
├── main.py          Entry point: connects to Furhat and wires the modules together
├── conversation/    Realtime Conversation: Furhat audio <-> Gemini Live websocket
├── store/           Conversation log: SQLite transcript of every session
├── vision/          Vision Perception: reads Furhat camera frames, passes them to listeners
├── scenario/        Scenario Setting: prompt -> WebSocket requests
├── gui/             GUI component
└── demos/           The original standalone examples from Furhat
```
