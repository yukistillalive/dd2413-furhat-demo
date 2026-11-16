import React, { useEffect, useRef } from 'react';

export function ConversationLog({ items, showEvents, onToggleEvents, onClear }) {
  const body = useRef(null);
  const stick = useRef(true);   // auto-scroll only while the user is at the bottom

  useEffect(() => {
    const el = body.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items]);

  return (
    <section className="log">
      <div className="log-head">
        <h2>Conversation</h2>
        <label>
          <input type="checkbox" checked={showEvents} onChange={(e) => onToggleEvents(e.target.checked)} /> events
        </label>
        <button onClick={onClear}>Clear</button>
      </div>
      <div
        className="log-body"
        ref={body}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {items.length === 0 && <p className="muted">Waiting for the conversation…</p>}
        {items.map((i) =>
          i.kind === 'message' ? (
            <div key={i.key} className={`msg ${i.role}`}>
              <div className="meta">{i.meta}</div>
              <div className="bubble">{i.text}</div>
            </div>
          ) : (
            <div key={i.key} className="event">{i.text}</div>
          )
        )}
      </div>
    </section>
  );
}
