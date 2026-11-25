"""Own-device MAC allowlist.

Privacy rule: only frames sent by or to team-owned devices are kept. The same
allowlist is applied twice: as a kernel BPF filter in the sniffer (frames of other
devices never reach user space or disk) and again in Python when reading pcaps.
"""
from __future__ import annotations

import re
from typing import Iterable

from common.config import is_tbd, load_config
from common.events import PacketRecord

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def normalize_mac(mac: str) -> str:
    m = mac.strip().lower().replace("-", ":")
    if not _MAC_RE.match(m):
        raise ValueError(f"invalid MAC address: {mac!r}")
    return m


class MacAllowlist:
    def __init__(self, macs: Iterable[str]):
        self.macs = frozenset(normalize_mac(m) for m in macs)
        if not self.macs:
            raise ValueError("MAC allowlist is empty: refusing to capture/read without an own-device filter")

    @classmethod
    def from_config(cls, cfg: dict | None = None) -> "MacAllowlist":
        cfg = cfg or load_config()
        macs = cfg["capture"]["allowed_macs"]
        if is_tbd(macs):
            raise ValueError("capture.allowed_macs is TBD: list the team-owned device MACs in configs/")
        return cls(macs)

    def match(self, pkt: PacketRecord) -> bool:
        return pkt.addr1 in self.macs or pkt.addr2 in self.macs

    def bpf(self) -> str:
        """tcpdump filter keeping frames whose RA or TA is an allowlisted MAC (pcap-filter(7): wlan addr1/addr2)."""
        return " or ".join(f"wlan addr1 {m} or wlan addr2 {m}" for m in sorted(self.macs))
