"""Build an OFFLINE demo session from dataset clips + synthetic packets (no hardware).

    python -m tools.make_demo_session --out data/runs/demo_synth \
        --clip data/external/examcheat/V1/V1/nor10.mp4 --clip data/external/examcheat/V1/V1/m16.mp4 \
        --clip data/external/examcheat/V1/V1/nor11.mp4 --upload-in-class mobile_phone \
        --preset esp32_lan --mcs 5 --resolution 1280x720
    python -m replay.run --pcap data/runs/demo_synth/net.pcap --video data/runs/demo_synth/video.mp4 \
        --out data/runs/demo_synth/replay --config configs/netsim_v1_derived.yaml \
        --config configs/examcheat.yaml --config data/runs/demo_synth/session.yaml --render

Writes video.mp4 (clips concatenated) + video.frames.csv (t = t0 + frame_index / container fps) +
video.meta.json, net.pcap (+ labels) from synth.netsim with uploads at the chosen times, session.json
(clip spans and classes) and session.yaml (overlay for replay).

session.yaml contains DEMO-ONLY settings that are NOT measurements (each is marked in the file):
fusion.pre_s/post_s = 0 because both streams share one synthetic clock and uploads are placed inside the
behaviour by construction; vision.action.min_duration_s = 0 (no minimum) because ExamCheat-MultiV gives
no usable duration. Real values come from team recordings (docs/recording_protocol.md, time_sync.md).
Dataset clips are CC BY-SA 4.0 (datasets.examcheat.ATTRIBUTION): credit them when showing the render.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import yaml

from common.config import load_config
from datasets.examcheat import ATTRIBUTION, CLASSES, parse_prefix_name
from synth.netsim.scenario import run as netsim_run


def concat(clips: list[str], out: Path, t0: float) -> list[dict]:
    spans, writer, size, fps0, t, idx = [], None, None, None, t0, 0
    rows = []
    for c in clips:
        cap = cv2.VideoCapture(c)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open {c}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            raise RuntimeError(f"{c}: container reports no fps")
        t_clip, n = t, 0
        while True:
            ok, img = cap.read()
            if not ok:
                break
            if writer is None:
                size, fps0 = (img.shape[1], img.shape[0]), fps
                writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps0, size)
            if (img.shape[1], img.shape[0]) != size:
                img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
            writer.write(img)
            rows.append((idx, t_clip + n / fps))
            idx, n = idx + 1, n + 1
        cap.release()
        cls = parse_prefix_name(Path(c).name)[0]
        spans.append({"clip": c, "class": cls, "t_start": t_clip, "t_end": t_clip + n / fps, "frames": n,
                      "fps": fps})
        t = t_clip + n / fps
    writer.release()
    with open(out.with_suffix(".frames.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame_idx", "ts"])
        w.writerows(rows)
    out.with_suffix(".meta.json").write_text(json.dumps(
        {"source": "file (dataset clips)", "pipeline_delay_s_subtracted": 0.0,
         "note": "timestamps = t0 + frame index / container fps; no camera pipeline"}, indent=1) + "\n")
    return spans, size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--clip", action="append", required=True)
    ap.add_argument("--upload-at", type=float, action="append", default=[],
                    help="upload start, seconds from the session start")
    ap.add_argument("--upload-in-class", action="append", default=[], choices=CLASSES,
                    help="also start one upload at the middle of every clip of this class")
    ap.add_argument("--preset", required=True)
    ap.add_argument("--mcs", type=int, required=True)
    ap.add_argument("--resolution", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--tail", type=float, default=5.0, help="seconds of network capture after the last clip")
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args()
    cfg = load_config(*a.config)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    spans, (w, h) = concat(a.clip, out / "video.mp4", a.t0)
    t_end = spans[-1]["t_end"] + a.tail
    ups = sorted([a.t0 + x for x in a.upload_at] +
                 [(s["t_start"] + s["t_end"]) / 2 for s in spans if s["class"] in a.upload_in_class])
    meta = netsim_run(cfg, a.preset, None, a.mcs, a.resolution, a.seed, out / "net.pcap", a.t0,
                      t_end - a.t0, None, None, ups)
    dev = cfg["synth"]["netsim"]["macs"]["device"]
    (out / "session.json").write_text(json.dumps({"clips": spans, "uploads_at": ups, "netsim": meta,
                                                  "attribution": ATTRIBUTION}, indent=1) + "\n")
    overlay = {
        "capture": {"allowed_macs": [dev]},
        "vision": {"seats": {"S1": [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]},
                   "pose": {"backend": "onnx"},
                   "camera": {"pipeline_delay_s": 0.0},
                   "action": {"min_duration_s": 0.0}},
        "fusion": {"pre_s": 0.0, "post_s": 0.0},
    }
    head = ("# OFFLINE DEMO SESSION overlay generated by tools/make_demo_session.py -- not for the live system.\n"
            "# capture.allowed_macs: the synthetic device MAC (synth.netsim.macs.device).\n"
            "# vision.seats.S1: whole frame (calc from the video size): one examinee per ExamCheat V1 clip.\n"
            "# vision.pose.backend onnx: dev PC has no NPU. camera.pipeline_delay_s 0: timestamps come from the\n"
            "#   frame index (file replay), there is no camera pipeline.\n"
            "# DEMO-ONLY (not measured): action.min_duration_s 0 = no minimum duration; fusion.pre_s/post_s 0 =\n"
            "#   one shared synthetic clock and uploads placed inside the behaviour clips by construction.\n")
    (out / "session.yaml").write_text(head + yaml.safe_dump(overlay, sort_keys=False))
    print(json.dumps({"clips": [(Path(s["clip"]).name, s["class"], round(s["t_start"], 2), round(s["t_end"], 2))
                                for s in spans], "uploads_at": [round(u, 2) for u in ups],
                      "frames": meta["frames"]}, indent=1))


if __name__ == "__main__":
    main()
