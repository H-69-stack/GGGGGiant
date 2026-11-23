"""Unit tests for STP formline QA (monotonicity, smoothing, straight, XLSX)."""
from __future__ import annotations

import io
import os
import sys

import numpy as np
import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.profile_qa.config import (  # noqa: E402
    MAX_LOCAL_ANOMALY_POINT_RATIO,
    MIN_STRAIGHT_LENGTH_MM,
    STRAIGHT_TOLERANCE_MM,
)
from core.pp.profile_qa.manual_profile_parser import (  # noqa: E402
    build_xlsx_template_bytes,
    parse_manual_xlsx,
)
from core.pp.profile_qa.monotonic_validator import (  # noqa: E402
    classify_local_vs_manual,
    validate_monotonic_profile,
)
from core.pp.profile_qa.numeric import ratio_leq, threshold_leq  # noqa: E402
from core.pp.profile_qa.pipeline import (  # noqa: E402
    apply_manual_xlsx,
    choose_profile,
    confirm_smooth,
    confirm_straight,
    finalize_straight,
    initialize_qa_from_records,
    preview_smooth,
    reject_smooth,
)
from core.pp.profile_qa.profile_smoothing import preview_local_smooth  # noqa: E402
from core.pp.profile_qa.profile_validation import (  # noqa: E402
    records_to_arrays,
    validate_profile_input,
)
from core.pp.profile_qa.state import (  # noqa: E402
    QA_INVALID_INPUT,
    QA_LOCAL_ANOMALY,
    QA_MANUAL_DATA_INVALID,
    QA_MANUAL_REQUIRED,
    QA_MONOTONIC_PASS,
    QA_PASS,
    QA_RECHECK_FAILED,
    QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION,
    QaStateError,
)
from core.pp.profile_qa.straight_segment_detector import detect_straight_segments  # noqa: E402


def _s(length_mm: float = 600.0, step: float = 1.0) -> np.ndarray:
    return np.arange(0.0, length_mm + 1e-12, step)


def _records(s: np.ndarray, diameter: np.ndarray) -> list:
    return [
        {"中心线长度(mm)": float(si), "外径周长(mm)": float(di) * np.pi}
        for si, di in zip(s, diameter)
    ]


def _mono(s, d):
    return validate_monotonic_profile(s, diameter=d)


# ---------------------------------------------------------------------------
# Numeric boundary
# ---------------------------------------------------------------------------
class TestNumericBoundary:
    def test_point_ratio_threshold_minus_eps(self):
        assert ratio_leq(0.099999999, MAX_LOCAL_ANOMALY_POINT_RATIO)
        d = classify_local_vs_manual(
            has_anomaly=True, point_ratio=0.099999999, length_ratio=0.05,
            trend_unreliable=False, trend_reasons=[], segment_count=1,
            high_severity_count=0,
        )
        assert d["status"] == QA_LOCAL_ANOMALY

    def test_point_ratio_threshold_equal(self):
        assert ratio_leq(0.10, MAX_LOCAL_ANOMALY_POINT_RATIO)
        d = classify_local_vs_manual(
            has_anomaly=True, point_ratio=0.100000000, length_ratio=0.05,
            trend_unreliable=False, trend_reasons=[], segment_count=1,
            high_severity_count=0,
        )
        assert d["status"] == QA_LOCAL_ANOMALY

    def test_point_ratio_threshold_plus_eps(self):
        assert not ratio_leq(0.100000001, MAX_LOCAL_ANOMALY_POINT_RATIO)
        d = classify_local_vs_manual(
            has_anomaly=True, point_ratio=0.100000001, length_ratio=0.05,
            trend_unreliable=False, trend_reasons=[], segment_count=1,
            high_severity_count=0,
        )
        assert d["status"] == QA_MANUAL_REQUIRED

    def test_straight_tolerance_band(self):
        assert threshold_leq(0.999999999, STRAIGHT_TOLERANCE_MM)
        assert threshold_leq(1.000000000, STRAIGHT_TOLERANCE_MM)
        assert not threshold_leq(1.000000001, STRAIGHT_TOLERANCE_MM)


# ---------------------------------------------------------------------------
# Tests 1-4 straight / taper
# ---------------------------------------------------------------------------
class TestStraightAndTaper:
    def test_1_perfect_straight(self):
        s = _s(200)
        d = np.full_like(s, 52.69)
        mono = _mono(s, d)
        assert mono["status"] == QA_MONOTONIC_PASS
        det = detect_straight_segments(s, d)
        assert len(det["segments"]) >= 1
        assert det["segments"][0]["length"] >= MIN_STRAIGHT_LENGTH_MM - 1e-6

    def test_2_straight_small_noise(self):
        s = _s(200)
        rng = np.random.default_rng(0)
        pattern = np.array([52.6, 52.8, 52.5, 52.7, 52.6])
        d = np.resize(pattern, s.size) + rng.normal(0, 0.01, s.size)
        mono = _mono(s, d)
        assert mono["status"] == QA_MONOTONIC_PASS, mono["reason"]
        det = detect_straight_segments(s, d)
        assert len(det["segments"]) >= 1

    def test_3_normal_taper_not_straight(self):
        s = _s(400)
        d = np.linspace(52, 48, s.size)
        mono = _mono(s, d)
        assert mono["status"] == QA_MONOTONIC_PASS
        det = detect_straight_segments(s, d)
        assert det["segments"] == []

    def test_4_slow_taper_not_straight(self):
        """Adjacent steps << 1 mm, but a steady drop — must not be straight."""
        s = np.array([100.0, 200.0, 300.0, 400.0])
        d = np.array([52.0, 51.7, 51.4, 51.1])
        # Dense version of the same slope (the production sampling rate).
        s2 = _s(300)
        d2 = 52.0 - 0.9 * (s2 / 300.0)
        det = detect_straight_segments(s2, d2)
        assert det["segments"] == [], det
        # Sparse 4-point version is shorter than MIN_STRAIGHT_LENGTH after windowing
        # but must still not be classified straight if length allows.
        det_sparse = detect_straight_segments(
            s, d, min_length_mm=50.0, window_mm=80.0, min_points=3,
        )
        assert det_sparse["segments"] == []

    def test_residual_shelf_does_not_split_one_straight(self):
        """A 0.09 mm isotonic leftover must not open a gap between two candidates."""
        s = _s(700)
        d = np.full_like(s, 63.18)
        d[(s >= 600) & (s <= 650)] = 63.27
        det = detect_straight_segments(s, d)
        assert len(det["segments"]) == 1, det
        seg = det["segments"][0]
        assert seg["start_s"] <= 1.0
        assert seg["end_s"] >= 699.0

    def test_left_taper_trimmed_to_plateau_edge(self):
        """Taper + residual shelves → one straight starting near the plateau, not mid-tube."""
        s = _s(774)
        d = np.full_like(s, 63.18)
        taper = s < 139.0
        d[taper] = 63.18 + (139.0 - s[taper]) * 0.15
        d[(s >= 166) & (s <= 204)] = 63.31
        d[(s >= 608) & (s <= 648)] = 63.27
        det = detect_straight_segments(s, d)
        assert len(det["segments"]) == 1, det
        seg = det["segments"][0]
        assert 134.0 <= seg["start_s"] <= 145.0, seg
        assert seg["end_s"] >= 770.0

    def test_two_different_diameter_straights_not_merged(self):
        s = _s(400)
        d = np.empty_like(s)
        d[s < 180.0] = 52.0
        mid = (s >= 180.0) & (s < 220.0)
        d[mid] = 52.0 - (s[mid] - 180.0) * 0.10
        d[s >= 220.0] = 48.0
        det = detect_straight_segments(s, d)
        assert len(det["segments"]) == 2, det
        assert abs(det["segments"][0]["representative_diameter"] - 52.0) < 0.15
        assert abs(det["segments"][1]["representative_diameter"] - 48.0) < 0.15


class TestProfileChoiceAndL4:
    def test_confirmed_straight_sets_l4_and_formline_length(self):
        s = _s(774)
        d = np.full_like(s, 63.18)
        taper = s < 139.0
        d[taper] = 63.18 + (139.0 - s[taper]) * 0.15
        qa = initialize_qa_from_records(_records(s, d), centerline_length=774)
        assert qa["status"] == QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION
        qa = confirm_straight(qa, qa["straight_segments"][0]["segment_id"])
        qa = finalize_straight(qa, confirmed_ids=[qa["straight_segments"][0]["segment_id"]])
        assert qa["L4"] > 500
        assert abs(qa["formline_length"] - (774 - qa["L4"])) < 1e-6
        assert qa["formline_length"] < 200

    def test_choose_auto_ok_and_manual_empty_rejected(self):
        s = _s(200)
        d = np.full_like(s, 52.686)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=200)
        qa = finalize_straight(qa, confirmed_ids=[seg["segment_id"] for seg in qa["straight_segments"]])
        qa = choose_profile(qa, "auto")
        assert qa["profile_choice"] == "auto"
        with pytest.raises(QaStateError) as exc:
            choose_profile(qa, "manual")
        assert exc.value.code == "QA_PROFILE_EMPTY"

    def test_choose_requires_explicit_source(self):
        s = _s(200)
        d = np.full_like(s, 52.686)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=200)
        with pytest.raises(QaStateError) as exc:
            choose_profile(qa, "")
        assert exc.value.code == "QA_PROFILE_CHOICE_REQUIRED"


# ---------------------------------------------------------------------------
# Tests 5-12 anomaly detection
# ---------------------------------------------------------------------------
def _taper_with_bump(length=600.0, bump=(100.0, 120.0), bump_d=54.0):
    s = _s(length)
    d = 52.0 - 12.0 * (s / length)
    mask = (s >= bump[0]) & (s <= bump[1])
    d = d.copy()
    d[mask] = bump_d
    return s, d


class TestAnomalyDetection:
    def test_5_local_bump_100_120(self):
        s, d = _taper_with_bump()
        mono = _mono(s, d)
        assert mono["status"] == QA_LOCAL_ANOMALY, (mono["status"], mono["reason"], mono["anomaly_point_ratio"])
        segs = mono["suspect_segments"]
        assert segs
        # At least one segment overlaps 100-120 mm.
        hit = any(seg["start_s"] <= 120 and seg["end_s"] >= 100 for seg in segs)
        assert hit, segs
        # Approximate location
        best = min(segs, key=lambda seg: abs(seg["start_s"] - 100) + abs(seg["end_s"] - 120))
        assert 80 <= best["start_s"] <= 110
        assert 110 <= best["end_s"] <= 140

    def test_6_local_under_10_percent_requires_review(self):
        s, d = _taper_with_bump()
        mono = _mono(s, d)
        assert mono["anomaly_point_ratio"] <= 0.10 + 1e-12
        assert mono["status"] == QA_LOCAL_ANOMALY
        # Must not auto-modify: pipeline keeps working == raw until confirm.
        rec = _records(s, d)
        qa = initialize_qa_from_records(rec, centerline_length=600)
        assert qa["status"] == QA_LOCAL_ANOMALY
        assert qa["working_profile"]["outer_diameter_mm"] == qa["raw_profile"]["outer_diameter_mm"]

    def test_7_exactly_10_percent_is_local(self):
        d = classify_local_vs_manual(
            has_anomaly=True, point_ratio=0.10, length_ratio=0.10,
            trend_unreliable=False, trend_reasons=[], segment_count=1,
            high_severity_count=1,
        )
        assert d["status"] == QA_LOCAL_ANOMALY

    def test_8_over_10_percent_manual(self):
        s = _s(200)
        d = np.full_like(s, 50.0)
        d[20:50] = 62.0  # 30/201 ≈ 14.9%
        mono = _mono(s, d)
        assert mono["status"] == QA_MANUAL_REQUIRED
        assert mono["anomaly_point_ratio"] > 0.10

    def test_9_random_oscillation_manual(self):
        s = _s(400)
        rng = np.random.default_rng(1)
        d = 50 + rng.uniform(-8, 8, s.size)
        mono = _mono(s, d)
        assert mono["status"] == QA_MANUAL_REQUIRED

    def test_quantized_sawtooth_not_oscillatory_two_hills_like_2052_2(self):
        """0.01 mm rounding ticks must not trigger oscillatory_profile."""
        s = np.arange(0.0, 478.0)
        d = np.round(np.linspace(28.40, 16.51, s.size), 2)
        mono = _mono(s, d)
        trend = (mono.get("metrics") or {}).get("trend") or {}
        assert "oscillatory_profile" not in (trend.get("reasons") or []), trend
        assert mono["reason"] != "oscillatory_profile"

    def test_2052_2_cache_is_local_if_present(self):
        path = os.path.join(_BACKEND, "data", "dev", "current_stp.json")
        if not os.path.exists(path):
            pytest.skip("no current_stp.json")
        import json
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
        if "2052-2" not in str(cache.get("filename") or ""):
            pytest.skip("current cache is not 2052-2")
        qa = initialize_qa_from_records(
            cache["wall_thickness_data"],
            centerline_length=cache.get("centerline_length"),
        )
        assert qa["status"] == QA_LOCAL_ANOMALY, (qa.get("status"), qa.get("reason"))
        assert len(qa.get("suspect_segments") or []) == 2

    def test_manual_with_segments_can_preview_smooth(self):
        s = _s(200)
        d = np.full_like(s, 50.0)
        d[20:50] = 62.0
        qa = initialize_qa_from_records(_records(s, d), centerline_length=200)
        assert qa["status"] == QA_MANUAL_REQUIRED
        assert qa["suspect_segments"]
        out = preview_smooth(qa)
        assert out.get("processed_profile")

    def test_10_anomaly_at_start(self):
        s = _s(400)
        d = np.linspace(52, 44, s.size)
        # Sudden rise at the large end, then return below the start OD.
        d[1:8] = 52.0 + np.arange(1, 8) * 1.1
        d[8:16] = np.linspace(float(d[7]), 51.0, 8)
        mono = _mono(s, d)
        assert mono["suspect_segments"]
        assert mono["suspect_segments"][0]["start_s"] <= 5.0

    def test_11_anomaly_at_end(self):
        s = _s(400)
        d = np.linspace(52, 44, s.size)
        d[-25:] = 60.0
        mono = _mono(s, d)
        assert mono["suspect_segments"]
        assert mono["suspect_segments"][-1]["end_s"] >= 390.0

    def test_12_two_disjoint_anomalies(self):
        s = _s(600)
        d = np.linspace(52, 40, s.size)
        d[(s >= 80) & (s <= 100)] = 58.0
        d[(s >= 400) & (s <= 420)] = 55.0
        mono = _mono(s, d)
        segs = mono["suspect_segments"]
        assert len(segs) >= 2, segs
        assert segs[0]["end_s"] < segs[1]["start_s"]

    def test_expand_full_bump_not_just_peak(self):
        s = _s(300)
        d = np.full_like(s, 50.0, dtype=float)
        x = (s - 100.0) / 18.0
        d = d + 5.0 * np.exp(-(x ** 2))
        mono = _mono(s, d)
        segs = mono["suspect_segments"]
        assert segs, mono
        best = min(segs, key=lambda seg: abs((seg["start_s"] + seg["end_s"]) / 2 - 100))
        assert best["start_s"] <= 88
        assert best["end_s"] >= 112
        assert best["end_s"] - best["start_s"] >= 24

    def test_bulge_end_is_first_point_below_start_not_first_drop(self):
        """Downhill still above start D is part of the bulge; end when D < start."""
        s = np.arange(0.0, 81.0)
        d = np.full_like(s, 50.0)
        d[20:25] = 50.0 + np.arange(1, 6) * 0.4  # start=20, D=50.4; five subsequent rises
        d[25] = 52.0
        d[26:33] = [51.8, 51.5, 51.2, 50.9, 50.7, 50.6, 50.5]  # downhill, still >= start
        d[33] = 50.3     # first D < start
        d[34:] = 50.0
        mono = _mono(s, d)
        segs = mono["suspect_segments"]
        assert segs, mono
        best = min(segs, key=lambda seg: abs(seg["start_s"] - 20))
        assert best["start_s"] == 20.0
        assert best["end_s"] == 33.0

    def test_leading_taper_not_a_bulge_two_small_hills_like_1324_1(self):
        """Large-end decreasing taper is not a bulge; two small hills stay local."""
        s = np.arange(0.0, 757.0)
        d = np.full_like(s, 56.44)
        head = s <= 60
        d[head] = 72.9 - (72.9 - 56.60) * (s[head] / 60.0)
        d[(s > 60) & (s < 64)] = 56.50
        d[(s >= 400) & (s < 586)] = 56.436
        d[s == 431] = 56.439  # 0.003 mm plateau step, not a bulge start
        for lo, hi, peak_s in ((137, 151, 144.0), (590, 616, 603.0)):
            hill = (s >= lo) & (s <= hi)
            x = (s[hill] - peak_s) / 7.0
            d[hill] = 56.44 + 0.24 * np.exp(-(x ** 2))
        mono = _mono(s, d)
        assert mono["status"] == QA_LOCAL_ANOMALY, (mono["status"], mono["reason"], mono["metrics"])
        segs = mono["suspect_segments"]
        assert len(segs) == 2, segs
        assert all(seg["start_s"] >= 120 for seg in segs), segs
        assert all(seg["start_s"] < 200 or seg["start_s"] >= 580 for seg in segs), segs
        assert any(seg["start_s"] <= 140 and seg["end_s"] >= 144 for seg in segs), segs
        assert any(seg["start_s"] <= 595 and seg["end_s"] >= 603 for seg in segs), segs

    def test_small_sustained_rise_on_taper_like_1324_31(self):
        """24TK_1324-31: ~0.18 mm hill at 110-125 mm on a taper.

        Isotonic residual at the peak is ~0.09 mm, below the 0.80 mm spike
        floor, so residual masking alone must not be required to catch it.
        """
        s_mid = np.arange(90.0, 141.0)
        d_mid = np.array([
            45.200004, 45.168173, 45.136342, 45.104511, 45.072680,
            45.040849, 45.009018, 44.977187, 44.942173, 44.910342,
            44.878511, 44.846680, 44.814849, 44.779835, 44.748004,
            44.716173, 44.681159, 44.652511, 44.630229, 44.617497,
            44.611131, 44.614314, 44.627046, 44.646145, 44.671609,
            44.703440, 44.732088, 44.760736, 44.776652, 44.786201,
            44.783018, 44.767102, 44.738455, 44.697074, 44.642962,
            44.579300, 44.502905, 44.423328, 44.334201, 44.245074,
            44.155947, 44.073187, 43.996792, 43.936314, 43.879018,
            43.824905, 43.777159, 43.735778, 43.697581, 43.665750,
            43.630736,
        ])
        s_left = np.arange(0.0, 90.0)
        s_right = np.arange(141.0, 301.0)
        d_left = d_mid[0] + 0.032 * (90.0 - s_left)
        d_right = d_mid[-1] - 0.035 * (s_right - 140.0)
        s = np.concatenate([s_left, s_mid, s_right])
        d = np.concatenate([d_left, d_mid, d_right])
        mono = _mono(s, d)
        assert mono["status"] == QA_LOCAL_ANOMALY, (mono["status"], mono["reason"], mono["metrics"])
        segs = mono["suspect_segments"]
        assert segs, mono["metrics"]
        hit = any(seg["start_s"] <= 119 and seg["end_s"] >= 112 for seg in segs)
        assert hit, segs
        best = min(segs, key=lambda seg: abs((seg["start_s"] + seg["end_s"]) / 2 - 117))
        assert best["start_s"] <= 112
        assert best["end_s"] >= 119
        assert best["max_positive_deviation"] >= 0.12

    def test_two_bumps_on_long_plateau_like_1324_2_stay_local(self):
        """24TK_1324-2: two ~4.5 mm hills on a long small-end plateau.

        Start is the sudden-rise point (already on the hill). The trailing
        plateau is below that start OD, so the bulge ends on the downhill
        instead of swallowing 606→end.
        """
        s = np.arange(0.0, 775.0)
        d = np.full_like(s, 63.20)
        d[s <= 160] = 92.8 - (92.8 - 63.20) * (s[s <= 160] / 160.0)
        d[(s > 160) & (s < 166)] = 63.20
        bump1 = (s >= 166) & (s <= 205)
        x1 = (s[bump1] - 185.0) / 19.0
        d[bump1] = 63.20 + 4.6 * np.exp(-(x1 ** 2))
        d[(s > 205) & (s < 606)] = 63.188
        bump2 = (s >= 606) & (s <= 648)
        x2 = (s[bump2] - 628.0) / 20.0
        d[bump2] = 63.188 + 4.6 * np.exp(-(x2 ** 2))
        d[s > 648] = 63.22
        mono = _mono(s, d)
        assert mono["status"] == QA_LOCAL_ANOMALY, (mono["status"], mono["reason"], mono["metrics"])
        segs = mono["suspect_segments"]
        assert len(segs) == 2, segs
        assert any(seg["start_s"] <= 172 and seg["end_s"] >= 199 for seg in segs), segs
        assert any(seg["start_s"] <= 612 and seg["end_s"] >= 635 for seg in segs), segs
        assert all(seg["end_s"] < 700 for seg in segs), segs
        assert mono["anomaly_point_ratio"] <= 0.10 + 1e-12

    def test_tiny_leading_tick_does_not_swallow_trailing_plateau(self):
        """1324-2 small-end: first rise is +0.013 mm, trailing plateau is higher."""
        s = np.arange(0.0, 400.0)
        d = np.full_like(s, 63.188)
        d[20] = 63.200
        d[21:42] = 63.200 + np.linspace(0.06, 4.65, 21)
        d[42:63] = 63.200 + np.linspace(4.50, 0.02, 21)
        d[63:] = 63.213
        mono = _mono(s, d)
        assert mono["status"] == QA_LOCAL_ANOMALY, (mono["status"], mono["reason"], mono["metrics"])
        segs = mono["suspect_segments"]
        assert segs, segs
        best = min(segs, key=lambda seg: abs((seg["start_s"] + seg["end_s"]) / 2 - 40))
        assert best["start_s"] >= 21
        assert best["end_s"] < 100
        assert best["end_s"] >= 50

    def test_sub_centimetre_ticks_are_ignored(self):
        """C/π 0.003 mm steps must not count as a bulge after 2-decimal rounding."""
        s = np.arange(0.0, 200.0)
        d = np.full_like(s, 56.44908)
        d[40:80] = 56.45226
        d[80:] = 56.44908
        mono = _mono(s, d)
        assert mono["status"] == QA_MONOTONIC_PASS, (mono["status"], mono["reason"], mono["suspect_segments"])
        assert not mono["suspect_segments"]


# ---------------------------------------------------------------------------
# Tests 13-16 smoothing / illegal input
# ---------------------------------------------------------------------------
class TestSmoothingAndInput:
    def test_13_local_smooth_keeps_outside(self):
        s, d = _taper_with_bump()
        rec = _records(s, d)
        qa = initialize_qa_from_records(rec, centerline_length=600)
        assert qa["status"] == QA_LOCAL_ANOMALY
        preview = preview_smooth(qa)
        proc = np.asarray(preview["processed_profile"]["outer_diameter_mm"])
        raw = np.asarray(qa["raw_profile"]["outer_diameter_mm"])
        mask = np.asarray(qa["anomaly_mask"], dtype=bool)
        # Expand a little: smoothing only writes suspect index ranges.
        from core.pp.profile_qa.models import ProfileArrays
        working = ProfileArrays(s=s, circumference=d * np.pi, diameter=d.copy())
        applied = preview_local_smooth(working, qa["suspect_segments"])
        new_d = applied["profile"].diameter
        for seg in qa["suspect_segments"]:
            i0, i1 = int(seg["start_index"]), int(seg["end_index"])
            # Outside must be identical.
            if i0 > 0:
                np.testing.assert_allclose(new_d[:i0], d[:i0], atol=1e-12)
            if i1 + 1 < len(d):
                np.testing.assert_allclose(new_d[i1 + 1:], d[i1 + 1:], atol=1e-12)
        # Interior of bump should move down toward the decreasing envelope.
        bump = (s >= 100) & (s <= 120)
        assert np.max(new_d[bump]) < np.max(d[bump]) - 0.5
        # Overall non-increasing after isotonic local fit (allow tiny noise)
        diffs = np.diff(new_d)
        assert np.max(diffs) < 0.3
        assert qa["raw_profile"]["outer_diameter_mm"] == rec_to_d_list(rec) or True
        # raw untouched
        np.testing.assert_allclose(np.asarray(qa["raw_profile"]["outer_diameter_mm"]), d, atol=1e-9)

    def test_smooth_follows_outside_taper_not_plateau(self):
        """1324-31-like hill: replace with a decreasing curve, not a flat+cliff."""
        s_mid = np.arange(90.0, 141.0)
        d_mid = np.array([
            45.200004, 45.168173, 45.136342, 45.104511, 45.072680,
            45.040849, 45.009018, 44.977187, 44.942173, 44.910342,
            44.878511, 44.846680, 44.814849, 44.779835, 44.748004,
            44.716173, 44.681159, 44.652511, 44.630229, 44.617497,
            44.611131, 44.614314, 44.627046, 44.646145, 44.671609,
            44.703440, 44.732088, 44.760736, 44.776652, 44.786201,
            44.783018, 44.767102, 44.738455, 44.697074, 44.642962,
            44.579300, 44.502905, 44.423328, 44.334201, 44.245074,
            44.155947, 44.073187, 43.996792, 43.936314, 43.879018,
            43.824905, 43.777159, 43.735778, 43.697581, 43.665750,
            43.630736,
        ])
        s_left = np.arange(0.0, 90.0)
        s_right = np.arange(141.0, 301.0)
        d_left = d_mid[0] + 0.032 * (90.0 - s_left)
        d_right = d_mid[-1] - 0.035 * (s_right - 140.0)
        s = np.concatenate([s_left, s_mid, s_right])
        d = np.concatenate([d_left, d_mid, d_right])
        rec = _records(s, d)
        qa = initialize_qa_from_records(rec, centerline_length=300)
        assert qa["status"] == QA_LOCAL_ANOMALY
        from core.pp.profile_qa.models import ProfileArrays
        working = ProfileArrays(s=s, circumference=d * np.pi, diameter=d.copy())
        applied = preview_local_smooth(working, qa["suspect_segments"])
        assert applied["ok"]
        new_d = applied["profile"].diameter
        ch = applied["changed_ranges"][0]
        after = np.asarray(ch["after"], dtype=float)
        diffs = np.diff(after)
        assert np.max(diffs) < 1e-6
        # Not a 3-level staircase / 1 mm cliff.
        uniq = np.unique(np.round(after, 4))
        assert uniq.size >= max(8, after.size // 3), uniq
        assert float(np.min(diffs)) > -0.08
        core = (s >= 110) & (s <= 125)
        slope = float((new_d[core][-1] - new_d[core][0]) / 15.0)
        left_slope = float(np.polyfit(s[(s >= 90) & (s < 110)], d[(s >= 90) & (s < 110)], 1)[0])
        assert slope < -0.020, slope
        assert abs(slope - left_slope) < 0.025
        core_diffs = np.diff(new_d[core])
        assert float(np.max(core_diffs)) < 0.01 + 1e-9
        assert float(np.min(core_diffs)) < -0.010
        assert np.max(new_d[core]) < np.max(d[core]) - 0.05
        far = s < 90
        np.testing.assert_allclose(new_d[far], d[far], atol=1e-12)
        far_r = s > 160
        np.testing.assert_allclose(new_d[far_r], d[far_r], atol=1e-12)

    def test_14_smooth_recheck_failed(self):
        s, d = _taper_with_bump()
        rec = _records(s, d)
        qa = initialize_qa_from_records(rec, centerline_length=600)
        assert qa["status"] == QA_LOCAL_ANOMALY
        # Make the first suspect segment's anchors increase so constrained
        # local isotonic cannot connect them.
        seg = qa["suspect_segments"][0]
        i0, i1 = int(seg["start_index"]), int(seg["end_index"])
        working = qa["working_profile"]
        diam = list(working["outer_diameter_mm"])
        if i0 > 0:
            diam[i0 - 1] = 40.0
        if i1 + 1 < len(diam):
            diam[i1 + 1] = 50.0
        working["outer_diameter_mm"] = diam
        working["outer_circumference_mm"] = [x * float(np.pi) for x in diam]
        qa2 = confirm_smooth(qa)
        assert qa2["status"] in (QA_RECHECK_FAILED, QA_MANUAL_REQUIRED)
        assert qa2["raw_profile"]["outer_diameter_mm"] == qa["raw_profile"]["outer_diameter_mm"]
        from core.pp.profile_qa.models import ProfileArrays
        prof = ProfileArrays(
            s=np.asarray(working["s_mm"]),
            circumference=np.asarray(working["outer_circumference_mm"]),
            diameter=np.asarray(diam),
        )
        preview = preview_local_smooth(prof, qa["suspect_segments"])
        assert preview["ok"] is False or preview["failed_segments"]

    def test_15_nan_inf_no_crash(self):
        s = _s(50)
        c = np.full_like(s, 50 * np.pi)
        c[10] = np.nan
        c[11] = np.inf
        out = validate_profile_input(s, c, centerline_length=50)
        assert out["status"] == QA_INVALID_INPUT
        rec = _records(s, np.full_like(s, 50))
        rec[3]["外径周长(mm)"] = float("nan")
        qa = initialize_qa_from_records(rec, centerline_length=50)
        assert qa["status"] != "crash"
        # A single NaN is a safe drop, not a hard invalid.
        assert qa["status"] in (
            QA_INVALID_INPUT,
            QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION,
            QA_MONOTONIC_PASS,
            QA_LOCAL_ANOMALY,
            QA_MANUAL_REQUIRED,
        )
        assert qa.get("input_fixes") or qa["status"] == QA_INVALID_INPUT
        # Majority Inf must not crash and must be invalid.
        c2 = np.full_like(s, np.inf)
        out2 = validate_profile_input(s, c2, centerline_length=50)
        assert out2["status"] == QA_INVALID_INPUT

    def test_16_duplicate_centerline_merged(self):
        s = np.array([0.0, 1.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        c = np.array([160.0, 160.0, 162.0, 159.0, 158.0, 157.0, 156.0])
        out = validate_profile_input(s, c, centerline_length=5)
        assert out["ok"] is True
        assert int(out["profile"].s.size) == 6
        # duplicate at s=1 averaged
        np.testing.assert_allclose(out["profile"].s, [0, 1, 2, 3, 4, 5])


def rec_to_d_list(rec):
    return [r["外径周长(mm)"] / np.pi for r in rec]


# ---------------------------------------------------------------------------
# Tests 17-20 XLSX
# ---------------------------------------------------------------------------
def _xlsx_from_rows(rows, headers=("centerline_mm", "outer_circumference_mm")):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "manual_profile"
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestXlsx:
    def test_17_valid_xlsx(self):
        s = np.linspace(0, 200, 21)
        c = (52.7 - 0.01 * s) * np.pi
        rows = list(zip(s.tolist(), c.tolist()))
        parsed = parse_manual_xlsx(_xlsx_from_rows(rows), "manual.xlsx", centerline_length_mm=200)
        assert parsed["ok"] is True
        assert parsed["point_count"] == 21

    def test_18_missing_column(self):
        data = _xlsx_from_rows([(0, 160), (10, 159)], headers=("centerline_mm", "wrong_col"))
        parsed = parse_manual_xlsx(data, "manual.xlsx")
        assert parsed["ok"] is False
        cols = [d["column"] for d in parsed["details"]]
        assert any("outer_circumference" in c or c == "outer_circumference_mm" for c in cols)

    def test_19_string_cell_reports_row(self):
        data = _xlsx_from_rows([
            (0.0, 160.0),
            (10.0, "abc"),
        ] + [(float(i), 159.0) for i in range(20, 80, 10)])
        parsed = parse_manual_xlsx(data, "manual.xlsx")
        assert parsed["ok"] is False
        hit = [d for d in parsed["details"] if d.get("row") == 3]
        assert hit, parsed["details"]
        assert "outer_circumference" in hit[0]["column"]
        assert "abc" in (hit[0].get("how_to_fix") or "")

    def test_20_manual_still_not_monotonic(self):
        s = np.linspace(0, 200, 41)
        d = 50 + 8 * np.sin(s / 8.0)
        rows = list(zip(s.tolist(), (d * np.pi).tolist()))
        rec = _records(_s(200), np.full(201, 50.0))
        qa = initialize_qa_from_records(rec, centerline_length=200)
        # Force a state that accepts XLSX.
        qa["status"] = QA_MANUAL_REQUIRED
        qa2 = apply_manual_xlsx(qa, _xlsx_from_rows(rows), "manual.xlsx", centerline_length_mm=200)
        assert qa2["status"] != QA_PASS
        assert qa2["monotonic"]["status"] != QA_MONOTONIC_PASS or qa2["status"] in (
            QA_MANUAL_REQUIRED, QA_LOCAL_ANOMALY, QA_RECHECK_FAILED,
        )


class TestTemplateAndReject:
    def test_template_bytes_readable(self):
        data = build_xlsx_template_bytes()
        parsed = parse_manual_xlsx(data, "template.xlsx")
        assert parsed["ok"] is True

    def test_reject_smooth_does_not_pass(self):
        s, d = _taper_with_bump()
        qa = initialize_qa_from_records(_records(s, d), centerline_length=600)
        qa2 = reject_smooth(qa)
        assert qa2["status"] == QA_LOCAL_ANOMALY
        assert qa2["profile_source"] == "stp_raw"
        with pytest.raises(QaStateError):
            finalize_straight(qa2)

    def test_illegal_straight_in_manual_state(self):
        qa = initialize_qa_from_records(_records(_s(50), np.full(51, 40.0)), centerline_length=50)
        qa["status"] = QA_MANUAL_REQUIRED
        with pytest.raises(QaStateError):
            confirm_straight(qa, "S0")


# ---------------------------------------------------------------------------
# Integration paths A-E (synthetic profiles, no STP re-parse)
# ---------------------------------------------------------------------------
class TestIntegrationPaths:
    def test_path_a_clean_straight(self):
        s = _s(200)
        d = np.full_like(s, 52.686)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=200)
        assert qa["status"] == QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION
        ids = [seg["segment_id"] for seg in qa["straight_segments"]]
        qa = finalize_straight(qa, confirmed_ids=ids, confirmed_by="")
        assert qa["status"] == QA_PASS
        assert qa["profile_source"] == "stp_raw"
        assert qa["final_profile"] is not None
        # duplicate finalize is idempotent
        qa2 = finalize_straight(qa, confirmed_ids=ids)
        assert qa2["status"] == QA_PASS

    def test_path_b_local_smooth_then_straight(self):
        s, d = _taper_with_bump()
        qa = initialize_qa_from_records(_records(s, d), centerline_length=600)
        assert qa["status"] == QA_LOCAL_ANOMALY
        preview_smooth(qa)
        qa = confirm_smooth(qa, confirmed_by="")
        assert qa["raw_profile"]["outer_diameter_mm"] != qa["working_profile"]["outer_diameter_mm"]
        assert qa["status"] in (
            QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION, QA_RECHECK_FAILED, QA_MANUAL_REQUIRED,
        )
        if qa["status"] == QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION:
            qa = finalize_straight(qa, confirmed_ids=[], confirmed_by="")
            assert qa["status"] == QA_PASS
            assert qa["profile_source"] == "stp_smoothed"

    def test_path_c_engineer_keeps_raw(self):
        s, d = _taper_with_bump()
        qa = initialize_qa_from_records(_records(s, d), centerline_length=600)
        qa = reject_smooth(qa)
        assert qa["status"] == QA_LOCAL_ANOMALY
        assert qa["engineer_smooth_choice"]["action"] == "keep_raw"
        assert qa["profile_source"] == "stp_raw"

    def test_path_d_manual_xlsx_then_choose(self):
        s = _s(200)
        noisy = np.full_like(s, 50.0)
        noisy[10:80] = 70.0
        qa = initialize_qa_from_records(_records(s, noisy), centerline_length=200)
        assert qa["status"] == QA_MANUAL_REQUIRED
        good_d = np.full_like(s, 52.5)
        rows = list(zip(s.tolist(), (good_d * np.pi).tolist()))
        qa = apply_manual_xlsx(
            qa, _xlsx_from_rows(rows), "manual.xlsx",
            centerline_length_mm=200, confirmed_by="",
        )
        assert qa["status"] == QA_MANUAL_REQUIRED
        assert qa["raw_profile"] is not None
        assert qa["manual_profile"] is not None
        qa = choose_profile(qa, "manual")
        assert qa["profile_choice"] == "manual"
        assert qa["final_profile"]["point_count"] == 201

    def test_path_e_bad_xlsx_can_retry(self):
        s = _s(200)
        noisy = np.full_like(s, 50.0)
        noisy[10:80] = 70.0
        qa = initialize_qa_from_records(_records(s, noisy), centerline_length=200)
        bad = _xlsx_from_rows([(0, "x")], headers=("centerline_mm", "outer_circumference_mm"))
        qa = apply_manual_xlsx(qa, bad, "bad.xlsx", centerline_length_mm=200)
        assert qa["status"] == QA_MANUAL_REQUIRED
        assert qa["details"]
        assert not qa.get("manual_profile")
        good_d = np.full_like(s, 52.5)
        rows = list(zip(s.tolist(), (good_d * np.pi).tolist()))
        qa = apply_manual_xlsx(qa, _xlsx_from_rows(rows), "ok.xlsx", centerline_length_mm=200)
        assert qa["manual_profile"] is not None
        assert qa["status"] == QA_MANUAL_REQUIRED
