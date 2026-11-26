"""Team-owned upload sink for capture experiments (runs on a team laptop on the same AP).

    python -m capture.upload_server --port 8080 --log data/raw/server_log.jsonl [--host ::]

Accepts POST /upload, discards the body after counting it, logs receive start/end times.
Images never leave the team's own network.
"""
from __future__ import annotations

import argparse
import json
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_handler(log_path: str):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            t0 = time.time()
            n = int(self.headers.get("Content-Length", 0))
            left = n
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 16))
                if not chunk:
                    break
                left -= len(chunk)
            t1 = time.time()
            with open(log_path, "a") as f:
                f.write(json.dumps({"t_recv_start": t0, "t_recv_end": t1, "bytes": n - left,
                                    "client": self.client_address[0]}) + "\n")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--host", default="0.0.0.0", help="'::' listens on IPv6 (incl. link-local)")
    a = ap.parse_args()
    server_cls = ThreadingHTTPServer
    if ":" in a.host:
        server_cls = type("ThreadingHTTPServer6", (ThreadingHTTPServer,), {"address_family": socket.AF_INET6})
    server_cls((a.host, a.port), make_handler(a.log)).serve_forever()


if __name__ == "__main__":
    main()
