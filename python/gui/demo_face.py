"""Cycle through every primitive and expression so they can be checked on the robot.

    cd python
    python -m gui.demo_face --host 127.0.0.1          # virtual Furhat
"""
import argparse
import asyncio
import os

from dotenv import load_dotenv
from furhat_realtime_api import AsyncFurhatClient

from .expressions import LABELS, perform
from .face import GolemFace
from .golem import Golem

PAUSE = 2.5  # seconds between steps, long enough to see each one


async def main(host: str, auth_key: str | None):
    furhat = AsyncFurhatClient(host, auth_key=auth_key)
    await furhat.connect()
    face = GolemFace(furhat)
    golem = Golem(face)

    steps = [
        ("awaken", golem.awaken),
        ("blink", face.blink),
        ("nod", face.nod),
        ("smile", face.smile),
        ("raise_brows", face.raise_brows),
        ("frown", face.frown),
        ("gaze_location left", lambda: face.gaze_location(-0.6, 0.0, 1.0)),
        ("gaze_location right", lambda: face.gaze_location(0.6, 0.0, 1.0)),
        ("gaze_user", face.gaze_user),
        ("gaze_away", face.gaze_away),
        ("close_eyes", face.close_eyes),
        ("open_eyes", face.open_eyes),
        ("set_led red", lambda: face.set_led("#ff0000")),
        ("set_led off", lambda: face.set_led("#000000")),
    ]
    steps += [(f"expression {label}", lambda label=label: perform(face, label)) for label in LABELS]
    steps += [
        ("smile while speaking (cheeks/eyes only)", lambda: speak_and_smile(furhat, face)),
        ("sleep", golem.sleep),
    ]

    try:
        for name, action in steps:
            print("->", name)
            await action()
            await asyncio.sleep(PAUSE)
    finally:
        await furhat.disconnect()


async def speak_and_smile(furhat: AsyncFurhatClient, face: GolemFace):
    await furhat.request_speak_text("I am the Golem. Ask, and I shall answer.")
    await asyncio.sleep(0.5)   # give response.speak.start time to arrive
    print("   face.speaking =", face.speaking)
    await face.smile(0.7)


if __name__ == "__main__":
    load_dotenv(override=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Furhat robot IP address")
    parser.add_argument("--auth_key", type=str, default=os.getenv("FURHAT_AUTH_KEY"), help="Realtime API key")
    args = parser.parse_args()
    asyncio.run(main(args.host, args.auth_key))
