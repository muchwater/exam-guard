"""ExamCheat-MultiV (Kaggle: rimmajeed/examcheating-multiv-video-based-dataset) index.

    python -m datasets.examcheat [--root data/external/examcheat] [--probe] [--out index.json]

Labels exist only in file names (the "CSV annotations" mentioned in the Kaggle description are
not in the file list). What was checked by viewing frames (2026-11-25, thumbnails of 3 clips x 2
frames per prefix):
  V1/V1/<prefix><n>.mp4 and the 224x224 crops, prefix -> class
    c   -> copying          (looks sideways at a neighbour / passes paper)
    g   -> gesture_sharing  (hand signs, e.g. V sign, towards a neighbour)
    m   -> mobile_phone     (phone in hands / lap)
    n   -> hidden_notes     (notes from pocket / shoe; deep bend)
    nor -> normal           (writing on the sheet)
    s   -> unresolved (crops only; frames look like ordinary writing) -> excluded
  V1: one seated examinee per clip filmed from the front, high angle, portrait 1080x1920 at 30 fps;
      the sampled clips all show the same clothing, so V1 is likely a single participant.
  crops (sample_of_our_preprocessed_data_224x224_cropped_clips): subject-centred 224x224 clips of
      several participants. 43 crops have the same name and frame count (+-1) as a V1 clip and are
      crops of that clip -> marked duplicate_of and excluded from statistics.
  V2: whole-classroom scenes with many examinees; a few names carry a scene-level word (copy,
      gesture, mobile, notes, all). Which person cheats is not annotated, so V2 is not usable for
      per-person statistics (label_scope = "scene").
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_ROOT = Path("data/external/examcheat")
KAGGLE_URL = "https://www.kaggle.com/datasets/rimmajeed/examcheating-multiv-video-based-dataset"
ATTRIBUTION = (
    '"ExamCheating _MultiV video-based Dataset" (ExamCheat-MultiV) by rimmajeed, Kaggle, '
    f"{KAGGLE_URL}, licensed under CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/). "
    "Changes: pose keypoints and derived features were extracted with YOLOv8n-pose; such derived data "
    "are adaptations and, if shared, must be shared under CC BY-SA 4.0."
)
CLASSES = ["normal", "copying", "gesture_sharing", "hidden_notes", "mobile_phone"]
PREFIX_CLASS = {"nor": "normal", "c": "copying", "g": "gesture_sharing", "n": "hidden_notes",
                "m": "mobile_phone", "s": None}
V2_WORD_CLASS = {"copy": "copying", "gesture": "gesture_sharing", "mobile": "mobile_phone",
                 "notes": "hidden_notes", "all": None}
_PREFIX_RE = re.compile(r"^(nor|c|g|m|n|s)(\d+)\.mp4$")
_V2_WORD_RE = re.compile(r"^(copy|gesture|mobile|notes|all)\b|^(copy|gesture|mobile|notes|all)[-_.]", re.IGNORECASE)
VERSION_DIRS = {"V1": "V1/V1", "V2": "V2/V2",
                "crops": "sample_of_our_preprocessed_data_224x224_cropped_clips/224x224_cropped_videos"}
VIDEO_EXT = {".mp4", ".mov"}


@dataclass
class Clip:
    rel_path: str                 # relative to the dataset root
    version: str                  # V1 | V2 | crops
    cls: str | None            # one of CLASSES, or None when unlabeled / unresolved
    label_source: str | None   # filename_prefix | filename_word | None
    label_scope: str              # subject (one examinee per clip) | scene (classroom, person unknown)
    note: str = ""
    duplicate_of: str | None = None
    frames: int | None = None

    @property
    def clip_id(self) -> str:
        return f"{self.version}__{Path(self.rel_path).stem}"

    @property
    def usable_for_stats(self) -> bool:
        return self.cls is not None and self.label_scope == "subject" and self.duplicate_of is None


def parse_prefix_name(name: str) -> tuple[str | None, str | None, str]:
    """(class, prefix, note) for V1 / crop file names like 'nor12.mp4'."""
    m = _PREFIX_RE.match(name)
    if not m:
        return None, None, "name does not follow <prefix><n>.mp4"
    prefix = m.group(1)
    cls = PREFIX_CLASS[prefix]
    return cls, prefix, ("prefix 's' unresolved -> excluded" if cls is None else "")


def parse_v2_name(name: str) -> tuple[str | None, str]:
    m = _V2_WORD_RE.match(name)
    if not m:
        return None, "no label word in name"
    word = (m.group(1) or m.group(2)).lower()
    cls = V2_WORD_CLASS[word]
    return cls, ("scene contains all behaviours" if word == "all" else f"scene-level word '{word}'")


def _frame_count(path: Path) -> int | None:
    import cv2
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if cap.isOpened() else None
    cap.release()
    return n


def build_index(root: Path | str = DEFAULT_ROOT, probe: bool = False) -> list[Clip]:
    """Index every video under the known folders. probe=True reads frame counts (needed for the
    crop-duplicate check); without it, duplicates are matched by name only."""
    root = Path(root)
    clips: list[Clip] = []
    for version, sub in VERSION_DIRS.items():
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() not in VIDEO_EXT:
                continue
            rel = str(p.relative_to(root))
            if version == "V2":
                cls, note = parse_v2_name(p.name)
                clips.append(Clip(rel, version, cls, "filename_word" if cls else None, "scene", note))
            else:
                cls, _prefix, note = parse_prefix_name(p.name)
                clips.append(Clip(rel, version, cls, "filename_prefix" if cls else None, "subject", note))
            if probe:
                clips[-1].frames = _frame_count(p)
    v1 = {Path(c.rel_path).name: c for c in clips if c.version == "V1"}
    for c in clips:
        if c.version != "crops":
            continue
        twin = v1.get(Path(c.rel_path).name)
        if twin is None:
            continue
        if not probe or (c.frames is not None and twin.frames is not None and abs(c.frames - twin.frames) <= 1):
            c.duplicate_of = twin.rel_path
            c.note = (c.note + "; " if c.note else "") + "crop of the V1 clip with the same name"
    return clips


def summary(clips: list[Clip]) -> str:
    lines = []
    for version in VERSION_DIRS:
        cs = [c for c in clips if c.version == version]
        if not cs:
            continue
        by = Counter(c.cls or "-" for c in cs)
        use = Counter(c.cls for c in cs if c.usable_for_stats)
        dup = sum(c.duplicate_of is not None for c in cs)
        lines.append(f"{version:5s} files={len(cs):3d} labels={dict(by)} usable={dict(use)} duplicates={dup}")
    tot = Counter(c.cls for c in clips if c.usable_for_stats)
    lines.append(f"usable for per-subject statistics: {sum(tot.values())} clips {dict(tot)}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--probe", action="store_true", help="read frame counts (crop duplicate check)")
    ap.add_argument("--out", help="write the index as JSON")
    a = ap.parse_args()
    clips = build_index(a.root, probe=a.probe)
    print(summary(clips))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(
            {"attribution": ATTRIBUTION, "clips": [asdict(c) | {"clip_id": c.clip_id,
                                                                 "usable_for_stats": c.usable_for_stats}
                                                   for c in clips]}, indent=1) + "\n")


if __name__ == "__main__":
    main()
