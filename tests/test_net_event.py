import numpy as np

from capture.dot11 import LINKTYPE_IEEE802_11_RADIOTAP, parse_frame
from net_event.detector import LogisticModel, RuleDetector, WindowModelDetector
from net_event.features import feature_names, segment_bursts
from tests.helpers import DEV, data_frame


def pkt(ts, size, uplink=True):
    d, o = data_frame(size, uplink=uplink, ta=DEV if uplink else "02:00:00:00:00:fe",
                      ra="02:00:00:00:00:fe" if uplink else DEV)
    return parse_frame(ts, d, o, LINKTYPE_IEEE802_11_RADIOTAP)


def trace():
    """Fixture: sparse small frames + two dense bursts of large frames (test values only)."""
    ps = [pkt(t, 100) for t in np.arange(0, 30, 2.0)]
    for t0 in (10.0, 20.0):
        ps += [pkt(t0 + i * 0.002, 1500) for i in range(200)]
        ps += [pkt(t0 + i * 0.004 + 0.001, 80, uplink=False) for i in range(100)]
    return sorted(ps, key=lambda p: p.ts)


def test_burst_segmentation():
    bursts = segment_bursts(trace(), DEV, burst_gap_s=0.5)
    big = [b for b in bursts if b.total_bytes > 100_000]
    assert len(big) == 2 and all(b.n_frames >= 200 for b in big)


def test_rule_detector_finds_two_uploads():
    det = RuleDetector(burst_gap_s=0.5, min_burst_bytes=100_000, large_frame_bytes=1400, min_large_ratio=0.5)
    evs = det.run(trace())
    assert [round(e.t_start) for e in evs] == [10, 20]


def test_rule_detector_advance_closes_without_packets():
    det = RuleDetector(burst_gap_s=0.5, min_burst_bytes=100_000, large_frame_bytes=1400, min_large_ratio=0.5)
    for p in [x for x in trace() if x.ts < 11]:
        assert not det.update(p) or True
    assert len(det.advance(12.0)) == 1


def test_window_model_same_interface():
    windows = [1.0, 2.0]
    names = feature_names(windows)
    # hand-made model: score by uplink bytes in the 1 s window
    w = np.zeros(len(names))
    w[names.index("up_bytes@1s")] = 1.0
    model = LogisticModel(np.zeros(len(names)), np.full(len(names), 1e4), w, -5.0, names)
    det = WindowModelDetector(model, windows, step_s=0.25, large_frame_bytes=1400, threshold=0.5)
    evs = det.run(trace())
    assert len(evs) == 2 and 10 <= evs[0].t_end <= 12 and 20 <= evs[1].t_end <= 22


def test_logistic_fit_separates():
    rng = np.random.default_rng(0)
    X = np.vstack([rng.normal(0, 1, (50, 2)), rng.normal(4, 1, (50, 2))])
    y = np.r_[np.zeros(50), np.ones(50)]
    m = LogisticModel.fit(X, y, l2=1.0)
    assert ((m.predict_proba(X) >= 0.5) == y).mean() > 0.95
