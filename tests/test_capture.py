import io

import pytest

from capture.dot11 import LINKTYPE_IEEE802_11_RADIOTAP, parse_frame, parse_radiotap
from capture.macfilter import MacAllowlist
from capture.pcap_io import PcapWriter, iter_packets
from tests.helpers import AP, DEV, OTHER, data_frame


def _pcap(frames):
    buf = io.BytesIO()
    w = PcapWriter(buf)
    for ts, (data, orig) in frames:
        w.write(ts, data, orig)
    buf.seek(0)
    return buf


def test_radiotap_fields():
    data, _ = data_frame(100)
    rt_len, f = parse_radiotap(data)
    assert rt_len == 15 and f["rssi_dbm"] == -40 and f["channel_mhz"] == 2437


def test_frame_len_excludes_radiotap_and_fcs():
    for fcs in (False, True):
        data, orig = data_frame(500, fcs=fcs)
        p = parse_frame(1.0, data, orig, LINKTYPE_IEEE802_11_RADIOTAP)
        assert p.frame_len == 500 and p.addr2 == DEV and p.addr1 == AP and p.to_ds and p.protected


def test_roundtrip_timestamps_and_truncated_capture():
    data, orig = data_frame(1400)
    buf = _pcap([(1700000000.123456, (data[:60], orig))])   # snaplen-truncated record keeps orig_len
    (p,) = list(iter_packets(buf))
    assert abs(p.ts - 1700000000.123456) < 1e-6 and p.frame_len == 1400


def test_allowlist_drops_other_devices():
    buf = _pcap([(1.0, data_frame(300)), (2.0, data_frame(300, ta=OTHER, ra=AP))])
    pkts = list(iter_packets(buf, MacAllowlist([DEV])))
    assert [p.ts for p in pkts] == [1.0]


def test_allowlist_refuses_empty_and_builds_bpf():
    with pytest.raises(ValueError):
        MacAllowlist([])
    assert MacAllowlist([DEV.upper()]).bpf() == f"wlan addr1 {DEV} or wlan addr2 {DEV}"


def test_pcapng_rejected():
    with pytest.raises(ValueError, match="pcapng"):
        list(iter_packets(io.BytesIO(b"\x0a\x0d\x0d\x0a" + bytes(20))))
