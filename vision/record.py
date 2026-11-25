"""Record camera video + per-frame timestamps (the replay input).

    python -m vision.record --out data/raw/act_01.mp4 --duration 300

Writes <out> (mp4v) and <out stem>.frames.csv with columns frame_idx, ts (host CLOCK_REALTIME,
pipeline delay already subtracted). Run it on the same host as the sniffer so both inputs
share one clock (docs/time_sync.md).
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import cv2

from common.config import load_config
from vision.camera import LiveCamera


def camera_from_config(cfg: dict) -> LiveCamera:
    c = cfg["vision"]["camera"]
    delay = c.get("pipeline_delay_s", 0.0)
    if isinstance(delay, str):          # TBD -> record raw grab time; .meta.json says nothing was subtracted
        delay = 0.0
    return LiveCamera(c["source"], c.get("width"), c.get("height"), c.get("fps"), delay)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, required=True, help="seconds")
    ap.add_argument("--fourcc", default="mp4v")
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    cam = camera_from_config(cfg)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    first = cam.read()
    if first is None:
        raise SystemExit("camera returned no frame")
    h, w = first[2].shape[:2]
    # container fps is only a playback hint; true timing is in the CSV
    fps_hint = cam.cap.get(cv2.CAP_PROP_FPS) or 30.0
    vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*a.fourcc), fps_hint, (w, h))
    n = 0
    t0 = time.time()
    with open(out.with_suffix(".frames.csv"), "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["frame_idx", "ts"])
        fr = first
        while fr is not None and time.time() - t0 < a.duration:
            _, ts, img = fr
            vw.write(img)
            wr.writerow([n, f"{ts:.6f}"])
            n += 1
            if a.preview:
                cv2.imshow("record", img)
                if cv2.waitKey(1) == 27:
                    break
            fr = cam.read()
    vw.release()
    cam.close()
    raw_delay = cfg["vision"]["camera"].get("pipeline_delay_s")
    out.with_suffix(".meta.json").write_text(json.dumps({
        "pipeline_delay_s_subtracted": raw_delay if not isinstance(raw_delay, str) else None,
        "source": cfg["vision"]["camera"]["source"], "width": w, "height": h, "frames": n,
        "clock": "CLOCK_REALTIME (time.time) on the recording host"}, indent=1))
    print(f"{n} frames, {n / (time.time() - t0):.2f} fps delivered -> {out}")


if __name__ == "__main__":
    main()
