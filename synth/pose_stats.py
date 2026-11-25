"""Per-behavior pose statistics from labeled recordings (input of synth/keypoint_gen.py).

    python -m synth.pose_stats --keypoints data/raw/act_01.kpts.jsonl --labels data/raw/act_01.labels.jsonl \
        --kpt-conf <vision.pose.kpt_conf> --out data/measured/pose_stats.json

Keypoints are expressed in a seat-independent frame: origin = shoulder midpoint, unit = shoulder width.
Frames outside every label interval are counted as "normal". Reported per label: mean and std of each
joint (x, y), mean keypoint confidence, interval durations. Per seat: shoulder midpoint and width in pixels.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

import numpy as np

L_SHO, R_SHO = 5, 6


def normalize(k: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    mid = (k[L_SHO, :2] + k[R_SHO, :2]) / 2
    sw = float(np.hypot(*(k[L_SHO, :2] - k[R_SHO, :2])))
    return (k[:, :2] - mid) / sw, mid, sw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keypoints", action="append", required=True)
    ap.add_argument("--labels", action="append", required=True)
    ap.add_argument("--kpt-conf", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    labels = [json.loads(line) for f in a.labels for line in open(f) if line.strip()]
    poses, confs = defaultdict(list), defaultdict(list)
    seat_mid, seat_sw = defaultdict(list), defaultdict(list)
    for f in a.keypoints:
        for line in open(f):
            fr = json.loads(line)
            for p in fr["persons"]:
                k = np.asarray(p["kpts"], float)
                if p["seat"] is None or k[L_SHO, 2] < a.kpt_conf or k[R_SHO, 2] < a.kpt_conf:
                    continue
                lab = next((lb["label"] for lb in labels if lb["seat"] == p["seat"] and lb["t_start"] <= fr["ts"] <= lb["t_end"]),
                           "normal")
                n, mid, sw = normalize(k)
                poses[lab].append(n)
                confs[lab].append(k[:, 2])
                seat_mid[p["seat"]].append(mid)
                seat_sw[p["seat"]].append(sw)
    durs = defaultdict(list)
    for lb in labels:
        durs[lb["label"]].append(lb["t_end"] - lb["t_start"])
    out = {"source": {"keypoints": a.keypoints, "labels": a.labels, "kpt_conf": a.kpt_conf}, "labels": {}, "seats": {}}
    for lab, ps in poses.items():
        P = np.stack(ps)
        out["labels"][lab] = {"n_frames": len(ps), "mean": P.mean(0).tolist(), "std": P.std(0).tolist(),
                              "conf_mean": np.stack(confs[lab]).mean(0).tolist(), "durations_s": durs.get(lab, [])}
    for s in seat_mid:
        out["seats"][s] = {"shoulder_mid_px": np.mean(seat_mid[s], 0).tolist(), "shoulder_w_px": float(np.mean(seat_sw[s]))}
    json.dump(out, open(a.out, "w"), indent=1)
    print({lab: v["n_frames"] for lab, v in out["labels"].items()})


if __name__ == "__main__":
    main()
