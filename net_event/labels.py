"""Ground-truth upload intervals written by capture/scenario_upload.py (one JSON object per line).

    {"seq": 0, "t_start": 1764.0, "t_end": 1765.2, "bytes": 412345, "resolution": "1280x720", "status": 200}

t_start/t_end are on the uploader's clock; `offset_s` (uploader clock - sniffer clock,
measured, see docs/capture_protocol.md) is subtracted to move them onto the sniffer clock.

Project scope (README "범위"): a PLAIN upload, i.e. the device sends the image while it is not loading pages or
downloading at the same time (the target is a dedicated camera / AI-glasses device). Uploads made during other
traffic of the same device carry "concurrent": <what ran alongside> (tools/ota_session.py, synth/netsim); they are
out of scope: evaluation neither counts them as misses nor counts events on them as false, and calibration ignores
them (net_event.evaluate, net_event.calibrate).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Interval:
    t_start: float
    t_end: float
    meta: dict


def load_intervals(path: str | Path, offset_s: float = 0.0) -> list[Interval]:
    out = []
    with open(path) as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                out.append(Interval(d["t_start"] - offset_s, d["t_end"] - offset_s, d))
    return sorted(out, key=lambda i: i.t_start)


def in_scope(i: Interval) -> bool:
    return not i.meta.get("concurrent")


def overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    return a0 <= b1 and b0 <= a1


def match_events(pred: list[tuple[float, float]], gt: list[Interval]) -> dict:
    """Event-level matching by interval overlap (no tolerance parameter)."""
    tp_gt = sum(any(overlaps(p0, p1, g.t_start, g.t_end) for p0, p1 in pred) for g in gt)
    tp_pred = sum(any(overlaps(p0, p1, g.t_start, g.t_end) for g in gt) for p0, p1 in pred)
    prec = tp_pred / len(pred) if pred else float("nan")
    rec = tp_gt / len(gt) if gt else float("nan")
    return {"n_pred": len(pred), "n_gt": len(gt), "precision": prec, "recall": rec,
            "false_positives": len(pred) - tp_pred, "missed": len(gt) - tp_gt}
