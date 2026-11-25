"""Classic pcap (not pcapng) reading/writing, from files or byte streams.

The reader works on any binary stream, so the live sniffer can parse
`tcpdump -w -` output with the same code that replays recorded files.
Format: https://www.tcpdump.org/manpages/pcap-savefile.5.html
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, Optional

from capture.dot11 import LINKTYPE_IEEE802_11_RADIOTAP, parse_frame
from capture.macfilter import MacAllowlist
from common.events import PacketRecord

_MAGIC = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),   # microsecond
    b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9),   # nanosecond
}
_PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"


def _read_exact(f: BinaryIO, n: int) -> Optional[bytes]:
    buf = b""
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            return None if not buf else buf
        buf += chunk
    return buf


def iter_raw(f: BinaryIO) -> Iterator[tuple[float, bytes, int, int]]:
    """Yield (ts, captured bytes, orig_len, linktype) for every record of a pcap stream."""
    gh = _read_exact(f, 24)
    if gh is None or len(gh) < 24:
        return
    if gh[:4] == _PCAPNG_MAGIC:
        raise ValueError("pcapng is not supported; convert with `editcap -F pcap in.pcapng out.pcap`")
    if gh[:4] not in _MAGIC:
        raise ValueError("not a pcap file")
    endian, frac = _MAGIC[gh[:4]]
    linktype = struct.unpack(endian + "I", gh[20:24])[0] & 0x0FFFFFFF
    rec = struct.Struct(endian + "IIII")
    while True:
        rh = _read_exact(f, 16)
        if rh is None or len(rh) < 16:
            return
        sec, sub, incl, orig = rec.unpack(rh)
        data = _read_exact(f, incl)
        if data is None or len(data) < incl:
            return
        yield sec + sub * frac, data, orig, linktype


def iter_packets(f: BinaryIO, allow: Optional[MacAllowlist] = None) -> Iterator[PacketRecord]:
    """Decode a pcap stream into PacketRecords, dropping frames not involving allowlisted MACs."""
    for ts, data, orig, linktype in iter_raw(f):
        pkt = parse_frame(ts, data, orig, linktype)
        if pkt is None:
            continue
        if allow is not None and not allow.match(pkt):
            continue
        yield pkt


def read_pcap(path: str | Path, allow: Optional[MacAllowlist] = None) -> list[PacketRecord]:
    with open(path, "rb") as f:
        return list(iter_packets(f, allow))


class PcapWriter:
    """Writes microsecond-resolution little-endian pcap."""

    def __init__(self, f: BinaryIO, linktype: int = LINKTYPE_IEEE802_11_RADIOTAP, snaplen: int = 262144):
        self.f = f
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, snaplen, linktype))

    def write(self, ts: float, data: bytes, orig_len: Optional[int] = None) -> None:
        sec = int(ts)
        usec = int(round((ts - sec) * 1e6))
        if usec >= 1_000_000:
            sec, usec = sec + 1, usec - 1_000_000
        self.f.write(struct.pack("<IIII", sec, usec, len(data), orig_len if orig_len is not None else len(data)))
        self.f.write(data)


def write_pcap(path: str | Path, frames: Iterable[tuple[float, bytes]], linktype: int = LINKTYPE_IEEE802_11_RADIOTAP) -> None:
    with open(path, "wb") as f:
        w = PcapWriter(f, linktype)
        for ts, data in frames:
            w.write(ts, data)


def main() -> None:
    ap = argparse.ArgumentParser(description="Print decoded 802.11 header fields of a pcap (own-MAC filter applied).")
    ap.add_argument("pcap")
    ap.add_argument("--mac", action="append", default=[], help="allowlisted MAC (repeatable); default: config capture.allowed_macs")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()
    allow = MacAllowlist(a.mac) if a.mac else MacAllowlist.from_config()
    n = 0
    with open(a.pcap, "rb") as f:
        for pkt in iter_packets(f, allow):
            if n < a.limit:
                print(f"{pkt.ts:.6f} type={pkt.ftype}/{pkt.subtype:<2d} len={pkt.frame_len:5d} "
                      f"ta={pkt.addr2} ra={pkt.addr1} toDS={int(pkt.to_ds)} retry={int(pkt.retry)} rssi={pkt.rssi_dbm}")
            n += 1
    print(f"{n} frames matched the allowlist")


if __name__ == "__main__":
    main()
