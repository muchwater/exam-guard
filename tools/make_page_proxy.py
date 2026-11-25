"""Render a stand-in exam page (A4, 300 dpi, Korean text) used as the JPEG source for synthetic uploads.

    python -m tools.make_page_proxy --out data/ref/page_proxy.png

This is a PROXY until a real photo of a printed page taken with the team's camera exists
(data/ref/page.jpg, docs/capture_protocol.md). A clean rendered page has no sensor noise, blur or
lighting gradient, so its JPEG is smaller than a real photo of the same page at the same resolution:
synthetic uploads are a lower bound on bytes, i.e. the harder case for the detector.
Text is generated from a fixed vocabulary with a fixed seed (deterministic output).
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

A4_MM = (210, 297)          # ISO 216
DPI = 300                   # common office print/scan resolution; only sets the source pixel count
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_KR_INDEX = 1           # face order in NotoSansCJK-Regular.ttc: JP, KR, SC, TC, HK (fc-list)

WORDS = ("다음 글을 읽고 물음에 답하시오 함수 그래프 조건 만족 모든 실수 값 구하시오 옳은 것만을 보기에서 있는 대로 고른 것은 "
         "수열 일반항 극한 미분 적분 확률 통계 경우의 수 집합 원소 개수 넓이 부피 속도 거리 시간 위치 점 직선 곡선 접선 "
         "실험 결과 자료 분석 가설 변인 통제 비율 증가 감소 평균 분산 표준편차 정규분포 독립 사건 조건부").split()


def render(out: Path, seed: int = 0) -> None:
    rnd = random.Random(seed)
    w, h = (round(mm / 25.4 * DPI) for mm in A4_MM)
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    body = ImageFont.truetype(FONT, 38, index=FONT_KR_INDEX)
    head = ImageFont.truetype(FONT, 64, index=FONT_KR_INDEX)
    m = round(15 / 25.4 * DPI)                                   # 15 mm margins
    d.text((m, m), "수학 영역 (가형)  제 2 교시", font=head, fill=0)
    d.line((m, m + 100, w - m, m + 100), fill=0, width=4)
    col_w = (w - 3 * m) // 2
    for col in range(2):
        x0 = m + col * (col_w + m)
        y = m + 140
        q = 1 + col * 6
        while y < h - m - 200:
            d.text((x0, y), f"{q}.", font=body, fill=0)
            y += 50
            for _ in range(rnd.randint(3, 6)):
                line, lw = "", 0
                while True:
                    word = rnd.choice(WORDS) + " "
                    ww = d.textlength(word, font=body)
                    if lw + ww > col_w - 40:
                        break
                    line, lw = line + word, lw + ww
                d.text((x0 + 40, y), line, font=body, fill=0)
                y += 52
            if rnd.random() < 0.4:                               # figure box with a curve
                bh = rnd.randint(180, 300)
                d.rectangle((x0 + 40, y, x0 + col_w - 40, y + bh), outline=0, width=3)
                pts = [(x0 + 40 + i * (col_w - 80) / 40,
                        y + bh / 2 + (bh / 3) * ((i / 20 - 1) ** 3)) for i in range(41)]
                d.line(pts, fill=0, width=3)
                y += bh + 20
            y += 60
            q += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    render(Path(a.out), a.seed)
    print(a.out)


if __name__ == "__main__":
    main()
