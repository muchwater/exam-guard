"""Derive detector parameters from a labeled calibration split (real captures or synth/netsim runs).

    python -m net_event.calibrate --index data/synth/netsim_v1/index.jsonl --split calib \
        --rule-preset esp32_lan --rule-preset esp32_lan_wifi --rule-preset esp32_wan --rule-preset linux_wan \
        --out-json data/measured/calib_netsim_v1.json --out-config configs/netsim_v1_derived.yaml

Every value is computed by a stated rule from the data (no hand-picked numbers):
  rule detector (calibrated on sessions where the device only uploads, i.e. --rule-preset):
    large_frame_bytes = min over sessions of the median uplink frame size inside uploads
                        (= the full-MSS data frame of the stack with the smallest MSS)
    burst_gap_s       = max uplink inter-arrival time inside any upload (no calibration upload splits)
    min_burst_bytes   = min bytes of the burst carrying each upload (bursts cut with burst_gap_s)
    min_large_ratio   = worst case over those bursts: min #large frames / (min #large + max #small frames)
                        (the small frames of an upload are handshake/ACK/FIN frames whose count varies with
                        timer phase; combining the extremes keeps every calibration upload and similar ones)
  count rule (frame counts only; for BlockAck-reconstructed streams, capture/ba_recon.py), on all runs of the split:
    burst_gap_s       = the rule detector's burst_gap_s
    min_up_mpdus      = min uplink MPDUs of the burst carrying each upload (keeps every calibration upload)
    min_up_share      = up_share cut that maximizes F1 over all bursts with >= min_up_mpdus uplink MPDUs
                        (positive = burst overlapping an upload)
  window model:
    step_s            = min(windows_s): with evaluations every shortest window, every upload overlaps
                        the shortest window of some evaluation
    l2                = candidate with the highest pooled leave-one-session-out log-likelihood
    threshold         = pooled leave-one-session-out probability cut that maximizes F1
The candidate l2 grid and the F1 criterion are choices; they are written into the output JSON.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import yaml

from capture.macfilter import MacAllowlist
from capture.pcap_io import read_pcap
from common.config import load_config
from net_event.detector import CountRuleDetector, LogisticModel
from net_event.features import BurstSegmenter, feature_names, is_uplink_data
from net_event.labels import load_intervals, overlaps
from net_event.train import session_xy

L2_GRID = [1e-4, 1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0, 1e3, 1e4]   # log-spaced candidates (choice, reported)


def load_index(path: str, split: str | None = None) -> list[dict]:
    rows = [json.loads(line) for line in open(path) if line.strip()]
    return [r for r in rows if split is None or r["split"] == split]


def _uplink(run: dict) -> list:
    dev = run["device"].lower()
    return [p for p in sorted(read_pcap(run["pcap"], MacAllowlist([dev])), key=lambda p: p.ts)
            if is_uplink_data(p, dev)]


def rule_stats(run: dict) -> dict:
    up = _uplink(run)
    ts = np.asarray([p.ts for p in up])
    sz = np.asarray([p.frame_len for p in up])
    gt = load_intervals(run["labels"])
    sizes_in, max_iat = [], []
    for g in gt:
        lo, hi = np.searchsorted(ts, g.t_start, "left"), np.searchsorted(ts, g.t_end, "right")
        sizes_in.append(sz[lo:hi])
        if hi - lo > 1:
            max_iat.append(float(np.diff(ts[lo:hi]).max()))
    allin = np.concatenate(sizes_in) if sizes_in else np.zeros(0)
    gaps = [b.t_start - a.t_end for a, b in zip(gt, gt[1:])]
    return {"run": run["run"], "median_size_in_upload": float(np.median(allin)) if allin.size else None,
            "max_iat_in_upload": max(max_iat) if max_iat else None, "min_gap_between_uploads": min(gaps) if gaps else None,
            "n_uploads": len(gt)}


def burst_stats(run: dict, gap: float, large: int) -> list[dict]:
    up = _uplink(run)
    seg = BurstSegmenter(run["device"].lower(), gap)
    bursts = []
    for p in up:
        bursts += seg.update(p)
    bursts += seg.flush()
    out = []
    for g in load_intervals(run["labels"]):
        cand = [b for b in bursts if b.t_start <= g.t_end and b.t_end >= g.t_start]
        if not cand:
            out.append({"run": run["run"], "seq": g.meta.get("seq"), "bytes": 0, "large_ratio": 0.0,
                        "n_large": 0, "n_small": 0})
            continue
        b = max(cand, key=lambda b: b.total_bytes)
        n_large = int(sum(s >= large for s in b.sizes))
        out.append({"run": run["run"], "seq": g.meta.get("seq"), "bytes": b.total_bytes,
                    "large_ratio": b.large_ratio(large), "n_large": n_large, "n_small": b.n_frames - n_large,
                    "n_bursts_overlapping": len(cand)})
    return out


def count_bursts(run: dict, gap: float) -> list[dict]:
    """Every uplink burst of the run with its MPDU counts (CountRuleDetector without thresholds)."""
    dev = run["device"].lower()
    pkts = sorted(read_pcap(run["pcap"], MacAllowlist([dev])), key=lambda p: p.ts)
    gt = load_intervals(run["labels"])
    out = []
    for e in CountRuleDetector(gap, 0, 0.0).run(pkts):
        hit = [g for g in gt if overlaps(e.t_start, e.t_end, g.t_start, g.t_end)]
        out.append({"run": run["run"], "t_start": e.t_start, "upload": bool(hit),
                    "seq": hit[0].meta.get("seq") if hit else None, **e.features})
    return out


def count_rule_params(bursts: list[dict]) -> dict:
    carrying: dict = {}
    for b in bursts:
        if b["upload"]:
            k = (b["run"], b["seq"])
            if k not in carrying or b["n_up"] > carrying[k]["n_up"]:
                carrying[k] = b
    n_min = min(b["n_up"] for b in carrying.values())
    cand = [b for b in bursts if b["n_up"] >= n_min]
    thr, prf = best_f1_threshold(np.array([b["up_share"] for b in cand]), np.array([b["upload"] for b in cand], float))
    neg = [b["up_share"] for b in cand if not b["upload"]]
    return {"min_up_mpdus": int(n_min), "min_up_share": thr, "calib_at_threshold": prf,
            "n_bursts": len(bursts), "n_candidates": len(cand), "n_uploads": len(carrying),
            "min_share_carrying": min(b["up_share"] for b in carrying.values()),
            "max_share_negative": max(neg) if neg else None}


def _xy(args):
    run, windows, step, large = args
    return session_xy(run["pcap"], run["labels"], run["device"].lower(), 0.0, windows, step, large,
                      t_begin=run.get("t0", 0.0))


def loso(data: list[tuple[np.ndarray, np.ndarray]], l2: float, names: list[str]) -> tuple[np.ndarray, np.ndarray]:
    ps, ys = [], []
    for i in range(len(data)):
        Xtr = np.vstack([d[0] for j, d in enumerate(data) if j != i and len(d[1])])
        ytr = np.concatenate([d[1] for j, d in enumerate(data) if j != i and len(d[1])])
        if len(data[i][1]) == 0:
            continue
        m = LogisticModel.fit(Xtr, ytr, l2, names)
        ps.append(m.predict_proba(data[i][0]))
        ys.append(data[i][1])
    return np.concatenate(ps), np.concatenate(ys)


def best_f1_threshold(p: np.ndarray, y: np.ndarray) -> tuple[float, dict]:
    best = (-1.0, 0.5, {})
    for thr in np.unique(p):
        pred = p >= thr
        tp = float((pred & (y == 1)).sum())
        prec, rec = tp / max(pred.sum(), 1), tp / max(y.sum(), 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-12)
        if f1 > best[0]:
            best = (f1, float(thr), {"precision": prec, "recall": rec, "f1": f1})
    return best[1], best[2]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", required=True)
    ap.add_argument("--split", default="calib")
    ap.add_argument("--rule-preset", action="append", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-config", required=True)
    ap.add_argument("--model-out", default="data/models/net_logreg_synth.npz")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args()
    cfg = load_config(*a.config)
    runs = load_index(a.index, a.split)
    rule_runs = [r for r in runs if r["preset"] in a.rule_preset and r["uploads"] > 0]

    with ProcessPoolExecutor(a.workers) as ex:
        rs = list(ex.map(rule_stats, rule_runs))
    large = int(min(r["median_size_in_upload"] for r in rs))
    gap = max(r["max_iat_in_upload"] for r in rs if r["max_iat_in_upload"] is not None)
    min_upload_gap = min(r["min_gap_between_uploads"] for r in rs if r["min_gap_between_uploads"] is not None)
    with ProcessPoolExecutor(a.workers) as ex:
        bs = [b for lst in ex.map(burst_stats, rule_runs, [gap] * len(rule_runs), [large] * len(rule_runs))
              for b in lst]
    min_bytes = int(min(b["bytes"] for b in bs))
    n_large_min = min(b["n_large"] for b in bs)
    n_small_max = max(b["n_small"] for b in bs)
    min_ratio = n_large_min / (n_large_min + n_small_max)

    with ProcessPoolExecutor(a.workers) as ex:
        cb = [b for lst in ex.map(count_bursts, runs, [gap] * len(runs)) for b in lst]
    cr = count_rule_params(cb)

    windows = [float(w) for w in cfg["net_event"]["window_model"]["windows_s"]]
    step = min(windows)
    names = feature_names(windows)
    with ProcessPoolExecutor(a.workers) as ex:
        data = list(ex.map(_xy, [(r, windows, step, large) for r in runs]))
    ll = {}
    for l2 in L2_GRID:
        p, y = loso(data, l2, names)
        pc = np.clip(p, 1e-12, 1 - 1e-12)
        ll[l2] = float(np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc)))
    l2_best = max(ll, key=ll.get)
    p, y = loso(data, l2_best, names)
    thr, prf = best_f1_threshold(p, y)
    model = LogisticModel.fit(np.vstack([d[0] for d in data]), np.concatenate([d[1] for d in data]), l2_best, names)
    Path(a.model_out).parent.mkdir(parents=True, exist_ok=True)
    model.save(a.model_out)

    res = {
        "index": a.index, "split": a.split, "rule_presets": a.rule_preset,
        "rule_runs": [r["run"] for r in rule_runs], "all_runs": [r["run"] for r in runs],
        "rule": {"large_frame_bytes": large, "burst_gap_s": gap, "min_burst_bytes": min_bytes,
                 "min_large_ratio": min_ratio, "n_large_min": n_large_min, "n_small_max": n_small_max,
                 "min_gap_between_uploads_s": min_upload_gap,
                 "gap_separates_uploads": gap < min_upload_gap},
        "count_rule": {"burst_gap_s": gap, **cr},
        "per_run": rs, "per_upload_burst": bs,
        "window_model": {"step_s": step, "l2_grid": L2_GRID, "loso_mean_loglik": {str(k): v for k, v in ll.items()},
                         "l2": l2_best, "threshold": thr, "threshold_criterion": "max F1 on pooled LOSO",
                         "loso_at_threshold": prf, "n_eval_points": int(len(y)), "n_pos": int(y.sum()),
                         "model": a.model_out},
    }
    Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out_json).write_text(json.dumps(res, indent=1) + "\n")
    src = f"net_event.calibrate on {a.index} split={a.split} -> {a.out_json}"
    overlay = {"net_event": {
        "rule": {"burst_gap_s": gap, "min_burst_bytes": min_bytes, "large_frame_bytes": large,
                 "min_large_ratio": min_ratio},
        "count_rule": {"burst_gap_s": gap, "min_up_mpdus": cr["min_up_mpdus"], "min_up_share": cr["min_up_share"]},
        "window_model": {"path": a.model_out, "step_s": step, "threshold": thr, "l2": l2_best}}}
    kinds = {r.get("source", "synthetic") for r in runs}
    banner = ("DERIVED FROM REAL CAPTURES (tools/real_session)" if kinds == {"real"} else
              "DERIVED FROM SYNTHETIC DATA - provisional until real captures exist" if kinds == {"synthetic"} else
              "DERIVED FROM MIXED REAL + SYNTHETIC DATA")
    text = (f"# {banner}.\n# source: {src}\n"
            f"# rules: see net_event/calibrate.py docstring\n" + yaml.safe_dump(overlay, sort_keys=False))
    Path(a.out_config).write_text(text)
    print(json.dumps({"rule": res["rule"], "count_rule": res["count_rule"], "window_model": {k: res["window_model"][k] for k in
                                                            ("step_s", "l2", "threshold", "loso_at_threshold")}},
                     indent=1))


if __name__ == "__main__":
    main()
