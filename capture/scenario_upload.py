"""Uploader side of the capture experiment (runs on the team device that plays the hidden camera).

    python -m capture.scenario_upload --image data/ref/page.jpg --resolution 1280x720 --quality <q> \
        --interval <s> --count <n> --url http://<server>:<port>/upload --out data/raw/up.labels.jsonl
    (IPv6 link-local server: --url 'http://[fe80::1%25wlan0]:8080/upload' pins the outgoing interface)

Only the experiment variables (interval, resolution) change between runs; image content, JPEG
quality, server, transport (HTTP/1.1 over TCP, one connection per upload) and AP stay fixed.
Each upload writes one ground-truth line on this device's clock:
    {"seq", "t_start", "t_end", "bytes", "resolution", "quality", "status"}
"""
from __future__ import annotations

import argparse
import http.client
import json
import time
from urllib.parse import unquote, urlparse

from synth.netsim.images import jpeg_encode


def encode(image: str, resolution: str, quality: int) -> bytes:
    """Same JPEG bytes as the synthetic set (center-crop to the frame aspect, INTER_AREA, OpenCV quality)."""
    return jpeg_encode(image, resolution, quality)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True)
    ap.add_argument("--resolution", required=True)
    ap.add_argument("--quality", type=int, required=True)
    ap.add_argument("--interval", type=float, required=True, help="seconds between upload starts")
    ap.add_argument("--count", type=int, required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    body = encode(a.image, a.resolution, a.quality)
    u = urlparse(a.url)
    t_next = time.time() + a.interval
    with open(a.out, "w") as f:
        for k in range(a.count):
            time.sleep(max(0.0, t_next - time.time()))
            t0 = time.time()
            conn = http.client.HTTPConnection(unquote(u.hostname), u.port or 80, timeout=30)
            conn.request("POST", u.path or "/upload", body, {"Content-Type": "image/jpeg"})
            status = conn.getresponse().status
            conn.close()
            t1 = time.time()
            f.write(json.dumps({"seq": k, "t_start": t0, "t_end": t1, "bytes": len(body),
                                "resolution": a.resolution, "quality": a.quality, "status": status}) + "\n")
            f.flush()
            print(f"#{k} {len(body)} B in {t1 - t0:.3f}s (HTTP {status})")
            t_next += a.interval


if __name__ == "__main__":
    main()
