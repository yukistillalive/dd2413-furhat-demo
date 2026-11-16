// Composition root: the only file that knows every layer and wires them together.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { DebugModel } from './model/DebugModel.js';
import { DebugSocketClient } from './services/DebugSocketClient.js';
import { DebugPresenter } from './presenter/DebugPresenter.js';
import App from './view/App.jsx';
import './styles.css';

const presenter = new DebugPresenter({
  model: new DebugModel(),
  createClient: (handlers) => new DebugSocketClient(handlers),
  storage: window.localStorage,
  // Must match the backend's --debug-ui-port (default 8765). Override: VITE_DEBUG_HUB_URL=ws://... npm run dev
  defaultUrl: import.meta.env.VITE_DEBUG_HUB_URL ?? 'ws://127.0.0.1:8765',
});

createRoot(document.getElementById('root')).render(<App presenter={presenter} />);
