"""Entry point: wires the backend modules together.

    python main.py --host <ROBOT_IP> --auth_key <KEY>

    conversation/  Gemini text -> Furhat TTS; Live audio path is parked
    store/         Conversation log       - SQLite transcript (--log-db)
    debug_hub/     Debug hub              - live feed for ../debug-ui (--debug-ui)
    vision/        Vision Perception      - Furhat camera frames
    scenario/      Scenario Setting       - prompt -> WebSocket requests
    gui/           Golem behaviour layer  - face, gaze, LED, idle, tools, debug panel (--gui)
    demos/         the original standalone examples
"""
import argparse
import asyncio
import os
import signal
from pathlib import Path

from dotenv import load_dotenv
from furhat_realtime_api import AsyncFurhatClient

from conversation import RealtimeConversation
from conversation.realtime_conversation import TEXT_MODEL
from debug_hub import DebugHub, DEFAULT_PORT
from gui import GolemGUI
from scenario import ScenarioManager, SCENARIOS
from store import ConversationLog
from vision import VisionModule

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


async def main(args):
    furhat = AsyncFurhatClient(args.host, auth_key=args.auth_key)
    try:
        await furhat.connect()
    except Exception as e:
        print(f"Failed to connect to Furhat on {args.host}: {e}")
        return

    # Scenario Setting module. Camera-on does not replace the chatbot persona;
    # it only adds a vision sentence inside the active behavior's prompt.
    # Golem behaviour layer + debug panel (see gui/INTEGRATION.md for the conversation hookup)
    gui = GolemGUI(furhat) if args.gui else None
    if gui:
        await gui.start(panel_port=args.gui_port)

    # Scenario Setting module
    if args.prompt:
        scenario = ScenarioManager.from_prompt(args.prompt)
        scenario.golem_face_id = args.golem_face
    else:
        name = args.scenario or "default"
        scenario = ScenarioManager(name, golem_face_id=args.golem_face)
    scenario.use_vision = not args.no_vision
    scenario.bind(furhat)
    await scenario.apply()

    # Realtime Conversation module. The log and debug hub are optional.
    log = ConversationLog(args.log_db) if args.log_db else None
    hub = DebugHub(port=args.debug_ui_port) if args.debug_ui else None
    if hub:
        await hub.start()
    conversation = RealtimeConversation(
        furhat, scenario, model=args.model, debug=args.debug, log=log, hub=hub, gui=gui)

    # Vision Perception module, feeding frames into the conversation
    vision = None
    if not args.no_vision:
        vision = VisionModule(furhat, frame_interval=args.frame_interval, debug=args.debug)
        vision.add_frame_listener(conversation.send_video_frame)
        if hub:
            vision.add_frame_listener(hub.send_frame)

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
        if gui:
            await gui.stop()
        await furhat.disconnect()
        if hub:
            await hub.stop()
        if log:
            log.close()


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
    parser.add_argument("--host", type=str, default=os.environ.get("FURHAT_HOST", "127.0.0.1"), help="Furhat robot IP address")
    parser.add_argument("--auth_key", type=str, default=os.environ.get("FURHAT_AUTH_KEY") or None, help="Authentication key for Realtime API")
    parser.add_argument("--model", type=str, default=TEXT_MODEL, help="Gemini text model. Default is TEXT_MODEL in realtime_conversation.py.")
    parser.add_argument("--scenario", type=str, choices=list(SCENARIOS), default=None, help="Starting behavior: default, golem, or vision")
    parser.add_argument("--golem-face", type=str, default=None, help="Exact Furhat face_id used by the golem behavior")
    parser.add_argument("--prompt", type=str, default=None, help="Custom system prompt, overrides --scenario")
    parser.add_argument("--no-vision", action="store_true", help="Run without the camera / vision module")
    parser.add_argument("--frame_interval", type=float, default=1.0, help="Seconds between camera frames sent to Gemini")
    parser.add_argument("--log-db", type=str, default=None, help="SQLite file for the text transcript (e.g. conversations.db)")
    parser.add_argument("--debug-ui", action="store_true", help="Stream camera and transcript to the debug-ui page on localhost")
    parser.add_argument("--debug-ui-port", type=int, default=DEFAULT_PORT, help=f"Port for --debug-ui (default {DEFAULT_PORT})")
    parser.add_argument("--gui", action="store_true", help="Enable the Golem behaviour layer and debug panel")
    parser.add_argument("--gui_port", type=int, default=8090, help="Port of the debug panel web page (8080 is used by the Furhat SDK)")
    parser.add_argument("--debug", action="store_true", help="Print debug info from the modules")
    asyncio.run(main(parser.parse_args()))
