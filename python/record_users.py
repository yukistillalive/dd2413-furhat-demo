"""Record what the real Furhat reports about users, to replay at home.

Run in the lab (from the python/ folder, with .venv active):
    python record_users.py --host 192.168.1.114 --seconds 60

Walk through: stand far away -> walk up close -> stay -> walk away -> leave.
Say the phase out loud or type nothing; each line gets a timestamp.
Output: users_recording.jsonl (one JSON object per line).
"""
import argparse
import json
import time

from furhat_realtime_api import FurhatClient

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="192.168.1.114")
parser.add_argument("--auth_key", default=None)
parser.add_argument("--seconds", type=float, default=60)
parser.add_argument("--interval", type=float, default=0.5)
parser.add_argument("--out", default="users_recording.jsonl")
args = parser.parse_args()

furhat = FurhatClient(args.host, auth_key=args.auth_key)
furhat.connect()
start = time.monotonic()
with open(args.out, "w") as f:
    while time.monotonic() - start < args.seconds:
        t = round(time.monotonic() - start, 2)
        data = furhat.request_users_once()
        f.write(json.dumps({"t": t, "data": data}, default=str) + "\n")
        users = data.get("users", []) if isinstance(data, dict) else []
        summary = ", ".join(f"id={u.get('id')} dist={u.get('distance')}" for u in users) or "nobody"
        print(f"{t:6.1f}s  {summary}")
        time.sleep(args.interval)
furhat.disconnect()
print("Saved to", args.out)
