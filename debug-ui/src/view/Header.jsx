import React, { useState } from 'react';

export function Header({ url, connection, robotState, onConnect }) {
  const [draft, setDraft] = useState(url);
  return (
    <header>
      <h1>Furhat debug</h1>
      <form onSubmit={(e) => { e.preventDefault(); onConnect(draft); }}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Debug hub URL" />
        <button type="submit">Connect</button>
      </form>
      <span className={`pill ${connection}`}>{connection}</span>
      <span className={`pill state-${robotState}`}>{robotState}</span>
    </header>
  );
}
