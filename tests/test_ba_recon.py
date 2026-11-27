"""capture/ba_recon: MPDU progress from BlockAck scoreboards. Sequence numbers and times are TEST FIXTURES."""
import struct

from capture.ba_recon import PPDU_MAX_S, BaReconstructor, parse_ba, reconstruct
from capture.dot11 import build_ctrl_frame, build_dot11_header, build_radiotap
from capture.macfilter import MacAllowlist
from capture.pcap_io import read_pcap, write_pcap
from net_event.features import is_uplink_data

DEV = "02:00:00:00:00:01"
AP = "02:00:00:00:00:fe"
OTHER = "02:00:00:00:00:99"
RT = build_radiotap(None, None, None)


def ba(ra: str, ta: str, ssn: int, bitmap: int, tid: int = 0) -> bytes:
    f = bytearray(build_ctrl_frame(9, ra, ta, body_len=12))
    struct.pack_into("<HHQ", f, 16, (tid << 12) | 0x4, ssn << 4, bitmap)   # compressed, single TID
    return bytes(f)


def test_parse_ba_fields_and_rejects_other_frames():
    assert parse_ba(ba(DEV, AP, 100, 0b1011, tid=6)) == (DEV, AP, 6, 100, 0b1011)
    assert parse_ba(build_ctrl_frame(11, AP, DEV)) is None                   # RTS
    multi = bytearray(ba(DEV, AP, 1, 1))
    multi[16] |= 0x2                                                         # Multi-TID BlockAck
    assert parse_ba(bytes(multi)) is None


def test_progress_counts_new_mpdus_across_missed_blockacks_and_wrap():
    r = BaReconstructor([DEV])
    assert r.feed(1.000, ba(DEV, AP, 100, 0b1111)) is None                   # first BA only sets the state
    p = r.feed(1.004, ba(DEV, AP, 104, (1 << 10) - 1))                       # SN 104..113
    assert p.direction == "up" and p.sns == list(range(104, 114)) and p.peer == AP
    assert r.feed(1.005, ba(DEV, AP, 104, (1 << 10) - 1)) is None            # same scoreboard again
    p = r.feed(1.030, ba(DEV, AP, 150, 0xFF))                                # BAs for 114..149 were missed
    assert len(p.sns) == 157 - 113
    assert r.feed(1.031, ba(DEV, AP, 120, 1)) is None                        # an older BlockAck
    r = BaReconstructor([DEV])
    r.feed(2.0, ba(DEV, AP, 4085, 0b11))                                     # high 4086
    p = r.feed(2.010, ba(DEV, AP, 0, 0b111))                                 # high 2: wraps past 4095
    assert len(p.sns) == 12 and p.sns[0] == 4087 and p.sns[-1] == 2
    assert p.t_from == 2.010 - PPDU_MAX_S                                    # spread over at most one PPDU


def test_direction_and_other_devices():
    r = BaReconstructor([DEV])
    r.feed(0.0, ba(AP, DEV, 10, 1))
    p = r.feed(0.01, ba(AP, DEV, 11, 0b111))                                 # DEV acknowledges AP's MPDUs
    assert p.direction == "down" and p.device == DEV and len(p.sns) == 3
    r.feed(0.0, ba(OTHER, AP, 10, 1))
    assert r.feed(0.01, ba(OTHER, AP, 20, 1)) is None


def test_reconstructed_pcap_reads_as_uplink_data(tmp_path):
    frames = [(1.000, RT + ba(DEV, AP, 0, 1)), (1.003, RT + ba(DEV, AP, 1, (1 << 20) - 1)),
              (1.004, RT + ba(AP, DEV, 50, 1)), (1.006, RT + ba(AP, DEV, 51, 0b11))]
    # a data frame the sniffer did decode (SN 5) must not be counted again
    frames.insert(1, (1.002, RT + build_dot11_header(2, 8, AP, DEV, AP, True, False, True, seq=5) + bytes(8)))
    out, counts = reconstruct(frames, [DEV])
    assert counts[(DEV, "up")] == 19 and counts[(DEV, "down")] == 2
    p = tmp_path / "r.pcap"
    write_pcap(p, out)
    pk = read_pcap(p, MacAllowlist([DEV]))
    up = [x for x in pk if is_uplink_data(x, DEV)]
    assert len(up) == 20                                                     # 19 pseudo + 1 decoded
    assert all(1.0 <= x.ts <= 1.003 for x in up) and [x.ts for x in pk] == sorted(x.ts for x in pk)
    down = [x for x in pk if x.is_data and x.addr1 == DEV and x.from_ds]
    assert len(down) == 2
