"""Entry point: wires the backend modules together.

    python main.py --host <ROBOT_IP> --auth_key <KEY>

    conversation/  Realtime Conversation  - Furhat audio <-> Gemini Live
    vision/        Vision Perception      - Furhat camera frames
    scenario/      Scenario Setting       - prompt -> WebSocket requests
    gui/           Golem behaviour layer  - face, gaze, LED, idle, tools, debug panel (--gui)
    demos/         the original standalone examples
"""
import argparse
import asyncio
import signal

from dotenv import load_dotenv
from furhat_realtime_api import AsyncFurhatClient

from conversation import RealtimeConversation
from gui import GolemGUI
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

    # Golem behaviour layer + debug panel (see gui/INTEGRATION.md for the conversation hookup)
    gui = GolemGUI(furhat) if args.gui else None
    if gui:
        await gui.start(panel_port=args.gui_port)

    # Scenario Setting module
    if args.prompt:
        scenario = ScenarioManager.from_prompt(args.prompt)
    else:
        name = args.scenario or ("default" if args.no_vision else "vision")
        scenario = ScenarioManager(name)

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

    print("Starting Gemini realtime conversation. Press Ctrl+C to stop gracefully")
    try:
        if vision:
            await vision.start()
        await conversation.run()
    except Exception as e:
        print(f"Error in main loop: {e}")
    finally:
        print("Shutting down...")
        if vision:
            try:
                await vision.stop()
            except Exception:
                pass
        if gui:
            await gui.stop()
        await furhat.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Furhat robot IP address")
    parser.add_argument("--auth_key", type=str, default=None, help="Authentication key for Realtime API")
    parser.add_argument("--model", type=str, default="gemini-3.8-live", help="Gemini Live model ID (without the 'models/' prefix)")
    parser.add_argument("--scenario", type=str, choices=list(SCENARIOS), default=None, help="Preset scenario (default: 'vision', or 'default' with --no-vision)")
    parser.add_argument("--prompt", type=str, default=None, help="Custom system prompt, overrides --scenario")
    parser.add_argument("--no-vision", action="store_true", help="Run without the camera / vision module")
    parser.add_argument("--frame_interval", type=float, default=1.0, help="Seconds between camera frames sent to Gemini")
    parser.add_argument("--gui", action="store_true", help="Enable the Golem behaviour layer and debug panel")
    parser.add_argument("--gui_port", type=int, default=8080, help="Port of the debug panel web page")
    parser.add_argument("--debug", action="store_true", help="Print debug info from the modules")
    asyncio.run(main(parser.parse_args()))
