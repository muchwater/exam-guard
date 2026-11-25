"""Frame sources with capture timestamps on the host CLOCK_REALTIME.

Live:   LiveCamera(source)  - V4L2 index/path or a GStreamer pipeline string (cv2.CAP_GSTREAMER)
Replay: VideoFileSource(video, ts_csv) - a recording made by `python -m vision.record`

A frame's timestamp is time.time() right after the driver returned it, i.e. it is late
by the camera pipeline delay (exposure -> user space). That constant is measured with
tools/sync_check.py and stored as vision.camera.pipeline_delay_s (subtracted here).

    python -m vision.camera --list              # probe /dev/video* and print what opens
"""
from __future__ import annotations

import argparse
import csv
import glob
import time
from pathlib import Path
from typing import Iterator, Optional

import cv2
import numpy as np


def _open(source: str | int) -> cv2.VideoCapture:
    if isinstance(source, str) and "!" in source:
        return cv2.VideoCapture(source, cv2.CAP_GSTREAMER)
    if isinstance(source, str) and source.isdigit():
        source = int(source)
    return cv2.VideoCapture(source, cv2.CAP_V4L2) if not isinstance(source, str) or source.startswith("/dev/") \
        else cv2.VideoCapture(source)


class LiveCamera:
    def __init__(self, source: str | int, width: Optional[int] = None, height: Optional[int] = None,
                 fps: Optional[float] = None, pipeline_delay_s: float = 0.0):
        self.cap = _open(source)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open camera {source!r}")
        if width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if fps:
            self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.delay = pipeline_delay_s
        self.idx = 0

    def read(self) -> Optional[tuple[int, float, np.ndarray]]:
        ok, frame = self.cap.read()
        ts = time.time() - self.delay
        if not ok:
            return None
        self.idx += 1
        return self.idx - 1, ts, frame

    def __iter__(self) -> Iterator[tuple[int, float, np.ndarray]]:
        while (r := self.read()) is not None:
            yield r

    def close(self) -> None:
        self.cap.release()


class VideoFileSource:
    """Recorded video + per-frame timestamp CSV (frame_idx, ts)."""

    def __init__(self, video: str | Path, ts_csv: Optional[str | Path] = None, offset_s: float = 0.0):
        self.cap = cv2.VideoCapture(str(video))
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open {video}")
        ts_csv = ts_csv or Path(video).with_suffix(".frames.csv")
        with open(ts_csv) as f:
            self.ts = [float(r["ts"]) + offset_s for r in csv.DictReader(f)]
        self.idx = 0

    def read(self) -> Optional[tuple[int, float, np.ndarray]]:
        ok, frame = self.cap.read()
        if not ok or self.idx >= len(self.ts):
            return None
        self.idx += 1
        return self.idx - 1, self.ts[self.idx - 1], frame

    def __iter__(self):
        while (r := self.read()) is not None:
            yield r

    def close(self) -> None:
        self.cap.release()


def measure_fps(cam: LiveCamera, n: int) -> dict:
    """Measured delivery rate (frames/s) and inter-frame interval quantiles over n frames."""
    ts = [cam.read()[1] for _ in range(n)]
    d = np.diff(ts)
    return {"n": n, "fps": float((n - 1) / (ts[-1] - ts[0])), "dt_p50": float(np.median(d)),
            "dt_p90": float(np.percentile(d, 90)), "dt_max": float(d.max())}


def main() -> None:
    ap = argparse.ArgumentParser(description="Probe cameras / measure delivered frame rate.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--source", default="0")
    ap.add_argument("--measure-fps", type=int, metavar="N", help="read N frames and report the delivered rate")
    a = ap.parse_args()
    if a.list:
        for dev in sorted(glob.glob("/dev/video*")):
            cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
            ok, fr = cap.read() if cap.isOpened() else (False, None)
            print(f"{dev}: opened={cap.isOpened()} frame={'%dx%d' % (fr.shape[1], fr.shape[0]) if ok else '-'}")
            cap.release()
        return
    cam = LiveCamera(a.source)
    if a.measure_fps:
        print(measure_fps(cam, a.measure_fps))
    cam.close()


if __name__ == "__main__":
    main()
