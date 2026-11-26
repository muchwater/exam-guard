"""Accuracy and latency of pose backends on the same frames (run on the board).

    python -m tools.pose_backend_compare --frames data/derived/pose_quant/eval \
        --ctx w8a8=models/pose_w8a8_htp.bin --ctx w8a16=models/pose_w8a16_htp.bin --repeat 3 \
        --out data/measured/pose_backend_compare.json

Reference = ONNX Runtime CPU on models/pose.onnx (fp32, same network). For each QNN HTP context:
  - detection agreement: reference persons (conf >= vision.pose.conf_thres) matched by box IoU >= MATCH_IOU
  - keypoint error: |kpt - kpt_ref| / reference box diagonal, over keypoints visible in the reference
    (conf >= vision.pose.kpt_conf), matched persons only
  - head_drop error: head_drop - head_drop_ref (signed and absolute; vision.action.pose_features), the B1 feature
  - latency: wall time of runner.run (incl. shared-memory copy and (de)quantization in Python), and the
    graphExecute time reported by qnn_runner; ONNX CPU wall time for comparison
Preprocessing/decoding are the pipeline's own (vision.pose.preprocess / decode).
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import cv2
import numpy as np

from common.config import load_config
from vision.action import pose_features
from vision.backends import OnnxCpuRunner, QnnHtpRunner
from vision.pose import decode, preprocess

MATCH_IOU = 0.5   # source: PASCAL VOC / COCO detection convention (IoU >= 0.5 counts as the same object)


def iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def run_all(runner, xs, repeat: int) -> tuple[list, list, list]:
    outs, wall, exe = [], [], []
    for _ in range(3):                                   # warm-up (first calls allocate / power up)
        runner.run(xs[0])
    for rep in range(repeat):
        for x in xs:
            t = time.perf_counter()
            y = runner.run(x)
            wall.append((time.perf_counter() - t) * 1e3)
            if getattr(runner, "last_execute_us", None) is not None:
                exe.append(runner.last_execute_us / 1e3)
            if rep == 0:
                outs.append(y[0])
    return outs, wall, exe


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", required=True)
    ap.add_argument("--ctx", action="append", required=True, help="name=path of a QNN context binary")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = load_config(*a.config)
    v = cfg["vision"]["pose"]
    conf, iou_t, kc = float(v["conf_thres"]), float(v["iou_thres"]), float(v["kpt_conf"])
    files = sorted(Path(a.frames).glob("*.jpg"))
    imgs = [cv2.imread(str(f)) for f in files]
    ref_runner = OnnxCpuRunner(v["onnx"], v.get("cpu_threads"))
    pre = [preprocess(im, ref_runner.input_hw) for im in imgs]
    xs = [p[0] for p in pre]
    ref_out, ref_wall, _ = run_all(ref_runner, xs, a.repeat)
    ref = [decode(o, r, pad, conf, iou_t) for o, (_, r, pad) in zip(ref_out, pre)]
    res = {"host": platform.node(), "frames": len(files), "repeat": a.repeat, "match_iou": MATCH_IOU,
           "reference": {"backend": "onnx-cpu fp32", "persons": sum(len(p) for p in ref),
                         "wall_ms_median": float(np.median(ref_wall)), "wall_ms_p90": float(np.percentile(ref_wall, 90))},
           "variants": {}}
    for spec in a.ctx:
        name, path = spec.split("=", 1)
        r = QnnHtpRunner(path)
        outs, wall, exe = run_all(r, xs, a.repeat)
        r.close()
        det = [decode(o, rr, pad, conf, iou_t) for o, (_, rr, pad) in zip(outs, pre)]
        matched = missed = extra = 0
        kerr, hderr, raw_err = [], [], []
        for o, ro in zip(outs, ref_out):
            raw_err.append(float(np.mean(np.abs(o - ro)) / (np.mean(np.abs(ro)) + 1e-9)))
        for P, R in zip(det, ref):
            used = set()
            for pr in R:
                cand = [(iou(pr.bbox, pq.bbox), j) for j, pq in enumerate(P) if j not in used]
                best = max(cand, default=(0.0, -1))
                if best[0] < MATCH_IOU:
                    missed += 1
                    continue
                used.add(best[1])
                matched += 1
                pq = P[best[1]]
                diag = float(np.hypot(pr.bbox[2] - pr.bbox[0], pr.bbox[3] - pr.bbox[1]))
                vis = pr.keypoints[:, 2] >= kc
                if vis.any():
                    kerr += (np.hypot(*(pq.keypoints[vis, :2] - pr.keypoints[vis, :2]).T) / diag).tolist()
                h0 = pose_features(pr.keypoints, kc).get("head_drop", np.nan)
                h1 = pose_features(pq.keypoints, kc).get("head_drop", np.nan)
                if np.isfinite(h0) and np.isfinite(h1):
                    hderr.append(h1 - h0)
            extra += len(P) - len(used)
        res["variants"][name] = {
            "context": path, "input_dtype": hex(r.inputs[0]["dtype"]), "output_dtype": hex(r.outputs[0]["dtype"]),
            "raw_output_rel_mae_median": float(np.median(raw_err)),
            "persons_matched": matched, "persons_missed": missed, "persons_extra": extra,
            "recall_vs_ref": matched / max(1, matched + missed),
            "kpt_err_norm_median": float(np.median(kerr)) if kerr else None,
            "kpt_err_norm_p90": float(np.percentile(kerr, 90)) if kerr else None,
            "head_drop_err_signed_median": float(np.median(hderr)) if hderr else None,
            "head_drop_abs_err_median": float(np.median(np.abs(hderr))) if hderr else None,
            "head_drop_abs_err_p90": float(np.percentile(np.abs(hderr), 90)) if hderr else None,
            "head_drop_pairs": len(hderr),
            "wall_ms_median": float(np.median(wall)), "wall_ms_p90": float(np.percentile(wall, 90)),
            "execute_ms_median": float(np.median(exe)) if exe else None,
        }
        print(name, json.dumps(res["variants"][name]))
    print("reference", json.dumps(res["reference"]))
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    main()
