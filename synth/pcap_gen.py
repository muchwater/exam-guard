"""Synthetic monitor-mode pcap from MEASURED distributions (config-only input).

    python -m synth.pcap_gen --out data/synth/upload.pcap [--config configs/synth_x.yaml]

Inputs (configs: synth.pcap.*): net_event.measure JSON of an upload scenario and of a background
scenario (idle / browsing). Generated traffic:
  * background: renewal process of uplink data frames, IAT ~ background uplink_iat_all,
    size ~ background uplink_size_all
  * uploads every upload_interval_s: frames with size ~ uplink_size_in_upload and
    IAT ~ uplink_iat_in_upload until the on-air bytes reach a draw from air_bytes_per_upload
Output: <out> (radiotap + 802.11 headers, protected data frames with zero payload bytes counted
in orig_len only) and <out stem>.labels.jsonl in the same format as capture/scenario_upload.py.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from capture.dot11 import build_dot11_header, build_radiotap
from capture.pcap_io import PcapWriter
from common.config import load_config, require
from synth.dist import QuantileDist

QOS_DATA = (2, 8)      # type data, subtype QoS data (IEEE 802.11 frame control)


def frame_bytes(size: int, device: str, ap: str, rssi: int, ch_mhz: int, seq: int) -> tuple[bytes, int]:
    """Header-only record; orig_len carries the full on-air length like a snaplen-truncated capture."""
    rt = build_radiotap(rssi, ch_mhz, rate_500kbps=0)
    hdr = build_dot11_header(*QOS_DATA, addr1=ap, addr2=device, addr3=ap, to_ds=True, from_ds=False,
                             protected=True, seq=seq)
    return rt + hdr, len(rt) + max(int(size), len(hdr))


def generate(cfg: dict, out: str | Path) -> dict:
    p = "synth.pcap."
    dur, interval, up_json, bg_json, ch, rssi = require(
        cfg, p + "duration_s", p + "upload_interval_s", p + "measured_upload", p + "measured_background",
        p + "channel_mhz", p + "rssi_dbm")
    s = cfg["synth"]["pcap"]
    rng = np.random.default_rng(s["seed"])
    up = json.loads(Path(up_json).read_text())
    bg = json.loads(Path(bg_json).read_text())
    up_size, up_iat = QuantileDist(up["uplink_size_in_upload"]), QuantileDist(up["uplink_iat_in_upload"])
    up_bytes = QuantileDist(up["air_bytes_per_upload"])
    bg_size, bg_iat = QuantileDist(bg["uplink_size_all"]), QuantileDist(bg["uplink_iat_all"])

    t0 = 0.0
    events: list[tuple[float, int]] = []
    t = t0 + float(bg_iat.sample(rng))
    while t < t0 + dur:
        events.append((t, int(bg_size.sample(rng))))
        t += float(bg_iat.sample(rng))
    labels = []
    k, tu = 0, t0 + float(interval)
    while tu < t0 + dur:
        target, sent, t = float(up_bytes.sample(rng)), 0, tu
        while sent < target:
            size = int(up_size.sample(rng))
            events.append((t, size))
            sent += size
            t += float(up_iat.sample(rng))
        labels.append({"seq": k, "t_start": tu, "t_end": t, "bytes": None, "air_bytes": sent, "synthetic": True})
        k, tu = k + 1, tu + float(interval)

    events.sort()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        w = PcapWriter(f)
        for i, (ts, size) in enumerate(events):
            data, orig = frame_bytes(size, s["device_mac"], s["ap_mac"], int(rssi), int(ch), i)
            w.write(ts, data, orig)
    with open(out.with_suffix(".labels.jsonl"), "w") as f:
        for lab in labels:
            f.write(json.dumps(lab) + "\n")
    return {"frames": len(events), "uploads": len(labels), "out": str(out)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args()
    print(generate(load_config(*a.config), a.out))


if __name__ == "__main__":
    main()
