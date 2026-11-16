import test from 'node:test';
import assert from 'node:assert/strict';
import { DebugModel } from '../model/DebugModel.js';
import { DebugPresenter } from './DebugPresenter.js';

function setup() {
  let handlers; const calls = [];
  let now = 10_000;
  const presenter = new DebugPresenter({
    model: new DebugModel(),
    createClient: (h) => { handlers = h; return { connect: (u) => calls.push(['connect', u]), close: () => calls.push(['close']) }; },
    defaultUrl: 'ws://x:1', clock: () => now, tickMs: 1e9,
  });
  presenter.start();
  return { presenter, handlers, calls, advance: (ms) => { now += ms; } };
}

test('connects to the default url on start', () => {
  const { presenter, calls } = setup();
  assert.deepEqual(calls[0], ['connect', 'ws://x:1']);
  presenter.stop();
});

test('messages become formatted items; events can be hidden', () => {
  const { presenter, handlers } = setup();
  handlers.onMessage({ type: 'message', role: 'user', text: 'hi', interrupted: false, ts: '2026-01-01T10:00:00Z' });
  handlers.onMessage({ type: 'event', name: 'reconnect', detail: 'x' });
  assert.equal(presenter.getSnapshot().items.length, 2);
  assert.match(presenter.getSnapshot().items[0].meta, /^user · /);
  presenter.setShowEvents(false);
  assert.deepEqual(presenter.getSnapshot().items.map((i) => i.kind), ['message']);
  presenter.stop();
});

test('frames give a data url, a counter and an age label', () => {
  const { presenter, handlers, advance } = setup();
  assert.equal(presenter.getSnapshot().video.ageLabel, '—');
  handlers.onMessage({ type: 'frame', image: 'AAAA' });
  advance(3000);
  presenter.setShowEvents(true);   // any refresh recomputes the age
  const v = presenter.getSnapshot().video;
  assert.equal(v.src, 'data:image/jpeg;base64,AAAA');
  assert.equal(v.count, 1);
  assert.equal(v.ageLabel, '3s ago');
  presenter.stop();
});

test('hello replaces history; state and connection are reflected; clear works', () => {
  const { presenter, handlers } = setup();
  handlers.onStatus('connected');
  handlers.onMessage({ type: 'hello', state: 'listening', history: [{ type: 'message', role: 'robot', text: 'yo' }], frame: null });
  let vm = presenter.getSnapshot();
  assert.equal(vm.connection, 'connected');
  assert.equal(vm.robotState, 'listening');
  assert.equal(vm.items.length, 1);
  presenter.clear();
  assert.equal(presenter.getSnapshot().items.length, 0);
  presenter.stop();
});

test('connect(url) reconnects with the new url', () => {
  const { presenter, calls } = setup();
  presenter.connect('ws://y:2');
  assert.deepEqual(calls.at(-1), ['connect', 'ws://y:2']);
  assert.equal(presenter.getSnapshot().url, 'ws://y:2');
  presenter.stop();
});
