"""JPEG payload sizes: encode a reference page image at a camera resolution (calculation, not a guess).

The camera frame is filled by the page: center-crop the page to the frame aspect ratio, resize with
INTER_AREA, encode with OpenCV at the given quality. capture/scenario_upload.py and tools/ota_session.py
upload exactly these bytes (jpeg_encode), so synthetic and real payloads are the same.
"""
from __future__ import annotations

from functools import lru_cache

import cv2


def parse_resolution(s: str) -> tuple[int, int]:
    w, h = (int(x) for x in s.lower().split("x"))
    return w, h


@lru_cache(maxsize=None)
def jpeg_bytes(image: str, resolution: str, quality: int) -> int:
    return len(jpeg_encode(image, resolution, quality))


@lru_cache(maxsize=None)
def jpeg_encode(image: str, resolution: str, quality: int) -> bytes:
    img = cv2.imread(image)
    if img is None:
        raise FileNotFoundError(image)
    w, h = parse_resolution(resolution)
    ih, iw = img.shape[:2]
    target = w / h
    if iw / ih > target:                       # too wide -> crop width
        cw = int(round(ih * target))
        x0 = (iw - cw) // 2
        img = img[:, x0:x0 + cw]
    else:                                      # too tall -> crop height
        ch = int(round(iw / target))
        y0 = (ih - ch) // 2
        img = img[y0:y0 + ch]
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()
