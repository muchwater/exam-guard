"""Acting cue player for behavior recordings; writes ground-truth labels on the host clock.

    python -m tools.cue_player --schedule docs/schedules/session01.yaml --seat S1 \
        --out data/raw/act_01.labels.jsonl

schedule yaml (all durations are protocol choices recorded with the data, see docs/recording_protocol.md):
    seed: 1
    repeats: {head_down_hold: 5, glasses_touch: 5, normal_writing: 5}
    durations_s: {head_down_hold: <s>, glasses_touch: <s>, normal_writing: <s>}
    rest_s: <s>           # neutral sitting between cues
Each cue is shown in large text (and printed). Label lines: {"t_start", "t_end", "label", "seat"}.
The actor's reaction time makes cue times approximate; refine with tools/label_refine.py if needed.
"""
from __future__ import annotations

import argparse
import json
import random
import time

import cv2
import numpy as np
import yaml


def show(text: str) -> None:
    img = np.zeros((300, 900, 3), np.uint8)
    cv2.putText(img, text, (20, 180), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 4)
    cv2.imshow("cue", img)
    cv2.waitKey(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schedule", required=True)
    ap.add_argument("--seat", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sc = yaml.safe_load(open(a.schedule))
    seq = [lab for lab, n in sc["repeats"].items() for _ in range(n)]
    random.Random(sc["seed"]).shuffle(seq)
    with open(a.out, "w") as f:
        for i, lab in enumerate(seq):
            show(f"rest  ({i + 1}/{len(seq)})")
            time.sleep(float(sc["rest_s"]))
            show(f"GO: {lab}")
            t0 = time.time()
            time.sleep(float(sc["durations_s"][lab]))
            t1 = time.time()
            f.write(json.dumps({"t_start": t0, "t_end": t1, "label": lab, "seat": a.seat, "cue": i}) + "\n")
            f.flush()
            print(f"{lab}: {t0:.3f}-{t1:.3f}")
    show("done")
    cv2.waitKey(1000)


if __name__ == "__main__":
    main()
