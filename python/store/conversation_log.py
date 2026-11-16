"""SQLite conversation log (text only: no audio or video is stored).

Tables:
    sessions(id, started_at, ended_at, model, scenario, system_prompt)
    messages(id, session_id, ts, role, text, interrupted)   role: 'user' | 'robot'
    events(id, session_id, ts, type, detail)                reconnects, goAway, errors

All writes go through one worker thread, so they never block the audio loop
and never overlap. The log contains transcripts of real people: keep the .db
file out of git and tell participants they are being recorded.
"""
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    model TEXT,
    scenario TEXT,
    system_prompt TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'robot')),
    text TEXT NOT NULL,
    interrupted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ConversationLog:
    def __init__(self, path: str = "conversations.db"):
        self.path = path
        self.session_id: int | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="convlog")
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _run(self, sql: str, params: tuple = ()) -> int:
        cur = self._db.execute(sql, params)
        self._db.commit()
        return cur.lastrowid

    async def _exec(self, sql: str, params: tuple = ()) -> int:
        return await asyncio.get_running_loop().run_in_executor(self._pool, self._run, sql, params)

    async def start_session(self, model: str, scenario: str, system_prompt: str) -> int:
        self.session_id = await self._exec(
            "INSERT INTO sessions(started_at, model, scenario, system_prompt) VALUES (?,?,?,?)",
            (_now(), model, scenario, system_prompt))
        return self.session_id

    async def log_message(self, role: str, text: str, interrupted: bool = False):
        text = text.strip()
        if not text or self.session_id is None:
            return
        await self._exec(
            "INSERT INTO messages(session_id, ts, role, text, interrupted) VALUES (?,?,?,?,?)",
            (self.session_id, _now(), role, text, int(interrupted)))

    async def log_event(self, type_: str, detail: str = ""):
        if self.session_id is None:
            return
        await self._exec(
            "INSERT INTO events(session_id, ts, type, detail) VALUES (?,?,?,?)",
            (self.session_id, _now(), type_, detail))

    async def end_session(self):
        if self.session_id is not None:
            await self._exec("UPDATE sessions SET ended_at=? WHERE id=?", (_now(), self.session_id))

    def close(self):
        self._pool.shutdown(wait=True)
        self._db.close()
