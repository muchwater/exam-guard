"""Hand-built fixtures. Numbers here are TEST FIXTURES ONLY (they exercise code paths);
they are not measurements and must never be copied into configs/."""
from __future__ import annotations

from capture.dot11 import build_dot11_header, build_radiotap

DEV = "02:00:00:00:00:01"
AP = "02:00:00:00:00:fe"
OTHER = "02:00:00:00:00:99"


def data_frame(size: int, ta: str = DEV, ra: str = AP, uplink: bool = True, fcs: bool = False,
               retry: bool = False) -> tuple[bytes, int]:
    rt = build_radiotap(-40, 2437, 0, fcs=fcs)
    hdr = build_dot11_header(2, 8, ra, ta, AP, to_ds=uplink, from_ds=not uplink, protected=True, retry=retry)
    body = bytes(max(0, size - len(hdr)))
    data = rt + hdr + body + (b"\0\0\0\0" if fcs else b"")
    return data, len(data)
