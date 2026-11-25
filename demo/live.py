"""Live demo on the Rubik Pi 3: sniffer + camera -> pipeline -> view. Every run is recorded for replay.

    sudo -E .venv/bin/python -m demo.live --run data/runs/live_001 [--no-show] [--no-net]

Records <run>/capture.pcap and <run>/video.mp4 (+ .frames.csv), so the same session can be replayed
with: python -m replay.run --pcap <run>/capture.pcap --video <run>/video.mp4 --out <run>/replay
If the sniffer fails (adapter missing, no monitor mode), the demo keeps running vision-only and says so.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import queue
from pathlib import Path

import cv2

from capture.sniffer import from_config as sniffer_from_config
from common.config import load_config
from demo.view import DemoView
from replay.run import Pipeline, _dump
from vision.record import camera_from_config

log = logging.getLogger("live")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="output directory for recordings and results")
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--no-show", action="store_true")
    ap.add_argument("--no-net", action="store_true", help="skip the sniffer (vision only)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(*a.config)
    run = Path(a.run)
    run.mkdir(parents=True, exist_ok=True)

    sniffer = None
    if not a.no_net:
        try:
            sniffer = sniffer_from_config(run / "capture.pcap", cfg).start()
        except Exception as e:  # noqa: BLE001
            log.error("sniffer not started (%s): continuing vision-only", e)

    cam = camera_from_config(cfg)
    pipe = Pipeline(cfg, with_vision=True)
    view = DemoView(cfg, None, show=not a.no_show)
    first = cam.read()
    h, w = first[2].shape[:2]
    vw = cv2.VideoWriter(str(run / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), cam.cap.get(cv2.CAP_PROP_FPS) or 30.0, (w, h))
    fcsv = open(run / "video.frames.csv", "w", newline="")
    wr = csv.writer(fcsv)
    wr.writerow(["frame_idx", "ts"])
    fr = first
    try:
        while fr is not None:
            idx, ts, img = fr
            vw.write(img)
            wr.writerow([idx, f"{ts:.6f}"])
            if sniffer is not None:
                while True:                          # packets that arrived before this frame
                    try:
                        pkt = sniffer.queue.get_nowait()
                    except queue.Empty:
                        break
                    view.on_packet(pkt)
                    for al in pipe.on_packet(pkt):
                        view.on_alert(al)
                        print(f"[ALERT] seat={al.seat} {al.behavior.label}")
                if sniffer.proc and sniffer.proc.poll() is not None and sniffer.error:
                    log.error("sniffer stopped: %s", sniffer.error)
                    sniffer = None
            for al in pipe.on_frame(idx, ts, img):
                view.on_alert(al)
                print(f"[ALERT] seat={al.seat} {al.behavior.label}")
            view.on_frame(ts, img, pipe)
            fr = cam.read()
    except KeyboardInterrupt:
        pass
    finally:
        if sniffer is not None:
            sniffer.stop()
        pipe.finish(fr[1] if fr else 0.0)
        vw.release()
        fcsv.close()
        cam.close()
        view.close()
        raw_delay = cfg["vision"]["camera"].get("pipeline_delay_s")
        (run / "video.meta.json").write_text(json.dumps({
            "pipeline_delay_s_subtracted": raw_delay if not isinstance(raw_delay, str) else None,
            "clock": "CLOCK_REALTIME (time.time) on the recording host"}))
        _dump(run / "alerts.jsonl", pipe.alerts)
        pipe.lat.dump_csv(run / "latency.csv")
        print(pipe.lat.format_summary())


if __name__ == "__main__":
    main()
