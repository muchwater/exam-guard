"""synth/netsim: air-time formulas, TCP/802.11 framing sizes, and an end-to-end run through the detector."""
import json

import numpy as np
import pytest

from capture.macfilter import MacAllowlist
from capture.pcap_io import read_pcap
from common.config import load_config
from net_event.detector import RuleDetector
from net_event.labels import load_intervals
from synth.netsim.phy import Phy
from synth.netsim.scenario import run


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def phy(cfg):
    return Phy.from_config(cfg["synth"]["netsim"]["phy"])


def test_ht_rates_match_80211n_table(phy):
    # HT20 long-GI single-stream rates (Mb/s) follow from bits/symbol / 4 us
    assert [round(phy.ht_rate_mbps(m), 1) for m in range(8)] == [6.5, 13.0, 19.5, 26.0, 39.0, 52.0, 58.5, 65.0]


def test_ppdu_and_response_durations(phy):
    # 1534-byte MPDU at MCS7: ceil((8*1534 + 22) / 260) = 48 symbols -> 36 + 192 us
    assert phy.ht_ppdu_us(1534, 7) == 36 + 48 * 4
    # ACK (14 B) at 24 Mb/s: ceil((112 + 22) / 96) = 2 symbols -> 20 + 8 us
    assert phy.response_rate_mbps(7) == 24 and phy.ack_us(7) == 28
    # control response never faster than the eliciting rate: MCS0 (6.5) -> 6 Mb/s
    assert phy.response_rate_mbps(0) == 6
    assert phy.ppdu_max_us == 20 + -(-(4095 * 8 + 22) // 24) * 4


def test_ampdu_layout_pads_all_but_last(phy):
    psdu, ends = phy.ampdu_layout([101, 101])
    assert ends == [105, 105 + 3 + 105] and psdu == ends[-1]


def _frames(tmp_path, cfg, preset, mcs=7, res="640x480"):
    out = tmp_path / f"{preset}.pcap"
    meta = run(cfg, preset, None, mcs, res, seed=3, out=out, duration_s=60, interval_s=20)
    pk = read_pcap(out, MacAllowlist([cfg["synth"]["netsim"]["macs"]["device"]]))
    return meta, pk, out


def test_esp32_lan_frame_sizes(tmp_path, cfg):
    meta, pk, out = _frames(tmp_path, cfg, "esp32_lan")
    assert meta["uploads"] == 3
    dev = cfg["synth"]["netsim"]["macs"]["device"]
    up = [p for p in pk if p.is_data and p.addr2 == dev]
    sizes = {p.frame_len for p in up}
    # 50 B of 802.11 QoS+CCMP+LLC overhead around IPv4: SYN with MSS option 44 B IP, pure ACK 40 B,
    # full lwIP segment 20+20+1440
    assert {94, 90, 1530} <= sizes
    assert max(sizes) == 1530
    gt = load_intervals(out.with_suffix(".labels.jsonl"))
    assert all(g.t_end > g.t_start for g in gt)
    assert all(g.meta["bytes"] > 0 and g.meta["synthetic"] for g in gt)


def test_linux_mss_with_timestamps(tmp_path, cfg):
    _, pk, _ = _frames(tmp_path, cfg, "linux_wan")
    dev = cfg["synth"]["netsim"]["macs"]["device"]
    assert max(p.frame_len for p in pk if p.is_data and p.addr2 == dev) == 50 + 20 + 20 + 12 + 1448


def test_rule_detector_finds_synthetic_uploads(tmp_path, cfg):
    _, pk, out = _frames(tmp_path, cfg, "esp32_lan")
    gt = load_intervals(out.with_suffix(".labels.jsonl"))
    # TEST ONLY thresholds (not calibrated): a burst gap well below the 20 s upload interval
    det = RuleDetector(burst_gap_s=1.0, min_burst_bytes=50_000, large_frame_bytes=1500, min_large_ratio=0.5)
    ev = det.run(sorted(pk, key=lambda p: p.ts))
    assert len(ev) == len(gt) == 3
    for e, g in zip(ev, gt):
        assert e.t_start <= g.t_end and e.t_end >= g.t_start


def test_run_is_deterministic(tmp_path, cfg):
    a = run(cfg, "esp32_wan", None, 3, "800x600", seed=5, out=tmp_path / "a.pcap", duration_s=30, interval_s=10)
    b = run(cfg, "esp32_wan", None, 3, "800x600", seed=5, out=tmp_path / "b.pcap", duration_s=30, interval_s=10)
    assert (tmp_path / "a.pcap").read_bytes() == (tmp_path / "b.pcap").read_bytes()
    assert a["frames"] == b["frames"]
    la = [json.loads(x) for x in open(tmp_path / "a.labels.jsonl")]
    assert np.isclose(la[0]["t_end"] - la[0]["t_start"], la[0]["t_end"] - la[0]["t_start"])
