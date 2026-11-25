"""ExamCheat-MultiV index and statistics helpers (no videos needed; index test skips if absent)."""
from pathlib import Path

import numpy as np
import pytest

from datasets.examcheat import (
    DEFAULT_ROOT,
    build_index,
    parse_prefix_name,
    parse_v2_name,
)
from tools.examcheat_extract import select_subject
from tools.examcheat_stats import auc, nan_gaps, runs


def test_prefix_names():
    assert parse_prefix_name("nor12.mp4")[0] == "normal"     # 'nor' must win over 'n'
    assert parse_prefix_name("n12.mp4")[0] == "hidden_notes"
    assert parse_prefix_name("c3.mp4")[0] == "copying"
    assert parse_prefix_name("g29.mp4")[0] == "gesture_sharing"
    assert parse_prefix_name("m16.mp4")[0] == "mobile_phone"
    assert parse_prefix_name("s4.mp4")[0] is None            # unresolved prefix
    assert parse_prefix_name("DSC_0032.MOV")[0] is None


def test_v2_names():
    assert parse_v2_name("copy.MOV")[0] == "copying"
    assert parse_v2_name("mobile-2_DSC_0036.MOV")[0] == "mobile_phone"
    assert parse_v2_name("notes_DSC_0033.MOV")[0] == "hidden_notes"
    assert parse_v2_name("all_DSC_0037.MOV")[0] is None
    assert parse_v2_name("1.mp4")[0] is None


def test_auc_basic():
    assert auc(np.array([2.0, 3.0]), np.array([0.0, 1.0])) == 1.0
    assert auc(np.array([0.0]), np.array([1.0])) == 0.0
    assert auc(np.array([1.0]), np.array([1.0])) == 0.5


def test_runs_and_gaps():
    ts = np.arange(6) * 0.1
    assert runs(np.array([1, 1, 0, 1, 1, 1], bool), ts) == pytest.approx([0.1, 0.2])
    x = np.array([1.0, np.nan, np.nan, 1.0, -1.0, np.nan])
    assert nan_gaps(x, ts) == pytest.approx([0.3])
    assert nan_gaps(x, ts, bracket=x > 0) == pytest.approx([0.3])
    assert nan_gaps(np.array([1.0, np.nan, -1.0]), ts[:3], bracket=np.array([True, False, False])) == []


class _P:
    def __init__(self, box):
        self.bbox = np.array(box, float)


def test_select_subject_prefers_persistent_region():
    subj, other = [100, 100, 300, 500], [0, 0, 50, 60]
    dets = [[_P(subj), _P(other)]] * 5 + [[_P(other)]] + [[]]
    region, chosen = select_subject(dets)
    assert np.allclose(region, subj)
    assert chosen[:5] == [0] * 5 and chosen[5] == -1 and chosen[6] == -1


@pytest.mark.skipif(not (Path(DEFAULT_ROOT) / "V1").is_dir(), reason="dataset not downloaded")
def test_index_counts_from_file_names():
    clips = build_index(DEFAULT_ROOT, probe=False)
    v1 = [c for c in clips if c.version == "V1"]
    assert len(v1) == 83 and all(c.cls is not None for c in v1)
    assert all(not c.usable_for_stats for c in clips if c.version == "V2")
