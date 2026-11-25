import numpy as np

from common.events import Person
from vision.action import RuleFrameClassifier, TemporalSegmenter, pose_features
from vision.pose import decode, letterbox
from vision.seats import SeatMap


def kpts(nose_y=40.0, wrist_y=150.0):
    k = np.zeros((17, 3), np.float32)
    k[:, 2] = 0.9
    k[5, :2], k[6, :2] = (120, 100), (80, 100)          # shoulders: width 40, mid (100, 100)
    k[0, :2] = (100, nose_y)
    k[9, :2], k[10, :2] = (125, wrist_y), (75, wrist_y)
    return k


def test_pose_features_scale_free():
    f = pose_features(kpts(nose_y=60), 0.5)
    assert abs(f["head_drop"] - (-1.0)) < 1e-6 and abs(f["shoulder_tilt"]) < 1e-6
    k2 = kpts(nose_y=60)
    k2[:, :2] = k2[:, :2] * 2 + 7                        # scale + shift
    assert abs(pose_features(k2, 0.5)["head_drop"] - f["head_drop"]) < 1e-6


def test_low_confidence_gives_nan_and_false_predicate():
    k = kpts()
    k[0, 2] = 0.1
    f = pose_features(k, 0.5)
    assert np.isnan(f["head_drop"])
    clf = RuleFrameClassifier({"x": [{"feature": "head_drop", "op": ">", "value": -10}]})
    assert clf(f) is None


def test_segmenter_min_duration_and_gap():
    seg = TemporalSegmenter(min_duration_s=1.0, max_gap_s=0.3)
    out = []
    for t in np.arange(0, 2.0, 0.1):
        out += seg.update("S1", t, ("a", 1.0) if t < 1.5 else None)
    out += seg.advance(3.0)
    assert len(out) == 1 and out[0].t_start == 0 and abs(out[0].t_end - 1.4) < 1e-9
    seg2 = TemporalSegmenter(min_duration_s=1.0, max_gap_s=0.3)
    for t in np.arange(0, 0.5, 0.1):
        seg2.update("S1", t, ("a", 1.0))
    assert seg2.flush() == []                            # shorter than min_duration


def test_seat_assignment_by_shoulder_mid():
    sm = SeatMap({"S1": [[0, 0], [200, 0], [200, 200], [0, 200]], "S2": [[200, 0], [400, 0], [400, 200], [200, 200]]}, 0.5)
    p = Person(np.array([0, 0, 1, 1], np.float32), 0.9, kpts())
    assert sm.seat_of(p) == "S1"


def test_decode_maps_back_to_image():
    img = np.zeros((480, 640, 3), np.uint8)
    _, r, pad = letterbox(img, (640, 640))
    out = np.zeros((1, 56, 3), np.float32)
    out[0, :5, 0] = [320, 320, 100, 200, 0.9]            # one person at the letterboxed center
    out[0, 5::3, 0] = 320
    out[0, 6::3, 0] = 320
    out[0, 7::3, 0] = 0.8
    (p,) = decode(out, r, pad, conf_thres=0.25, iou_thres=0.7)
    assert np.allclose(p.keypoints[0, :2], (320, 240), atol=1)
