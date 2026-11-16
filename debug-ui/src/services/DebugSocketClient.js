/**
 * SERVICE: talks to the Python debug hub. Knows the WebSocket and the
 * reconnect policy, nothing about the model or the UI.
 *
 *   const client = new DebugSocketClient({ onStatus, onMessage });
 *   client.connect('ws://127.0.0.1:8765');   // reconnects until close()
 */
export class DebugSocketClient {
  #ws = null;
  #timer = null;
  #url = null;
  #closed = true;

  constructor({ onStatus, onMessage, retryMs = 2000, WebSocketImpl = globalThis.WebSocket }) {
    this.onStatus = onStatus;
    this.onMessage = onMessage;
    this.retryMs = retryMs;
    this.WebSocketImpl = WebSocketImpl;
  }

  connect(url) {
    this.close();
    this.#url = url;
    this.#closed = false;
    this.#open();
  }

  close() {
    this.#closed = true;
    clearTimeout(this.#timer);
    if (this.#ws) {
      this.#ws.onclose = null;
      this.#ws.close();
      this.#ws = null;
    }
  }

  #open() {
    this.onStatus('connecting');
    const ws = new this.WebSocketImpl(this.#url);
    this.#ws = ws;
    ws.onopen = () => this.onStatus('connected');
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      this.onStatus('disconnected');
      if (!this.#closed) this.#timer = setTimeout(() => this.#open(), this.retryMs);
    };
    ws.onmessage = (e) => {
      try {
        this.onMessage(JSON.parse(e.data));
      } catch (err) {
        console.warn('Bad message from debug hub', err);
      }
    };
  }
}
