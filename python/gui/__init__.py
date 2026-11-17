"""Golem expressive behaviour layer (the "Act" part of sense-plan-act)."""
from furhat_realtime_api import AsyncFurhatClient

from .face import GolemFace
from .golem import Golem
from .idle import IdleBehaviour
from .panel import ControlPanel
from .tools import GolemTools, TOOL_DECLARATIONS


class GolemGUI:
    """Bundles face, Golem state, idle behaviour, tools and the optional debug panel."""

    def __init__(self, furhat: AsyncFurhatClient):
        self.face = GolemFace(furhat)
        self.golem = Golem(self.face)
        self.idle = IdleBehaviour(self.face, self.golem)
        self.tools = GolemTools(self.golem)
        self.panel = None

    async def start(self, panel_port: int | None = None, awake: bool = True):
        """awake=False starts dormant (eyes closed), e.g. when the interaction controller decides."""
        if awake:
            await self.golem.awaken()
        else:
            await self.golem.sleep()
        self.idle.start()
        if panel_port:
            self.panel = ControlPanel(self)
            await self.panel.start(panel_port)

    async def stop(self):
        if self.panel:
            await self.panel.stop()
        await self.idle.stop()
        await self.golem.sleep()
