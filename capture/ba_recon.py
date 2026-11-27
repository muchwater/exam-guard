"""Reconstruct a device's data-frame (MPDU) progress from BlockAck frames, for sniffers that cannot decode its data.

    python -m capture.ba_recon --in capture.pcap --out recon.pcap --device <team MAC> [--device ...]

Why (docs/monitor_mode_test.md, 2026-11-27): the board's RTL8192EU decodes the HT-SIG of every HT PPDU but fails the
CRC of all of them (BB counter 0xF90 during uploads: HT OK 0, HT CRC error ~134/s), because the team device and the AP
use LDPC (both advertise it in HT Capabilities) and the RTL8192E has no LDPC decoder (vendor driver
hal/rtl8192e/rtl8192e_hal_init.c: HAL_DEF_RX_LDPC = FALSE). Control frames are sent at legacy rates and are received.

A compressed BlockAck (IEEE 802.11-2016 9.3.1.9.3) carries the recipient's scoreboard: Starting Sequence Number (SSN)
and a 64-bit bitmap, bit i = MPDU with SN (SSN + i) mod 4096 received. A transmitter numbers the QoS data MPDUs of
one TID consecutively, so the advance of the highest acknowledged SN between two captured BlockAcks is the number of
new MPDUs sent in between, also when BlockAcks in between were missed (while fewer than 2048 MPDUs pass: half the SN
space, beyond which an advance and an old BlockAck cannot be told apart).
  BA with RA = team device -> MPDUs the device sent (uplink)
  BA with TA = team device -> MPDUs the device received (downlink)
Each new MPDU becomes a pseudo QoS-data frame (MAC header only: its frame_len carries NO size information) timed
evenly over the A-MPDU that preceded the BlockAck: back to the previous BlockAck of the same flow, at most
aPPDUMaxTime = 5.484 ms (IEEE 802.11-2016 Table 19-25, HT PHY characteristics). The input frames are kept, so the
output pcap feeds every existing tool (net_event, tools/real_session, replay). Only frames of the given devices are
used; the input should already be MAC-filtered at capture (tools/linux_monitor_capture.sh).
"""
from __future__ import annotations

import argparse
import struct
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional

from capture.dot11 import LINKTYPE_IEEE802_11_RADIOTAP, build_dot11_header, build_radiotap, mac_str, parse_radiotap
from capture.macfilter import normalize_mac
from capture.pcap_io import iter_raw, write_pcap

SN_MOD = 4096            # IEEE 802.11-2016 9.2.4.4.2: 12-bit sequence number
HALF = SN_MOD // 2       # comparison range of sequence numbers (IEEE 802.11-2016 10.3.2.11.1: modulo 4096, half space)
PPDU_MAX_S = 5.484e-3    # source: IEEE 802.11-2016 Table 19-25 aPPDUMaxTime (HT PHY)
_RT = build_radiotap(None, None, None)


@dataclass
class MpduProgress:
    ts: float            # time of the BlockAck that revealed the MPDUs
    t_from: float        # start of the interval the MPDUs are spread over
    device: str
    peer: str            # the other end (AP)
    direction: str       # "up": device -> peer, "down": peer -> device
    tid: int
    sns: list[int]


def parse_ba(mac: bytes) -> Optional[tuple[str, str, int, int, int]]:
    """(RA, TA, TID, SSN, bitmap) of a compressed single-TID BlockAck (IEEE 802.11-2016 9.3.1.9); None otherwise."""
    if len(mac) < 28 or (mac[0] >> 2) & 3 != 1 or (mac[0] >> 4) & 0xF != 9:
        return None
    ctl, ssc = struct.unpack_from("<HH", mac, 16)
    if not ctl & 0x4 or ctl & 0x2:          # bit 2 Compressed Bitmap, bit 1 Multi-TID
        return None
    return mac_str(mac[4:10]), mac_str(mac[10:16]), ctl >> 12, ssc >> 4, struct.unpack_from("<Q", mac, 20)[0]


class BaReconstructor:
    """Feed MAC frames (radiotap stripped) in capture order; returns the MPDUs each BlockAck reveals."""

    def __init__(self, devices: Iterable[str]):
        self.devices = {normalize_mac(d) for d in devices}
        self.state: dict[tuple, tuple[int, float]] = {}      # (device, peer, direction, tid) -> (highest SN, ts)

    def feed(self, ts: float, mac: bytes) -> Optional[MpduProgress]:
        ba = parse_ba(mac)
        if ba is None:
            return None
        ra, ta, tid, ssn, bitmap = ba
        if ra in self.devices:
            dev, peer, direction = ra, ta, "up"
        elif ta in self.devices:
            dev, peer, direction = ta, ra, "down"
        else:
            return None
        high = (ssn + bitmap.bit_length() - 1) % SN_MOD      # empty bitmap -> SSN - 1
        key = (dev, peer, direction, tid)
        prev = self.state.get(key)
        if prev is None:
            self.state[key] = (high, ts)
            return None
        delta = (high - prev[0]) % SN_MOD
        if delta == 0 or delta >= HALF:                      # same scoreboard again, or an older BlockAck
            return None
        self.state[key] = (high, ts)
        return MpduProgress(ts, max(prev[1], ts - PPDU_MAX_S), dev, peer, direction, tid,
                            [(prev[0] + 1 + i) % SN_MOD for i in range(delta)])


def pseudo_frames(p: MpduProgress) -> list[tuple[float, bytes]]:
    up = p.direction == "up"
    a1, a2 = (p.peer, p.device) if up else (p.device, p.peer)
    n = len(p.sns)
    return [(p.t_from + (p.ts - p.t_from) * (i + 1) / n,
             _RT + build_dot11_header(2, 8, a1, a2, p.peer, to_ds=up, from_ds=not up, protected=True, seq=sn))
            for i, sn in enumerate(p.sns)]


def _decoded_qos_sn(mac: bytes) -> Optional[tuple[str, str, int]]:
    """(TA, RA, SN) of a QoS data frame the sniffer did decode (e.g. legacy-rate), to avoid counting it twice."""
    if len(mac) < 26 or (mac[0] >> 2) & 3 != 2 or not (mac[0] >> 4) & 0x8:
        return None
    return mac_str(mac[10:16]), mac_str(mac[4:10]), struct.unpack_from("<H", mac, 22)[0] >> 4


def reconstruct(frames: Iterable[tuple[float, bytes]], devices: Iterable[str]
                ) -> tuple[list[tuple[float, bytes]], Counter]:
    """frames: (ts, radiotap + MAC bytes). Returns input frames + pseudo data frames sorted by time, and counts."""
    rec = BaReconstructor(devices)
    out, pseudo, decoded = [], [], set()
    for ts, data in frames:
        out.append((ts, data))
        rt_len, _ = parse_radiotap(data)
        mac = data[rt_len:]
        d = _decoded_qos_sn(mac)
        if d is not None:
            decoded.add(d)
        p = rec.feed(ts, mac)
        if p is not None:
            pseudo.append(p)
    counts: Counter = Counter()
    for p in pseudo:
        ta, ra = (p.device, p.peer) if p.direction == "up" else (p.peer, p.device)
        keep = [(t, f) for (t, f), sn in zip(pseudo_frames(p), p.sns) if (ta, ra, sn) not in decoded]
        counts[(p.device, p.direction)] += len(keep)
        out += keep
    out.sort(key=lambda x: x[0])
    return out, counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", action="append", required=True)
    a = ap.parse_args()
    raw = list(iter_raw(open(a.inp, "rb")))
    if any(lt != LINKTYPE_IEEE802_11_RADIOTAP for _ts, _d, _o, lt in raw):
        raise SystemExit("need a radiotap capture (linktype 127)")
    frames = [(ts, data) for ts, data, _orig, _lt in raw]
    out, counts = reconstruct(frames, a.device)
    write_pcap(a.out, out)
    for (dev, d), n in sorted(counts.items()):
        print(f"{dev} {d}: {n} MPDUs reconstructed")
    print(f"{len(frames)} input frames -> {len(out)} frames in {a.out}")


if __name__ == "__main__":
    main()
