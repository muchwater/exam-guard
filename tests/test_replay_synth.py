"""End-to-end: synthetic pcap (from a fixture 'measurement') -> replay merge -> detector -> fusion."""
import json

import numpy as np

from capture.macfilter import MacAllowlist
from common.config import load_config
from common.events import BehaviorEvent
from net_event.labels import load_intervals, match_events
from replay.run import Pipeline, merged_stream
from synth.dist import QuantileDist
from synth.pcap_gen import generate

# TEST FIXTURE ONLY - shaped like net_event.measure output, values are not measurements.
def q(lo, mid, hi):
    return {"min": lo, "p50": mid, "max": hi}


FIXTURE_UPLOAD = {"uplink_size_in_upload": q(1400, 1500, 1550), "uplink_iat_in_upload": q(0.0005, 0.001, 0.003),
                  "air_bytes_per_upload": q(150_000, 200_000, 250_000)}
FIXTURE_BG = {"uplink_size_all": q(60, 120, 400), "uplink_iat_all": q(0.5, 2.0, 4.0)}


def fixture_cfg(tmp_path):
    (tmp_path / "up.json").write_text(json.dumps(FIXTURE_UPLOAD))
    (tmp_path / "bg.json").write_text(json.dumps(FIXTURE_BG))
    cfg = load_config()
    s = cfg["synth"]["pcap"]
    s.update(duration_s=60, upload_interval_s=15, measured_upload=str(tmp_path / "up.json"),
             measured_background=str(tmp_path / "bg.json"), channel_mhz=2437, rssi_dbm=-40)
    cfg["capture"]["allowed_macs"] = [s["device_mac"]]
    cfg["net_event"]["rule"].update(burst_gap_s=0.1, min_burst_bytes=100_000, large_frame_bytes=1400,
                                    min_large_ratio=0.5)
    cfg["fusion"].update(pre_s=1.0, post_s=3.0)
    return cfg


def test_quantile_dist_monotone_and_bounded():
    d = QuantileDist(q(1, 5, 9))
    x = d.sample(np.random.default_rng(0), 1000)
    assert x.min() >= 1 and x.max() <= 9


def test_synth_replay_detects_every_upload_and_fuses(tmp_path):
    cfg = fixture_cfg(tmp_path)
    info = generate(cfg, tmp_path / "s.pcap")
    gt = load_intervals(tmp_path / "s.labels.jsonl")
    assert info["uploads"] == len(gt) == 3

    class FakeVideo:  # frames every 0.5 s, timestamps only
        def __iter__(self):
            for i, t in enumerate(np.arange(0, 60, 0.5)):
                yield i, float(t), None

    pipe = Pipeline(cfg, with_vision=False)
    last = -1.0
    kinds = set()
    for ts, kind, item in merged_stream(str(tmp_path / "s.pcap"), MacAllowlist.from_config(cfg), FakeVideo()):
        assert ts >= last
        last = ts
        kinds.add(kind)
        if kind == "pkt":
            pipe.on_packet(item)
        else:
            pipe.on_frame(*item)
    pipe.finish(last)
    assert kinds == {"pkt", "frame"}
    m = match_events([(e.t_start, e.t_end) for e in pipe.net_events], gt)
    assert m["recall"] == 1.0 and m["precision"] == 1.0
    # a behavior just before the 2nd upload fuses; one far from any upload does not
    al = pipe.fusion.add_behavior(BehaviorEvent(gt[1].t_start - 2.0, gt[1].t_start - 1.0, "S3", "head_down_hold", 1.0))
    assert [a.seat for a in al] == ["S3"]
    assert pipe.fusion.add_behavior(BehaviorEvent(5.0, 6.0, "S4", "head_down_hold", 1.0)) == []
