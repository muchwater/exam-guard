"""Synthetic COCO-17 keypoint sequences from MEASURED pose statistics (config-only input).

    python -m synth.keypoint_gen --out data/synth/kpts.jsonl [--config ...]

Inputs (configs: synth.keypoints.*): fps, duration_s, measured_pose_stats (synth/pose_stats.py output).
For each seat in the stats, the timeline alternates "normal" with the other labels in random order;
each segment length is drawn from that label's measured durations (resampled), and every frame's pose
is the label's measured mean + Gaussian noise with the measured per-joint std, mapped back to pixels with
the seat's measured shoulder midpoint/width. Transitions are abrupt (no motion model) -- a known limitation.
Writes <out> (same JSONL format as vision.pipeline --dump-keypoints) and <out stem>.labels.jsonl.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterator

import numpy as np

from common.config import load_config, require
from common.events import Person


def generate(stats: dict, fps: float, duration_s: float, seed: int) -> tuple[list[dict], list[dict]]:
    rng = np.random.default_rng(seed)
    labs = stats["labels"]
    abnormal = [x for x in labs if x != "normal"]
    frames: dict[float, list] = {}
    labels = []
    for seat, geo in stats["seats"].items():
        mid, sw = np.asarray(geo["shoulder_mid_px"]), geo["shoulder_w_px"]
        t, turn = 0.0, 0
        while t < duration_s:
            lab = "normal" if turn % 2 == 0 or not abnormal else abnormal[rng.integers(len(abnormal))]
            durs = labs[lab]["durations_s"] or labs["normal"]["durations_s"]
            if not durs:
                raise ValueError(f"no measured durations for '{lab}' (and none for 'normal')")
            d = float(rng.choice(durs))
            t_end = min(duration_s, t + d)
            if lab != "normal":
                labels.append({"t_start": t, "t_end": t_end, "label": lab, "seat": seat, "synthetic": True})
            mean, std = np.asarray(labs[lab]["mean"]), np.asarray(labs[lab]["std"])
            conf = np.asarray(labs[lab]["conf_mean"])
            for i in range(int(np.ceil(t * fps)), int(np.ceil(t_end * fps))):
                ts = i / fps
                xy = (mean + rng.normal(0, 1, mean.shape) * std) * sw + mid
                frames.setdefault(ts, []).append({"seat": seat, "score": 1.0, "bbox": None,
                                                  "kpts": np.column_stack([xy, conf]).tolist()})
            t, turn = t_end, turn + 1
    out = [{"ts": ts, "frame_idx": i, "persons": frames[ts]} for i, ts in enumerate(sorted(frames))]
    return out, labels


def iter_persons(frames: list[dict]) -> Iterator[tuple[float, list[Person]]]:
    """Feed synthetic frames to vision.action.ActionRecognizer.update(ts, persons)."""
    for fr in frames:
        ps = []
        for p in fr["persons"]:
            k = np.asarray(p["kpts"], np.float32)
            bbox = np.array([k[:, 0].min(), k[:, 1].min(), k[:, 0].max(), k[:, 1].max()], np.float32)
            ps.append(Person(bbox=bbox, score=p["score"], keypoints=k, seat=p["seat"]))
        yield fr["ts"], ps


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args()
    cfg = load_config(*a.config)
    k = "synth.keypoints."
    fps, dur, stats_path = require(cfg, k + "fps", k + "duration_s", k + "measured_pose_stats")
    frames, labels = generate(json.loads(Path(stats_path).read_text()), float(fps), float(dur),
                              int(cfg["synth"]["keypoints"]["seed"]))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for fr in frames:
            f.write(json.dumps(fr) + "\n")
    with open(out.with_suffix(".labels.jsonl"), "w") as f:
        for lab in labels:
            f.write(json.dumps(lab) + "\n")
    print(f"{len(frames)} frames, {len(labels)} abnormal segments -> {out}")


if __name__ == "__main__":
    main()
