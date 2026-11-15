"""Serve the web viewer locally: python web/serve.py  ->  http://localhost:8000

A plain static server. It pins the MIME types the page needs, because on
Windows Python's guess for .js can come from the registry as text/plain,
which browsers refuse for module scripts.
"""
import http.server
import socketserver
import sys
from functools import partial
from pathlib import Path

WEB = Path(__file__).resolve().parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".js": "text/javascript", ".json": "application/json", ".css": "text/css",
                      ".bin": "application/octet-stream", ".jpg": "image/jpeg", ".png": "image/png"}

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), partial(Handler, directory=str(WEB))) as s:
        print(f"Lantern viewer: http://localhost:{PORT}", flush=True)
        s.serve_forever()
