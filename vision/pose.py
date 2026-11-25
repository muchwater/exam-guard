"""Multi-person 2D pose with a YOLOv8/YOLO11-pose style model (COCO 17 keypoints).

Pre/post-processing follow the Ultralytics implementation the model was trained with:
  * letterbox resize, pad color (114, 114, 114)  - source: ultralytics/data/augment.py LetterBox
  * BGR -> RGB, scale to [0, 1]                  - source: ultralytics/engine/predictor.py preprocess
  * output (1, 4 + 1 + 17*3, N): cx, cy, w, h, person score, (x, y, visibility) x 17
                                                 - source: ultralytics/nn/modules/head.py Pose
  * conf / NMS IoU thresholds: configs vision.pose.conf_thres / iou_thres
"""
from __future__ import annotations

import cv2
import numpy as np

from common.events import Person
from common.profiling import LatencyLog

N_KPT = 17
COCO_KPT = ["nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
            "l_wrist", "r_wrist", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle"]
SKELETON = [(5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15),
            (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4)]
LETTERBOX_PAD = 114


def letterbox(img: np.ndarray, hw: tuple[int, int]) -> tuple[np.ndarray, float, tuple[float, float]]:
    h, w = img.shape[:2]
    r = min(hw[0] / h, hw[1] / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    top, left = (hw[0] - nh) / 2, (hw[1] - nw) / 2
    out = np.full((hw[0], hw[1], 3), LETTERBOX_PAD, np.uint8)
    out[int(round(top - 0.1)):int(round(top - 0.1)) + nh, int(round(left - 0.1)):int(round(left - 0.1)) + nw] = \
        cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return out, r, (left, top)


def preprocess(img_bgr: np.ndarray, hw: tuple[int, int]) -> tuple[np.ndarray, float, tuple[float, float]]:
    lb, r, pad = letterbox(img_bgr, hw)
    x = lb[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(x), r, pad


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep = []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / (area[i] + area[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thres]
    return keep


def decode(out: np.ndarray, r: float, pad: tuple[float, float], conf_thres: float, iou_thres: float) -> list[Person]:
    """Decode raw (1, 56, N) or (1, N, 56) output into persons in original-image pixels."""
    a = np.asarray(out, np.float32)[0]
    if a.shape[0] != 5 + 3 * N_KPT:
        a = a.T
    a = a.T                                      # (N, 56)
    a = a[a[:, 4] >= conf_thres]
    if not len(a):
        return []
    cx, cy, w, h = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1)
    keep = nms(boxes, a[:, 4], iou_thres)
    persons = []
    for i in keep:
        b = boxes[i].copy()
        b[[0, 2]] = (b[[0, 2]] - pad[0]) / r
        b[[1, 3]] = (b[[1, 3]] - pad[1]) / r
        k = a[i, 5:].reshape(N_KPT, 3).copy()
        k[:, 0] = (k[:, 0] - pad[0]) / r
        k[:, 1] = (k[:, 1] - pad[1]) / r
        persons.append(Person(bbox=b, score=float(a[i, 4]), keypoints=k))
    return persons


class PoseEstimator:
    def __init__(self, runner, conf_thres: float, iou_thres: float, latency: LatencyLog | None = None):
        self.runner, self.conf, self.iou = runner, conf_thres, iou_thres
        self.lat = latency or LatencyLog()

    def __call__(self, img_bgr: np.ndarray) -> list[Person]:
        with self.lat.stage("vision.preprocess"):
            x, r, pad = preprocess(img_bgr, self.runner.input_hw)
        with self.lat.stage(f"vision.inference[{self.runner.name}]"):
            outs = self.runner.run(x)
        us = getattr(self.runner, "last_execute_us", None)
        if us is not None:
            self.lat.add("vision.inference.npu_execute", us / 1e3)
        with self.lat.stage("vision.postprocess"):
            return decode(outs[0], r, pad, self.conf, self.iou)


def draw(img: np.ndarray, persons: list[Person], kpt_conf: float, color=(0, 255, 0)) -> None:
    for p in persons:
        x1, y1, x2, y2 = p.bbox.astype(int)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 1)
        k = p.keypoints
        for i, j in SKELETON:
            if k[i, 2] >= kpt_conf and k[j, 2] >= kpt_conf:
                cv2.line(img, tuple(k[i, :2].astype(int)), tuple(k[j, :2].astype(int)), color, 2)
        for x, y, c in k:
            if c >= kpt_conf:
                cv2.circle(img, (int(x), int(y)), 3, (0, 200, 255), -1)
        if p.seat:
            cv2.putText(img, p.seat, (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
