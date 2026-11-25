"""Vision pipeline: frame -> pose (NPU, CPU fallback) -> seat -> behavior events. Runs standalone:

    python -m vision.pipeline --video data/raw/act_01.mp4            # recorded (uses act_01.frames.csv)
    python -m vision.pipeline --live --show                           # camera from configs
    python -m vision.pipeline --video ... --dump-features feats.csv   # per-frame features (for threshold measurement)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging

import numpy as np

from common.config import load_config, require
from common.events import BehaviorEvent, PoseFrame
from common.profiling import LatencyLog
from vision.action import FEATURES, ActionRecognizer, pose_features
from vision.backends import build_runner
from vision.pose import PoseEstimator
from vision.seats import SeatMap


class VisionPipeline:
    def __init__(self, cfg: dict, latency: LatencyLog | None = None, with_action: bool = True):
        self.lat = latency or LatencyLog()
        conf, iou, kpt_conf = require(cfg, "vision.pose.conf_thres", "vision.pose.iou_thres", "vision.pose.kpt_conf")
        self.runner = build_runner(cfg)
        self.pose = PoseEstimator(self.runner, float(conf), float(iou), self.lat)
        self.seats = SeatMap(require(cfg, "vision.seats"), float(kpt_conf))
        self.action = ActionRecognizer.from_config(cfg) if with_action else None
        self.kpt_conf = float(kpt_conf)

    def process(self, idx: int, ts: float, img: np.ndarray) -> tuple[PoseFrame, list[BehaviorEvent]]:
        with self.lat.stage("vision.total"):
            persons = self.pose(img)
            with self.lat.stage("vision.seat"):
                persons = self.seats.assign(persons)
            events = []
            if self.action is not None:
                with self.lat.stage("vision.action"):
                    events = self.action.update(ts, persons)
        return PoseFrame(ts, idx, persons), events

    def flush(self) -> list[BehaviorEvent]:
        return self.action.seg.flush() if self.action else []

    def close(self) -> None:
        self.runner.close()


def main() -> None:
    import cv2

    from vision.camera import VideoFileSource
    from vision.pose import draw
    from vision.record import camera_from_config

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video")
    src.add_argument("--live", action="store_true")
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--dump-features", help="write per-frame seat features to CSV and skip behavior rules")
    ap.add_argument("--dump-keypoints", help="write per-frame keypoints (JSONL) for synth/pose_stats.py")
    ap.add_argument("--latency-csv")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(*a.config)
    pipe = VisionPipeline(cfg, with_action=not (a.dump_features or a.dump_keypoints))
    kj = open(a.dump_keypoints, "w") if a.dump_keypoints else None
    source = VideoFileSource(a.video) if a.video else camera_from_config(cfg)
    fw = None
    if a.dump_features:
        fh = open(a.dump_features, "w", newline="")
        fw = csv.writer(fh)
        fw.writerow(["frame_idx", "ts", "seat"] + FEATURES)
    try:
        for idx, ts, img in source:
            pf, events = pipe.process(idx, ts, img)
            for e in events:
                print(f"[behavior] {e.t_start:.3f}-{e.t_end:.3f} seat={e.seat} {e.label} score={e.score:.2f}")
            if kj:
                kj.write(json.dumps({"ts": ts, "frame_idx": idx, "persons": [
                    {"seat": p.seat, "score": p.score, "bbox": p.bbox.tolist(), "kpts": p.keypoints.tolist()}
                    for p in pf.persons]}) + "\n")
            if fw:
                for p in pf.persons:
                    f = pose_features(p.keypoints, pipe.kpt_conf)
                    fw.writerow([idx, f"{ts:.6f}", p.seat] + [f"{f[n]:.4f}" for n in FEATURES])
            if a.show:
                draw(img, pf.persons, pipe.kpt_conf)
                pipe.seats.draw(img)
                cv2.imshow("vision", img)
                if cv2.waitKey(1) == 27:
                    break
    finally:
        for e in pipe.flush():
            print(f"[behavior] {e.t_start:.3f}-{e.t_end:.3f} seat={e.seat} {e.label} score={e.score:.2f}")
        source.close()
        pipe.close()
        print(pipe.lat.format_summary())
        if a.latency_csv:
            pipe.lat.dump_csv(a.latency_csv)


if __name__ == "__main__":
    main()
