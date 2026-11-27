"""Evaluate the configured network detector on labeled sessions listed in an index (event level).

    python -m net_event.evaluate --index data/synth/netsim_v1/index.jsonl --split eval \
        --config configs/netsim_v1_derived.yaml --detector rule --out data/measured/eval_rule.json

Per session: detector events vs ground-truth uploads by interval overlap (net_event.labels.match_events),
plus false events per hour, the median event duration and the onset error (event start - upload start)
of matched uploads. Sessions without uploads count only false events.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from capture.macfilter import MacAllowlist
from capture.pcap_io import read_pcap
from common.config import load_config
from net_event.calibrate import load_index
from net_event.detector import build_detector
from net_event.labels import load_intervals, overlaps


def eval_run(args) -> dict:
    cfg, run = args
    pkts = sorted(read_pcap(run["pcap"], MacAllowlist([run["device"]])), key=lambda p: p.ts)
    events = build_detector(cfg).run(pkts)
    gt = load_intervals(run["labels"])
    hit = [any(overlaps(e.t_start, e.t_end, g.t_start, g.t_end) for e in events) for g in gt]
    false_ev = [e for e in events if not any(overlaps(e.t_start, e.t_end, g.t_start, g.t_end) for g in gt)]
    onset = [min((e.t_start - g.t_start for e in events if overlaps(e.t_start, e.t_end, g.t_start, g.t_end)),
                 key=abs) for g, h in zip(gt, hit) if h]
    dur_h = (pkts[-1].ts - pkts[0].ts) / 3600 if len(pkts) > 1 else 0.0
    return {"run": run["run"], "preset": run["preset"], "n_gt": len(gt), "n_hit": int(sum(hit)),
            "n_events": len(events), "n_false": len(false_ev), "hours": dur_h,
            "event_durations": [e.t_end - e.t_start for e in events], "onset_errors": onset}


def summarize(rows: list[dict]) -> dict:
    by = defaultdict(list)
    for r in rows:
        by[r["preset"]].append(r)
    out = {}
    for k, rs in list(by.items()) + [("ALL", rows)]:
        n_gt, n_hit = sum(r["n_gt"] for r in rs), sum(r["n_hit"] for r in rs)
        n_ev, n_false = sum(r["n_events"] for r in rs), sum(r["n_false"] for r in rs)
        hours = sum(r["hours"] for r in rs)
        durs = [d for r in rs for d in r["event_durations"]]
        ons = [o for r in rs for o in r["onset_errors"]]
        out[k] = {"sessions": len(rs), "uploads": n_gt, "recall": n_hit / n_gt if n_gt else None,
                  "events": n_ev, "precision": (n_ev - n_false) / n_ev if n_ev else None,
                  "false_per_hour": n_false / hours if hours else None,
                  "event_dur_median_s": float(np.median(durs)) if durs else None,
                  "event_dur_max_s": float(np.max(durs)) if durs else None,
                  "onset_err_median_s": float(np.median(ons)) if ons else None}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", required=True)
    ap.add_argument("--split", default="eval")
    ap.add_argument("--detector", choices=["rule", "count_rule", "window_model"])
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    cfg = load_config(*a.config)
    if a.detector:
        cfg["net_event"]["detector"] = a.detector
    runs = load_index(a.index, a.split)
    with ProcessPoolExecutor(a.workers) as ex:
        rows = list(ex.map(eval_run, [(cfg, r) for r in runs]))
    summ = summarize(rows)
    res = {"index": a.index, "split": a.split, "detector": cfg["net_event"]["detector"], "configs": a.config,
           "summary": summ, "sessions": rows}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    print(f"detector={res['detector']}")
    fmt = "{:18s} {:>4} {:>7} {:>7} {:>7} {:>9} {:>9} {:>9}"
    print(fmt.format("preset", "ses", "uploads", "recall", "prec", "false/h", "dur_med", "onset_med"))
    for k, s in summ.items():
        f = lambda v, p=3: "-" if v is None else f"{v:.{p}f}"  # noqa: E731
        print(fmt.format(k, s["sessions"], s["uploads"], f(s["recall"]), f(s["precision"]), f(s["false_per_hour"], 1),
                         f(s["event_dur_median_s"], 2), f(s["onset_err_median_s"], 3)))


if __name__ == "__main__":
    main()
