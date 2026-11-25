"""Traffic features from one device's frames.

inter-arrival time (IAT): time between two consecutive frames of the same flow.
burst: a maximal run of uplink data frames whose consecutive IATs are all < burst_gap_s.

Two views are provided:
  * BurstSegmenter     - event-driven bursts (used by the rule-based detector)
  * window_features()  - fixed sliding-window feature vectors (used by model detectors)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np

from common.events import PacketRecord


def is_uplink_data(pkt: PacketRecord, device: str) -> bool:
    """Data frame transmitted by `device` towards the AP (To DS = 1)."""
    return pkt.is_data and pkt.addr2 == device and pkt.to_ds and not pkt.from_ds


@dataclass
class Burst:
    device: str
    t_start: float
    t_end: float
    sizes: list[int] = field(default_factory=list)
    retries: int = 0

    @property
    def n_frames(self) -> int:
        return len(self.sizes)

    @property
    def total_bytes(self) -> int:
        return int(sum(self.sizes))

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start

    def large_ratio(self, large_frame_bytes: int) -> float:
        if not self.sizes:
            return 0.0
        return float(np.mean(np.asarray(self.sizes) >= large_frame_bytes))

    def summary(self, large_frame_bytes: Optional[int] = None) -> dict:
        d = {"n_frames": self.n_frames, "total_bytes": self.total_bytes, "duration_s": self.duration,
             "retries": self.retries}
        if large_frame_bytes is not None:
            d["large_ratio"] = self.large_ratio(large_frame_bytes)
        return d


class BurstSegmenter:
    """Groups one device's uplink data frames into bursts; a burst closes after a gap >= burst_gap_s."""

    def __init__(self, device: str, burst_gap_s: float, count_retries: bool = True):
        self.device, self.gap, self.count_retries = device, burst_gap_s, count_retries
        self.cur: Optional[Burst] = None

    def update(self, pkt: PacketRecord) -> list[Burst]:
        if not is_uplink_data(pkt, self.device) or (pkt.retry and not self.count_retries):
            return self.advance(pkt.ts)
        closed = self.advance(pkt.ts)
        if self.cur is None:
            self.cur = Burst(self.device, pkt.ts, pkt.ts)
        self.cur.t_end = pkt.ts
        self.cur.sizes.append(pkt.frame_len)
        self.cur.retries += int(pkt.retry)
        return closed

    def advance(self, now: float) -> list[Burst]:
        """Close the open burst if `now` is a full gap past its last frame."""
        if self.cur is not None and now - self.cur.t_end >= self.gap:
            b, self.cur = self.cur, None
            return [b]
        return []

    def flush(self) -> list[Burst]:
        b, self.cur = self.cur, None
        return [b] if b else []


def segment_bursts(pkts: Iterable[PacketRecord], device: str, burst_gap_s: float) -> list[Burst]:
    seg = BurstSegmenter(device, burst_gap_s)
    out: list[Burst] = []
    for p in pkts:
        out += seg.update(p)
    return out + seg.flush()


WINDOW_FEATURES = [
    "up_frames", "up_bytes", "up_large_ratio", "up_size_mean", "up_size_std",
    "up_iat_median", "up_iat_p90", "down_frames", "down_bytes", "retry_ratio",
]


def window_features(pkts: list[PacketRecord], device: str, t0: float, t1: float,
                    large_frame_bytes: int) -> np.ndarray:
    """Feature vector (order = WINDOW_FEATURES) for frames of `device` with t0 <= ts < t1."""
    up = [p for p in pkts if t0 <= p.ts < t1 and is_uplink_data(p, device)]
    down = [p for p in pkts if t0 <= p.ts < t1 and p.is_data and p.addr1 == device and p.from_ds]
    sizes = np.asarray([p.frame_len for p in up], float)
    ts = np.asarray([p.ts for p in up], float)
    iat = np.diff(ts) if ts.size > 1 else np.zeros(0)
    return np.array([
        sizes.size,
        sizes.sum(),
        float(np.mean(sizes >= large_frame_bytes)) if sizes.size else 0.0,
        sizes.mean() if sizes.size else 0.0,
        sizes.std() if sizes.size else 0.0,
        np.median(iat) if iat.size else t1 - t0,
        np.percentile(iat, 90) if iat.size else t1 - t0,
        len(down),
        float(sum(p.frame_len for p in down)),
        float(np.mean([p.retry for p in up])) if up else 0.0,
    ], dtype=np.float32)


def multi_window_features(pkts: list[PacketRecord], device: str, t_end: float, windows_s: list[float],
                          large_frame_bytes: int) -> np.ndarray:
    """Lumos-style multi-timescale features: window_features over [t_end - w, t_end) for each w, concatenated.

    Lumos (Sharma et al., USENIX Security 2022, Algorithm 1) aggregates per-packet attributes over
    windows of several lengths at the same time t; here the windows are causal (they end at t_end).
    """
    return np.concatenate([window_features(pkts, device, t_end - w, t_end, large_frame_bytes) for w in windows_s])


def feature_names(windows_s: list[float]) -> list[str]:
    return [f"{n}@{w:g}s" for w in windows_s for n in WINDOW_FEATURES]


def sliding_windows(pkts: list[PacketRecord], device: str, window_s: float, step_s: float,
                    large_frame_bytes: int, t_begin: Optional[float] = None,
                    t_end: Optional[float] = None) -> tuple[np.ndarray, np.ndarray]:
    """Return (window start times (N,), features (N, F)) over [t_begin, t_end)."""
    if not pkts:
        return np.zeros(0), np.zeros((0, len(WINDOW_FEATURES)), np.float32)
    pkts = sorted(pkts, key=lambda p: p.ts)
    tb = pkts[0].ts if t_begin is None else t_begin
    te = pkts[-1].ts if t_end is None else t_end
    starts = np.arange(tb, max(tb, te - window_s) + 1e-9, step_s)
    ts = np.asarray([p.ts for p in pkts])
    lo = np.searchsorted(ts, starts, "left")
    hi = np.searchsorted(ts, starts + window_s, "left")
    feats = [window_features(pkts[i:j], device, s, s + window_s, large_frame_bytes) for s, i, j in zip(starts, lo, hi)]
    return starts, (np.stack(feats) if feats else np.zeros((0, len(WINDOW_FEATURES)), np.float32))
