"""Scripted over-the-air traffic on the board for real-capture sessions (positives + realistic negatives).

    bash tools/ota_netns.sh up <eth> $(python -m tools.ota_session --print-blob-sizes)   # -> server address
    python -m tools.ota_session --server <fe80::...> --plan calib --seed 1 --out data/raw/real/<session>
    bash tools/ota_netns.sh down

All traffic goes from this device's Wi-Fi (--wlan) to the server address "<addr>%<wlan>" (see ota_netns.sh),
so the only frames on the air are this team device's. Every activity is logged on this device's clock:
  activities.jsonl   every activity {kind, t_start, t_end, bytes, ...}
  uploads.labels.jsonl  kind == upload (ground truth for net_event; same fields as capture.scenario_upload)
  background.jsonl   everything else (page loads, ping patterns, idle)
  markers.jsonl      clock markers: IPv6 UDP datagrams of distinct sizes (tools.real_session pairs them with
                     the sniffer's frames to get the clock offset)
Plans (values from configs/default.yaml unless noted):
  smoke  2 markers blocks, 1 VGA upload, 1 p50 page load (setup check, ~30 s)
  calib  every synth.netsim.image.resolutions x REPS uploads in random order, gaps U[GAP_MIN, GAP_MAX];
         after every len(resolutions) uploads one background block: page loads (each synth.netsim
         page_weight_bytes percentile once, back to back), sparse large pings, small periodic pings, idle;
         then CONCURRENT uploads made while page loads run (browse + upload); ~16 min
JPEG payloads are synth.netsim.images.jpeg_encode (the synthetic set's exact bytes).
"""
from __future__ import annotations

import argparse
import http.client
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from common.config import load_config
from synth.netsim.images import jpeg_encode

REPS = 4             # analysis choice: uploads per resolution (24 positives per calib session)
GAP_MIN = 12.0       # source: > max(net_event.window_model.windows_s) = 10 s, so no window spans two uploads
GAP_MAX = 25.0       # analysis choice: bounds the session length (~16 min)
CONCURRENT = 4       # analysis choice: browse + upload cases per session
PING_LARGE = (1400, 0.5, 20)   # bytes, interval s, duration s: the sparse large-frame pattern that produced
#                                window-model false events in docs/real_capture_spark3.md
PING_SMALL = (56, 1.0, 30)     # ping(8) default payload 56 B at its default 1 s interval, 30 s
IDLE_S = 30.0        # analysis choice
MARKER_SIZES = [1000 + 7 * i for i in range(10)]   # distinct IPv6 UDP payloads; MPDU = payload + 98 B
MARKER_SPACING_S = 0.2                             # analysis choice: send spacing between markers


class Session:
    def __init__(self, server: str, wlan: str, out: Path):
        self.host = f"{server}%{wlan}"
        self.wlan = wlan
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.files = {k: open(out / f"{k}.jsonl", "a") for k in ("activities", "markers")}
        self.lock = threading.Lock()

    def log(self, rec: dict, kind: str = "activities") -> None:
        with self.lock:
            self.files[kind].write(json.dumps(rec) + "\n")
            self.files[kind].flush()

    def markers(self) -> None:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        addr = socket.getaddrinfo(self.host, 9, socket.AF_INET6, socket.SOCK_DGRAM)[0][4]
        for n in MARKER_SIZES:
            t = time.time()
            s.sendto(bytes(n), addr)
            self.log({"udp_payload": n, "ip": 6, "t_send": t}, "markers")
            time.sleep(MARKER_SPACING_S)
        s.close()

    def upload(self, body: bytes, resolution: str, seq: int, concurrent: str | None = None) -> None:
        t0 = time.time()
        c = http.client.HTTPConnection(self.host, 8080, timeout=30)
        c.request("POST", "/upload", body, {"Content-Type": "image/jpeg"})
        st = c.getresponse().status
        c.close()
        self.log({"kind": "upload", "seq": seq, "t_start": t0, "t_end": time.time(), "bytes": len(body),
                  "resolution": resolution, "status": st, "concurrent": concurrent})

    def page_load(self, nbytes: int) -> None:
        t0 = time.time()
        c = http.client.HTTPConnection(self.host, 8081, timeout=60)
        c.request("GET", f"/{nbytes}.bin")
        r = c.getresponse()
        got = len(r.read())
        c.close()
        self.log({"kind": "page_load", "t_start": t0, "t_end": time.time(), "bytes": got, "status": r.status})

    def ping(self, kind: str, size: int, interval: float, duration: float) -> None:
        t0 = time.time()
        subprocess.run(["ping", "-6", "-q", "-s", str(size), "-i", str(interval), "-w", str(int(duration)),
                        self.host], capture_output=True)
        self.log({"kind": kind, "t_start": t0, "t_end": time.time(), "bytes": size, "interval_s": interval})

    def idle(self, d: float) -> None:
        t0 = time.time()
        time.sleep(d)
        self.log({"kind": "idle", "t_start": t0, "t_end": time.time()})

    def close(self) -> None:
        for f in self.files.values():
            f.close()
        acts = [json.loads(line) for line in open(self.out / "activities.jsonl")]
        with open(self.out / "uploads.labels.jsonl", "w") as f:
            for a in acts:
                if a["kind"] == "upload":
                    f.write(json.dumps(a) + "\n")
        with open(self.out / "background.jsonl", "w") as f:
            for a in acts:
                if a["kind"] != "upload":
                    f.write(json.dumps(a) + "\n")


def page_sizes(cfg: dict) -> list[int]:
    return [int(v) for v in cfg["synth"]["netsim"]["page_weight_bytes"].values()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server")
    ap.add_argument("--wlan", default="wlan0")
    ap.add_argument("--plan", choices=["smoke", "calib"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out")
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--print-blob-sizes", action="store_true", help="download sizes the server must host")
    a = ap.parse_args()
    cfg = load_config(*a.config)
    if a.print_blob_sizes:
        print(" ".join(str(n) for n in page_sizes(cfg)))
        return
    img = cfg["synth"]["netsim"]["image"]
    ref, q, resolutions = img["reference"], int(img["jpeg_quality"]), list(img["resolutions"])
    rng = np.random.default_rng(a.seed)
    out = Path(a.out)
    (out / "session.json").parent.mkdir(parents=True, exist_ok=True)
    (out / "session.json").write_text(json.dumps({"plan": a.plan, "seed": a.seed, "wlan": a.wlan, "server": a.server,
                                                  "image": ref, "quality": q, "started": time.time()}, indent=1))
    s = Session(a.server, a.wlan, out)
    s.markers()
    seq = 0
    if a.plan == "smoke":
        s.idle(2.0)
        s.upload(jpeg_encode(ref, "640x480", q), "640x480", seq)
        s.idle(3.0)
        s.page_load(page_sizes(cfg)[2])
        s.idle(2.0)
    else:
        s.idle(IDLE_S)
        order = [r for r in resolutions for _ in range(REPS)]
        rng.shuffle(order)
        for i, res in enumerate(order):
            s.upload(jpeg_encode(ref, res, q), res, seq)
            seq += 1
            time.sleep(float(rng.uniform(GAP_MIN, GAP_MAX)))
            if (i + 1) % len(resolutions) == 0:                      # background block
                for n in rng.permutation(page_sizes(cfg)):
                    s.page_load(int(n))
                time.sleep(GAP_MIN)
                s.ping("ping_large", *PING_LARGE)
                time.sleep(GAP_MIN)
                s.ping("ping_small", *PING_SMALL)
                s.idle(IDLE_S)
        for _ in range(CONCURRENT):                                  # browse + upload
            stop = threading.Event()
            brng = np.random.default_rng(int(rng.integers(1 << 31)))   # own generator: rng is not thread-safe

            def browse(stop: threading.Event = stop, brng: np.random.Generator = brng) -> None:
                while not stop.is_set():
                    s.page_load(int(brng.choice(page_sizes(cfg))))
            th = threading.Thread(target=browse)
            th.start()
            time.sleep(float(rng.uniform(2.0, 4.0)))
            res = str(rng.choice(resolutions))
            s.upload(jpeg_encode(ref, res, q), res, seq, concurrent="page_load")
            seq += 1
            time.sleep(2.0)
            stop.set()
            th.join()
            time.sleep(float(rng.uniform(GAP_MIN, GAP_MAX)))
    s.markers()
    s.close()
    print(f"done: {seq if a.plan == 'calib' else 1} uploads -> {out}")


if __name__ == "__main__":
    main()
