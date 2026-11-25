"""Measure traffic distributions from a real capture -> JSON that configs/ and synth/ cite.

    python -m net_event.measure data/raw/upload_5s_720p.pcap --device aa:bb:cc:dd:ee:ff \
        --labels data/raw/upload_5s_720p.labels.jsonl --offset 0.012 --out data/measured/upload_5s_720p.json

Reported (all from the data, nothing assumed):
  * uplink data frame size: quantiles + histogram, inside vs outside labeled uploads
  * uplink IAT: quantiles inside vs outside labeled uploads
  * per labeled upload: bytes on air, frame count, duration, max intra-upload IAT
  * gaps between consecutive uploads (end -> next start)
These are the inputs for burst_gap_s, large_frame_bytes, min_burst_bytes, etc.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

import numpy as np

from capture.macfilter import MacAllowlist
from capture.pcap_io import read_pcap
from net_event.features import is_uplink_data
from net_event.labels import load_intervals

QUANTILES = [0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]


def _q(x) -> Optional[dict]:
    x = np.asarray(x, float)
    if x.size == 0:
        return None
    return {"n": int(x.size), "min": float(x.min()), "max": float(x.max()), "mean": float(x.mean()),
            **{f"p{int(q * 100):02d}": float(np.quantile(x, q)) for q in QUANTILES}}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def measure(pcap: str, device: str, labels: Optional[str], offset_s: float) -> dict:
    pkts = read_pcap(pcap, MacAllowlist([device]))
    up = [p for p in pkts if is_uplink_data(p, device)]
    ts = np.asarray([p.ts for p in up])
    sizes = np.asarray([p.frame_len for p in up])
    res: dict = {
        "source": {"pcap": str(pcap), "sha256": _sha256(pcap), "device": device, "labels": labels,
                   "label_offset_s": offset_s, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
        "n_frames_total": len(pkts),
        "n_uplink_data": len(up),
        "capture_span_s": float(ts[-1] - ts[0]) if ts.size > 1 else 0.0,
        "uplink_size_all": _q(sizes),
        "uplink_size_hist": dict(zip(*[a.tolist() for a in np.unique(sizes, return_counts=True)])) if sizes.size else {},
        "uplink_iat_all": _q(np.diff(ts)),
        "retry_ratio": float(np.mean([p.retry for p in up])) if up else None,
    }
    if labels:
        gt = load_intervals(labels, offset_s)
        inside = np.zeros(ts.size, bool)
        per = []
        for g in gt:
            m = (ts >= g.t_start) & (ts <= g.t_end)
            inside |= m
            t_in = ts[m]
            per.append({"seq": g.meta.get("seq"), "app_bytes": g.meta.get("bytes"),
                        "air_bytes": int(sizes[m].sum()), "frames": int(m.sum()),
                        "label_duration_s": g.t_end - g.t_start,
                        "air_span_s": float(t_in[-1] - t_in[0]) if t_in.size > 1 else 0.0,
                        "max_intra_iat_s": float(np.diff(t_in).max()) if t_in.size > 1 else None})
        iat = np.diff(ts)
        both_in = inside[1:] & inside[:-1]
        res.update({
            "uplink_size_in_upload": _q(sizes[inside]),
            "uplink_size_outside_upload": _q(sizes[~inside]),
            "uplink_iat_in_upload": _q(iat[both_in]),
            "uplink_iat_outside_upload": _q(iat[~both_in]),
            "per_upload": per,
            "air_bytes_per_upload": _q([p["air_bytes"] for p in per]),
            "app_bytes_per_upload": _q([p["app_bytes"] for p in per if p["app_bytes"] is not None]),
            "upload_duration_s": _q([p["label_duration_s"] for p in per]),
            "gap_between_uploads_s": _q([b.t_start - a.t_end for a, b in zip(gt, gt[1:])]),
        })
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcap")
    ap.add_argument("--device", required=True, help="uploader MAC")
    ap.add_argument("--labels", help="ground-truth upload log (jsonl)")
    ap.add_argument("--offset", type=float, default=0.0, help="uploader clock - sniffer clock [s], measured")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = measure(a.pcap, a.device.lower(), a.labels, a.offset)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k not in ("uplink_size_hist", "per_upload")}, indent=1))


if __name__ == "__main__":
    main()
