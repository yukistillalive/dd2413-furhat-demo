"""Distance-driven interaction: decides when the Golem sleeps, summons or wakes."""
from .controller import (
    AWAKE, DORMANT, SUMMONING,
    InteractionConfig, InteractionController,
)
