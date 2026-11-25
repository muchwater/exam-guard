"""Measure application-layer byte counts used by the spec-based network simulator (synth/netsim).

    python -m tools.measure_app_bytes --out data/measured/app_bytes.json [--tls-host api.openai.com ...]

Everything here is measured by running real code, not assumed:
  * HTTP POST request header bytes produced by http.client exactly as capture/scenario_upload.py
    calls it (header length as a function of Content-Length digits)
  * raw HTTP response bytes produced by capture/upload_server.py (started on loopback)
  * HTTP GET request header bytes from http.client (proxy for a browser request; lower bound)
  * TLS 1.3 handshake flight sizes (client hello, server flight, client finished) against the given
    public hosts. Only a TLS handshake is made (SNI = host), no HTTP request, nothing identifying is sent.
"""
from __future__ import annotations

import argparse
import http.client
import json
import socket
import ssl
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from capture.upload_server import make_handler


class _Recorder:
    """Socket stand-in that records what http.client writes."""

    def __init__(self):
        self.buf = b""

    def sendall(self, data):
        self.buf += bytes(data)

    def close(self):
        pass


def http_request_header(method: str, host: str, port: int, path: str, body_len: int | None,
                        headers: dict) -> int:
    conn = http.client.HTTPConnection(host, port)
    rec = _Recorder()
    conn.sock = rec
    body = b"\0" * body_len if body_len is not None else None
    conn.request(method, path, body, headers)
    return len(rec.buf) - (body_len or 0)


def upload_server_response() -> dict:
    with tempfile.TemporaryDirectory() as d:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(Path(d) / "log.jsonl")))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        body = b"\0" * 1000
        s = socket.create_connection(("127.0.0.1", port))
        req = (f"POST /upload HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: {len(body)}\r\n"
               f"Content-Type: image/jpeg\r\n\r\n").encode() + body
        s.sendall(req)
        chunks = []
        while True:
            c = s.recv(65536)
            if not c:
                break
            chunks.append(c)
        s.close()
        srv.shutdown()
    raw = b"".join(chunks)
    head, _, payload = raw.partition(b"\r\n\r\n")
    return {"response_bytes": len(raw), "status_line": head.split(b"\r\n")[0].decode(),
            "header_bytes": len(head) + 4, "body_bytes": len(payload),
            "server_closes_connection": True}   # http.server default protocol_version HTTP/1.0


def tls_flights(host: str, port: int = 443, timeout: float = 5.0) -> dict:
    """Byte counts of each TLS handshake flight using a MemoryBIO-driven handshake."""
    ctx = ssl.create_default_context()
    inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
    obj = ctx.wrap_bio(inc, out, server_hostname=host)
    sock = socket.create_connection((host, port), timeout=timeout)
    flights = []   # (direction, bytes)
    try:
        while True:
            try:
                obj.do_handshake()
                done = True
            except ssl.SSLWantReadError:
                done = False
            data = out.read()
            if data:
                sock.sendall(data)
                flights.append(("c2s", len(data)))
            if done:
                break
            got = sock.recv(65536)
            if not got:
                raise ConnectionError("server closed during handshake")
            inc.write(got)
            # gather the rest of the server flight that is already in flight
            sock.settimeout(0.3)
            try:
                while True:
                    more = sock.recv(65536)
                    if not more:
                        break
                    inc.write(more)
                    got += more
            except (TimeoutError, socket.timeout):
                pass
            sock.settimeout(timeout)
            flights.append(("s2c", len(got)))
        version = obj.version()
    finally:
        sock.close()
    # merge consecutive same-direction entries
    merged = []
    for d, n in flights:
        if merged and merged[-1][0] == d:
            merged[-1] = (d, merged[-1][1] + n)
        else:
            merged.append((d, n))
    return {"host": host, "version": version, "flights": merged}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tls-host", action="append", default=[])
    a = ap.parse_args()

    post = {}
    for n in (9, 99_999, 999_999):   # header length depends only on the number of digits
        post[len(str(n))] = http_request_header("POST", "192.168.0.2", 8080, "/upload", n,
                                                {"Content-Type": "image/jpeg"})
    base = {d: v - d for d, v in post.items()}
    assert len(set(base.values())) == 1, base
    res = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "http_post_header_bytes_base": next(iter(base.values())),
        "http_post_header_note": "bytes = base + number of digits of Content-Length; host '192.168.0.2:8080', "
                                 "path /upload, Content-Type image/jpeg (capture/scenario_upload.py call)",
        "http_get_header_bytes": http_request_header("GET", "example.org", 80, "/", None, {}),
        "upload_server_response": upload_server_response(),
        "tls": [],
    }
    for h in a.tls_host:
        try:
            res["tls"].append(tls_flights(h))
        except OSError as e:
            res["tls"].append({"host": h, "error": str(e)})
        time.sleep(0.2)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
