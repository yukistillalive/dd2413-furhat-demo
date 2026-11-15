"""Entry point: wires the backend modules together.

    python main.py --host <ROBOT_IP> --auth_key <KEY>

    conversation/  Gemini text -> Furhat TTS; Live audio path is parked
    vision/        Vision Perception      - Furhat camera frames
    scenario/      Scenario Setting       - prompt -> WebSocket requests
    gui/           GUI component          - (not wired in yet)
    demos/         the original standalone examples
"""
import argparse
import asyncio
import signal

from dotenv import load_dotenv
from furhat_realtime_api import AsyncFurhatClient

from conversation import RealtimeConversation
from scenario import ScenarioManager, SCENARIOS
from vision import VisionModule


async def main(args):
    load_dotenv(override=True)

    furhat = AsyncFurhatClient(args.host, auth_key=args.auth_key)
    try:
        await furhat.connect()
    except Exception as e:
        print(f"Failed to connect to Furhat on {args.host}: {e}")
        return

    # Scenario Setting module. Camera-on does not replace the chatbot persona;
    # it only adds a vision sentence inside the active behavior's prompt.
    if args.prompt:
        scenario = ScenarioManager.from_prompt(args.prompt)
        scenario.golem_face_id = args.golem_face
    else:
        name = args.scenario or "default"
        scenario = ScenarioManager(name, golem_face_id=args.golem_face)
    scenario.use_vision = not args.no_vision
    scenario.bind(furhat)
    await scenario.apply()

    # Realtime Conversation module
    conversation = RealtimeConversation(furhat, scenario, model=args.model, debug=args.debug)

    # Vision Perception module, feeding frames into the conversation
    vision = None
    if not args.no_vision:
        vision = VisionModule(furhat, frame_interval=args.frame_interval, debug=args.debug)
        vision.add_frame_listener(conversation.send_video_frame)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(conversation.stop()))

    print("Gemini writes each reply. Furhat TTS speaks it. Press Ctrl+C to stop.")
    print("Switch behavior by typing default or golem, or by saying exactly:")
    print("  golem mode    |    normal mode")
    try:
        if vision:
            await vision.start()
        commands = asyncio.create_task(_behavior_commands(scenario, conversation))
        await conversation.run()
        commands.cancel()
    except Exception as e:
        print(f"Error in main loop: {e}")
    finally:
        print("Shutting down...")
        try:
            await scenario.aclose()
        except Exception:
            pass
        if vision:
            try:
                await vision.stop()
            except Exception:
                pass
        await furhat.disconnect()


async def _behavior_commands(scenario: ScenarioManager, conversation: RealtimeConversation):
    aliases = {"1": "default", "2": "golem", "normal": "default"}
    while not conversation.stop_event.is_set():
        try:
            line = (await asyncio.to_thread(input, "")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            await conversation.stop()
            return
        if not line or conversation.stop_event.is_set():
            continue
        name = aliases.get(line, line)
        if name not in ("default", "golem"):
            print("Unknown behavior. Type default or golem.")
            continue
        try:
            changed = await scenario.switch(name)
        except Exception as e:
            print(f"Could not switch behavior: {e}")
            continue
        if not changed:
            print(f"Already in {name}.")
            continue
        conversation.request_reconnect()
        print(f"Switched to {name}. Furhat TTS will speak the next line in that voice.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Furhat robot IP address")
    parser.add_argument("--auth_key", type=str, default=None, help="Authentication key for Realtime API")
    parser.add_argument("--model", type=str, default="gemini-3.5-flash", help="Gemini text model ID")
    parser.add_argument("--scenario", type=str, choices=list(SCENARIOS), default=None, help="Starting behavior: default, golem, or vision")
    parser.add_argument("--golem-face", type=str, default=None, help="Exact Furhat face_id used by the golem behavior")
    parser.add_argument("--prompt", type=str, default=None, help="Custom system prompt, overrides --scenario")
    parser.add_argument("--no-vision", action="store_true", help="Run without the camera / vision module")
    parser.add_argument("--frame_interval", type=float, default=1.0, help="Seconds between camera frames sent to Gemini")
    parser.add_argument("--debug", action="store_true", help="Print debug info from the modules")
    asyncio.run(main(parser.parse_args()))
