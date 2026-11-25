"""Seat assignment: person -> seat polygon (pixel coordinates of the fixed exam camera).

Examinees stay in their seats, so the seat itself is the identity; no tracker is needed.
Anchor point = shoulder midpoint when both shoulders are confident, else the bbox center.
Seat polygons are annotated once per camera placement with tools/seat_annotator.py.
"""
from __future__ import annotations

import cv2
import numpy as np

from common.events import Person

L_SHO, R_SHO = 5, 6


def anchor(p: Person, kpt_conf: float) -> tuple[float, float]:
    k = p.keypoints
    if k[L_SHO, 2] >= kpt_conf and k[R_SHO, 2] >= kpt_conf:
        return float((k[L_SHO, 0] + k[R_SHO, 0]) / 2), float((k[L_SHO, 1] + k[R_SHO, 1]) / 2)
    x1, y1, x2, y2 = p.bbox
    return float((x1 + x2) / 2), float((y1 + y2) / 2)


class SeatMap:
    def __init__(self, seats: dict[str, list[list[float]]], kpt_conf: float):
        self.polys = {name: np.asarray(pts, np.float32).reshape(-1, 1, 2) for name, pts in seats.items()}
        self.kpt_conf = kpt_conf

    def seat_of(self, p: Person) -> str | None:
        pt = anchor(p, self.kpt_conf)
        for name, poly in self.polys.items():
            if cv2.pointPolygonTest(poly, pt, False) >= 0:
                return name
        return None

    def assign(self, persons: list[Person]) -> list[Person]:
        """Label persons with seats; if two persons fall in one seat keep the higher-scoring one."""
        best: dict[str, Person] = {}
        for p in persons:
            s = self.seat_of(p)
            p.seat = s
            if s is not None and (s not in best or p.score > best[s].score):
                best[s] = p
        return list(best.values())

    def draw(self, img: np.ndarray, highlight: set[str] = frozenset()) -> None:
        for name, poly in self.polys.items():
            color = (0, 0, 255) if name in highlight else (200, 200, 200)
            cv2.polylines(img, [poly.astype(np.int32)], True, color, 3 if name in highlight else 1)
            x, y = poly[0, 0]
            cv2.putText(img, name, (int(x) + 3, int(y) + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
