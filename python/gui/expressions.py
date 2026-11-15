"""Mapping from expression labels (model/plan output) to robot behaviour."""
import logging

from .face import GolemFace

log = logging.getLogger(__name__)

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
}
LABELS = list(EXPRESSIONS)


async def perform(face: GolemFace, label: str) -> str:
    """Run the actions for `label`; unknown labels fall back to "neutral". Returns the label used."""
    if label not in EXPRESSIONS:
        log.warning("Unknown expression %r, using 'neutral'", label)
        label = "neutral"
    for method, kwargs in EXPRESSIONS[label]:
        await getattr(face, method)(**kwargs)
    return label
