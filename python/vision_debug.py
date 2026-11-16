"""独立入口：--simulate 模拟；--host IP 读取真实机器人。"""
import argparse
import asyncio
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import threading
import time

from vision.perception_state import PerceptionConfig, PerceptionTracker

SCENARIOS = {
    "none": [], "far": [{"id": "demo-1", "distance": 2.0}],
    "near": [{"id": "demo-1", "distance": 1.0}],
    "two": [{"id": "demo-1", "distance": 1.0}, {"id": "demo-2", "distance": 0.8}],
    "missing": [{"id": "demo-1"}],
}
TIMELINE = [("none", 3), ("far", 6), ("near", 6), ("two", 4),
            ("occlusion", 1), ("near", 4), ("missing", 4),
            ("stale", 5), ("near", 4), ("none", 4)]


class Dashboard:
    def __init__(self, simulated):
        self.source = "simulated" if simulated else "robot"
        self.payload = {"source": self.source, "state": {}, "events": []}
        self.frame = None
        self.lock = threading.Lock()
        self.commands = queue.Queue()

    def publish(self, tracker, scenario=None, vision=None):
        age = (time.monotonic() - vision.last_frame_at
               if vision and vision.last_frame_at is not None else None)
        with self.lock:
            self.payload = dict(source=self.source, state=tracker.snapshot(),
                                events=list(tracker.events), scenario=scenario,
                                frame_age_s=age, camera_error=vision.camera_error if vision else None)
            self.frame = vision.latest_frame if vision else None


def start_server(dashboard, port):
    page = Path(__file__).parent / "vision" / "debug.html"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, body, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                self.reply(200, page.read_bytes(), "text/html; charset=utf-8")
            elif path in ("/api/state", "/api/frame"):
                with dashboard.lock:
                    value = dashboard.payload if path == "/api/state" else {"image": dashboard.frame}
                    body = json.dumps(value, allow_nan=False).encode()
                self.reply(200, body, "application/json")
            else:
                self.reply(404, b"Not found", "text/plain")

        def do_POST(self):
            choice = self.path.removeprefix("/simulate/")
            origin = self.headers.get("Origin")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if dashboard.source != "simulated" or (origin and origin != expected):
                self.reply(403, b"Not allowed", "text/plain")
            elif not self.path.startswith("/simulate/") or choice not in (*SCENARIOS, "stale", "occlusion", "auto"):
                self.reply(400, b"Unknown scenario", "text/plain")
            else:
                dashboard.commands.put(choice)
                self.reply(200, b"OK", "text/plain")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def simulate(args, dashboard, config):
    tracker = PerceptionTracker(config)
    started = cycle_started = chosen_at = time.monotonic()
    chosen = "auto"
    while not args.duration or time.monotonic() - started < args.duration:
        now = time.monotonic()
        try:
            while True:
                chosen = dashboard.commands.get_nowait()
                chosen_at = cycle_started = now
        except queue.Empty:
            pass
        scenario = chosen
        if chosen == "auto":
            elapsed = (now - cycle_started) % sum(seconds for _, seconds in TIMELINE)
            for name, seconds in TIMELINE:
                if elapsed < seconds:
                    scenario = name
                    break
                elapsed -= seconds
        elif chosen == "occlusion" and now - chosen_at > 1:
            scenario = "near"
        if scenario != "stale":
            tracker.update({"users": SCENARIOS.get(scenario, [])})
        tracker.tick()
        dashboard.publish(tracker, scenario)
        await asyncio.sleep(0.2)


async def run_robot(args, dashboard, config):
    from furhat_realtime_api import AsyncFurhatClient
    from vision import VisionModule
    furhat = AsyncFurhatClient(args.host, auth_key=os.environ.get("FURHAT_AUTH_KEY"))
    vision = None
    try:
        await asyncio.wait_for(furhat.connect(), timeout=8)
        vision = VisionModule(furhat, config=config, camera_enabled=args.camera)
        await vision.start()
        started = time.monotonic()
        while not args.duration or time.monotonic() - started < args.duration:
            dashboard.publish(vision.tracker, vision=vision)
            await asyncio.sleep(0.2)
    except Exception as exc:
        tracker = vision.tracker if vision else PerceptionTracker(config)
        tracker.mark_error(f"Robot connection failed: {type(exc).__name__}. Check host, Wi-Fi and API access.")
        dashboard.publish(tracker)
        print(tracker.error, flush=True)
        # 保留错误供查看，Ctrl+C 可退出。
        await asyncio.sleep(args.duration if args.duration else 3600)
    finally:
        if vision:
            await vision.stop()
        with suppress(Exception):
            await asyncio.wait_for(furhat.disconnect(), timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--host", help="机器人 IP，例如 192.168.1.114")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--camera", action="store_true", help="请求相机画面，仅本地显示")
    parser.add_argument("--distance-unit", choices=["unknown", "m", "cm"], default="unknown")
    parser.add_argument("--enter-distance", type=float, default=1.2)
    parser.add_argument("--exit-distance", type=float, default=1.5)
    parser.add_argument("--duration", type=float, default=0, help="运行秒数；0 表示持续运行")
    args = parser.parse_args()
    config = PerceptionConfig(distance_unit="m" if args.simulate else args.distance_unit,
                              enter_distance_m=args.enter_distance, exit_distance_m=args.exit_distance)
    if args.duration < 0:
        parser.error("duration must be >= 0")
    dashboard = Dashboard(args.simulate)
    try:
        server = start_server(dashboard, args.port)
    except OSError:
        parser.exit(1, "端口被占用。先停止之前的程序，或加 --port 8766\n")
    print(f"Vision dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    print("SIMULATED DATA" if args.simulate else "ROBOT DATA (no speech or movement commands)", flush=True)
    try:
        asyncio.run(simulate(args, dashboard, config) if args.simulate else run_robot(args, dashboard, config))
    except KeyboardInterrupt:
        print("Vision debugger stopped.")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
