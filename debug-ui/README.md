# Furhat debug UI

React + Vite frontend showing the robot's camera feed and the live conversation
(transcript, events, robot state). For debugging only.

```
# 1. backend, with the debug stream on (localhost only)
cd python && python main.py --host=<ip> --auth_key=<key> --debug-ui

# 2. frontend
cd debug-ui && npm install && npm run dev
```

The page connects to `ws://127.0.0.1:8765` (change it in the header box; the
backend port is `--debug-ui-port`). It reconnects automatically and replays
recent history when you reload.

## Architecture (Model–View–Presenter)

```
src/
├── model/DebugModel.js            data + rules (frame, transcript, states); no React, no sockets
├── services/DebugSocketClient.js  WebSocket + auto-reconnect; knows nothing about the model/UI
├── presenter/DebugPresenter.js    service -> model; model -> view model (formatting, filtering);
│                                  handles user intents (connect, clear, toggle events)
├── view/                          passive React components; render the view model, call the presenter
│   ├── App.jsx  Header.jsx  VideoPanel.jsx  ConversationLog.jsx
│   └── usePresenter.js            React binding (useSyncExternalStore)
└── main.jsx                       composition root: wires the layers together
```

`npm test` runs the presenter tests (plain Node, no browser needed).
