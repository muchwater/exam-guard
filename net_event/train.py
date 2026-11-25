"""Train / evaluate the window-model detector on labeled captures.

    python -m net_event.train --session data/raw/s1.pcap,data/raw/s1.labels.jsonl,aa:bb:..,0.012 \
                              --session data/raw/s2.pcap,data/raw/s2.labels.jsonl,aa:bb:..,0.010 \
                              --out data/models/net_logreg.npz

An evaluation time is labeled positive when it lies inside a ground-truth upload interval.
Reports leave-one-session-out precision/recall at the configured threshold, then fits on all
sessions. windows_s, step_s, threshold and l2 come from configs (net_event.window_model.*).
"""
from __future__ import annotations

import argparse

import numpy as np

from capture.macfilter import MacAllowlist
from capture.pcap_io import read_pcap
from common.config import load_config, require
from net_event.detector import LogisticModel
from net_event.features import feature_names, multi_window_features
from net_event.labels import load_intervals


def session_xy(pcap: str, labels: str, device: str, offset: float, windows_s: list[float], step_s: float,
               large: int) -> tuple[np.ndarray, np.ndarray]:
    """Evaluation times every step_s; label = 1 if the evaluation time lies inside a ground-truth upload."""
    pkts = sorted(read_pcap(pcap, MacAllowlist([device])), key=lambda p: p.ts)
    gt = load_intervals(labels, offset)
    if not pkts:
        return np.zeros((0, len(feature_names(windows_s)))), np.zeros(0)
    ts = np.asarray([p.ts for p in pkts])
    t_eval = np.arange(pkts[0].ts + max(windows_s), pkts[-1].ts, step_s)
    X = []
    for t in t_eval:
        lo, hi = np.searchsorted(ts, t - max(windows_s)), np.searchsorted(ts, t)
        X.append(multi_window_features(pkts[lo:hi], device, t, windows_s, large))
    y = np.array([any(g.t_start <= t <= g.t_end for g in gt) for t in t_eval], float)
    return (np.stack(X) if X else np.zeros((0, len(feature_names(windows_s))))), y


def _prf(p: np.ndarray, y: np.ndarray) -> dict:
    tp = float(((p == 1) & (y == 1)).sum())
    return {"precision": tp / max(p.sum(), 1), "recall": tp / max(y.sum(), 1), "n_pos": int(y.sum()), "n": len(y)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", action="append", required=True, help="pcap,labels,device_mac,offset_s")
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = load_config(*a.config)
    m = "net_event.window_model."
    windows_s, step_s, thr, l2 = require(cfg, m + "windows_s", m + "step_s", m + "threshold", m + "l2")
    windows_s = [float(w) for w in windows_s]
    large = require(cfg, "net_event.rule.large_frame_bytes")
    data = []
    for s in a.session:
        pcap, labels, dev, off = s.split(",")
        data.append(session_xy(pcap, labels, dev.lower(), float(off), windows_s, float(step_s), int(large)))
    if len(data) > 1:
        for i in range(len(data)):
            Xtr = np.vstack([d[0] for j, d in enumerate(data) if j != i])
            ytr = np.concatenate([d[1] for j, d in enumerate(data) if j != i])
            model = LogisticModel.fit(Xtr, ytr, float(l2), feature_names(windows_s))
            pred = (model.predict_proba(data[i][0]) >= float(thr)).astype(float)
            print(f"held-out session {i}: {_prf(pred, data[i][1])}")
    model = LogisticModel.fit(np.vstack([d[0] for d in data]), np.concatenate([d[1] for d in data]), float(l2),
                              feature_names(windows_s))
    model.save(a.out)
    print(f"saved {a.out}")


if __name__ == "__main__":
    main()
