/**
 * MODEL: the debug session's data and the rules for changing it.
 * Plain JS: no React, no WebSocket, no formatting. State is replaced (never
 * mutated) on every change, and subscribers are told after each change.
 */
export class DebugModel {
  #state;
  #listeners = new Set();
  #nextId = 1;

  constructor({ maxItems = 1000 } = {}) {
    this.maxItems = maxItems;
    this.#state = {
      connection: 'connecting',   // 'connecting' | 'connected' | 'disconnected'
      robotState: 'unknown',      // 'listening' | 'speaking' | 'reconnecting' | 'stopped' | ...
      frame: null,                // base64 JPEG or null
      frameCount: 0,
      lastFrameAt: null,          // ms timestamp
      items: [],                  // {id, type:'message'|'event', ...} in arrival order
    };
  }

  getState() { return this.#state; }

  subscribe(listener) {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #update(patch) {
    this.#state = { ...this.#state, ...patch };
    this.#listeners.forEach((l) => l());
  }

  #withId(item) { return { ...item, id: this.#nextId++ }; }

  setConnection(connection) { this.#update({ connection }); }
  setRobotState(robotState) { this.#update({ robotState }); }

  /** Initial snapshot the backend sends on connect (replaces what we had). */
  applyHello({ state, history = [], frame = null }) {
    this.#update({
      robotState: state ?? 'unknown',
      frame,
      items: history.map((h) => this.#withId(h)),
    });
  }

  addFrame(image, now) {
    this.#update({ frame: image, frameCount: this.#state.frameCount + 1, lastFrameAt: now });
  }

  addItem(item) {
    const items = [...this.#state.items, this.#withId(item)].slice(-this.maxItems);
    this.#update({ items });
  }

  clearItems() { this.#update({ items: [] }); }
}
