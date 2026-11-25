"""Pose + per-frame features for every ExamCheat-MultiV clip usable for statistics.

    python -m tools.examcheat_extract [--root data/external/examcheat] [--out data/derived/examcheat]
                                      [--workers 12 --threads 4] [--include-scenes] [--limit N]

Every frame is processed with the same code path as the live system: vision.pose.PoseEstimator
(ONNX Runtime CPU, conf/iou from configs) and vision.action.pose_features (kpt_conf from configs).

Subject choice (clips can show a second person in the background, e.g. V1 c28):
  pass 1: per frame, the detection with the largest bbox area; the per-coordinate median of those
          boxes over the clip is the clip's "subject region";
  pass 2: per frame, the detection with the highest IoU with that region is the subject (IoU 0 ->
          no subject in that frame, features NaN).
Why: each clip films one seated examinee who stays in one place, which is exactly how the live
system assigns identity (a fixed seat polygon, no tracker). The median of the largest boxes is
robust to a few frames where a passer-by is larger. Person count per frame is stored too.

Outputs per clip (<clip_id> = <version>__<stem>):
  kpts/<clip_id>.npz      ts, n_persons, subj_kpts (N,17,3; NaN = no subject), subj_bbox, subj_score
  features/<clip_id>.csv  frame, ts, n_persons, subject, <vision.action.FEATURES>
  meta/<clip_id>.json     class, size, container fps, measured fps (from frame timestamps), frames
Derived data are adaptations of a CC BY-SA 4.0 dataset: keep them under data/ (not committed).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path

import numpy as np

from datasets.examcheat import ATTRIBUTION, DEFAULT_ROOT, build_index

_EST = None
_CFG = None


def _init_worker(threads: int, config_paths: list[str]) -> None:
    global _EST, _CFG
    from common.config import load_config
    from vision.backends import OnnxCpuRunner
    from vision.pose import PoseEstimator
    _CFG = load_config(*config_paths)
    p = _CFG["vision"]["pose"]
    _EST = PoseEstimator(OnnxCpuRunner(p["onnx"], threads=threads), p["conf_thres"], p["iou_thres"])


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / ua) if ua > 0 else 0.0


def select_subject(dets: list[list]) -> tuple[np.ndarray | None, list[int]]:
    """dets[i] = list of Person for frame i. Returns (subject region, chosen index per frame or -1)."""
    largest = [max(d, key=lambda p: (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1])).bbox for d in dets if d]
    if not largest:
        return None, [-1] * len(dets)
    region = np.median(np.stack(largest), axis=0)
    chosen = []
    for d in dets:
        if not d:
            chosen.append(-1)
            continue
        ious = [_iou(p.bbox, region) for p in d]
        j = int(np.argmax(ious))
        chosen.append(j if ious[j] > 0 else -1)
    return region, chosen


def process_clip(args: tuple[str, str, str, str]) -> dict:
    import cv2

    from vision.action import FEATURES, pose_features
    root, rel, clip_id, out = args
    out = Path(out)
    kpt_conf = _CFG["vision"]["pose"]["kpt_conf"]
    cap = cv2.VideoCapture(str(Path(root) / rel))
    fps_container = cap.get(cv2.CAP_PROP_FPS)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    ts, dets = [], []
    t0 = time.perf_counter()
    while True:
        ok, img = cap.read()
        if not ok:
            break
        ts.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1e3)
        dets.append(_EST(img))
    cap.release()
    n = len(ts)
    ts = np.asarray(ts, float)
    region, chosen = select_subject(dets)
    kp = np.full((n, 17, 3), np.nan, np.float32)
    bb = np.full((n, 4), np.nan, np.float32)
    sc = np.full(n, np.nan, np.float32)
    for i, j in enumerate(chosen):
        if j >= 0:
            kp[i], bb[i], sc[i] = dets[i][j].keypoints, dets[i][j].bbox, dets[i][j].score
    n_persons = np.array([len(d) for d in dets], np.int16)
    for sub in ("kpts", "features", "meta"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "kpts" / f"{clip_id}.npz", ts=ts, n_persons=n_persons, subj_kpts=kp,
                        subj_bbox=bb, subj_score=sc,
                        region=region if region is not None else np.full(4, np.nan))
    with open(out / "features" / f"{clip_id}.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["frame", "ts", "n_persons", "subject", *FEATURES])
        for i in range(n):
            if chosen[i] >= 0:
                feats = pose_features(kp[i], kpt_conf)
                row = [feats[k] for k in FEATURES]
            else:
                row = [float("nan")] * len(FEATURES)
            wr.writerow([i, f"{ts[i]:.6f}", int(n_persons[i]), int(chosen[i] >= 0), *row])
    dts = np.diff(ts)
    dts = dts[dts > 0]
    meta = {"clip_id": clip_id, "rel_path": rel, "width": w, "height": h, "frames": n,
            "fps_container": fps_container,
            "fps_measured": float(1.0 / np.median(dts)) if dts.size else None,
            "duration_s": float(ts[-1] - ts[0]) if n > 1 else 0.0,
            "subject_frames": int(sum(c >= 0 for c in chosen)),
            "multi_person_frames": int((n_persons > 1).sum()),
            "proc_s": time.perf_counter() - t0}
    (out / "meta" / f"{clip_id}.json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--out", default="data/derived/examcheat")
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) // 4))
    ap.add_argument("--threads", type=int, default=4, help="ONNX Runtime intra-op threads per worker")
    ap.add_argument("--include-scenes", action="store_true", help="also process V2 classroom scenes")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args()
    from concurrent.futures import ProcessPoolExecutor
    clips = build_index(a.root, probe=True)
    todo = [c for c in clips if c.usable_for_stats or (a.include_scenes and c.version == "V2")]
    if a.skip_existing:
        todo = [c for c in todo if not (Path(a.out) / "meta" / f"{c.clip_id}.json").exists()]
    todo = todo[: a.limit] if a.limit else todo
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ATTRIBUTION.txt").write_text(ATTRIBUTION + "\n")
    (out / "index.json").write_text(json.dumps(
        [{"clip_id": c.clip_id, "rel_path": c.rel_path, "version": c.version, "cls": c.cls,
          "label_scope": c.label_scope, "usable_for_stats": c.usable_for_stats,
          "duplicate_of": c.duplicate_of, "frames": c.frames} for c in clips], indent=1) + "\n")
    t0 = time.time()
    jobs = [(a.root, c.rel_path, c.clip_id, str(out)) for c in todo]
    # long clips first so the pool finishes evenly
    jobs.sort(key=lambda j: -next(c.frames or 0 for c in todo if c.clip_id == j[2]))
    with ProcessPoolExecutor(a.workers, initializer=_init_worker, initargs=(a.threads, a.config)) as ex:
        for k, m in enumerate(ex.map(process_clip, jobs), 1):
            print(f"[{k}/{len(jobs)}] {m['clip_id']:22s} {m['frames']:5d} fr  subj {m['subject_frames']:5d}  "
                  f"multi {m['multi_person_frames']:4d}  {m['proc_s']:.1f}s", flush=True)
    print(f"done {len(jobs)} clips in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
