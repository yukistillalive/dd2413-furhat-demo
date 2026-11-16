"""Mapping from expression labels (model/plan output) to robot behaviour."""
import logging

from .face import GolemFace

log = logging.getLogger(__name__)

# ---- "serene" tuning (divine, downcast gaze) ----
SERENE_EYELIDS = 0.45        # EYE_BLINK_*: 0 = open, 1 = closed
SERENE_LOOK_DOWN = 0.5       # EYE_LOOK_DOWN_*
SERENE_HEAD_Y = -0.3         # gaze target height (m); negative tilts the head down
SERENE_SMILE = 0.2           # very slight smile
SERENE_BLINK_DURATION = 2.0  # slow blink (s)
SERENE_LED = "#5a2a08"       # dim warm amber
SERENE_REFRESH = 1.0         # re-send the eye params every N s in case they decay (0 = send once)
SERENE_PARAMS = {
    "EYE_BLINK_LEFT": SERENE_EYELIDS, "EYE_BLINK_RIGHT": SERENE_EYELIDS,
    "EYE_LOOK_DOWN_LEFT": SERENE_LOOK_DOWN, "EYE_LOOK_DOWN_RIGHT": SERENE_LOOK_DOWN,
}

# label -> list of (GolemFace method, keyword arguments), executed in order
EXPRESSIONS = {
    "neutral":  [("set_led", {"color": "#c86414"}),        # ember orange: the Golem's resting glow
                 ("gaze_user", {})],
    "solemn":   [("set_led", {"color": "#801010"}),        # dark red
                 ("frown", {"intensity": 0.4}),
                 ("nod", {})],
    "curious":  [("set_led", {"color": "#20a0c0"}),        # cyan
                 ("raise_brows", {"intensity": 0.7}),
                 ("gaze_user", {})],
    "warm":     [("set_led", {"color": "#ffa040"}),        # bright amber
                 ("smile", {"intensity": 0.6}),
                 ("gaze_user", {})],
    "thinking": [("set_led", {"color": "#6040a0"}),        # violet
                 ("gaze_location", {"x": -0.4, "y": 0.4, "z": 1.0}),  # look up and to the side
                 ("frown", {"intensity": 0.3})],
    "serene":   [("set_led", {"color": SERENE_LED}),
                 ("gaze_location", {"x": 0.0, "y": SERENE_HEAD_Y, "z": 1.0}),
                 ("hold_params", {"params": SERENE_PARAMS, "refresh": SERENE_REFRESH}),
                 ("blink", {"duration": SERENE_BLINK_DURATION}),
                 ("smile", {"intensity": SERENE_SMILE})],    # cheeks/eyes only while speaking
}
LABELS = list(EXPRESSIONS)


async def perform(face: GolemFace, label: str) -> str:
    """Run the actions for `label`; unknown labels fall back to "neutral". Returns the label used."""
    if label not in EXPRESSIONS:
        log.warning("Unknown expression %r, using 'neutral'", label)
        label = "neutral"
    await face.release_params()   # end any held expression (e.g. "serene")
    for method, kwargs in EXPRESSIONS[label]:
        await getattr(face, method)(**kwargs)
    face.expression = label
    return label
