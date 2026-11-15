"""Golem state: "dormant" (met, dead clay) or "awake" (emet, alive).

In the myth the Golem lives while "emet" (truth) is written on its forehead,
and returns to clay when the aleph is erased, leaving "met" (death).
"""
from .face import GolemFace

EMET_LED = "#c86414"   # awake: ember glow, same as the "neutral" expression
MET_LED = "#000000"    # dormant: LED off

# TODO: the Realtime API cannot draw a symbol on the face. If the robot has a
# fitting mask/character, put its id here (see furhat.request_face_status()["face_list"]).
GOLEM_FACE_ID = None


class Golem:
    def __init__(self, face: GolemFace):
        self.face = face
        self.state = "dormant"

    @property
    def awake(self) -> bool:
        return self.state == "awake"

    async def awaken(self):
        """emet: open the eyes, light up, look at the visitor."""
        if GOLEM_FACE_ID:
            await self.face.furhat.request_face_config(face_id=GOLEM_FACE_ID)
        await self.face.set_led(EMET_LED)
        await self.face.open_eyes()
        await self.face.gaze_user()
        self.state = "awake"

    async def sleep(self):
        """met: lower the gaze, close the eyes, go dark."""
        self.state = "dormant"
        await self.face.gaze_location(0.0, -0.4, 1.0)
        await self.face.close_eyes()
        await self.face.set_led(MET_LED)
