"""Data types shared by all modules.

All timestamps are float seconds on ONE clock: the capture host's CLOCK_REALTIME
(Unix epoch). libpcap stamps packets with that clock and the video recorder stamps
frames with time.time() on the same host, so live and replay runs line up without
conversion (see docs/time_sync.md).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class PacketRecord:
    """One captured 802.11 frame (payload is never kept, only header fields)."""

    ts: float                 # capture timestamp [s, epoch]
    frame_len: int            # 802.11 MPDU length in bytes, FCS excluded
    ftype: int                # 0 mgmt, 1 ctrl, 2 data
    subtype: int
    addr1: Optional[str]      # receiver address (RA)
    addr2: Optional[str]      # transmitter address (TA); None for ACK/CTS
    to_ds: bool
    from_ds: bool
    retry: bool
    protected: bool
    rssi_dbm: Optional[int] = None
    channel_mhz: Optional[int] = None

    @property
    def is_data(self) -> bool:
        return self.ftype == 2


@dataclass
class NetEvent:
    """A traffic interval that a network detector flagged as image-upload-like."""

    t_start: float
    t_end: float
    device: str               # transmitter MAC
    score: float
    detector: str
    features: dict = field(default_factory=dict)


@dataclass
class Person:
    """One detected person in one frame. keypoints: (K, 3) = x, y, confidence in pixels."""

    bbox: np.ndarray          # (4,) x1, y1, x2, y2
    score: float
    keypoints: np.ndarray     # (K, 3)
    seat: Optional[str] = None


@dataclass
class PoseFrame:
    ts: float
    frame_idx: int
    persons: list[Person]


@dataclass
class BehaviorEvent:
    t_start: float
    t_end: float
    seat: str
    label: str
    score: float


@dataclass
class Alert:
    ts: float                 # time the alert was raised
    seat: str
    behavior: BehaviorEvent
    net: NetEvent

    def to_json(self) -> str:
        return json.dumps({
            "ts": self.ts,
            "seat": self.seat,
            "behavior": asdict(self.behavior),
            "net": asdict(self.net),
        }, default=float)
