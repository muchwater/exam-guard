"""Per-seat abnormal-behavior recognition from COCO-17 keypoint sequences.

1. pose_features(): scale-free geometric features per frame (normalized by shoulder width,
   which stays visible for a seated examinee behind a desk; hips are often occluded).
2. A frame classifier gives each frame a label (or None):
     RuleFrameClassifier  - behaviors defined in configs as AND of feature predicates
     ModelFrameClassifier - any ONNX model on a keypoint window, same output
3. TemporalSegmenter turns per-frame labels into BehaviorEvents: an event starts after the
   label has held for min_duration_s (gaps <= max_gap_s tolerated) and ends after a longer gap.
   Time-based, so results do not depend on the camera frame rate.
"""
from __future__ import annotations

import math
import operator
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from common.config import require
from common.events import BehaviorEvent, Person

NOSE, L_EYE, R_EYE, L_EAR, R_EAR, L_SHO, R_SHO, L_ELB, R_ELB, L_WRI, R_WRI, L_HIP, R_HIP = range(13)
FEATURES = [
    "head_drop",          # (nose_y - shoulder_mid_y) / shoulder_w ; image y grows downward
    "head_yaw",           # (nose_x - shoulder_mid_x) / shoulder_w
    "shoulder_tilt",      # (r_sho_y - l_sho_y) / shoulder_w
    "l_wrist_height",     # (shoulder_mid_y - l_wrist_y) / shoulder_w ; > 0 above shoulders
    "r_wrist_height",
    "max_wrist_height",
    "l_wrist_to_nose",    # |l_wrist - nose| / shoulder_w
    "r_wrist_to_nose",
    "min_wrist_to_nose",
    "l_wrist_visible",    # 1 if keypoint confidence >= kpt_conf else 0
    "r_wrist_visible",
    "ear_asym",           # l_ear_conf - r_ear_conf : head turned sideways shows one ear
]


def pose_features(k: np.ndarray, kpt_conf: float) -> dict[str, float]:
    """k: (17, 3). Missing (low-confidence) inputs yield NaN, which makes any predicate false."""
    ok = k[:, 2] >= kpt_conf
    nan = float("nan")
    f = {name: nan for name in FEATURES}
    f["l_wrist_visible"], f["r_wrist_visible"] = float(ok[L_WRI]), float(ok[R_WRI])
    f["ear_asym"] = float(k[L_EAR, 2] - k[R_EAR, 2])
    if not (ok[L_SHO] and ok[R_SHO]):
        return f
    sw = float(np.hypot(*(k[L_SHO, :2] - k[R_SHO, :2])))
    if sw < 1e-6:
        return f
    mid = (k[L_SHO, :2] + k[R_SHO, :2]) / 2
    f["shoulder_tilt"] = float((k[R_SHO, 1] - k[L_SHO, 1]) / sw)
    if ok[NOSE]:
        f["head_drop"] = float((k[NOSE, 1] - mid[1]) / sw)
        f["head_yaw"] = float((k[NOSE, 0] - mid[0]) / sw)
    hs, dn = [], []
    for side, w in (("l", L_WRI), ("r", R_WRI)):
        if ok[w]:
            f[f"{side}_wrist_height"] = float((mid[1] - k[w, 1]) / sw)
            hs.append(f[f"{side}_wrist_height"])
            if ok[NOSE]:
                f[f"{side}_wrist_to_nose"] = float(np.hypot(*(k[w, :2] - k[NOSE, :2])) / sw)
                dn.append(f[f"{side}_wrist_to_nose"])
    if hs:
        f["max_wrist_height"] = max(hs)
    if dn:
        f["min_wrist_to_nose"] = min(dn)
    return f


_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


class RuleFrameClassifier:
    """behaviors: {label: [{"feature": name, "op": ">", "value": x}, ...]} (all must hold)."""

    def __init__(self, behaviors: dict[str, list[dict]]):
        for label, preds in behaviors.items():
            for p in preds:
                if p["feature"] not in FEATURES:
                    raise ValueError(f"{label}: unknown feature {p['feature']}")
                if p["op"] not in _OPS:
                    raise ValueError(f"{label}: unknown op {p['op']}")
                if isinstance(p["value"], str):
                    raise ValueError(f"{label}: threshold for {p['feature']} is {p['value']}")
        self.behaviors = behaviors

    def __call__(self, feats: dict[str, float]) -> Optional[tuple[str, float]]:
        for label, preds in self.behaviors.items():
            if all(not math.isnan(feats[p["feature"]]) and _OPS[p["op"]](feats[p["feature"]], p["value"])
                   for p in preds):
                return label, 1.0
        return None


class ModelFrameClassifier:
    """ONNX model: input (1, T, len(FEATURES)) float32 window -> (1, C) class probabilities.

    labels[0] must be the 'normal' class. Missing (NaN) features are zero-filled; the *_visible
    features tell the model which inputs were missing.
    """

    def __init__(self, onnx_path: str, labels: list[str], window: int, threshold: float):
        import onnxruntime as ort
        self.sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        self.labels, self.threshold = labels, threshold
        self.hist: dict[str, deque] = {}
        self.window = window

    def __call__(self, feats: dict[str, float], seat: str = "") -> Optional[tuple[str, float]]:
        h = self.hist.setdefault(seat, deque(maxlen=self.window))
        h.append(np.nan_to_num(np.array([feats[n] for n in FEATURES], np.float32)))
        if len(h) < self.window:
            return None
        p = self.sess.run(None, {self.inp: np.stack(h)[None]})[0][0]
        c = int(np.argmax(p))
        return (self.labels[c], float(p[c])) if c != 0 and p[c] >= self.threshold else None


@dataclass
class _Run:
    label: str
    t_start: float
    t_last: float
    score: float
    emitted: bool = False


class TemporalSegmenter:
    def __init__(self, min_duration_s: float, max_gap_s: float):
        self.min_dur, self.max_gap = min_duration_s, max_gap_s
        self.runs: dict[str, _Run] = {}

    def update(self, seat: str, ts: float, pred: Optional[tuple[str, float]]) -> list[BehaviorEvent]:
        out = []
        run = self.runs.get(seat)
        if run is not None and (ts - run.t_last > self.max_gap or (pred is not None and pred[0] != run.label)):
            out += self._close(seat)
            run = None
        if pred is not None:
            if run is None:
                self.runs[seat] = _Run(pred[0], ts, ts, pred[1])
            else:
                run.t_last, run.score = ts, max(run.score, pred[1])
        return out

    def advance(self, ts: float) -> list[BehaviorEvent]:
        return [e for s, r in list(self.runs.items()) if ts - r.t_last > self.max_gap for e in self._close(s)]

    def _close(self, seat: str) -> list[BehaviorEvent]:
        r = self.runs.pop(seat)
        if r.t_last - r.t_start >= self.min_dur:
            return [BehaviorEvent(r.t_start, r.t_last, seat, r.label, r.score)]
        return []

    def flush(self) -> list[BehaviorEvent]:
        return [e for s in list(self.runs) for e in self._close(s)]

    def active(self) -> dict[str, str]:
        """seat -> label of runs that already satisfy min_duration (for live display)."""
        return {s: r.label for s, r in self.runs.items() if r.t_last - r.t_start >= self.min_dur}


class ActionRecognizer:
    def __init__(self, frame_clf, segmenter: TemporalSegmenter, kpt_conf: float):
        self.clf, self.seg, self.kpt_conf = frame_clf, segmenter, kpt_conf
        self.last_feats: dict[str, dict[str, float]] = {}

    @classmethod
    def from_config(cls, cfg: dict) -> "ActionRecognizer":
        a = "vision.action."
        kind = cfg["vision"]["action"]["classifier"]
        min_dur, max_gap = require(cfg, a + "min_duration_s", a + "max_gap_s")
        kpt_conf = require(cfg, "vision.pose.kpt_conf")
        if kind == "rule":
            behaviors = cfg["vision"]["action"]["behaviors"]
            clf = RuleFrameClassifier(behaviors)
        elif kind == "model":
            m = cfg["vision"]["action"]["model"]
            window, thr = require(cfg, a + "model.window_frames", a + "model.threshold")
            clf = ModelFrameClassifier(m["onnx"], m["labels"], int(window), float(thr))
        else:
            raise ValueError(f"unknown vision.action.classifier {kind}")
        return cls(clf, TemporalSegmenter(float(min_dur), float(max_gap)), float(kpt_conf))

    def update(self, ts: float, persons: list[Person]) -> list[BehaviorEvent]:
        out = []
        for p in persons:
            if p.seat is None:
                continue
            f = pose_features(p.keypoints, self.kpt_conf)
            self.last_feats[p.seat] = f
            pred = self.clf(f, p.seat) if isinstance(self.clf, ModelFrameClassifier) else self.clf(f)
            out += self.seg.update(p.seat, ts, pred)
        return out + self.seg.advance(ts)
