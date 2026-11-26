"""tools/real_session: clock offset from in-band markers, segmentation. Timings are TEST FIXTURES."""
import json

import numpy as np

from capture.dot11 import build_dot11_header, build_radiotap
from capture.pcap_io import read_pcap, write_pcap
from tools.real_session import cut_points, marker_offset, shift

DEV = "02:00:00:00:00:01"
AP = "02:00:00:00:00:fe"
OTHER = "02:00:00:00:00:99"


def _frame(ta: str, ra: str, mpdu_len: int) -> bytes:
    hdr = build_dot11_header(2, 8, ra, ta, ra, to_ds=True, from_ds=False, protected=True)
    return build_radiotap(None, None, None) + hdr + bytes(mpdu_len - len(hdr))


def test_marker_offset_recovers_clock_difference(tmp_path):
    true_off = -0.5911                                   # uploader - sniffer
    markers, frames = [], []
    for i, n in enumerate(1000 + 7 * k for k in range(10)):
        t_send = 100.0 + 0.2 * i
        markers.append({"udp_payload": n, "ip": 6, "t_send": t_send})
        t_air = t_send - true_off + 0.0004               # sniffer clock + queueing/air time
        frames.append((t_air, _frame(DEV, AP, n + 98)))
        frames.append((t_air + 0.003, _frame(DEV, AP, n + 98)))      # a retry of the same frame
        frames.append((t_air + 0.05, _frame(OTHER, AP, n + 98)))     # another device: must be ignored
    frames.append((150.0, _frame(DEV, AP, 1000 + 98)))               # same size sent much later
    frames.sort()
    p = tmp_path / "c.pcap"
    write_pcap(p, frames)
    off, picked = marker_offset(p, markers, DEV)
    assert abs(off - true_off - (-0.0004)) < 1e-3
    assert len(picked) == 10


def test_cut_points_split_at_largest_gaps():
    acts = [{"t_start": t, "t_end": t + 1} for t in (0, 3, 6, 50, 53, 100, 103)]
    assert cut_points(acts, 3) == [28.5, 77.0]     # midpoints of (7, 50) and (54, 100)


def test_shift_moves_to_sniffer_clock():
    r = shift([{"t_start": 10.0, "t_end": 11.0, "bytes": 5}], -0.5)
    assert r == [{"t_start": 10.5, "t_end": 11.5, "bytes": 5}]


def test_written_pcap_roundtrip(tmp_path):
    p = tmp_path / "x.pcap"
    write_pcap(p, [(1.0, _frame(DEV, AP, 1550))])
    pk = read_pcap(p)
    assert len(pk) == 1 and pk[0].frame_len == 1550 and pk[0].addr2 == DEV
    assert json.dumps({"ok": bool(np.isfinite(pk[0].ts))})
