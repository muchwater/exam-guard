"""Measure clock offset between two team hosts (e.g. uploader vs sniffer) over UDP, NTP-style.

    sniffer host:  python -m tools.clock_offset serve --port <p>
    uploader host: python -m tools.clock_offset probe --host <sniffer ip> --port <p> --n <N> --out off.json

offset = server_time - (t_send + t_recv) / 2 for each probe; the probe with the smallest round trip
is reported (its error is bounded by RTT/2). Use -offset as `uploader clock - sniffer clock`
(net_event.measure --offset).
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import time


def serve(port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    while True:
        data, addr = s.recvfrom(64)
        s.sendto(data + struct.pack("!d", time.time()), addr)


def probe(host: str, port: int, n: int) -> dict:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    best = None
    rtts = []
    for i in range(n):
        t0 = time.time()
        s.sendto(struct.pack("!I", i), (host, port))
        try:
            data, _ = s.recvfrom(64)
        except socket.timeout:
            continue
        t1 = time.time()
        ts = struct.unpack("!d", data[4:12])[0]
        rtt = t1 - t0
        rtts.append(rtt)
        off = ts - (t0 + t1) / 2              # server - client
        if best is None or rtt < best[0]:
            best = (rtt, off)
    return {"n_ok": len(rtts), "min_rtt_s": best[0], "server_minus_client_s": best[1],
            "client_minus_server_s": -best[1], "error_bound_s": best[0] / 2}


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("serve")
    a1.add_argument("--port", type=int, required=True)
    a2 = sub.add_parser("probe")
    a2.add_argument("--host", required=True)
    a2.add_argument("--port", type=int, required=True)
    a2.add_argument("--n", type=int, required=True)
    a2.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "serve":
        serve(a.port)
    else:
        r = probe(a.host, a.port, a.n)
        json.dump(r, open(a.out, "w"), indent=1)
        print(r)


if __name__ == "__main__":
    main()
