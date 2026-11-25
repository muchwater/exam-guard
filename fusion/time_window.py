"""Time-window fusion: alert only when a network event and a seat's abnormal behavior coincide.

Network events say WHEN an image-upload-like transmission happened (one sniffer cannot
tell where); behavior events say WHO (seat). A (behavior, net) pair raises one alert when

    net.t_start <= behavior.t_end + post_s   and   net.t_end >= behavior.t_start - pre_s

post_s: allowed delay from the end of the photographing motion to the upload start
        (capture -> encode -> connect); pre_s: allowed lead, covers clock error between
        the camera and sniffer timestamps. Both are measured (configs: fusion.*).
Each pair alerts at most once; several seats matching the same upload all alert.
"""
from __future__ import annotations

from common.config import require
from common.events import Alert, BehaviorEvent, NetEvent

_MAX_KEEP = 10_000   # memory cap on stored events (a bound, not a detection parameter)


class TimeWindowFusion:
    def __init__(self, pre_s: float, post_s: float, behavior_labels: set[str] | None = None):
        self.pre_s, self.post_s = pre_s, post_s
        self.behavior_labels = behavior_labels    # None = every behavior label can fuse
        self.net: list[tuple[int, NetEvent]] = []
        self.beh: list[tuple[int, BehaviorEvent]] = []
        self._seq = 0
        self._fired: set[tuple[int, int]] = set()

    @classmethod
    def from_config(cls, cfg: dict) -> "TimeWindowFusion":
        pre, post = require(cfg, "fusion.pre_s", "fusion.post_s")
        labels = cfg["fusion"].get("behavior_labels")
        return cls(float(pre), float(post), set(labels) if labels else None)

    def _match(self, b: BehaviorEvent, n: NetEvent) -> bool:
        if self.behavior_labels is not None and b.label not in self.behavior_labels:
            return False
        return n.t_start <= b.t_end + self.post_s and n.t_end >= b.t_start - self.pre_s

    def _pair(self, bi: int, b: BehaviorEvent, ni: int, n: NetEvent, now: float) -> list[Alert]:
        key = (bi, ni)
        if key in self._fired or not self._match(b, n):
            return []
        self._fired.add(key)
        return [Alert(ts=now, seat=b.seat, behavior=b, net=n)]

    def add_net(self, n: NetEvent, now: float | None = None) -> list[Alert]:
        self._seq += 1
        self.net = (self.net + [(self._seq, n)])[-_MAX_KEEP:]
        now = n.t_end if now is None else now
        return [a for bi, b in self.beh for a in self._pair(bi, b, self._seq, n, now)]

    def add_behavior(self, b: BehaviorEvent, now: float | None = None) -> list[Alert]:
        self._seq += 1
        self.beh = (self.beh + [(self._seq, b)])[-_MAX_KEEP:]
        now = b.t_end if now is None else now
        return [a for ni, n in self.net for a in self._pair(self._seq, b, ni, n, now)]
