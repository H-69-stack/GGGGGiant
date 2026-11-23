"""Session-level API helper tests for profile QA (no HTTP auth)."""
from __future__ import annotations

import os
import sys

import numpy as np

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.pp_qa import _store, ensure_qa  # noqa: E402
from core.pp.profile_qa.state import QA_PASS, QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION  # noqa: E402
from core.pp.profile_qa.pipeline import finalize_straight  # noqa: E402


def _sess(d=52.7, n=120):
    s = np.arange(n, dtype=float)
    diam = np.full(n, d)
    wt = [{"中心线长度(mm)": float(si), "外径周长(mm)": float(di) * np.pi} for si, di in zip(s, diam)]
    return {
        "original_filename": "unit.stp",
        "results": {
            "wall_thickness_data": wt,
            "centerline_perimeter_data": wt,
            "centerline_length": float(n - 1),
        },
    }


def test_ensure_qa_does_not_reparse_and_is_idempotent():
    sess = _sess()
    qa1 = ensure_qa(sess)
    qa2 = ensure_qa(sess)
    assert qa1 is qa2
    assert sess["results"]["qa"]["raw_profile"] is not None
    # Original PP records still present with original keys.
    assert "中心线长度(mm)" in sess["results"]["wall_thickness_data"][0]
    assert "外径周长(mm)" in sess["results"]["wall_thickness_data"][0]


def test_store_on_pass_overlays_circumference_without_renaming():
    sess = _sess()
    qa = ensure_qa(sess)
    assert qa["status"] == QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION
    ids = [s["segment_id"] for s in qa["straight_segments"]]
    qa = finalize_straight(qa, confirmed_ids=ids)
    _store(sess, qa)
    assert sess["results"]["qa"]["status"] == QA_PASS
    row = sess["results"]["wall_thickness_data"][0]
    assert set(["中心线长度(mm)", "外径周长(mm)"]).issubset(row)
    assert sess["results"]["qa"]["raw_profile"]["source"] == "stp_raw"
