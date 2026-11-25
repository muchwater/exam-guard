"""Measure the camera pipeline delay (exposure -> grab timestamp) on the capture host.

A full-screen marker on the host's display switches black -> white; the switch times are logged
with time.time(). The camera looks at the screen and the first bright frame after each switch is
found by mean brightness.

    python -m tools.sync_check --source 0 --blinks <N> --period <s> --out data/measured/camera_delay.json

delay = (frame grab ts of the first bright frame) - (logged switch-to-white time). The reported
median/p90 are the source for vision.camera.pipeline_delay_s; their spread bounds fusion.pre_s.
"""
from __future__ import annotations

import argparse
import json
import threading
import time

import cv2
import numpy as np

from vision.camera import LiveCamera


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0")
    ap.add_argument("--blinks", type=int, required=True, help="number of dark->bright switches to measure")
    ap.add_argument("--period", type=float, required=True, help="seconds per half cycle; must exceed several frame intervals")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cam = LiveCamera(a.source)
    switches: list[float] = []
    frames: list[tuple[float, float]] = []
    stop = threading.Event()

    def grab():
        while not stop.is_set():
            r = cam.read()
            if r:
                frames.append((r[1], float(cv2.cvtColor(r[2], cv2.COLOR_BGR2GRAY).mean())))

    th = threading.Thread(target=grab, daemon=True)
    th.start()
    cv2.namedWindow("marker", cv2.WINDOW_NORMAL)
    cv2.setWindowProperty("marker", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    black, white = np.zeros((480, 640, 3), np.uint8), np.full((480, 640, 3), 255, np.uint8)
    for _ in range(a.blinks):
        cv2.imshow("marker", black)
        cv2.waitKey(int(a.period * 1000))
        cv2.imshow("marker", white)
        cv2.waitKey(1)
        switches.append(time.time())
        cv2.waitKey(int(a.period * 1000))
    stop.set()
    th.join()
    cam.close()
    ts = np.array([f[0] for f in frames])
    br = np.array([f[1] for f in frames])
    thr = (br.min() + br.max()) / 2           # midpoint between observed dark and bright levels
    delays = []
    for s in switches:
        after = np.where((ts >= s) & (br > thr))[0]
        if after.size:
            delays.append(float(ts[after[0]] - s))
    res = {"n": len(delays), "median_s": float(np.median(delays)), "p10_s": float(np.percentile(delays, 10)),
           "p90_s": float(np.percentile(delays, 90)), "delays_s": delays,
           "note": "includes display latency of the marker screen; see docs/time_sync.md"}
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print({k: v for k, v in res.items() if k != "delays_s"})


if __name__ == "__main__":
    main()
