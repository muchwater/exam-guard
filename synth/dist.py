"""Sampling from measured distributions (net_event.measure output format).

QuantileDist: inverse-CDF sampling by linear interpolation between measured quantiles
(min, p01 ... p99, max). No parametric family is assumed.
"""
from __future__ import annotations

import numpy as np


class QuantileDist:
    def __init__(self, q: dict):
        pts = [(0.0, q["min"])]
        for k, v in q.items():
            if k.startswith("p") and k[1:].isdigit():
                pts.append((int(k[1:]) / 100.0, v))
        pts.append((1.0, q["max"]))
        pts.sort()
        self.u = np.array([p for p, _ in pts])
        self.x = np.maximum.accumulate(np.array([v for _, v in pts], float))

    def sample(self, rng: np.random.Generator, n: int | None = None):
        u = rng.random(n)
        return np.interp(u, self.u, self.x)


class HistDist:
    """Categorical distribution from a {value: count} histogram."""

    def __init__(self, hist: dict):
        self.v = np.array([float(k) for k in hist])
        c = np.array(list(hist.values()), float)
        self.p = c / c.sum()

    def sample(self, rng: np.random.Generator, n: int | None = None):
        return rng.choice(self.v, size=n, p=self.p)
