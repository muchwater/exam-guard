"""Per-stage latency recording (milliseconds).

    lat = LatencyLog()
    with lat.stage("pose.inference"):
        ...
    lat.summary()            # {stage: {n, median_ms, p90_ms, mean_ms, max_ms}}
    lat.dump_csv(path)
"""
from __future__ import annotations

import csv
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import numpy as np


class LatencyLog:
    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = defaultdict(list)
        self.rows: list[tuple[int, str, float]] = []
        self._seq = 0

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, (time.perf_counter() - t0) * 1e3)

    def add(self, name: str, ms: float) -> None:
        self.samples[name].append(ms)
        self.rows.append((self._seq, name, ms))
        self._seq += 1

    def summary(self) -> dict[str, dict[str, float]]:
        out = {}
        for name, xs in self.samples.items():
            a = np.asarray(xs)
            out[name] = {
                "n": int(a.size),
                "median_ms": float(np.median(a)),
                "p90_ms": float(np.percentile(a, 90)),
                "mean_ms": float(a.mean()),
                "max_ms": float(a.max()),
            }
        return out

    def dump_csv(self, path: str | Path) -> None:
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["seq", "stage", "ms"])
            w.writerows(self.rows)

    def format_summary(self) -> str:
        lines = [f"{'stage':32s} {'n':>6s} {'median':>9s} {'p90':>9s} {'max':>9s}  (ms)"]
        for name, s in sorted(self.summary().items()):
            lines.append(f"{name:32s} {s['n']:6d} {s['median_ms']:9.2f} {s['p90_ms']:9.2f} {s['max_ms']:9.2f}")
        return "\n".join(lines)
