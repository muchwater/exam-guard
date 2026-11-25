"""Network event detectors behind one interface.

    det = build_detector(cfg)           # "rule" or "window_model" (configs: net_event.detector)
    for pkt in packets:
        for ev in det.update(pkt): ...  # NetEvent(s) whose decision became final
    det.advance(now)                    # let time pass without packets (live mode)
    det.flush()                         # end of stream

Detectors only use packet timestamps, never the wall clock, so a replay gives the
same events as the live run that recorded it.
"""
from __future__ import annotations

import argparse
import collections
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np

from common.config import load_config, require
from common.events import NetEvent, PacketRecord
from net_event.features import Burst, BurstSegmenter, feature_names, multi_window_features


class NetDetector(ABC):
    name = "base"

    @abstractmethod
    def update(self, pkt: PacketRecord) -> list[NetEvent]: ...

    @abstractmethod
    def advance(self, now: float) -> list[NetEvent]: ...

    @abstractmethod
    def flush(self) -> list[NetEvent]: ...

    def run(self, pkts) -> list[NetEvent]:
        out: list[NetEvent] = []
        for p in pkts:
            out += self.update(p)
        return out + self.flush()


class RuleDetector(NetDetector):
    """Upload burst = uplink burst with enough bytes and enough near-MTU frames."""

    name = "rule"

    def __init__(self, burst_gap_s: float, min_burst_bytes: int, large_frame_bytes: int,
                 min_large_ratio: float, count_retries: bool = True):
        self.burst_gap_s, self.min_burst_bytes = burst_gap_s, min_burst_bytes
        self.large_frame_bytes, self.min_large_ratio = large_frame_bytes, min_large_ratio
        self.count_retries = count_retries
        self.seg: dict[str, BurstSegmenter] = {}

    @classmethod
    def from_config(cls, cfg: dict) -> "RuleDetector":
        r = "net_event.rule."
        gap, nbytes, large, ratio = require(cfg, r + "burst_gap_s", r + "min_burst_bytes",
                                            r + "large_frame_bytes", r + "min_large_ratio")
        return cls(float(gap), int(nbytes), int(large), float(ratio),
                   bool(cfg["net_event"]["rule"].get("count_retries", True)))

    def _judge(self, bursts: list[Burst]) -> list[NetEvent]:
        out = []
        for b in bursts:
            ratio = b.large_ratio(self.large_frame_bytes)
            if b.total_bytes >= self.min_burst_bytes and ratio >= self.min_large_ratio:
                out.append(NetEvent(b.t_start, b.t_end, b.device, score=1.0, detector=self.name,
                                    features=b.summary(self.large_frame_bytes)))
        return out

    def update(self, pkt: PacketRecord) -> list[NetEvent]:
        closed: list[Burst] = []
        for s in self.seg.values():
            closed += s.advance(pkt.ts)
        if pkt.is_data and pkt.to_ds and not pkt.from_ds and pkt.addr2:
            s = self.seg.get(pkt.addr2)
            if s is None:
                s = self.seg[pkt.addr2] = BurstSegmenter(pkt.addr2, self.burst_gap_s, self.count_retries)
            closed += s.update(pkt)
        return self._judge(closed)

    def advance(self, now: float) -> list[NetEvent]:
        return self._judge([b for s in self.seg.values() for b in s.advance(now)])

    def flush(self) -> list[NetEvent]:
        return self._judge([b for s in self.seg.values() for b in s.flush()])

    def open_bursts(self) -> list[Burst]:
        """Bursts still in progress (for live display)."""
        return [s.cur for s in self.seg.values() if s.cur is not None]


# ---------------------------------------------------------------- model-based detector

def _sigmoid(z: np.ndarray) -> np.ndarray:
    """Overflow-free logistic function."""
    z = np.asarray(z, np.float64)
    e = np.exp(-np.abs(z))
    return np.where(z >= 0, 1.0 / (1.0 + e), e / (1.0 + e))


class LogisticModel:
    """Standardized logistic regression; stored as .npz (mean, std, w, b, feature names)."""

    def __init__(self, mean, std, w, b, features=()):
        self.mean, self.std = np.asarray(mean, np.float64), np.asarray(std, np.float64)
        self.w, self.b, self.features = np.asarray(w, np.float64), float(b), list(features)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        z = ((np.asarray(X, np.float64) - self.mean) / self.std) @ self.w + self.b
        return _sigmoid(z)

    def save(self, path: str | Path) -> None:
        np.savez(path, mean=self.mean, std=self.std, w=self.w, b=self.b, features=np.array(self.features))

    @classmethod
    def load(cls, path: str | Path) -> "LogisticModel":
        d = np.load(path)
        return cls(d["mean"], d["std"], d["w"], d["b"], [str(x) for x in d["features"]])

    @classmethod
    def fit(cls, X: np.ndarray, y: np.ndarray, l2: float, features=(), max_iter: int = 100) -> "LogisticModel":
        """L2-regularized logistic regression by Newton/IRLS (no learning rate to choose).

        max_iter only bounds the solver loop; it stops earlier once the Newton step is ~0.
        """
        mean, std = X.mean(0), X.std(0)
        std[std == 0] = 1.0
        Z = np.hstack([(X - mean) / std, np.ones((len(X), 1))])
        theta = np.zeros(Z.shape[1])
        reg = np.full(Z.shape[1], l2)
        reg[-1] = 0.0                                  # bias is not regularized
        for _ in range(max_iter):
            p = _sigmoid(Z @ theta)
            grad = Z.T @ (p - y) + reg * theta
            H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(reg) + 1e-9 * np.eye(Z.shape[1])
            step = np.linalg.solve(H, grad)
            theta -= step
            if np.abs(step).max() < 1e-8:
                break
        return cls(mean, std, theta[:-1], theta[-1], features)


class OnnxModel:
    """Any small classifier exported to ONNX: input (N, F) float32 -> probability of 'upload'."""

    def __init__(self, path: str | Path):
        import onnxruntime as ort
        self.sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        y = self.sess.run(None, {self.inp: np.asarray(X, np.float32)})[0]
        return np.asarray(y).reshape(len(X), -1)[:, -1]


class WindowModelDetector(NetDetector):
    """Every step_s, scores multi-timescale features of each device with a model; merges positive steps.

    An event spans from the first to the last positive evaluation time (plus the longest window before it).
    """

    name = "window_model"

    def __init__(self, model, windows_s: list[float], step_s: float, large_frame_bytes: int, threshold: float):
        self.model, self.windows_s, self.step_s = model, list(windows_s), step_s
        self.window_s = max(self.windows_s)
        names = getattr(model, "features", None)
        if names and list(names) != feature_names(self.windows_s):
            raise ValueError("model was trained with different features/windows than configured")
        self.large, self.threshold = large_frame_bytes, threshold
        self.buf: dict[str, collections.deque] = {}
        self.next_start: dict[str, float] = {}
        self.open_ev: dict[str, Optional[NetEvent]] = {}

    @classmethod
    def from_config(cls, cfg: dict) -> "WindowModelDetector":
        m = "net_event.window_model."
        path, windows, step, thr = require(cfg, m + "path", m + "windows_s", m + "step_s", m + "threshold")
        large = require(cfg, "net_event.rule.large_frame_bytes")
        model = OnnxModel(path) if str(path).endswith(".onnx") else LogisticModel.load(path)
        return cls(model, [float(w) for w in windows], float(step), int(large), float(thr))

    def _windows_until(self, dev: str, now: float) -> list[NetEvent]:
        out = []
        buf = self.buf[dev]
        while self.next_start[dev] <= now:
            t1 = self.next_start[dev]                 # evaluation time = end of all windows
            while buf and buf[0].ts < t1 - self.window_s:
                buf.popleft()
            x = multi_window_features(list(buf), dev, t1, self.windows_s, self.large)
            p = float(self.model.predict_proba(x[None])[0])
            ev = self.open_ev.get(dev)
            if p >= self.threshold:
                if ev is None:
                    self.open_ev[dev] = NetEvent(t1 - min(self.windows_s), t1, dev, p, self.name,
                                                 dict(zip(feature_names(self.windows_s), x.tolist())))
                else:
                    ev.t_end, ev.score = t1, max(ev.score, p)
            elif ev is not None:
                out.append(ev)
                self.open_ev[dev] = None
            self.next_start[dev] = t1 + self.step_s
        return out

    def update(self, pkt: PacketRecord) -> list[NetEvent]:
        out = self.advance(pkt.ts)
        dev = pkt.addr2 if (pkt.is_data and pkt.to_ds and not pkt.from_ds) else None
        if dev is None:
            return out
        if dev not in self.buf:
            self.buf[dev] = collections.deque()
            self.next_start[dev] = pkt.ts + self.step_s
        self.buf[dev].append(pkt)
        return out

    def advance(self, now: float) -> list[NetEvent]:
        return [e for d in list(self.buf) for e in self._windows_until(d, now)]

    def flush(self) -> list[NetEvent]:
        out = []
        for d in self.buf:
            if self.buf[d]:
                out += self._windows_until(d, self.buf[d][-1].ts + self.step_s)
            if self.open_ev.get(d) is not None:
                out.append(self.open_ev[d])
                self.open_ev[d] = None
        return out


def build_detector(cfg: dict) -> NetDetector:
    kind = cfg["net_event"]["detector"]
    if kind == "rule":
        return RuleDetector.from_config(cfg)
    if kind == "window_model":
        return WindowModelDetector.from_config(cfg)
    raise ValueError(f"unknown net_event.detector: {kind}")


def main() -> None:
    from capture.macfilter import MacAllowlist
    from capture.pcap_io import read_pcap

    ap = argparse.ArgumentParser(description="Run the configured network detector on a pcap.")
    ap.add_argument("pcap")
    ap.add_argument("--config", action="append", default=[], help="override yaml (repeatable)")
    a = ap.parse_args()
    cfg = load_config(*a.config)
    pkts = read_pcap(a.pcap, MacAllowlist.from_config(cfg))
    for ev in build_detector(cfg).run(pkts):
        print(f"{ev.t_start:.3f}-{ev.t_end:.3f} {ev.device} score={ev.score:.2f} {ev.features}")


if __name__ == "__main__":
    main()
