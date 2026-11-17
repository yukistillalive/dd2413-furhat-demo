# Interaction controller (`--interaction`)

The Golem reacts to how far away the visitor is.

```
python main.py --host 192.168.1.114 --scenario golem --gui --interaction
```

`--enter-distance` (default 1.2 m) and `--exit-distance` (default 1.5 m) set the
interaction zone. `--interaction` needs `--gui` and the camera (no `--no-vision`).

## States

```
                 far zone >= 1.5 s                   interaction zone
   DORMANT  ─────────────────────────▶  SUMMONING  ─────────────────────▶  AWAKE
      │  ▲                                  │                               ▲  │
      │  │◀── person_left + 5 s nobody back ┘                               │  │
      │  └─────────────────────── person_left + 5 s nobody back ────────────┼──┘
      └─────────── interaction zone without 1.5 s in the far zone ──────────┘
```

| state | robot | conversation |
|---|---|---|
| DORMANT | eyes closed, gaze down, LED dim (`#140802`) | silent, not listening |
| SUMMONING | not awakened; "serene" half-closed downcast eyes; one slow line, a second one after 10 s, then silence | silent, not listening |
| AWAKE | `awaken()`, gaze at user, "warm" | opening line (camera frame + one clothing item, or a fixed fallback after 6 s), then question and answer |

- AWAKE ends only when the person leaves, not when they step back into the far zone.
- `person_left` comes from `vision/`: about 2 s after the user disappears, which on
  the real robot is itself 2–3 s after they walk off (the last distance stays frozen).
  With the 5 s grace the Golem falls asleep roughly 9–10 s after someone walks away.
- **Same visitor:** after `person_left` the Golem waits 5 s before `sleep()`. If someone
  is confirmed in that window (the user id may have changed), the current state just
  continues: no sleep/awaken flicker, no new summon line or greeting, same conversation.
  Summon repeats pause while nobody is there. Only after 5 s with nobody back does it
  go DORMANT and end the conversation.
- Perception stale or error: the state is kept; a far-zone wait that was running restarts.

## Turn-taking while AWAKE

- **Waiting for Gemini:** the face goes into "thinking" (gaze up to the side, wandering).
  If the reply takes more than 3 s the Golem says "Hmm... let me consider." and keeps
  thinking; the reply follows when the filler is finished, with its own emotion.
- **Silent visitor:** an empty listen ends after 5 s. After about 10 s of silence (two
  empty listens) the Golem asks once "Do you have a question for me, traveller?"; after
  another ~10 s it waits quietly in "serene" (no glances) until the visitor speaks or leaves.
  Speaking restarts the count, so each new silence gets one prompt again.
- Silence only counts while the visitor stands in the interaction zone: the controller
  calls `conversation.set_visitor_close()`, so nobody is prompted while walking off or
  during the person_left grace.

## Who owns the LED

One owner at a time:

- **Behavior with an LED pattern (golem):** `ScenarioManager` owns the LED. GUI
  expressions, `awaken()` and `sleep()` do not set it (`GolemFace.led_allowed`).
  The controller asks the scenario for the dim sleep light with `hold_led()`,
  which pauses the breathing loop, and `release_led()` on AWAKE, which resumes it.
- **Behavior without a pattern (default):** the GUI owns the LED, as before.

With `--gui`, Furhat's built-in blinking is off; only the GUI idle layer blinks.

## Testing without the robot

```
python -m interaction.replay --check          # recording + synthetic walks, prints every transition
python -m unittest discover -s interaction -p 'test_*.py' -t .
python -m interaction.sdk_demo                # live, on the Furhat SDK virtual robot (127.0.0.1)
```

`sdk_demo` walks one scripted visitor in, lets them ask, stay silent for ~25 s, ask
again and leave. Everything except the user distance and the visitor's words is the
real code; replies come from Gemini (`--scripted` for fixed ones).

`users_recording.jsonl` never comes closer than about 1.44 m, so at 1.2 m it only
reaches SUMMONING. The synthetic segments cover approach, walk-in, same visitor,
and stale perception.
