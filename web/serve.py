"""Serve the site locally: python web/serve.py [port]  ->  http://localhost:8000

A plain static server for web/, plus one local-only route: a request for
campaigns/<batch>/<run>/record.json, poses.bin or poses.json is answered
from the campaign data itself (<DATA>/campaigns/, docs/handoffs.md), with
poses.npz converted on first request (web/collect_campaigns.py). So a
batch of thousands of runs needs no copy into web/. A static host gets
copies instead (python web/collect_campaigns.py --copy).

It pins the MIME types the page needs, because on Windows Python's guess for
.js can come from the registry as text/plain, which browsers refuse for
module scripts.
"""
import http.server
import re
import socketserver
import sys
import threading
from functools import partial
from pathlib import Path

WEB = Path(__file__).resolve().parent
sys.path.insert(0, str(WEB))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
CONVERT = threading.Lock()   # one conversion at a time: poses.bin and poses.json are asked for together
RUN_FILE = re.compile(r"^/campaigns/([A-Za-z0-9][A-Za-z0-9_.-]*)/([A-Za-z0-9][A-Za-z0-9_.-]*)/(record\.json|poses\.bin|poses\.json|fp/[A-Za-z0-9_.-]+\.jpg)$")


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".js": "text/javascript", ".json": "application/json", ".css": "text/css",
                      ".bin": "application/octet-stream", ".jpg": "image/jpeg", ".png": "image/png"}

    def translate_path(self, path):
        m = RUN_FILE.match(path.split("?", 1)[0])
        if m:
            from collect_campaigns import SRC, DST, convert_poses
            batch, run, name = m.groups()
            src = SRC / batch / run
            if (src / "record.json").exists():
                if name == "record.json" or name.startswith("fp/"):   # fp/: the showcase's frames
                    return str(src / name)
                with CONVERT:
                    convert_poses(src / "poses.npz", DST / batch / run)
                return str(DST / batch / run / name)
        return super().translate_path(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), partial(Handler, directory=str(WEB))) as s:
        print(f"Lantern site: http://localhost:{PORT}", flush=True)
        s.serve_forever()
