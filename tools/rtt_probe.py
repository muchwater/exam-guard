"""TCP connect round-trip time to public hosts (wired-side RTT for the network simulator).

    python -m tools.rtt_probe --host api.openai.com --host generativelanguage.googleapis.com \
        --n 15 --out data/measured/wan_rtt_devpc.json

connect() returns after SYN -> SYN/ACK, so its duration is one TCP round trip from this machine to
the host's TCP terminator (often a CDN edge). Nothing but a TCP handshake is sent. Record where the
probe ran: the value describes that network, not the exam room.
"""
from __future__ import annotations

import argparse
import json
import socket
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path


def probe(host: str, port: int, n: int, gap_s: float) -> dict:
    addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
    rtts = []
    for _ in range(n):
        s = socket.socket()
        s.settimeout(3)
        t = time.perf_counter()
        try:
            s.connect(addr)
            rtts.append(time.perf_counter() - t)
        except OSError:
            pass
        finally:
            s.close()
        time.sleep(gap_s)
    return {"host": host, "ip": addr[0], "n_ok": len(rtts), "rtt_s": rtts,
            "min_s": min(rtts), "median_s": statistics.median(rtts), "max_s": max(rtts)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", action="append", required=True)
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--gap", type=float, default=0.2, help="pause between probes (politeness only)")
    ap.add_argument("--where", default="dev PC (wired campus network)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = {"measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "where": a.where,
           "method": "TCP connect() duration", "hosts": [probe(h, a.port, a.n, a.gap) for h in a.host]}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    for h in res["hosts"]:
        print(f"{h['host']:40s} n={h['n_ok']} median={h['median_s'] * 1e3:.2f} ms min={h['min_s'] * 1e3:.2f} ms")


if __name__ == "__main__":
    main()
