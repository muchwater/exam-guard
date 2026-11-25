"""Seat evidence accumulated over a device's repeated uploads ("who", beyond a single coincidence).

One coincidence of an upload with a seat's head-down posture says little, because normal writing looks
the same. A cheater, however, is in the behaviour at (almost) every upload, while a normal seat matches
only as often as chance allows. For device d with uploads n_1..n_N and seat s:

  k_s       number of uploads that match a behaviour event of s (TimeWindowFusion window rule)
  p_{s,i}   chance that upload i matches s if its timing were independent of s's behaviour: the fraction
            of possible start times t in [T0, T1 - D_i] (T0..T1 = time s was observed, D_i = upload
            duration) for which [t, t + D_i] matches one of s's behaviour intervals [a, b], i.e. the length
            of the union of [a - pre_s - D_i, b + post_s] inside [T0, T1 - D_i], divided by T1 - D_i - T0
  p-value   P(K >= k_s), K ~ Poisson-binomial(p_{s,1..N}) (exact convolution)
  p_adj     min(1, p-value * S * N(N+1))  with S seats and N uploads so far. The ranking is re-tested after
            every upload, so the level is split over seats (Bonferroni) and over looks: look N spends
            alpha / (N(N+1)), and sum_N 1/(N(N+1)) = 1, so P(any normal seat is ever marked) <= alpha
            for the whole exam, however many uploads come.

There is no tunable constant: each seat's own observed behaviour sets its chance level, so a seat that is
head-down most of the time gains little from matching, and a seat whose behaviour appears only around
uploads gains a lot. Assumptions: a normal seat's behaviour is independent of the upload times, and
uploads are far enough apart that their chance matches are independent (one upload per question,
minutes apart, vs behaviour lasting seconds). Optional fusion.evidence.alpha (null = ranking only)
marks the top seat as identified when its p_adj < alpha.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np

from common.config import require
from common.events import BehaviorEvent, NetEvent


@dataclass
class SeatScore:
    seat: str
    matches: int
    uploads: int
    p_chance_mean: float
    p_value: float
    p_adj: float
    identified: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def union_length(intervals: list[tuple[float, float]], lo: float, hi: float) -> float:
    """Length of the union of intervals clipped to [lo, hi]."""
    segs = sorted((max(a, lo), min(b, hi)) for a, b in intervals if b > lo and a < hi)
    total, cur_a, cur_b = 0.0, None, None
    for a, b in segs:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total


def poisson_binomial_sf(p: list[float], k: int) -> float:
    """P(K >= k) for K = sum of independent Bernoulli(p_i)."""
    if k <= 0:
        return 1.0
    dist = np.zeros(len(p) + 1)
    dist[0] = 1.0
    for pi in p:
        dist[1:] = dist[1:] * (1 - pi) + dist[:-1] * pi
        dist[0] *= 1 - pi
    return float(min(1.0, dist[k:].sum()))


class SeatEvidence:
    def __init__(self, seats: list[str], pre_s: float, post_s: float,
                 behavior_labels: Optional[set[str]] = None, alpha: Optional[float] = None):
        self.seats = list(seats)
        self.pre_s, self.post_s = pre_s, post_s
        self.behavior_labels, self.alpha = behavior_labels, alpha
        self.net: dict[str, list[NetEvent]] = {}
        self.beh: dict[str, list[BehaviorEvent]] = {s: [] for s in self.seats}
        self.t0: Optional[float] = None
        self.t1: Optional[float] = None

    @classmethod
    def from_config(cls, cfg: dict) -> "SeatEvidence":
        seats, pre, post = require(cfg, "vision.seats", "fusion.pre_s", "fusion.post_s")
        labels = cfg["fusion"].get("behavior_labels")
        alpha = (cfg["fusion"].get("evidence") or {}).get("alpha")
        return cls(list(seats), float(pre), float(post), set(labels) if labels else None,
                   None if alpha is None else float(alpha))

    # --- inputs ---------------------------------------------------------------------------------
    def observe(self, ts: float) -> None:
        """Mark that every seat was observed (camera frame processed) at ts."""
        self.t0 = ts if self.t0 is None else min(self.t0, ts)
        self.t1 = ts if self.t1 is None else max(self.t1, ts)

    def add_net(self, n: NetEvent) -> None:
        self.net.setdefault(n.device, []).append(n)

    def add_behavior(self, b: BehaviorEvent) -> None:
        if self.behavior_labels is None or b.label in self.behavior_labels:
            self.beh.setdefault(b.seat, []).append(b)

    # --- scores ---------------------------------------------------------------------------------
    def devices(self) -> list[str]:
        return sorted(self.net)

    def table(self, device: str) -> tuple[np.ndarray, np.ndarray]:
        """(P, M): chance probability and match flag, shape (seats, uploads), uploads in time order.

        Chance levels use everything observed so far ([T0, T1]); uploads outside it are left out."""
        if self.t0 is None:
            return np.zeros((len(self.seats), 0)), np.zeros((len(self.seats), 0), bool)
        ups = [n for n in sorted(self.net.get(device, []), key=lambda n: n.t_start)
               if n.t_start >= self.t0 and n.t_end <= self.t1 and self.t1 - (n.t_end - n.t_start) > self.t0]
        P = np.zeros((len(self.seats), len(ups)))
        M = np.zeros((len(self.seats), len(ups)), bool)
        for i, s in enumerate(self.seats):
            bs = self.beh.get(s, [])
            a = np.fromiter((b.t_start for b in bs), float, len(bs))
            b_ = np.fromiter((b.t_end for b in bs), float, len(bs))
            cache: dict[float, float] = {}
            for j, n in enumerate(ups):
                d = n.t_end - n.t_start
                if d not in cache:
                    span = self.t1 - d - self.t0
                    cover = union_length(list(zip(a - self.pre_s - d, b_ + self.post_s)), self.t0, self.t1 - d)
                    cache[d] = min(1.0, cover / span)
                P[i, j] = cache[d]
                # same rule as fusion.time_window.matches, vectorized over this seat's events
                M[i, j] = bool(np.any((n.t_start <= b_ + self.post_s) & (n.t_end >= a - self.pre_s)))
        return P, M

    def ranking(self, device: str, upto: Optional[int] = None, _table=None) -> list[SeatScore]:
        """Seats sorted by p-value using the first `upto` uploads of `device` (all if None)."""
        P, M = self.table(device) if _table is None else _table
        P, M = P[:, :upto], M[:, :upto]
        n = P.shape[1]
        corr = max(1, len(self.seats)) * max(1, n * (n + 1))    # seats x looks (see module docstring)
        out = []
        for i, s in enumerate(self.seats):
            k = int(M[i].sum())
            pv = poisson_binomial_sf(P[i].tolist(), k)
            out.append(SeatScore(s, k, n, float(P[i].mean()) if n else 0.0, pv, min(1.0, pv * corr)))
        out.sort(key=lambda r: (r.p_value, -r.matches, r.seat))
        if out and self.alpha is not None and out[0].matches > 0 and out[0].p_adj < self.alpha:
            out[0].identified = True
        return out

    def history(self, device: str) -> list[list[dict]]:
        """Ranking after each upload (for reports): element i = ranking using uploads 1..i+1."""
        t = self.table(device)
        return [[r.to_dict() for r in self.ranking(device, i, t)] for i in range(1, t[0].shape[1] + 1)]
