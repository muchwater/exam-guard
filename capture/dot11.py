"""Minimal radiotap + 802.11 MAC header parser (header fields only, no payload).

radiotap: a variable-length metadata header the capture driver prepends to each
frame in monitor mode (signal strength, rate, channel, flags). Field layout and
alignment follow the radiotap specification (https://www.radiotap.org/fields/defined).
Only fields 0-5 of the first namespace are decoded; that is enough to reach the
dBm antenna signal (bit 5) and the Flags byte (bit 1, carries "FCS at end").
"""
from __future__ import annotations

import struct
from typing import Optional

from common.events import PacketRecord

# radiotap field bit -> (alignment, size) for the fields that precede/contain bit 5
# source: https://www.radiotap.org/fields/defined (TSFT, Flags, Rate, Channel, FHSS, Antenna signal)
_RT_FIELDS = {0: (8, 8), 1: (1, 1), 2: (1, 1), 3: (2, 4), 4: (1, 2), 5: (1, 1)}
RT_FLAG_FCS = 0x10           # Flags field: frame includes FCS (radiotap.org/fields/Flags)
FCS_LEN = 4                  # IEEE 802.11 FCS is a 32-bit CRC

LINKTYPE_IEEE802_11 = 105          # tcpdump.org/linktypes.html
LINKTYPE_IEEE802_11_RADIOTAP = 127  # tcpdump.org/linktypes.html


def mac_str(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)


def parse_radiotap(buf: bytes) -> tuple[int, dict]:
    """Return (radiotap header length, fields) where fields may hold flags, rate, channel_mhz, rssi_dbm."""
    if len(buf) < 8:
        raise ValueError("short radiotap header")
    version, _pad, it_len, present = struct.unpack_from("<BBHI", buf, 0)
    if version != 0:
        raise ValueError(f"unsupported radiotap version {version}")
    # skip extended present bitmaps (bit 31 set -> another 32-bit word follows)
    off = 8
    word = present
    while word & (1 << 31):
        if off + 4 > len(buf):
            raise ValueError("truncated radiotap present bitmap")
        (word,) = struct.unpack_from("<I", buf, off)
        off += 4
    fields: dict = {}
    for bit in range(6):
        if not present & (1 << bit):
            continue
        align, size = _RT_FIELDS[bit]
        off = (off + align - 1) & ~(align - 1)
        if off + size > it_len:
            break
        if bit == 1:
            fields["flags"] = buf[off]
        elif bit == 2:
            fields["rate_500kbps"] = buf[off]
        elif bit == 3:
            fields["channel_mhz"] = struct.unpack_from("<H", buf, off)[0]
        elif bit == 5:
            fields["rssi_dbm"] = struct.unpack_from("<b", buf, off)[0]
        off += size
    return it_len, fields


def parse_frame(ts: float, data: bytes, orig_len: int, linktype: int) -> Optional[PacketRecord]:
    """Decode one captured frame into a PacketRecord; None if it is not decodable."""
    rt_len, rt = 0, {}
    if linktype == LINKTYPE_IEEE802_11_RADIOTAP:
        try:
            rt_len, rt = parse_radiotap(data)
        except ValueError:
            return None
    elif linktype != LINKTYPE_IEEE802_11:
        raise ValueError(f"unsupported linktype {linktype} (need 105 or 127)")
    mac = data[rt_len:]
    if len(mac) < 10:            # FC(2) + Duration(2) + addr1(6) is the shortest frame (ACK/CTS)
        return None
    fc0, fc1 = mac[0], mac[1]
    ftype = (fc0 >> 2) & 0x3
    subtype = (fc0 >> 4) & 0xF
    addr1 = mac_str(mac[4:10])
    addr2 = mac_str(mac[10:16]) if len(mac) >= 16 and not (ftype == 1 and subtype in (12, 13)) else None
    frame_len = orig_len - rt_len - (FCS_LEN if rt.get("flags", 0) & RT_FLAG_FCS else 0)
    return PacketRecord(
        ts=ts,
        frame_len=frame_len,
        ftype=ftype,
        subtype=subtype,
        addr1=addr1,
        addr2=addr2,
        to_ds=bool(fc1 & 0x01),
        from_ds=bool(fc1 & 0x02),
        retry=bool(fc1 & 0x08),
        protected=bool(fc1 & 0x40),
        rssi_dbm=rt.get("rssi_dbm"),
        channel_mhz=rt.get("channel_mhz"),
    )


def build_radiotap(rssi_dbm: int, channel_mhz: int, rate_500kbps: int, fcs: bool = False) -> bytes:
    """Radiotap header with Flags, Rate, Channel, dBm antenna signal (used by synth/ and tests)."""
    present = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 5)
    flags = RT_FLAG_FCS if fcs else 0
    # Flags@8, Rate@9, Channel@10 (2-aligned), antenna signal@14 -> length 15
    body = struct.pack("<BBHHb", flags, rate_500kbps, channel_mhz, 0x0080, rssi_dbm)  # 0x0080: 2 GHz channel flag
    return struct.pack("<BBHI", 0, 0, 8 + len(body), present) + body


def build_dot11_header(ftype: int, subtype: int, addr1: str, addr2: str, addr3: str,
                       to_ds: bool, from_ds: bool, protected: bool, retry: bool = False, seq: int = 0) -> bytes:
    """802.11 MAC header for data/mgmt frames (QoS Control added for QoS data, subtype 8)."""
    fc0 = (subtype << 4) | (ftype << 2)
    fc1 = (0x01 if to_ds else 0) | (0x02 if from_ds else 0) | (0x08 if retry else 0) | (0x40 if protected else 0)
    hdr = struct.pack("<BBH", fc0, fc1, 0)
    for a in (addr1, addr2, addr3):
        hdr += bytes(int(x, 16) for x in a.split(":"))
    hdr += struct.pack("<H", (seq & 0xFFF) << 4)
    if ftype == 2 and subtype & 0x8:
        hdr += struct.pack("<H", 0)
    return hdr
