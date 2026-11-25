"""Generate a synthetic run set (parallel) with a calib / eval split.

    python -m synth.netsim.dataset --out data/synth/netsim_v1 --runs-per-preset 8 --seed 0 \
        --browse-duration 300 [--workers 24]

Each run draws subject, MCS and resolution uniformly from the configured lists (synth.netsim.*).
Runs alternate between the calib and eval split per preset, so both splits contain every preset.
--browse-duration caps runs that include background browsing (dataset size only: back-to-back page
loads produce ~3,000 frames/s). Writes <out>/index.jsonl.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from common.config import load_config
from synth.netsim.scenario import run


def _job(args) -> dict:
    cfg, spec = args
    meta = run(cfg, spec["preset"], spec["subject"], spec["mcs"], spec["resolution"], spec["seed"],
               spec["pcap"], spec["t0"], spec["duration_s"])
    return {**spec, "frames": meta["frames"], "uploads": meta["uploads"], "pages": meta["pages"],
            "wan_host": meta["wan_host"], "image_bytes": meta["image_bytes"]}


def plan(cfg: dict, out: Path, runs_per_preset: int, seed: int, browse_duration: float,
         presets: list[str] | None = None) -> list[dict]:
    ns = cfg["synth"]["netsim"]
    rng = np.random.default_rng(seed)
    subjects = list(ns["exam_subjects"])
    specs = []
    for pname in presets or list(ns["presets"]):
        pr = ns["presets"][pname]
        for k in range(runs_per_preset):
            i = len(specs)
            stem = out / "runs" / f"{i:04d}_{pname}"
            specs.append({
                "run": i, "preset": pname, "split": "calib" if k % 2 == 0 else "eval",
                "subject": subjects[int(rng.integers(len(subjects)))],
                "mcs": int(ns["mcs"][int(rng.integers(len(ns["mcs"])))]),
                "resolution": str(ns["image"]["resolutions"][int(rng.integers(len(ns["image"]["resolutions"])))]),
                "seed": seed * 100000 + i, "t0": 0.0,
                "duration_s": browse_duration if pr.get("browse") else None,
                "device": ns["macs"]["device"],
                "pcap": str(stem.with_suffix(".pcap")), "labels": str(stem.with_suffix(".labels.jsonl")),
                "background": str(stem.with_suffix(".background.jsonl")),
            })
    return specs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs-per-preset", type=int, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--browse-duration", type=float, required=True)
    ap.add_argument("--preset", action="append", help="restrict to these presets")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args()
    cfg = load_config(*a.config)
    out = Path(a.out)
    specs = plan(cfg, out, a.runs_per_preset, a.seed, a.browse_duration, a.preset)
    (out / "runs").mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as ex:
        rows = list(ex.map(_job, [(cfg, s) for s in specs]))
    with open(out / "index.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    for r in rows:
        print(f"{r['run']:4d} {r['split']:5s} {r['preset']:16s} mcs={r['mcs']} {r['resolution']:9s} "
              f"{r['subject']:8s} uploads={r['uploads']:3d} pages={r['pages']:5d} frames={r['frames']}")


if __name__ == "__main__":
    main()
