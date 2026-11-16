/**
 * PRESENTER: the only place that knows both sides.
 *   - listens to the service and updates the MODEL
 *   - turns model state into a ready-to-render VIEW MODEL (formatting, filtering, labels)
 *   - handles the user's intents coming from the view (connect, clear, toggle events)
 * No React in here, so it can be tested with plain `node --test`.
 */
export class DebugPresenter {
  #vm;
  #listeners = new Set();
  #ticker = null;
  #unsubscribe = null;
  #client;

  constructor({ model, createClient, storage = null, defaultUrl, clock = () => Date.now(), tickMs = 1000 }) {
    this.model = model;
    this.storage = storage;
    this.clock = clock;
    this.tickMs = tickMs;
    this.ui = { url: this.#loadUrl(defaultUrl), showEvents: true };
    this.#client = createClient({
      onStatus: (s) => model.setConnection(s),
      onMessage: (m) => this.#onServerMessage(m),
    });
    this.#vm = this.#build();
  }

  // ---- lifecycle ----

  start() {
    if (this.#unsubscribe) return;
    this.#unsubscribe = this.model.subscribe(() => this.#refresh());
    this.#ticker = setInterval(() => this.#refresh(), this.tickMs);   // keeps "Ns ago" fresh
    this.#client.connect(this.ui.url);
  }

  stop() {
    this.#unsubscribe?.();
    this.#unsubscribe = null;
    clearInterval(this.#ticker);
    this.#client.close();
  }

  // ---- what the view subscribes to (React's useSyncExternalStore contract) ----

  subscribe = (listener) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };
  getSnapshot = () => this.#vm;

  // ---- user intents ----

  connect(url) {
    this.ui = { ...this.ui, url };
    try { this.storage?.setItem('debugUrl', url); } catch { /* storage may be unavailable */ }
    this.model.setConnection('connecting');
    this.#client.connect(url);
    this.#refresh();
  }

  clear() { this.model.clearItems(); }

  setShowEvents(showEvents) {
    this.ui = { ...this.ui, showEvents };
    this.#refresh();
  }

  // ---- server -> model ----

  #onServerMessage(m) {
    switch (m.type) {
      case 'hello': this.model.applyHello(m); break;
      case 'frame': this.model.addFrame(m.image, this.clock()); break;
      case 'state': this.model.setRobotState(m.state); break;
      case 'message':
      case 'event': this.model.addItem(m); break;
      default: break;
    }
  }

  // ---- model -> view model ----

  #loadUrl(defaultUrl) {
    try { return this.storage?.getItem('debugUrl') || defaultUrl; } catch { return defaultUrl; }
  }

  #refresh() {
    this.#vm = this.#build();
    this.#listeners.forEach((l) => l());
  }

  #build() {
    const s = this.model.getState();
    const { url, showEvents } = this.ui;
    const age = s.lastFrameAt === null ? null : Math.max(0, Math.round((this.clock() - s.lastFrameAt) / 1000));
    const items = s.items
      .filter((i) => i.type === 'message' || showEvents)
      .map((i) => this.#formatItem(i));
    return {
      url,
      showEvents,
      connection: s.connection,
      robotState: s.robotState,
      video: {
        src: s.frame ? `data:image/jpeg;base64,${s.frame}` : null,
        count: s.frameCount,
        ageLabel: age === null ? '—' : `${age}s ago`,
      },
      items,
    };
  }

  #formatItem(i) {
    const time = i.ts ? new Date(i.ts).toLocaleTimeString() : '';
    if (i.type === 'message') {
      return {
        kind: 'message', key: i.id, role: i.role, text: i.text,
        meta: `${i.role} · ${time}${i.interrupted ? ' · interrupted' : ''}`,
      };
    }
    return { kind: 'event', key: i.id, text: `${time} · ${i.name}${i.detail ? ` — ${i.detail}` : ''}`, name: i.name };
  }
}
