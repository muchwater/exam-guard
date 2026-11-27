"""Build an OFFLINE demo session from dataset clips + synthetic packets (no hardware).

    python -m tools.make_demo_session --out data/runs/demo_synth \
        --clip data/external/examcheat/V1/V1/nor10.mp4 --clip data/external/examcheat/V1/V1/m16.mp4 \
        --clip data/external/examcheat/V1/V1/nor11.mp4 --upload-in-class mobile_phone \
        --preset esp32_lan --mcs 5 --resolution 1280x720
    python -m replay.run --pcap data/runs/demo_synth/net.pcap --video data/runs/demo_synth/video.mp4 \
        --out data/runs/demo_synth/replay --config configs/netsim_v1_derived.yaml \
        --config configs/examcheat.yaml --config data/runs/demo_synth/session.yaml --render

Real packets instead of netsim (DEMO COMPOSITE, see --real-session): every upload burst is a REAL capture
(tools/real_session: sniffer clock via in-band markers), cut with --real-window seconds of context and
re-timed so that the upload starts at the requested time; only the timing is constructed.

    python -m tools.make_demo_session --out data/runs/demo_real --clip ... --upload-in-class mobile_phone \
        --real-session data/raw/real/<s> --real-device <team MAC> --real-resolution 1600x1200

Board-dongle sessions (tools/dongle_session.sh): the sniffer and the uploader share the board clock, and the dongle
cannot decode the markers, so pass --real-offset 0 and the BlockAck-reconstructed capture (--real-pcap recon.pcap,
capture/ba_recon.py); replay then needs the count_rule detector (configs/real_dongle_v1_derived.yaml).

Writes video.mp4 (clips concatenated) + video.frames.csv (t = t0 + frame_index / container fps) +
video.meta.json, net.pcap (+ labels) from synth.netsim with uploads at the chosen times, session.json
(clip spans and classes) and session.yaml (overlay for replay).

session.yaml contains DEMO-ONLY settings that are NOT measurements (each is marked in the file):
fusion.pre_s/post_s = 0 because both streams share one synthetic clock and uploads are placed inside the
behaviour by construction; vision.action.min_duration_s = 0 (no minimum) because ExamCheat-MultiV gives
no usable duration. Real values come from team recordings (docs/recording_protocol.md, time_sync.md).
Dataset clips are CC BY-SA 4.0 (datasets.examcheat.ATTRIBUTION): credit them when showing the render.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import yaml

from common.config import load_config
from datasets.examcheat import ATTRIBUTION, CLASSES, parse_prefix_name
from capture.macfilter import MacAllowlist
from capture.dot11 import parse_frame
from capture.pcap_io import iter_raw, write_pcap
from synth.netsim.scenario import run as netsim_run
from net_event.labels import Interval, in_scope
from tools.real_session import load_jsonl, marker_offset, shift


def concat(clips: list[str], out: Path, t0: float) -> list[dict]:
    spans, writer, size, fps0, t, idx = [], None, None, None, t0, 0
    rows = []
    for c in clips:
        cap = cv2.VideoCapture(c)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open {c}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            raise RuntimeError(f"{c}: container reports no fps")
        t_clip, n = t, 0
        while True:
            ok, img = cap.read()
            if not ok:
                break
            if writer is None:
                size, fps0 = (img.shape[1], img.shape[0]), fps
                writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps0, size)
            if (img.shape[1], img.shape[0]) != size:
                img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
            writer.write(img)
            rows.append((idx, t_clip + n / fps))
            idx, n = idx + 1, n + 1
        cap.release()
        cls = parse_prefix_name(Path(c).name)[0]
        spans.append({"clip": c, "class": cls, "t_start": t_clip, "t_end": t_clip + n / fps, "frames": n,
                      "fps": fps})
        t = t_clip + n / fps
    writer.release()
    with open(out.with_suffix(".frames.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame_idx", "ts"])
        w.writerows(rows)
    out.with_suffix(".meta.json").write_text(json.dumps(
        {"source": "file (dataset clips)", "pipeline_delay_s_subtracted": 0.0,
         "note": "timestamps = t0 + frame index / container fps; no camera pipeline"}, indent=1) + "\n")
    return spans, size


def real_composite(sdir: Path, device: str, uploads: str, markers: str, pcap: str, resolution: str | None,
                   window: float, targets: list[float], out_pcap: Path, offset: float | None = None) -> dict:
    """Real upload bursts re-timed to `targets` (DEMO COMPOSITE: packet content/timing inside a burst is real).
    offset: uploader - sniffer clock when known (same clock: 0); None = from the in-band markers."""
    dev = device.lower()
    off, picked = (offset, []) if offset is not None else marker_offset(sdir / pcap, load_jsonl(sdir / markers), dev)
    real = [u for u in shift(load_jsonl(sdir / uploads), off)
            if resolution in (None, u.get("resolution")) and in_scope(Interval(u["t_start"], u["t_end"], u))]
    if not real:
        raise SystemExit(f"no real uploads with resolution {resolution}")
    allow = MacAllowlist([dev])
    raw = [(ts, data) for ts, data, orig, lt in iter_raw(open(sdir / pcap, "rb"))
           if (p := parse_frame(ts, data, orig, lt)) is not None and allow.match(p)]
    frames, labels, spans = [], [], []
    for i, t in enumerate(targets):
        u = real[i % len(real)]
        d = t - u["t_start"]
        lo, hi = u["t_start"] - window + d, u["t_end"] + window + d
        if spans and lo < spans[-1][1]:
            raise SystemExit(f"upload windows overlap at {t:.2f}s: increase the spacing or lower --real-window")
        spans.append((lo, hi))
        frames += [(ts + d, data) for ts, data in raw if u["t_start"] - window <= ts <= u["t_end"] + window]
        labels.append({**u, "t_start": u["t_start"] + d, "t_end": u["t_end"] + d, "real_t_start": u["t_start"]})
    write_pcap(out_pcap, sorted(frames, key=lambda f: f[0]))
    out_pcap.with_suffix(".labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in labels))
    return {"source": str(sdir), "clock_offset_s": off, "markers_used": len(picked), "window_s": window,
            "resolution": resolution, "frames": len(frames), "uploads": len(labels)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--clip", action="append", required=True)
    ap.add_argument("--upload-at", type=float, action="append", default=[],
                    help="upload start, seconds from the session start")
    ap.add_argument("--upload-in-class", action="append", default=[], choices=CLASSES,
                    help="also start one upload at the middle of every clip of this class")
    ap.add_argument("--preset", help="netsim preset (synthetic packets)")
    ap.add_argument("--mcs", type=int)
    ap.add_argument("--resolution")
    ap.add_argument("--real-session", help="real capture session dir (tools/real_session layout)")
    ap.add_argument("--real-device", help="team MAC of the uploader in the real session")
    ap.add_argument("--real-uploads", default="uploads.labels.jsonl")
    ap.add_argument("--real-markers", default="markers.jsonl")
    ap.add_argument("--real-pcap", default="capture.pcap")
    ap.add_argument("--real-offset", type=float,
                    help="uploader - sniffer clock (s) when known, e.g. 0 for board-dongle sessions; skips markers")
    ap.add_argument("--real-resolution", help="re-use only real uploads of this resolution")
    ap.add_argument("--real-window", type=float, default=1.0,
                    help="DEMO-ONLY: seconds of real capture kept before/after each upload")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--tail", type=float, default=5.0, help="seconds of network capture after the last clip")
    ap.add_argument("--config", action="append", default=[])
    a = ap.parse_args()
    cfg = load_config(*a.config)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    spans, (w, h) = concat(a.clip, out / "video.mp4", a.t0)
    t_end = spans[-1]["t_end"] + a.tail
    ups = sorted([a.t0 + x for x in a.upload_at] +
                 [(s["t_start"] + s["t_end"]) / 2 for s in spans if s["class"] in a.upload_in_class])
    if a.real_session:
        meta = real_composite(Path(a.real_session), a.real_device, a.real_uploads, a.real_markers, a.real_pcap,
                              a.real_resolution, a.real_window, ups, out / "net.pcap", a.real_offset)
        dev = a.real_device.lower()
    else:
        if a.preset is None or a.mcs is None or a.resolution is None:
            raise SystemExit("--preset, --mcs and --resolution are required without --real-session")
        meta = netsim_run(cfg, a.preset, None, a.mcs, a.resolution, a.seed, out / "net.pcap", a.t0,
                          t_end - a.t0, None, None, ups)
        dev = cfg["synth"]["netsim"]["macs"]["device"]
    (out / "session.json").write_text(json.dumps({"clips": spans, "uploads_at": ups, "netsim": meta,
                                                  "attribution": ATTRIBUTION}, indent=1) + "\n")
    overlay = {
        "capture": {"allowed_macs": [dev]},
        "vision": {"seats": {"S1": [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]},
                   "pose": {"backend": "onnx"},
                   "camera": {"pipeline_delay_s": 0.0},
                   "action": {"min_duration_s": 0.0}},
        "fusion": {"pre_s": 0.0, "post_s": 0.0},
    }
    mac_note = ("# capture.allowed_macs: the real uploader's team MAC (--real-device); packets are REAL bursts\n"
                f"#   re-timed to the clips (DEMO COMPOSITE, --real-window {a.real_window} s of context).\n"
                if a.real_session else
                "# capture.allowed_macs: the synthetic device MAC (synth.netsim.macs.device).\n")
    head = ("# OFFLINE DEMO SESSION overlay generated by tools/make_demo_session.py -- not for the live system.\n"
            + mac_note +
            "# vision.seats.S1: whole frame (calc from the video size): one examinee per ExamCheat V1 clip.\n"
            "# vision.pose.backend onnx: dev PC has no NPU. camera.pipeline_delay_s 0: timestamps come from the\n"
            "#   frame index (file replay), there is no camera pipeline.\n"
            "# DEMO-ONLY (not measured): action.min_duration_s 0 = no minimum duration; fusion.pre_s/post_s 0 =\n"
            "#   uploads placed inside the behaviour clips by construction (one clock).\n")
    (out / "session.yaml").write_text(head + yaml.safe_dump(overlay, sort_keys=False))
    print(json.dumps({"clips": [(Path(s["clip"]).name, s["class"], round(s["t_start"], 2), round(s["t_end"], 2))
                                for s in spans], "uploads_at": [round(u, 2) for u in ups],
                      "frames": meta["frames"], "packets": "real composite" if a.real_session else "netsim"},
                     indent=1))


if __name__ == "__main__":
    main()
