"""SQLite log of what the robot perceived and did (text and numbers only: no audio, no images).

The unit of analysis is a VISIT: one visitor's perception-action loop, from the
moment they are confirmed approaching (person_entered) until the Golem goes back
to sleep after they left. A visit that returns inside the 5 s same-visitor grace
window stays one visit.

    sessions     one per program run (model, scenario, flags)
    visits       one per visitor: started_at, ended_at, end_reason, reached (DORMANT/SUMMONING/AWAKE)
    perception   distance, zone, user count, tracking, robot state; only while a visit is open
    transitions  DORMANT/SUMMONING/AWAKE changes with their reason
    messages     what was said: user (asr) and robot (opening, reply, filler, summon, ...)
    events       technical events: skips, rate limits, errors, silence prompts, emotions, ...

Rows written during a visit carry visit_id and visit_t (seconds since the visit
started), so "how long after arrival did the robot speak" is a subtraction.

Example queries:
    -- one visit as a timeline
    SELECT visit_t, 'said' AS what, role || ': ' || text FROM messages WHERE visit_id = 3
    UNION ALL SELECT visit_t, 'moved', from_state || ' -> ' || to_state || ' (' || reason || ')'
        FROM transitions WHERE visit_id = 3 ORDER BY 1;
    -- distance over time
    SELECT visit_t, distance_m, zone, robot_state FROM perception WHERE visit_id = 3;
    -- how many visits reached a conversation
    SELECT reached, COUNT(*) FROM visits GROUP BY reached;
    -- response times
    SELECT AVG(latency_ms), MAX(latency_ms) FROM messages WHERE source = 'reply';

All writes go through one worker thread, in the order they are called, so they never
block the conversation loop and never overlap. The log contains transcripts of real
people: keep the .db file out of git and tell participants they are being recorded.
"""
import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# perception rows: a changed value is written at once, an unchanged one at most this often
PERCEPTION_MIN_INTERVAL_S = 1.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    model TEXT,
    scenario TEXT,
    system_prompt TEXT
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id),
    user_id TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    scenario TEXT,
    reached TEXT NOT NULL DEFAULT 'DORMANT'
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    visit_id INTEGER REFERENCES visits(id),
    visit_t REAL,
    ts TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'robot')),
    text TEXT NOT NULL,
    source TEXT,
    mode TEXT,
    emotion TEXT,
    latency_ms INTEGER,
    raw_text TEXT,
    interrupted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    visit_id INTEGER REFERENCES visits(id),
    visit_t REAL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS perception (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    visit_id INTEGER NOT NULL REFERENCES visits(id),
    visit_t REAL NOT NULL,
    ts TEXT NOT NULL,
    status TEXT,
    tracking TEXT,
    user_count INTEGER,
    target_user_id TEXT,
    target_visible INTEGER,
    distance_raw REAL,
    distance_m REAL,
    smoothed_distance_m REAL,
    zone TEXT,
    data_age_s REAL,
    robot_state TEXT
);
CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    visit_id INTEGER REFERENCES visits(id),
    visit_t REAL,
    ts TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    reason TEXT
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_messages_visit ON messages(visit_id, id);
CREATE INDEX IF NOT EXISTS idx_events_visit ON events(visit_id, id);
CREATE INDEX IF NOT EXISTS idx_perception_visit ON perception(visit_id, id);
CREATE INDEX IF NOT EXISTS idx_transitions_visit ON transitions(visit_id, id);
"""

# Columns added after the first version: older .db files get them via ALTER TABLE.
ADDED_COLUMNS = {
    "messages": {"visit_id": "INTEGER", "visit_t": "REAL", "source": "TEXT", "mode": "TEXT",
                 "emotion": "TEXT", "latency_ms": "INTEGER", "raw_text": "TEXT"},
    "events": {"visit_id": "INTEGER", "visit_t": "REAL"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ConversationLog:
    def __init__(self, path: str = "conversations.db", clock=time.monotonic):
        self.path = path
        self.clock = clock
        self.session_id: int | None = None
        self.visit_id: int | None = None
        self._visit_t0: float = 0.0
        self._perception_key = None
        self._perception_at: float = 0.0
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="convlog")
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._migrate()
        self._db.executescript(INDEXES)
        self._db.commit()
        # Visit ids are chosen here, not by SQLite, so start_visit() can be called from
        # synchronous code and every later write already knows the id.
        self._next_visit_id = self._db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM visits").fetchone()[0]

    def _migrate(self):
        for table, columns in ADDED_COLUMNS.items():
            have = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
            for name, kind in columns.items():
                if name not in have:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    # ---- plumbing ----

    def _run(self, sql: str, params: tuple = ()) -> int:
        cur = self._db.execute(sql, params)
        self._db.commit()
        return cur.lastrowid

    def _submit(self, sql: str, params: tuple = ()):
        """Queue a write without waiting. Writes run in the order they were queued."""
        return self._pool.submit(self._run, sql, params)

    async def _exec(self, sql: str, params: tuple = ()) -> int:
        return await asyncio.wrap_future(self._submit(sql, params))

    def _visit_t(self) -> float | None:
        return round(self.clock() - self._visit_t0, 3) if self.visit_id is not None else None

    # ---- program run ----

    async def start_session(self, model: str, scenario: str, system_prompt: str) -> int:
        self.session_id = await self._exec(
            "INSERT INTO sessions(started_at, model, scenario, system_prompt) VALUES (?,?,?,?)",
            (_now(), model, scenario, system_prompt))
        return self.session_id

    async def end_session(self):
        self.end_visit("shutdown")
        if self.session_id is not None:
            await self._exec("UPDATE sessions SET ended_at=? WHERE id=?", (_now(), self.session_id))

    # ---- visits (synchronous: safe to call from the perception callback) ----

    def start_visit(self, user_id: str | None = None, scenario: str | None = None) -> int:
        """A visitor was confirmed approaching. Everything logged from now on belongs to them."""
        if self.visit_id is not None:
            self.end_visit("replaced")
        self.visit_id = self._next_visit_id
        self._next_visit_id += 1
        self._visit_t0 = self.clock()
        self._perception_key = None
        self._submit(
            "INSERT INTO visits(id, session_id, user_id, started_at, scenario) VALUES (?,?,?,?,?)",
            (self.visit_id, self.session_id, user_id, _now(), scenario))
        return self.visit_id

    def end_visit(self, reason: str):
        """The visitor is gone and the robot went back to sleep (or the program stopped)."""
        if self.visit_id is None:
            return
        self._submit("UPDATE visits SET ended_at=?, end_reason=? WHERE id=?",
                     (_now(), reason, self.visit_id))
        self.visit_id = None

    def log_transition(self, from_state: str, to_state: str, reason: str = ""):
        self._submit(
            "INSERT INTO transitions(session_id, visit_id, visit_t, ts, from_state, to_state, reason)"
            " VALUES (?,?,?,?,?,?,?)",
            (self.session_id, self.visit_id, self._visit_t(), _now(), from_state, to_state, reason))
        if self.visit_id is not None:
            # 'reached' = the furthest state of the visit, so visits can be counted by outcome
            if to_state == "AWAKE":
                self._submit("UPDATE visits SET reached='AWAKE' WHERE id=?", (self.visit_id,))
            elif to_state == "SUMMONING":
                self._submit("UPDATE visits SET reached='SUMMONING' WHERE id=? AND reached='DORMANT'",
                             (self.visit_id,))

    def log_perception(self, state: dict, robot_state: str | None = None):
        """One perception snapshot. Only inside a visit; unchanged values are thinned to 1 per second."""
        if self.visit_id is None:
            return
        key = (state.get("status"), state.get("tracking"), state.get("user_count"),
               state.get("target_user_id"), state.get("target_visible"), state.get("zone"), robot_state)
        now = self.clock()
        if key == self._perception_key and now - self._perception_at < PERCEPTION_MIN_INTERVAL_S:
            return
        self._perception_key, self._perception_at = key, now
        visible = state.get("target_visible")
        self._submit(
            "INSERT INTO perception(session_id, visit_id, visit_t, ts, status, tracking, user_count,"
            " target_user_id, target_visible, distance_raw, distance_m, smoothed_distance_m, zone,"
            " data_age_s, robot_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.session_id, self.visit_id, self._visit_t(), _now(), state.get("status"),
             state.get("tracking"), state.get("user_count"), state.get("target_user_id"),
             None if visible is None else int(visible), state.get("distance_raw"),
             state.get("distance_m"), state.get("smoothed_distance_m"), state.get("zone"),
             state.get("data_age_s"), robot_state))

    # ---- conversation ----

    async def log_message(self, role: str, text: str, interrupted: bool = False, *,
                          source: str | None = None, mode: str | None = None,
                          emotion: str | None = None, latency_ms: int | None = None,
                          raw_text: str | None = None):
        """source: asr | opening | reply | cue | filler | silence_prompt | summon | system.
        latency_ms: from the end of the visitor's speech until the robot starts to answer."""
        text = text.strip()
        if not text or self.session_id is None:
            return
        await self._exec(
            "INSERT INTO messages(session_id, visit_id, visit_t, ts, role, text, source, mode,"
            " emotion, latency_ms, raw_text, interrupted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.session_id, self.visit_id, self._visit_t(), _now(), role, text, source, mode,
             emotion, latency_ms, raw_text, int(interrupted)))

    async def log_event(self, type_: str, detail: str = ""):
        if self.session_id is None:
            return
        await self._exec(
            "INSERT INTO events(session_id, visit_id, visit_t, ts, type, detail) VALUES (?,?,?,?,?,?)",
            (self.session_id, self.visit_id, self._visit_t(), _now(), type_, detail))

    def close(self):
        self.end_visit("shutdown")
        self._pool.shutdown(wait=True)
        self._db.close()
