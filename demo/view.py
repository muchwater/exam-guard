"""Demo view: video with skeletons/seats, network timeline, behavior bars, alert log.

Layout (one image):  [ video + overlay | alert log ]
                     [ network timeline (uplink bytes per bin, net events) + behavior bars ]
Shared by replay (replay.run --render/--show) and the live demo (demo.live).
"""
from __future__ import annotations

import collections
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from common.events import Alert

LOG_W = 360          # pixels, layout only
STRIP_H = 160
BINS = 120           # timeline bars across the strip, layout only


class DemoView:
    def __init__(self, cfg: dict, out_path: Optional[Path] = None, show: bool = False):
        self.span = float(cfg["demo"]["timeline_s"])
        self.hold = float(cfg["demo"]["alert_hold_s"])
        self.kpt_conf = float(cfg["vision"]["pose"]["kpt_conf"])
        self.out_path, self.show = out_path, show
        self.writer = None
        self.up: collections.deque = collections.deque()      # (ts, bytes) uplink data frames
        self.alerts: list[Alert] = []
        self.log: collections.deque = collections.deque(maxlen=18)

    def on_packet(self, pkt) -> None:
        if pkt.is_data and pkt.to_ds and not pkt.from_ds:
            self.up.append((pkt.ts, pkt.frame_len))

    def on_alert(self, a: Alert) -> None:
        self.alerts.append(a)
        import time as _t
        self.log.appendleft(f"{_t.strftime('%H:%M:%S', _t.localtime(a.ts))} seat {a.seat}: {a.behavior.label}"
                            f" + upload {a.net.features.get('total_bytes', '?')}B")

    def _timeline(self, now: float, width: int, pipe) -> np.ndarray:
        img = np.full((STRIP_H, width, 3), 30, np.uint8)
        t0 = now - self.span
        while self.up and self.up[0][0] < t0:
            self.up.popleft()
        x = lambda t: int((t - t0) / self.span * (width - 1))  # noqa: E731
        bins = np.zeros(BINS)
        for ts, b in self.up:
            bins[min(BINS - 1, int((ts - t0) / self.span * BINS))] += b
        top = 20
        h_net = 70
        if bins.max() > 0:
            bw = width / BINS
            for i, v in enumerate(bins):
                hh = int(v / bins.max() * (h_net - 4))
                cv2.rectangle(img, (int(i * bw), top + h_net - hh), (int((i + 1) * bw) - 1, top + h_net), (200, 160, 60), -1)
        for e in pipe.net_events:
            if e.t_end >= t0:
                cv2.rectangle(img, (max(0, x(e.t_start)), top), (x(e.t_end) + 1, top + h_net), (0, 200, 255), 2)
        cv2.putText(img, f"uplink bytes / {self.span / BINS:.2f}s  (boxes: network events)", (6, 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1)
        seats = sorted({e.seat for e in pipe.beh_events} | set(getattr(pipe.vision.action.seg, "runs", {}) if pipe.vision else []))
        y = top + h_net + 8
        rows = max(1, len(seats))
        rh = max(8, (STRIP_H - y - 4) // rows)
        for i, s in enumerate(seats):
            yy = y + i * rh
            cv2.putText(img, s, (2, yy + rh - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
            for e in pipe.beh_events:
                if e.seat == s and e.t_end >= t0:
                    cv2.rectangle(img, (max(40, x(e.t_start)), yy), (x(e.t_end) + 1, yy + rh - 2), (80, 80, 255), -1)
        return img

    def on_frame(self, now: float, img: np.ndarray, pipe) -> None:
        from vision.pose import draw
        frame = img.copy()
        hot = {a.seat for a in self.alerts if now - a.ts <= self.hold}
        if pipe.vision is not None:
            pipe.vision.seats.draw(frame, hot)
            if pipe.last_pose is not None:
                draw(frame, pipe.last_pose.persons, self.kpt_conf)
            for seat, label in pipe.vision.action.seg.active().items():
                cv2.putText(frame, f"{seat}: {label}", (10, 24 + 20 * list(pipe.vision.action.seg.runs).index(seat)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
        h, w = frame.shape[:2]
        panel = np.full((h, LOG_W, 3), 20, np.uint8)
        cv2.putText(panel, "ALERTS", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255) if hot else (200, 200, 200), 2)
        for i, line in enumerate(self.log):
            cv2.putText(panel, line, (10, 50 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (230, 230, 230), 1)
        top = np.hstack([frame, panel])
        out = np.vstack([top, self._timeline(now, top.shape[1], pipe)])
        if self.out_path is not None:
            if self.writer is None:
                self.writer = cv2.VideoWriter(str(self.out_path), cv2.VideoWriter_fourcc(*"mp4v"), 15.0,
                                              (out.shape[1], out.shape[0]))
            self.writer.write(out)
        if self.show:
            cv2.imshow("exam-guard", out)
            cv2.waitKey(1)

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
        if self.show:
            cv2.destroyAllWindows()
