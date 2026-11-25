"""One synthetic exam-length run -> monitor-mode pcap + ground-truth labels.

    python -m synth.netsim.scenario --preset esp32_lan --subject math --mcs 7 --resolution 1600x1200 \
        --seed 0 --out data/synth/netsim/run.pcap [--duration 600] [--t0 1764000000]

Outputs next to --out:
  <stem>.pcap              radiotap (Flags only) + 802.11 headers; orig_len carries the true frame length
  <stem>.labels.jsonl      one line per image upload (same keys as capture/scenario_upload.py + extras)
  <stem>.background.jsonl  one line per background page load (browse presets)
  <stem>.meta.json         parameters, provenance (sha256 of measured inputs), simulator counters
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Optional

import numpy as np

from capture.dot11 import build_ctrl_frame, build_dot11_header, build_radiotap
from capture.pcap_io import PcapWriter
from common.config import load_config
from synth.dist import QuantileDist
from synth.netsim.apps import AppBytes, hosts_from_measured, http_upload_lan, https_upload_wan, page_load
from synth.netsim.images import jpeg_bytes
from synth.netsim.net import Topology
from synth.netsim.sim import CapturedFrame

RADIOTAP = build_radiotap(None, None, None)


def _sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def subject_timing(ns: dict, subject: str) -> tuple[float, float]:
    s = ns["exam_subjects"][subject]
    dur = float(s["minutes"]) * 60.0
    return dur / float(s["questions"]), dur


def frame_record(f: CapturedFrame) -> tuple[bytes, int]:
    if f.ftype == 1:
        body = 4 if f.subtype == 9 else 0      # BlockAck: BA Control 2 + SSC 2 (+ bitmap in orig_len)
        hdr = build_ctrl_frame(f.subtype, f.addr1, f.addr2, body)
    else:
        hdr = build_dot11_header(f.ftype, f.subtype, f.addr1, f.addr2, f.addr3, f.to_ds, f.from_ds,
                                 f.protected, f.retry, f.seq)
    return RADIOTAP + hdr, len(RADIOTAP) + max(f.frame_len, len(hdr))


def run(cfg: dict, preset: str, subject: Optional[str], mcs: int, resolution: Optional[str], seed: int,
        out: str | Path, t0: float = 0.0, duration_s: Optional[float] = None,
        interval_s: Optional[float] = None, wan_host: Optional[str] = None,
        upload_times: Optional[list[float]] = None) -> dict:
    """Simulate one run. Uploads are periodic (interval_s, random phase) unless upload_times (absolute
    timestamps) are given, e.g. to place uploads inside acted behaviour for an offline demo."""
    ns = cfg["synth"]["netsim"]
    pr = ns["presets"][preset]
    rng = np.random.default_rng(seed)
    meas = ns["measured"]
    app = json.loads(Path(meas["app_bytes"]).read_text())
    rtt = json.loads(Path(meas["wan_rtt"]).read_text())
    ab = AppBytes.from_measured(app, ns["tls"])
    hosts = hosts_from_measured(rtt, app)
    topo = Topology(ns, mcs, pr["device"], rng, ns["macs"])

    if subject is not None:
        iv, dur = subject_timing(ns, subject)
        interval_s = interval_s or iv
        duration_s = duration_s or dur
    if duration_s is None:
        raise ValueError("give --subject or --duration")
    t_stop = t0 + duration_s

    uploads: list[dict] = []
    pages: list[dict] = []
    img = ns["image"]
    body = None
    host = None
    if pr.get("upload"):
        if (interval_s is None and upload_times is None) or resolution is None:
            raise ValueError("an upload preset needs --resolution and an interval (--subject/--interval) "
                             "or explicit upload times")
        body = jpeg_bytes(img["reference"], resolution, int(img["jpeg_quality"]))
        phase = float(rng.random()) * interval_s if interval_s else 0.0   # random phase in the first interval
        host = next(h for h in hosts if h.name == wan_host) if wan_host else hosts[int(rng.integers(len(hosts)))]
        if upload_times is None:
            upload_times = []
            k, t = 0, t0 + phase
            while t < t_stop:
                upload_times.append(t)
                k += 1
                t = t0 + phase + k * interval_s
        for t in upload_times:
            if pr["upload"] == "wan":
                https_upload_wan(topo, host, t, body, ab, uploads.append)
            else:
                http_upload_lan(topo, pr["upload"], t, body, ab, uploads.append)

    if pr.get("browse"):
        pw = QuantileDist({"min": ns["page_weight_bytes"]["p10"], "max": ns["page_weight_bytes"]["p90"],
                           **ns["page_weight_bytes"]})

        def next_page(_lab=None):
            if _lab is not None:
                pages.append(_lab)
            if topo.loop.t < t_stop:                       # back-to-back page loads (worst case: no think time)
                h = hosts[int(rng.integers(len(hosts)))]
                page_load(topo, h, topo.loop.t, int(pw.sample(rng)), ab, next_page)
        topo.loop.at(t0, next_page)

    topo.loop.run()
    frames = sorted(topo.sniffer.frames, key=lambda f: f.ts)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        w = PcapWriter(f)
        for fr in frames:
            data, orig = frame_record(fr)
            w.write(fr.ts, data, orig)
    uploads.sort(key=lambda d: d["t_start"])
    with open(out.with_suffix(".labels.jsonl"), "w") as f:
        for i, u in enumerate(uploads):
            u.update(seq=i, resolution=resolution, quality=int(img["jpeg_quality"]), mcs=mcs, preset=preset,
                     status=200, synthetic=True)
            f.write(json.dumps(u) + "\n")
    with open(out.with_suffix(".background.jsonl"), "w") as f:
        for p in sorted(pages, key=lambda d: d["t_start"]):
            f.write(json.dumps(p) + "\n")
    meta = {
        "preset": preset, "device": pr["device"], "subject": subject, "mcs": mcs, "resolution": resolution,
        "seed": seed, "t0": t0, "duration_s": duration_s, "interval_s": interval_s,
        "image_bytes": body, "wan_host": host.name if host else None,
        "inputs": {k: {"path": v, "sha256": _sha(v)} for k, v in
                   {"app_bytes": meas["app_bytes"], "wan_rtt": meas["wan_rtt"], "image": img["reference"]}.items()},
        "frames": len(frames), "uploads": len(uploads), "pages": len(pages),
        "medium_busy_s": topo.medium.airtime_us * 1e-6,
        "collisions": {s.name: s.collisions for s in topo.medium.stations},
        "ppdus": {s.name: s.tx_ppdus for s in topo.medium.stations},
        "devices_mac": ns["macs"]["device"],
    }
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", required=True)
    ap.add_argument("--subject", help="exam subject -> upload interval and run duration (configs exam_subjects)")
    ap.add_argument("--mcs", type=int, required=True)
    ap.add_argument("--resolution")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--t0", type=float, default=0.0, help="timestamp of the run start (epoch seconds)")
    ap.add_argument("--duration", type=float, help="override run length (s)")
    ap.add_argument("--interval", type=float, help="override upload interval (s)")
    ap.add_argument("--wan-host", help="pin the WAN host (default: drawn per run)")
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args()
    meta = run(load_config(*a.config), a.preset, a.subject, a.mcs, a.resolution, a.seed, a.out, a.t0,
               a.duration, a.interval, a.wan_host)
    print(json.dumps({k: meta[k] for k in ("preset", "mcs", "resolution", "frames", "uploads", "pages",
                                           "medium_busy_s", "collisions")}))


if __name__ == "__main__":
    main()
