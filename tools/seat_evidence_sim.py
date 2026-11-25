"""How many uploads until fusion/seat_evidence singles out the cheating seat? (analysis, not a detector)

    python -m tools.seat_evidence_sim --subject math --seats 2 --seats 10 --seats 30 \
        --recall 1.0 --recall 0.8 --recall 0.5 --trials 200 --alpha 0.05 --out data/measured/seat_evidence_sim.json

Normal seats: behaviour timelines are REAL B1 events of ExamCheat-MultiV Normal clips (per-frame head_drop
from tools.examcheat_extract, segmented with vision.action.TemporalSegmenter at the given threshold and
max_gap_s, min duration 0), clips drawn at random and stitched end to end to cover the exam. Each
threshold is a percentile of Normal head_drop frames (analysis grid, --percentile).
Cheating seat: shows B1 around an upload with probability --recall (unknown until the acted AI-glasses
pose is recorded, hence swept); its other time is a Normal timeline as well.
Uploads: one per question at the exam timing (synth.netsim.exam_subjects), duration taken from the
synthetic eval set median (passed with --upload-s). fusion pre/post = 0 (same clock in this analysis).
Reported: for each (threshold, seats, recall), the fraction of trials in which the cheater is ranked first
with Bonferroni p_adj < alpha, after k uploads (k = 1..N), the median k at which this first holds, and the
fraction of trials in which a NORMAL seat was ever marked identified (false accusation) at any k.
"""
from __future__ import annotations

import argparse
import csv
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from common.config import load_config
from common.events import BehaviorEvent, NetEvent
from fusion.seat_evidence import SeatEvidence
from vision.action import TemporalSegmenter

DERIVED = Path("data/derived/examcheat")


def normal_clips() -> list[tuple[np.ndarray, np.ndarray]]:
    idx = json.loads((DERIVED / "index.json").read_text())
    out = []
    for c in idx:
        if c["cls"] != "normal" or not c["usable_for_stats"]:
            continue
        ts, hd = [], []
        with open(DERIVED / "features" / f"{c['clip_id']}.csv") as f:
            for r in csv.DictReader(f):
                ts.append(float(r["ts"]))
                hd.append(float(r["head_drop"]) if r["head_drop"] not in ("", "nan") else np.nan)
        out.append((np.asarray(ts), np.asarray(hd)))
    return out


def clip_events(ts: np.ndarray, hd: np.ndarray, thr: float, max_gap: float) -> list[tuple[float, float]]:
    seg = TemporalSegmenter(0.0, max_gap)
    ev = []
    for t, h in zip(ts, hd):
        ev += seg.update("x", float(t), ("b1", 1.0) if np.isfinite(h) and h > thr else None)
    ev += seg.flush()
    return [(e.t_start, e.t_end) for e in ev]


def timeline(rng, clips, t_end) -> list[tuple[float, float]]:
    out, t = [], 0.0
    while t < t_end:
        ts, ev = clips[int(rng.integers(len(clips)))]
        out += [(t + a, t + b) for a, b in ev]
        t += float(ts[-1] - ts[0]) + float(np.median(np.diff(ts)))
    return out


def trial(rng, clips, n_seats, recall, interval, n_up, up_s, alpha) -> list[bool]:
    t_end = interval * (n_up + 1)
    ev = SeatEvidence([f"S{i}" for i in range(n_seats)], 0.0, 0.0, alpha=alpha)
    ev.observe(0.0)
    ev.observe(t_end)
    ups = [interval * (i + 1) for i in range(n_up)]
    for t in ups:
        ev.add_net(NetEvent(t, t + up_s, "dev", 1.0, "sim"))
    for s in range(n_seats):
        for a, b in timeline(rng, clips, t_end):
            ev.add_behavior(BehaviorEvent(a, b, f"S{s}", "head_down_hold", 1.0))
    for t in ups:                                  # cheater = S0
        if rng.random() < recall:
            ev.add_behavior(BehaviorEvent(t - 1.0, t + up_s, "S0", "head_down_hold", 1.0))
    tab = ev.table("dev")
    ranks = [ev.ranking("dev", k, tab) for k in range(1, n_up + 1)]
    return ([r[0].seat == "S0" and r[0].identified for r in ranks],
            [r[0].seat != "S0" and r[0].identified for r in ranks])


def _cell(job) -> dict:
    cell, clips, interval, n_up, up_s, alpha, trials, seed = job
    rng = np.random.default_rng(seed)
    res = [trial(rng, clips, cell["seats"], cell["recall"], interval, n_up, up_s, alpha) for _ in range(trials)]
    ok = np.array([r[0] for r in res])
    wrong = np.array([r[1] for r in res])
    first = [int(np.argmax(row)) + 1 if row.any() else None for row in ok]
    done = [f for f in first if f is not None]
    return {**cell, "p_identified_after_k": ok.mean(0).round(3).tolist(),
            "median_first_k": float(np.median(done)) if len(done) > trials / 2 else None,
            "identified_by_end": float(ok[:, -1].mean()),
            "wrong_seat_ever": float(wrong.any(1).mean())}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--seats", type=int, action="append", required=True)
    ap.add_argument("--recall", type=float, action="append", required=True)
    ap.add_argument("--percentile", type=float, action="append", required=True,
                    help="B1 threshold = this percentile of Normal head_drop frames")
    ap.add_argument("--upload-s", type=float, required=True, help="upload event duration (s)")
    ap.add_argument("--max-gap", type=float, required=True, help="vision.action.max_gap_s")
    ap.add_argument("--alpha", type=float, required=True)
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = load_config()
    sub = cfg["synth"]["netsim"]["exam_subjects"][a.subject]
    interval, n_up = sub["minutes"] * 60 / sub["questions"], int(sub["questions"])
    raw = normal_clips()
    allhd = np.concatenate([h for _, h in raw])
    allhd = allhd[np.isfinite(allhd)]
    res = {"subject": a.subject, "interval_s": interval, "uploads": n_up, "alpha": a.alpha, "trials": a.trials,
           "normal_clips": len(raw), "upload_s": a.upload_s, "max_gap_s": a.max_gap, "cells": []}
    jobs = []
    for pct in a.percentile:
        thr = float(np.percentile(allhd, pct))
        clips = [(ts, clip_events(ts, hd, thr, a.max_gap)) for ts, hd in raw]
        cover = sum(sum(b - a_ for a_, b in ev) for _, ev in clips) / sum(float(ts[-1] - ts[0]) for ts, _ in clips)
        for m in a.seats:
            for r in a.recall:
                jobs.append((dict(percentile=pct, threshold=thr, normal_coverage=cover, seats=m, recall=r),
                             clips, interval, n_up, a.upload_s, a.alpha, a.trials, a.seed + len(jobs)))
    with ProcessPoolExecutor(a.workers) as ex:
        for cell in ex.map(_cell, jobs):
            res["cells"].append(cell)
            print(f"pct={cell['percentile']:>4} thr={cell['threshold']:+.3f} cover={cell['normal_coverage']:.2f} "
                  f"seats={cell['seats']:3d} recall={cell['recall']:.1f}  median first k={cell['median_first_k']}  "
                  f"identified at end={cell['identified_by_end']:.2f}  wrong seat ever={cell['wrong_seat_ever']:.3f}")
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()
