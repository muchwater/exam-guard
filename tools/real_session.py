"""Turn a real capture session into net_event index rows (sniffer clock), via in-band clock markers.

    python -m tools.real_session offset --session data/raw/real/<s> --device <team MAC>
    python -m tools.real_session index --session data/raw/real/<s> --device <team MAC> --preset real_board \
        --segments 4 --split alternate --out data/raw/real/index.jsonl

Session directory (tools/ota_session.py on the uploader + tools/linux_monitor_capture.sh on the sniffer):
  capture.pcap            sniffer capture (sniffer clock)
  uploads.labels.jsonl    ground-truth uploads (uploader clock)
  background.jsonl        other activities (uploader clock), optional
  markers.jsonl           {udp_payload, ip, t_send} clock markers (uploader clock)
Clock offset (uploader - sniffer): each marker is a UDP datagram of a distinct size; its first transmission
is the device's data frame with MPDU length payload + UDP 8 + IP (40 for IPv6, 20 for IPv4) + QoS MAC header 26
+ CCMP 8 + LLC/SNAP 8 + MIC 8 (FCS excluded; same constants as configs/default.yaml synth.netsim.encap). The
offset is the median over markers of (t_send - t_first_frame), t_first_frame being the earliest candidate within
half the marker spacing of the consensus (later ones are retries); its error is the device's queueing + air time (sub-ms here).
--offset S skips the markers when the sniffer and the uploader share one clock (e.g. the board's own dongle sniffing
the board's uploads: offset 0). --ba-recon first reconstructs the device's MPDUs from BlockAck frames
(capture/ba_recon.py), for sniffers that cannot decode its data frames (RTL8192EU, LDPC).
Segments cut the session at the midpoints of the largest gaps between activities so that
calibrate/evaluate can treat them as separate sessions (leave-one-session-out). Labels and pcap slices are
written next to the capture (data/raw is not committed; rows carry the real MAC).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from capture.macfilter import MacAllowlist
from capture.pcap_io import iter_raw, read_pcap, write_pcap
from capture.ba_recon import reconstruct
from capture.dot11 import LINKTYPE_IEEE802_11_RADIOTAP, parse_frame
from tools.ota_session import MARKER_SPACING_S

UDP_HDR, QOS_HDR, CCMP_HDR, LLC, MIC = 8, 26, 8, 8, 8   # see module docstring
IP_HDR = {4: 20, 6: 40}


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in open(p) if line.strip()] if p.exists() else []


def marker_offset(pcap: Path, markers: list[dict], device: str) -> tuple[float, list[float]]:
    dev = device.lower()
    up = [p for p in read_pcap(pcap, MacAllowlist([dev])) if p.ftype == 2 and p.addr2 == dev]
    deltas = []
    for m in markers:
        n = m["udp_payload"] + UDP_HDR + IP_HDR[m.get("ip", 6)] + QOS_HDR + CCMP_HDR + LLC + MIC
        c = [p.ts - m["t_send"] for p in up if p.frame_len == n]
        if c:
            deltas.append(c)          # several candidates: retries, or the same size sent in another block
    if not deltas:
        raise RuntimeError("no marker frame found: wrong device MAC, or the sniffer missed every marker")
    flat = np.concatenate([np.asarray(c) for c in deltas])
    center = float(np.median(flat))
    center = float(np.median([min(c, key=lambda d: abs(d - center)) for c in deltas]))   # consensus
    # per marker: the EARLIEST candidate within half the marker spacing of the consensus is the first
    # transmission (later ones are retries); markers with none matched another block of the same size
    picked = [min(w) for c in deltas if (w := [d for d in c if abs(d - center) <= MARKER_SPACING_S / 2])]
    return -float(np.median(picked)), picked          # uploader - sniffer


def shift(recs: list[dict], off: float) -> list[dict]:
    return [{**r, "t_start": r["t_start"] - off, "t_end": r["t_end"] - off} for r in recs]


def cut_points(acts: list[dict], n_seg: int) -> list[float]:
    acts = sorted(acts, key=lambda r: r["t_start"])
    gaps = [(acts[i + 1]["t_start"] - acts[i]["t_end"], (acts[i]["t_end"] + acts[i + 1]["t_start"]) / 2)
            for i in range(len(acts) - 1)]
    return sorted(m for _, m in sorted(gaps, reverse=True)[: n_seg - 1])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["offset", "index"])
    ap.add_argument("--session", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--uploads", default="uploads.labels.jsonl")
    ap.add_argument("--background", default="background.jsonl")
    ap.add_argument("--markers", default="markers.jsonl")
    ap.add_argument("--pcap", default="capture.pcap")
    ap.add_argument("--preset", default="real")
    ap.add_argument("--segments", type=int, default=1)
    ap.add_argument("--split", choices=["alternate", "calib", "eval"], default="alternate")
    ap.add_argument("--out")
    ap.add_argument("--offset", type=float, help="known clock offset uploader - sniffer [s] (skips the markers)")
    ap.add_argument("--ba-recon", action="store_true", help="reconstruct MPDUs from BlockAcks (capture/ba_recon)")
    a = ap.parse_args()
    S = Path(a.session)
    dev = a.device.lower()
    if a.offset is not None:
        off = a.offset
        print(f"clock offset (uploader - sniffer) {off:+.4f} s (given)")
    else:
        off, picked = marker_offset(S / a.pcap, load_jsonl(S / a.markers), dev)
        print(f"clock offset (uploader - sniffer) {off:+.4f} s from {len(picked)} markers "
              f"(spread {1e3 * (max(picked) - min(picked)):.2f} ms)")
    if a.cmd == "offset":
        return
    ups = shift(load_jsonl(S / a.uploads), off)
    bg = shift(load_jsonl(S / a.background), off)
    cuts = cut_points(ups + bg, a.segments) if a.segments > 1 else []
    edges = [-np.inf] + cuts + [np.inf]
    raw = list(iter_raw(open(S / a.pcap, "rb")))
    if a.ba_recon:
        frames, counts = reconstruct([(ts, d) for ts, d, _o, _lt in raw], [dev])
        print("BA reconstruction:", {d: n for (_dev, d), n in counts.items()})
        raw = [(ts, d, len(d), LINKTYPE_IEEE802_11_RADIOTAP) for ts, d in frames]
    allow = MacAllowlist([dev])
    out_rows = []
    for k in range(len(edges) - 1):
        lo, hi = edges[k], edges[k + 1]
        tag = f"seg{k}"
        u = [r for r in ups if lo <= r["t_start"] < hi]
        b = [r for r in bg if lo <= r["t_start"] < hi]
        frames = []
        for ts, data, orig, lt in raw:
            if lo <= ts < hi:
                p = parse_frame(ts, data, orig, lt)
                if p is not None and allow.match(p):
                    frames.append((ts, data))
        if not frames:
            continue
        pcap = S / f"{tag}.pcap"
        write_pcap(pcap, frames)
        for name, recs in ((f"{tag}.labels.jsonl", u), (f"{tag}.background.jsonl", b)):
            (S / name).write_text("".join(json.dumps(r) + "\n" for r in recs))
        split = a.split if a.split != "alternate" else ("calib" if k % 2 == 0 else "eval")
        out_rows.append({"run": f"{S.name}/{tag}", "preset": a.preset, "split": split, "device": dev,
                         "pcap": str(pcap), "labels": str(S / f"{tag}.labels.jsonl"),
                         "background": str(S / f"{tag}.background.jsonl"), "t0": frames[0][0],
                         "uploads": len(u), "clock_offset_s": off, "source": "real",
                         **({"recon": "ba"} if a.ba_recon else {})})
        print(f"{tag}: {len(frames)} frames, {len(u)} uploads, {len(b)} background, split {split}")
    if a.out:
        with open(a.out, "a") as f:
            for r in out_rows:
                f.write(json.dumps(r) + "\n")
        print(f"appended {len(out_rows)} rows -> {a.out}")


if __name__ == "__main__":
    main()
