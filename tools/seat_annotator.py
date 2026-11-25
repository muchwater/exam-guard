"""Click seat polygons on a camera frame and write them as a config override.

    python -m tools.seat_annotator --image frame.png --out configs/site_seats.yaml
    left click: add vertex | n: close polygon, type seat name in terminal | u: undo | q: save & quit
"""
from __future__ import annotations

import argparse

import cv2
import numpy as np
import yaml


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="a frame from the fixed exam camera")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    img = cv2.imread(a.image)
    seats: dict[str, list[list[int]]] = {}
    cur: list[list[int]] = []

    def on_mouse(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN:
            cur.append([x, y])

    cv2.namedWindow("seats")
    cv2.setMouseCallback("seats", on_mouse)
    while True:
        vis = img.copy()
        for name, pts in seats.items():
            cv2.polylines(vis, [np.array(pts, np.int32)], True, (0, 255, 0), 2)
            cv2.putText(vis, name, tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        if cur:
            cv2.polylines(vis, [np.array(cur, np.int32)], False, (0, 200, 255), 2)
        cv2.imshow("seats", vis)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("u") and cur:
            cur.pop()
        elif k == ord("n") and len(cur) >= 3:
            seats[input("seat name: ").strip()] = [list(p) for p in cur]
            cur.clear()
        elif k == ord("q"):
            break
    with open(a.out, "w") as f:
        yaml.safe_dump({"vision": {"seats": seats}}, f)
    print(f"{len(seats)} seats -> {a.out} (source: annotated on {a.image})")


if __name__ == "__main__":
    main()
