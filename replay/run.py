"""Offline mode: run the full pipeline on a recorded pcap + video (works without any live hardware).

    python -m replay.run --pcap data/raw/demo1.pcap --video data/raw/demo1.mp4 \
        --out data/runs/demo1 [--speed 1.0] [--render]

Time sync (docs/time_sync.md): pcap timestamps and the video's .frames.csv timestamps are both
CLOCK_REALTIME of the capture host. The two streams are merged in timestamp order, so every module
sees exactly the sequence it saw live. If the two were recorded on different hosts, pass the measured
clock offset with --video-offset (added to the video timestamps).

Outputs in --out: alerts.jsonl, net_events.jsonl, behavior_events.jsonl, seat_evidence.json (per-device seat
ranking over repeated uploads, fusion/seat_evidence.py), latency.csv (+ render.mp4).
"""
from __future__ import annotations

import argparse
import heapq
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Optional

from capture.macfilter import MacAllowlist
from capture.pcap_io import iter_packets
from common.config import load_config
from common.events import Alert, BehaviorEvent, NetEvent
from common.profiling import LatencyLog
from fusion.seat_evidence import SeatEvidence
from fusion.time_window import TimeWindowFusion
from net_event.detector import build_detector

log = logging.getLogger("replay")


def merged_stream(pcap: str, allow: MacAllowlist, video_source) -> Iterator[tuple[float, str, object]]:
    """Yield (ts, kind, item) for packets ("pkt") and frames ("frame") in global timestamp order.

    On equal timestamps the packet comes first (priority 0), so a frame sees every packet up to its time."""
    f = open(pcap, "rb")

    def pkts():
        for p in iter_packets(f, allow):
            yield (p.ts, 0, "pkt", p)

    def frames():
        if video_source is None:
            return
        for idx, ts, img in video_source:
            yield (ts, 1, "frame", (idx, ts, img))

    try:
        for ts, _prio, kind, item in heapq.merge(pkts(), frames(), key=lambda r: (r[0], r[1])):
            yield ts, kind, item
    finally:
        f.close()


class Pipeline:
    """Network + vision + fusion, driven by timestamped inputs (shared by replay and live demo)."""

    def __init__(self, cfg: dict, with_vision: bool = True, latency: Optional[LatencyLog] = None):
        self.lat = latency or LatencyLog()
        self.net = build_detector(cfg)
        self.fusion = TimeWindowFusion.from_config(cfg)
        self.vision = None
        self.evidence: Optional[SeatEvidence] = None
        if with_vision:
            from vision.pipeline import VisionPipeline
            self.vision = VisionPipeline(cfg, self.lat)
            self.evidence = SeatEvidence.from_config(cfg)
        self.net_events: list[NetEvent] = []
        self.beh_events: list[BehaviorEvent] = []
        self.alerts: list[Alert] = []
        self.last_pose = None

    def _on_net(self, evs: list[NetEvent], now: float) -> list[Alert]:
        out = []
        for e in evs:
            self.net_events.append(e)
            with self.lat.stage("fusion"):
                out += self.fusion.add_net(e, now)
                if self.evidence is not None:
                    self.evidence.add_net(e)
        return out

    def _on_beh(self, evs: list[BehaviorEvent], now: float) -> list[Alert]:
        out = []
        for e in evs:
            self.beh_events.append(e)
            with self.lat.stage("fusion"):
                out += self.fusion.add_behavior(e, now)
                if self.evidence is not None:
                    self.evidence.add_behavior(e)
        return out

    def on_packet(self, pkt) -> list[Alert]:
        with self.lat.stage("net.detector"):
            evs = self.net.update(pkt)
        a = self._on_net(evs, pkt.ts)
        self.alerts += a
        return a

    def on_frame(self, idx: int, ts: float, img) -> list[Alert]:
        with self.lat.stage("net.detector"):
            nevs = self.net.advance(ts)
        a = self._on_net(nevs, ts)
        if self.vision is not None:
            self.last_pose, bevs = self.vision.process(idx, ts, img)
            self.evidence.observe(ts)
            a += self._on_beh(bevs, ts)
        self.alerts += a
        return a

    def finish(self, now: float) -> list[Alert]:
        a = self._on_net(self.net.flush(), now)
        if self.vision is not None:
            a += self._on_beh(self.vision.flush(), now)
            self.vision.close()
        self.alerts += a
        return a


def _dump(path: Path, items) -> None:
    with open(path, "w") as f:
        for x in items:
            f.write((x.to_json() if isinstance(x, Alert) else json.dumps(asdict(x), default=float)) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pcap", required=True)
    ap.add_argument("--video", help="recorded video (with <stem>.frames.csv); omit for network-only replay")
    ap.add_argument("--video-offset", type=float, default=0.0, help="seconds added to video timestamps (cross-host)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", action="append", default=[])
    ap.add_argument("--speed", type=float, default=0.0, help="1.0 = real time, 0 = as fast as possible")
    ap.add_argument("--render", action="store_true", help="write render.mp4 with the demo view")
    ap.add_argument("--show", action="store_true", help="show the demo view in a window")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(*a.config)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    video = None
    if a.video:
        from vision.camera import VideoFileSource
        meta = Path(a.video).with_suffix(".meta.json")
        offset = a.video_offset
        if meta.exists() and json.loads(meta.read_text()).get("pipeline_delay_s_subtracted") is None:
            delay = cfg["vision"]["camera"].get("pipeline_delay_s")
            if not isinstance(delay, str):
                offset -= float(delay)
            else:
                log.warning("camera pipeline delay is TBD: video timestamps are raw grab times")
        video = VideoFileSource(a.video, offset_s=offset)

    pipe = Pipeline(cfg, with_vision=video is not None)
    view = None
    if a.render or a.show:
        from demo.view import DemoView
        view = DemoView(cfg, out / "render.mp4" if a.render else None, show=a.show)

    allow = MacAllowlist.from_config(cfg)
    wall0 = data0 = None
    last_ts = 0.0
    for ts, kind, item in merged_stream(a.pcap, allow, video):
        if a.speed > 0:
            wall0, data0 = (time.monotonic(), ts) if wall0 is None else (wall0, data0)
            lag = (ts - data0) / a.speed - (time.monotonic() - wall0)
            if lag > 0:
                time.sleep(lag)
        last_ts = ts
        if kind == "pkt":
            alerts = pipe.on_packet(item)
            if view:
                view.on_packet(item)
        else:
            alerts = pipe.on_frame(*item)
            if view:
                view.on_frame(item[1], item[2], pipe)
        for al in alerts:
            print(f"[ALERT] t={al.ts:.3f} seat={al.seat} behavior={al.behavior.label} "
                  f"({al.behavior.t_start:.2f}-{al.behavior.t_end:.2f}) net=({al.net.t_start:.2f}-{al.net.t_end:.2f})")
            if view:
                view.on_alert(al)
    for al in pipe.finish(last_ts):
        print(f"[ALERT] t={al.ts:.3f} seat={al.seat} behavior={al.behavior.label}")
    if view:
        view.close()
    if video:
        video.close()
    _dump(out / "alerts.jsonl", pipe.alerts)
    _dump(out / "net_events.jsonl", pipe.net_events)
    _dump(out / "behavior_events.jsonl", pipe.beh_events)
    if pipe.evidence is not None:
        ev = {d: {"final": [r.to_dict() for r in pipe.evidence.ranking(d)], "after_each_upload": pipe.evidence.history(d)}
              for d in pipe.evidence.devices()}
        (out / "seat_evidence.json").write_text(json.dumps(ev, indent=1) + "\n")
        for d, v in ev.items():
            top = v["final"][0] if v["final"] else None
            if top:
                print(f"[EVIDENCE] device {d}: top seat {top['seat']} matched {top['matches']}/{top['uploads']} uploads, "
                      f"p={top['p_value']:.3g} (Bonferroni {top['p_adj']:.3g})")
    pipe.lat.dump_csv(out / "latency.csv")
    print(f"{len(pipe.net_events)} net events, {len(pipe.beh_events)} behavior events, {len(pipe.alerts)} alerts")
    print(pipe.lat.format_summary())


if __name__ == "__main__":
    main()
