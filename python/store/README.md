# store: conversation and perception data logging

run the command

```
python main.py --host <ip> --auth_key <key> --gui --interaction --log-db conversations.db
```

## The unit: a visit

A **visit** is one visitor's perception-action loop. It starts when the controller
confirms a person approaching (`person_entered`) and ends when the Golem goes back to
sleep after they left. Everything logged in between carries the `visit_id`.

- Someone who comes back within the 5 s same-visitor window stays the same visit.
- Someone detected who leaves before the Golem reacts is a short visit (`passed_by`).
- `visit_t` on every row is the number of seconds since the visit started, so "how long
  after arrival did the robot speak" is a subtraction.

Visits are only opened by the interaction controller (`--interaction`). Without it, no
visits exist: messages and events are still logged, with `visit_id` empty.

## Tables

| Table | One row per | Main columns |
|---|---|---|
| `sessions` | program run | `started_at`, `ended_at`, `model`, `scenario`, `system_prompt` |
| `visits` | visitor | `user_id`, `started_at`, `ended_at`, `end_reason` (`left`, `passed_by`, `shutdown`), `reached` (`DORMANT`, `SUMMONING` or `AWAKE`, the furthest state) |
| `perception` | perception snapshot, only inside a visit | `distance_m`, `distance_raw`, `smoothed_distance_m`, `zone`, `user_count`, `tracking`, `target_visible`, `robot_state`, `data_age_s` |
| `transitions` | state change | `from_state`, `to_state`, `reason` |
| `messages` | thing said | `role` (`user`/`robot`), `text`, `source`, `mode`, `emotion`, `latency_ms`, `raw_text` |
| `events` | technical event | `type`, `detail` (skips, rate limits, errors, silence prompts, emotions, filler, ...) |

All tables have `session_id`, `ts` (UTC, milliseconds). `messages`, `events`,
`perception` and `transitions` also have `visit_id` and `visit_t`.

`messages.source` says where a line came from: `asr` (what the visitor said), `opening`,
`reply`, `cue` (the line after a mode switch), `filler` ("Hmm..."), `silence_prompt`,
`summon` (DORMANT/SUMMONING lines from the controller), `system`.

`messages.latency_ms` is set on `reply` lines only: the time from the end of the
visitor's speech until the robot starts to answer.

`perception` rows are written at once when something changes (zone, tracking, user count,
target, robot state) and otherwise at most once per second
(`PERCEPTION_MIN_INTERVAL_S`). The distance itself is not part of the change check, so a
person walking inside one zone is sampled at 1 Hz.

## Example queries

```sql
-- one visit as a timeline
SELECT visit_t, 'said' AS what, role || ': ' || text FROM messages WHERE visit_id = 3
UNION ALL
SELECT visit_t, 'state', from_state || ' -> ' || to_state || ' (' || reason || ')'
  FROM transitions WHERE visit_id = 3
ORDER BY 1;

-- distance over a visit
SELECT visit_t, distance_m, zone, robot_state FROM perception WHERE visit_id = 3;

-- how many visits reached a conversation?
SELECT reached, COUNT(*) FROM visits GROUP BY reached;

-- how long did visitors stay?
SELECT id, ROUND((julianday(ended_at) - julianday(started_at)) * 86400) AS seconds
  FROM visits WHERE ended_at IS NOT NULL;

-- response times
SELECT COUNT(*), AVG(latency_ms), MAX(latency_ms) FROM messages WHERE source = 'reply';

-- what went wrong
SELECT visit_id, visit_t, type, detail FROM events
 WHERE type IN ('gemini_error', 'rate_limit', 'tts_failed', 'appearance', 'skipped');
```

Open it with `sqlite3 conversations.db` or any SQLite viewer.

## Using it from code

```python
log = ConversationLog("conversations.db")
await log.start_session(model, scenario, system_prompt)   # once per run

log.start_visit(user_id, scenario)                        # sync: safe in perception callbacks
log.log_perception(snapshot, robot_state)                 # sync, thinned
log.log_transition("DORMANT", "AWAKE", "walked straight in")
await log.log_message("robot", text, source="reply", mode="golem", emotion="warm", latency_ms=900)
await log.log_event("silence", "prompt")
log.end_visit("left")

await log.end_session()
log.close()
```

- All writes happen on one worker thread, in the order they are called, so they never
  block the conversation loop. `start_visit`, `end_visit`, `log_perception` and
  `log_transition` are plain functions that queue the write and return at once.
  `log_message`, `log_event`, `start_session` and `end_session` are `async`: awaiting
  them waits until the row is written.
- Writes before `start_session()` are ignored (messages and events need a session).
- An existing `.db` from an older version is migrated automatically (new columns are added).

## Test database

```
cd python
python -m store.make_test_db            # creates store/testdata/test_conversations.db
python -m store.make_test_db --force    # rebuild it
```

Five invented visits written through the real logger (same code path as a live run), with
a fake clock, so the output is identical every time: one that is summoned and talks (with a
slow reply, a filler line and a silence prompt), one that walks straight in (a Gemini error
and a skipped utterance), one that only passes by, one that leaves and comes back inside the
5 s grace window, and one that switches mode by voice while perception goes stale.
Try the example queries above against it. The `.db` is ignored by git; the script is
what is committed.
