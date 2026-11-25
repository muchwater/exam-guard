"""Statistics of pose features on ExamCheat-MultiV (output of tools/examcheat_extract.py).

    python -m tools.examcheat_stats [--derived data/derived/examcheat] \
        --out data/measured/examcheat_feature_stats.json --md data/derived/examcheat/tables.md

Reports (all numbers come from the extracted features; nothing is tuned by hand):
  * per-class / per-source quantiles of every vision.action feature and its NaN rate
  * B1 head_down_hold: head_drop, Normal (negative) vs Mobile-phone + Hidden-notes (positive proxy for
    "head down at something"): frame-level AUC with a clip-level bootstrap CI; thresholds set at a
    target Normal-frame FPR on training clips and evaluated on held-out clips (clip-grouped K-fold,
    subjects are not annotated); cross-source check (fit on V1, test on crops and vice versa);
    longest continuous hold above the threshold per clip
  * B2 glasses_touch: no positives exist in the dataset -> only the firing rate of candidate
    min_wrist_to_nose thresholds per class (false-alarm baseline)
  * keypoint dropout gaps (head_drop NaN runs), in particular gaps bracketed by head-down frames
    on both sides (what vision.action.max_gap_s has to bridge)
  * delivered fps per clip (from decoded frame timestamps)
The FPR targets and the B2 threshold grid are reporting grids, not chosen operating points.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from common.config import load_config
from datasets.examcheat import ATTRIBUTION, CLASSES
from vision.action import FEATURES, TemporalSegmenter

QS = [1, 5, 10, 25, 50, 75, 90, 95, 99]
B1_POS = ["mobile_phone", "hidden_notes"]
B1_NEG = ["normal"]
FPR_TARGETS = [0.01, 0.05, 0.10]           # reporting grid
B2_GRID = [0.25, 0.5, 0.75, 1.0]           # reporting grid (shoulder widths)
K_FOLDS = 5
N_BOOT = 1000
SEED = 0


# ---------------------------------------------------------------- loading
def load(derived: Path) -> list[dict]:
    index = json.loads((derived / "index.json").read_text())
    clips = []
    for c in index:
        if not c["usable_for_stats"]:
            continue
        fp = derived / "features" / f"{c['clip_id']}.csv"
        mp = derived / "meta" / f"{c['clip_id']}.json"
        if not fp.exists():
            continue
        with open(fp) as f:
            rows = list(csv.DictReader(f))
        arr = {k: np.array([float(r[k]) for r in rows]) for k in ["ts", "n_persons", "subject", *FEATURES]}
        clips.append({**c, "f": arr, "meta": json.loads(mp.read_text())})
    return clips


# ---------------------------------------------------------------- helpers
def quantiles(x: np.ndarray) -> dict:
    x = x[~np.isnan(x)]
    if x.size == 0:
        return {"n": 0}
    return {"n": int(x.size), **{f"p{q:02d}": float(np.percentile(x, q)) for q in QS},
            "min": float(x.min()), "max": float(x.max()), "mean": float(x.mean())}


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Mann-Whitney AUC = P(pos > neg) + 0.5 P(tie)."""
    x = np.concatenate([pos, neg])
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(x.size)
    xs = x[order]
    i = 0
    while i < xs.size:                      # average ranks for ties
        j = i
        while j + 1 < xs.size and xs[j + 1] == xs[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    rp = ranks[: pos.size].sum()
    return float((rp - pos.size * (pos.size + 1) / 2) / (pos.size * neg.size))


def runs(mask: np.ndarray, ts: np.ndarray) -> list[float]:
    """Durations (s, first to last frame) of maximal runs of consecutive True frames (no gap tolerance)."""
    out, start = [], None
    for i in range(mask.size):
        if mask[i] and start is None:
            start = i
        elif not mask[i] and start is not None:
            out.append(float(ts[i - 1] - ts[start]))
            start = None
    if start is not None:
        out.append(float(ts[-1] - ts[start]))
    return out


def nan_gaps(x: np.ndarray, ts: np.ndarray, bracket: np.ndarray | None = None) -> list[float]:
    """Durations of NaN runs inside the clip (time from last valid frame to next valid frame).
    bracket: if given, keep only gaps whose neighbouring valid frames both satisfy bracket."""
    valid = ~np.isnan(x)
    idx = np.flatnonzero(valid)
    out = []
    for a, b in itertools.pairwise(idx):
        if b - a > 1 and (bracket is None or (bracket[a] and bracket[b])):
            out.append(float(ts[b] - ts[a]))
    return out


# ---------------------------------------------------------------- analyses
def per_class_quantiles(clips):
    res = {}
    for src in ["all", "V1", "crops"]:
        res[src] = {}
        for cls in CLASSES:
            cs = [c for c in clips if c["cls"] == cls and (src == "all" or c["version"] == src)]
            if not cs:
                continue
            subj = np.concatenate([c["f"]["subject"] for c in cs])
            d = {"clips": len(cs), "frames": int(subj.size), "subject_rate": float(subj.mean())}
            for k in FEATURES:
                x = np.concatenate([c["f"][k] for c in cs])
                d[k] = quantiles(x)
                d[k]["nan_rate_given_subject"] = float(np.isnan(x[subj > 0]).mean()) if (subj > 0).any() else None
            res[src][cls] = d
    return res


def _frames(cs, k="head_drop"):
    x = np.concatenate([c["f"][k] for c in cs]) if cs else np.zeros(0)
    return x[~np.isnan(x)]


def folds(clips, k, seed):
    rng = np.random.default_rng(seed)
    fold = {}
    for cls in sorted({c["cls"] for c in clips}):
        ids = sorted(c["clip_id"] for c in clips if c["cls"] == cls)
        rng.shuffle(ids)
        for i, cid in enumerate(ids):
            fold[cid] = i % k
    return fold


def b1_analysis(clips):
    rng = np.random.default_rng(SEED)
    out = {"feature": "head_drop", "positive_classes": B1_POS, "negative_classes": B1_NEG,
           "note": "positive = whole clip of a class whose behaviour involves looking down at an object; "
                   "not every frame of such a clip is head-down (label noise)"}
    sel = [c for c in clips if c["cls"] in B1_POS + B1_NEG]
    # AUC with clip bootstrap
    out["auc"] = {}
    for src in ["all", "V1", "crops"]:
        cs = [c for c in sel if src == "all" or c["version"] == src]
        pos_c = [c for c in cs if c["cls"] in B1_POS]
        neg_c = [c for c in cs if c["cls"] in B1_NEG]
        if not pos_c or not neg_c:
            continue
        a = auc(_frames(pos_c), _frames(neg_c))
        boots = []
        for _ in range(N_BOOT):
            bp = [pos_c[i] for i in rng.integers(0, len(pos_c), len(pos_c))]
            bn = [neg_c[i] for i in rng.integers(0, len(neg_c), len(neg_c))]
            fp, fn = _frames(bp), _frames(bn)
            if fp.size and fn.size:
                boots.append(auc(fp, fn))
        nan_pos = np.isnan(np.concatenate([c["f"]["head_drop"][c["f"]["subject"] > 0] for c in pos_c])).mean()
        nan_neg = np.isnan(np.concatenate([c["f"]["head_drop"][c["f"]["subject"] > 0] for c in neg_c])).mean()
        out["auc"][src] = {"auc": a, "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
                           "pos_clips": len(pos_c), "neg_clips": len(neg_c),
                           "pos_frames": int(_frames(pos_c).size), "neg_frames": int(_frames(neg_c).size),
                           "head_drop_nan_rate_pos": float(nan_pos), "head_drop_nan_rate_neg": float(nan_neg)}
    # per-class AUC vs normal (each positive class separately, and the other classes for reference)
    neg_all = _frames([c for c in sel if c["cls"] in B1_NEG])
    out["auc_by_class_vs_normal"] = {cls: auc(_frames([c for c in clips if c["cls"] == cls]), neg_all)
                                     for cls in CLASSES if cls != "normal"}
    # held-out thresholds (clip-grouped K-fold)
    fold = folds(sel, K_FOLDS, SEED)
    cv = {}
    for tgt in FPR_TARGETS:
        rows = []
        for k in range(K_FOLDS):
            tr = [c for c in sel if fold[c["clip_id"]] != k]
            te = [c for c in sel if fold[c["clip_id"]] == k]
            thr = float(np.quantile(_frames([c for c in tr if c["cls"] in B1_NEG]), 1 - tgt))
            tn = _frames([c for c in te if c["cls"] in B1_NEG])
            tp = _frames([c for c in te if c["cls"] in B1_POS])
            rows.append({"fold": k, "threshold": thr,
                         "test_fpr": float((tn > thr).mean()) if tn.size else None,
                         "test_tpr": float((tp > thr).mean()) if tp.size else None})
        f = [r["test_fpr"] for r in rows if r["test_fpr"] is not None]
        t = [r["test_tpr"] for r in rows if r["test_tpr"] is not None]
        thr_all = float(np.quantile(neg_all, 1 - tgt))
        cv[f"{tgt:.2f}"] = {"folds": rows, "test_fpr_mean": float(np.mean(f)), "test_fpr_range": [min(f), max(f)],
                            "test_tpr_mean": float(np.mean(t)), "test_tpr_range": [min(t), max(t)],
                            "threshold_all_normal_frames": thr_all}
    out["heldout_kfold"] = {"k": K_FOLDS, "seed": SEED, "by_target_fpr": cv}
    # cross-source
    xs = {}
    for fit_src, test_src in [("V1", "crops"), ("crops", "V1")]:
        nf = _frames([c for c in sel if c["version"] == fit_src and c["cls"] in B1_NEG])
        tn = _frames([c for c in sel if c["version"] == test_src and c["cls"] in B1_NEG])
        tp = _frames([c for c in sel if c["version"] == test_src and c["cls"] in B1_POS])
        xs[f"fit_{fit_src}_test_{test_src}"] = {
            f"{tgt:.2f}": {"threshold": float(np.quantile(nf, 1 - tgt)),
                           "test_fpr": float((tn > np.quantile(nf, 1 - tgt)).mean()),
                           "test_tpr": float((tp > np.quantile(nf, 1 - tgt)).mean())} for tgt in FPR_TARGETS}
    out["cross_source"] = xs
    # longest hold per clip at the all-normal thresholds
    hold = {}
    for tgt in FPR_TARGETS:
        thr = cv[f"{tgt:.2f}"]["threshold_all_normal_frames"]
        per_cls = {}
        for cls in CLASSES:
            longest, frac, clip_len = [], [], []
            for c in clips:
                if c["cls"] != cls:
                    continue
                x, ts = c["f"]["head_drop"], c["f"]["ts"]
                r = runs(np.nan_to_num(x, nan=-np.inf) > thr, ts)
                longest.append(max(r) if r else 0.0)
                v = ~np.isnan(x)
                frac.append(float((x[v] > thr).mean()) if v.any() else 0.0)
                clip_len.append(float(ts[-1] - ts[0]))
            per_cls[cls] = {"longest_hold_s": quantiles(np.array(longest)),
                            "fraction_of_valid_frames_above": quantiles(np.array(frac)),
                            "clip_duration_s": quantiles(np.array(clip_len))}
        hold[f"{tgt:.2f}"] = {"threshold": thr, "by_class": per_cls}
    out["hold_duration_strict"] = hold
    return out


def max_event_duration(c: dict, thr: float, max_gap_s: float) -> float:
    """Longest head_down_hold event the real TemporalSegmenter (min_duration 0) produces on a clip."""
    seg = TemporalSegmenter(0.0, max_gap_s)
    ev = []
    for t, x in zip(c["f"]["ts"], c["f"]["head_drop"], strict=True):
        ev += seg.update("s", float(t), ("head_down_hold", 1.0) if x > thr else None)   # NaN > thr is False
    ev += seg.flush()
    return max((e.t_end - e.t_start for e in ev), default=0.0)


def b1_segment_analysis(clips, thresholds: dict, max_gap_s: float):
    """Clip-level view with segmenter semantics: per clip, the longest event; min_duration candidate =
    the longest event over Normal clips (on training clips in the K-fold), so no training Normal clip fires."""
    sel = [c for c in clips if c["cls"] in B1_POS + B1_NEG]
    fold = folds(sel, K_FOLDS, SEED)
    out = {"max_gap_s": max_gap_s, "by_target_fpr": {}}
    for tgt, thr in thresholds.items():
        dur = {c["clip_id"]: max_event_duration(c, thr, max_gap_s) for c in clips}
        pos = np.array([dur[c["clip_id"]] for c in sel if c["cls"] in B1_POS])
        neg = np.array([dur[c["clip_id"]] for c in sel if c["cls"] in B1_NEG])
        cand = float(neg.max())
        rows = []
        for k in range(K_FOLDS):
            tr_neg = [dur[c["clip_id"]] for c in sel if fold[c["clip_id"]] != k and c["cls"] in B1_NEG]
            te = [c for c in sel if fold[c["clip_id"]] == k]
            md = max(tr_neg)
            tn = [dur[c["clip_id"]] > md for c in te if c["cls"] in B1_NEG]
            tp = [dur[c["clip_id"]] > md for c in te if c["cls"] in B1_POS]
            rows.append({"fold": k, "min_duration_s": md, "test_clip_fpr": float(np.mean(tn)) if tn else None,
                         "test_clip_tpr": float(np.mean(tp)) if tp else None})
        out["by_target_fpr"][tgt] = {
            "threshold": thr, "clip_auc_longest_event": auc(pos, neg),
            "longest_event_s_by_class": {cls: quantiles(np.array([dur[c["clip_id"]] for c in clips if c["cls"] == cls]))
                                         for cls in CLASSES},
            "min_duration_candidate_s": cand,
            "clip_tpr_at_candidate": float((pos > cand).mean()),
            "heldout_kfold": rows,
            "heldout_clip_fpr_mean": float(np.mean([r["test_clip_fpr"] for r in rows if r["test_clip_fpr"] is not None])),
            "heldout_clip_tpr_mean": float(np.mean([r["test_clip_tpr"] for r in rows if r["test_clip_tpr"] is not None]))}
    return out


def b2_analysis(clips):
    res = {"feature": "min_wrist_to_nose", "op": "<", "grid": B2_GRID,
           "note": "no glasses-touch positives in ExamCheat-MultiV: firing rates are a false-alarm baseline only"}
    by = {}
    for cls in CLASSES:
        cs = [c for c in clips if c["cls"] == cls]
        subj = np.concatenate([c["f"]["subject"] for c in cs]) > 0
        x = np.concatenate([c["f"]["min_wrist_to_nose"] for c in cs])[subj]
        wv = np.concatenate([np.maximum(c["f"]["l_wrist_visible"], c["f"]["r_wrist_visible"]) for c in cs])[subj]
        d = {"subject_frames": int(subj.sum()), "any_wrist_visible_rate": float(np.nanmean(wv)),
             "nan_rate": float(np.isnan(x).mean())}
        for t in B2_GRID:
            fire = np.nan_to_num(x, nan=np.inf) < t
            longest, any_fire = [], 0
            for c in cs:
                xx = np.nan_to_num(c["f"]["min_wrist_to_nose"], nan=np.inf) < t
                any_fire += int(xx.any())
                r = runs(xx, c["f"]["ts"])
                longest.append(max(r) if r else 0.0)
            d[f"{t:g}"] = {"frame_rate": float(fire.mean()), "clips_with_any_fire": any_fire,
                           "clips": len(cs), "longest_run_s": quantiles(np.array(longest))}
        by[cls] = d
    res["by_class"] = by
    return res


def gap_analysis(clips, b1_thr: float):
    res = {"definition": "gap = time between two consecutive frames with a valid head_drop that are separated "
                         "by >= 1 frame without it (nose or a shoulder below kpt_conf, or no subject)",
           "bracketed_threshold": b1_thr}
    for cls in CLASSES:
        allg, br = [], []
        for c in clips:
            if c["cls"] != cls:
                continue
            x, ts = c["f"]["head_drop"], c["f"]["ts"]
            allg += nan_gaps(x, ts)
            br += nan_gaps(x, ts, bracket=np.nan_to_num(x, nan=-np.inf) > b1_thr)
        res[cls] = {"all_gaps_s": quantiles(np.array(allg)), "gaps_bracketed_by_head_down_s": quantiles(np.array(br))}
    return res


def fps_analysis(clips):
    res = {}
    for src in ["V1", "crops"]:
        f = np.array([c["meta"]["fps_measured"] for c in clips if c["version"] == src and c["meta"]["fps_measured"]])
        fc = np.array([c["meta"]["fps_container"] for c in clips if c["version"] == src])
        res[src] = {"fps_measured": quantiles(f), "fps_container": quantiles(fc),
                    "multi_person_frame_rate": float(sum(c["meta"]["multi_person_frames"] for c in clips
                                                         if c["version"] == src) /
                                                     max(1, sum(c["meta"]["frames"] for c in clips
                                                                if c["version"] == src))),
                    "sizes": sorted({f"{c['meta']['width']}x{c['meta']['height']}" for c in clips
                                     if c["version"] == src})}
    return res


# ---------------------------------------------------------------- markdown
def _fmt(v, nd=3):
    return "-" if v is None else f"{v:.{nd}f}"


def markdown(r) -> str:
    L = []
    pc = r["per_class_quantiles"]["all"]
    L.append("### head_drop 분위수 (class별, 모든 source)\n")
    L.append("| class | clips | frames | subject rate | head_drop NaN | p05 | p25 | p50 | p75 | p95 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for cls, d in pc.items():
        h = d["head_drop"]
        L.append(f"| {cls} | {d['clips']} | {d['frames']} | {_fmt(d['subject_rate'], 2)} | "
                 f"{_fmt(h.get('nan_rate_given_subject'), 2)} | {_fmt(h.get('p05'))} | {_fmt(h.get('p25'))} | "
                 f"{_fmt(h.get('p50'))} | {_fmt(h.get('p75'))} | {_fmt(h.get('p95'))} |")
    for src in ["V1", "crops"]:
        L.append(f"\n### head_drop 분위수 ({src})\n")
        L.append("| class | clips | p05 | p25 | p50 | p75 | p95 |")
        L.append("|---|---|---|---|---|---|---|")
        for cls, d in r["per_class_quantiles"][src].items():
            h = d["head_drop"]
            L.append(f"| {cls} | {d['clips']} | {_fmt(h.get('p05'))} | {_fmt(h.get('p25'))} | {_fmt(h.get('p50'))} | "
                     f"{_fmt(h.get('p75'))} | {_fmt(h.get('p95'))} |")
    b1 = r["b1"]
    L.append("\n### B1: head_drop AUC (positive = mobile_phone + hidden_notes, negative = normal)\n")
    L.append("| source | AUC | 95% CI (clip bootstrap) | pos clips/frames | neg clips/frames | NaN pos | NaN neg |")
    L.append("|---|---|---|---|---|---|---|")
    for src, d in b1["auc"].items():
        L.append(f"| {src} | {d['auc']:.3f} | {d['ci95'][0]:.3f}–{d['ci95'][1]:.3f} | {d['pos_clips']}/{d['pos_frames']} | "
                 f"{d['neg_clips']}/{d['neg_frames']} | {d['head_drop_nan_rate_pos']:.2f} | {d['head_drop_nan_rate_neg']:.2f} |")
    L.append("\nclass별 AUC (vs normal): " + ", ".join(f"{k} {v:.3f}" for k, v in b1["auc_by_class_vs_normal"].items()))
    L.append(f"\n### B1: held-out 평가 (clip 단위 {b1['heldout_kfold']['k']}-fold, seed {b1['heldout_kfold']['seed']})\n")
    L.append("| target Normal FPR | threshold (전체 normal) | fold thresholds | test FPR mean [min,max] | test TPR mean [min,max] |")
    L.append("|---|---|---|---|---|")
    for tgt, d in b1["heldout_kfold"]["by_target_fpr"].items():
        ths = ", ".join(f"{x['threshold']:.3f}" for x in d["folds"])
        L.append(f"| {tgt} | {d['threshold_all_normal_frames']:.3f} | {ths} | {d['test_fpr_mean']:.3f} "
                 f"[{d['test_fpr_range'][0]:.3f}, {d['test_fpr_range'][1]:.3f}] | {d['test_tpr_mean']:.3f} "
                 f"[{d['test_tpr_range'][0]:.3f}, {d['test_tpr_range'][1]:.3f}] |")
    L.append("\n### B1: cross-source (한 source의 normal로 threshold → 다른 source에서 평가)\n")
    L.append("| fit → test | target FPR | threshold | test FPR | test TPR |")
    L.append("|---|---|---|---|---|")
    for k, d in b1["cross_source"].items():
        for tgt, e in d.items():
            L.append(f"| {k} | {tgt} | {e['threshold']:.3f} | {e['test_fpr']:.3f} | {e['test_tpr']:.3f} |")
    L.append("\n### B1: clip별 최장 연속 유지 시간 (head_drop > threshold, gap 허용 없음)\n")
    L.append("| target FPR | threshold | class | longest hold p50 / p90 / max (s) | clip 길이 p50 / max (s) |")
    L.append("|---|---|---|---|---|")
    for tgt, d in b1["hold_duration_strict"].items():
        for cls, e in d["by_class"].items():
            h, cl = e["longest_hold_s"], e["clip_duration_s"]
            L.append(f"| {tgt} | {d['threshold']:.3f} | {cls} | {_fmt(h.get('p50'), 2)} / {_fmt(h.get('p90'), 2)} / "
                     f"{_fmt(h.get('max'), 2)} | {_fmt(cl.get('p50'), 1)} / {_fmt(cl.get('max'), 1)} |")
    b2 = r["b2"]
    sg = b1["segmenter"]
    L.append(f"\n### B1: TemporalSegmenter 기준 clip 단위 (max_gap_s = {sg['max_gap_s']:.3f}, min_duration 후보 = "
             "normal clip 최장 event)\n")
    L.append("| target FPR | threshold | clip AUC (최장 event) | min_duration 후보 (s) | clip TPR @후보 | "
             "held-out clip FPR / TPR | 최장 event p50/max: normal | mobile_phone | hidden_notes |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for tgt, d in sg["by_target_fpr"].items():
        e = d["longest_event_s_by_class"]
        L.append(f"| {tgt} | {d['threshold']:.3f} | {d['clip_auc_longest_event']:.3f} | {d['min_duration_candidate_s']:.2f} | "
                 f"{d['clip_tpr_at_candidate']:.3f} | {d['heldout_clip_fpr_mean']:.3f} / {d['heldout_clip_tpr_mean']:.3f} | "
                 f"{_fmt(e['normal'].get('p50'), 2)}/{_fmt(e['normal'].get('max'), 2)} | "
                 f"{_fmt(e['mobile_phone'].get('p50'), 2)}/{_fmt(e['mobile_phone'].get('max'), 2)} | "
                 f"{_fmt(e['hidden_notes'].get('p50'), 2)}/{_fmt(e['hidden_notes'].get('max'), 2)} |")
    L.append("\n### B2: min_wrist_to_nose < t 인 frame 비율 (false-alarm baseline, positive 없음)\n")
    L.append("| class | subject frames | wrist visible | " + " | ".join(f"t={t:g}" for t in b2["grid"]) + " |")
    L.append("|---|---|---|" + "---|" * len(b2["grid"]))
    for cls, d in b2["by_class"].items():
        L.append(f"| {cls} | {d['subject_frames']} | {d['any_wrist_visible_rate']:.2f} | " +
                 " | ".join(f"{d[f'{t:g}']['frame_rate']:.3f}" for t in b2["grid"]) + " |")
    g = r["gaps"]
    L.append(f"\n### keypoint dropout gap (head_drop NaN 구간, bracketed threshold = {g['bracketed_threshold']:.3f})\n")
    L.append("| class | 모든 gap n / p50 / p95 / max (s) | head-down 사이 gap n / p50 / p95 / max (s) |")
    L.append("|---|---|---|")
    for cls in CLASSES:
        a, b = g[cls]["all_gaps_s"], g[cls]["gaps_bracketed_by_head_down_s"]
        L.append(f"| {cls} | {a.get('n', 0)} / {_fmt(a.get('p50'), 3)} / {_fmt(a.get('p95'), 3)} / {_fmt(a.get('max'), 3)} | "
                 f"{b.get('n', 0)} / {_fmt(b.get('p50'), 3)} / {_fmt(b.get('p95'), 3)} / {_fmt(b.get('max'), 3)} |")
    L.append("\n### fps (decoded frame timestamp 기준)\n")
    L.append("| source | sizes | fps measured p05 / p50 / p95 | container fps p50 | multi-person frame 비율 |")
    L.append("|---|---|---|---|---|")
    for src, d in r["fps"].items():
        f = d["fps_measured"]
        L.append(f"| {src} | {', '.join(d['sizes'])} | {_fmt(f.get('p05'), 2)} / {_fmt(f.get('p50'), 2)} / "
                 f"{_fmt(f.get('p95'), 2)} | {_fmt(d['fps_container'].get('p50'), 2)} | {d['multi_person_frame_rate']:.3f} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--derived", default="data/derived/examcheat")
    ap.add_argument("--out", default="data/measured/examcheat_feature_stats.json")
    ap.add_argument("--md", default="data/derived/examcheat/tables.md")
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args()
    cfg = load_config(*a.config)
    clips = load(Path(a.derived))
    if not clips:
        raise SystemExit("no extracted clips; run python -m tools.examcheat_extract first")
    r = {"measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
         "dataset": ATTRIBUTION,
         "pose": {"model": cfg["vision"]["pose"]["onnx"], "conf_thres": cfg["vision"]["pose"]["conf_thres"],
                  "iou_thres": cfg["vision"]["pose"]["iou_thres"], "kpt_conf": cfg["vision"]["pose"]["kpt_conf"]},
         "clips": {cls: {src: sum(1 for c in clips if c["cls"] == cls and c["version"] == src) for src in ["V1", "crops"]}
                   for cls in CLASSES},
         "per_class_quantiles": per_class_quantiles(clips)}
    r["b1"] = b1_analysis(clips)
    r["b2"] = b2_analysis(clips)
    r["gaps"] = gap_analysis(clips, r["b1"]["heldout_kfold"]["by_target_fpr"]["0.05"]["threshold_all_normal_frames"])
    # gap tolerance for the segmenter: longest keypoint dropout between head-down frames in positive clips
    g = max(r["gaps"][cls]["gaps_bracketed_by_head_down_s"].get("max", 0.0) for cls in B1_POS)
    thr = {t: d["threshold_all_normal_frames"] for t, d in r["b1"]["heldout_kfold"]["by_target_fpr"].items()}
    r["b1"]["segmenter"] = b1_segment_analysis(clips, thr, g)
    r["fps"] = fps_analysis(clips)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(r, indent=1) + "\n")
    md = markdown(r)
    Path(a.md).parent.mkdir(parents=True, exist_ok=True)
    Path(a.md).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
