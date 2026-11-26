"""Calibration inputs for QAIRT quantization of the pose model, plus held-out frames for accuracy checks.

    python -m tools.pose_quant_data --out data/derived/pose_quant --n-calib 100 --n-eval 60 \
        --path-prefix /workspace/exam-guard/

PROVISIONAL SOURCE: frames come from ExamCheat-MultiV V1 clips (classroom exam scenes, CC BY-SA 4.0), because
team camera frames do not exist yet. Replace with frames from our own camera once recorded (same command with
--video). Clips are split by sorted index (even -> calibration, odd -> held-out) so no clip feeds both sets;
frames are evenly spaced inside each clip. The preprocessing is vision.pose.preprocess (letterbox to the
model input, RGB, /255, NCHW float32), i.e. exactly what the live pipeline feeds the network.

Writes:
  calib/NNNN.raw + calib/input_list.txt   float32 NCHW raw files for `qairt-quantizer --input_list`
  eval/NNNN.jpg + eval/frames.json         held-out BGR frames (JPEG q95) and their source clip/frame
The set sizes are analysis choices (no physical parameter); they are recorded in eval/frames.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from datasets.examcheat import build_index
from vision.pose import preprocess

ROOT = Path("data/external/examcheat")
INPUT_HW = (640, 640)   # source: models/pose.onnx input 'images' [1, 3, 640, 640] (onnxruntime get_inputs)


def frames_from(path: Path, k: int) -> list[tuple[int, np.ndarray]]:
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for i in np.linspace(0, max(n - 1, 0), k + 2)[1:-1].astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, img = cap.read()
        if ok:
            out.append((int(i), img))
    cap.release()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-calib", type=int, required=True)
    ap.add_argument("--n-eval", type=int, required=True)
    ap.add_argument("--path-prefix", default="", help="prefix for paths in input_list.txt (e.g. container mount)")
    a = ap.parse_args()
    clips = sorted((c for c in build_index(ROOT) if c.version == "V1" and c.duplicate_of is None),
                   key=lambda c: c.rel_path)
    calib_clips, eval_clips = clips[0::2], clips[1::2]
    out = Path(a.out)
    (out / "calib").mkdir(parents=True, exist_ok=True)
    (out / "eval").mkdir(parents=True, exist_ok=True)

    def take(cs, n):
        per = max(1, int(np.ceil(n / len(cs))))
        got = []
        for c in cs:
            for fi, img in frames_from(ROOT / c.rel_path, per):
                got.append((c.rel_path, fi, img))
        rng = np.random.default_rng(0)
        idx = sorted(rng.choice(len(got), size=min(n, len(got)), replace=False))
        return [got[i] for i in idx]

    lines = []
    for j, (_rel, _fi, img) in enumerate(take(calib_clips, a.n_calib)):
        x, _, _ = preprocess(img, INPUT_HW)
        p = out / "calib" / f"{j:04d}.raw"
        x.astype(np.float32).tofile(p)
        lines.append(f"{a.path_prefix}{p}")
    (out / "calib" / "input_list.txt").write_text("\n".join(lines) + "\n")
    meta = []
    for j, (rel, fi, img) in enumerate(take(eval_clips, a.n_eval)):
        cv2.imwrite(str(out / "eval" / f"{j:04d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        meta.append({"file": f"{j:04d}.jpg", "clip": rel, "frame": fi})
    (out / "eval" / "frames.json").write_text(json.dumps(
        {"source": "ExamCheat-MultiV V1 (provisional)", "n_calib": len(lines), "n_eval": len(meta),
         "calib_clips": len(calib_clips), "eval_clips": len(eval_clips), "frames": meta}, indent=1))
    print(f"calib {len(lines)} raw files, eval {len(meta)} frames -> {out}")


if __name__ == "__main__":
    main()
