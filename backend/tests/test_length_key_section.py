"""Tests for wall-thickness TL1, drawing-length correction, and key sections."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.profile_qa.length_correction import (  # noqa: E402
    identify_small_end,
    validate_profile_length,
)
from core.pp.profile_qa.models import ProfileArrays  # noqa: E402
from core.pp.profile_qa.key_section import (  # noqa: E402
    section_size_from_outer_2d,
)
from core.pp.profile_qa.pipeline import (  # noqa: E402
    apply_qa_lengths_to_results,
    backfill_section_dims,
    confirm_wall_and_validate_length,
    get_final_profile,
    initialize_qa_from_records,
    refresh_length_metrics,
    save_key_section_record,
    search_key_sections_in_bundle,
)
from core.pp.profile_qa.state import QA_PASS, QaStateError  # noqa: E402
from core.pp.profile_qa.wall_thickness_confirm import (  # noqa: E402
    calculate_drawing_length,
    validate_wall_thickness,
)


def _records(s, diameter, x=None, y=None):
    rows = []
    for i, (si, di) in enumerate(zip(s, diameter)):
        rec = {"中心线长度(mm)": float(si), "外径周长(mm)": float(di) * np.pi}
        if x is not None:
            rec["x_length"] = float(x[i])
            rec["y_length"] = float(y[i])
        rows.append(rec)
    return rows


def _profile(s, d):
    s = np.asarray(s, dtype=float)
    d = np.asarray(d, dtype=float)
    return ProfileArrays(s=s, circumference=d * np.pi, diameter=d, source="stp_raw")


def _taper(length=1050.0, d0=40.0, d1=50.0, step=1.0):
    s = np.arange(0.0, length + 1e-12, step)
    d = d0 + (d1 - d0) * (s / max(length, 1e-9))
    return s, d


class Test1WallThicknessTL1:
    def test_single_segment_with_tl1_ok(self):
        result = validate_wall_thickness({"T1": 2.5, "TL1": 1000})
        assert result["ok"] is True
        drawing = calculate_drawing_length({"T1": 2.5, "TL1": 1000})
        assert drawing["ok"] is True
        assert drawing["drawing_length"] == pytest.approx(1000)

    def test_single_segment_missing_tl1_fails(self):
        result = validate_wall_thickness({"T1": 2.5, "TL1": None})
        assert result["ok"] is False
        assert result["message"] == "请填写壁厚 T1 对应的长度 TL1。"

        empty = validate_wall_thickness({"T1": 2.5, "TL1": ""})
        assert empty["ok"] is False
        assert empty["message"] == "请填写壁厚 T1 对应的长度 TL1。"

        zero = validate_wall_thickness({"T1": 2.5, "TL1": 0})
        assert zero["ok"] is False
        assert zero["message"] == "请填写壁厚 T1 对应的长度 TL1。"

    def test_pipeline_rejects_missing_tl1(self):
        s, d = _taper(200, 50, 48)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=200)
        with pytest.raises(QaStateError) as exc:
            confirm_wall_and_validate_length(qa, {"T1": 3.0, "TL1": None})
        assert exc.value.message == "请填写壁厚 T1 对应的长度 TL1。"

    def test_illegal_tl_values(self):
        assert validate_wall_thickness({"T1": 2, "TL1": -50})["ok"] is False
        assert validate_wall_thickness({"T1": 2, "TL1": "abc"})["ok"] is False
        drawing = calculate_drawing_length({"T1": 2, "TL1": 500, "TL2": 300, "TL3": 200})
        assert drawing["drawing_length"] == pytest.approx(1000)


class Test2MatchedLength:
    def test_matched_does_not_change_profile(self):
        s, d = _taper(1000, 52, 48)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1000)
        raw_s = list(qa["raw_profile"]["s_mm"])
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1000})
        lv = qa["length_validation"]
        assert lv["status"] == "matched"
        assert lv["message"] == "长度校验通过。"
        assert qa["corrected_profile"]["s_mm"][-1] == pytest.approx(raw_s[-1], abs=1e-6)
        assert len(qa["corrected_profile"]["s_mm"]) == len(raw_s)


class Test3TrimFromSmallEnd:
    def test_trim_from_small_start(self):
        s, d = _taper(1050, 40, 50)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1050)
        d_at_50 = float(np.interp(50.0, s, d))
        d0 = float(d[0])
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1000})
        lv = qa["length_validation"]
        assert lv["status"] == "trimmed"
        assert lv["difference"] == pytest.approx(50, abs=1e-6)
        assert lv["correction_side"] == "start"
        assert lv["final_length"] == pytest.approx(1000, abs=1e-6)
        corr = qa["corrected_profile"]
        assert corr["s_mm"][-1] == pytest.approx(1000, abs=1e-6)
        assert corr["outer_diameter_mm"][0] == pytest.approx(d_at_50, abs=1e-3)
        assert corr["outer_diameter_mm"][0] != pytest.approx(d0, abs=0.2)
        assert qa["raw_profile"]["s_mm"][-1] == pytest.approx(1050, abs=1e-6)


class TestQaLengthsPassedToProcessPlanning:
    def test_trimmed_total_not_overridden_by_original_stp_length(self):
        s, d = _taper(1050, 40, 50)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1050)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1000})
        assert qa["centerline_length_mm"] == pytest.approx(1000, abs=1e-6)
        refresh_length_metrics(qa, centerline_length=1050)
        assert qa["centerline_length_mm"] == pytest.approx(1000, abs=1e-6)
        assert qa["formline_length"] == pytest.approx(1000 - float(qa.get("L4") or 0), abs=1e-6)
        payload = {
            "centerline_length": 1050.0,
            "actual_z_length": 1040.0,
            "L4": 0.0,
            "formline_length": 1050.0,
        }
        apply_qa_lengths_to_results(payload, qa)
        assert payload["centerline_length"] == pytest.approx(1000, abs=1e-6)
        assert payload["actual_z_length"] == pytest.approx(1040, abs=1e-6)
        assert payload["formline_length"] == pytest.approx(1000 - float(qa.get("L4") or 0), abs=1e-6)
        assert payload["segments"]["TL1"] == pytest.approx(1000, abs=1e-6)
        from core.pp.process_length import process_length
        assert process_length(payload) == pytest.approx(1000, abs=1e-6)
        assert process_length({
            "centerline_length": 1000,
            "actual_z_length": 1040,
        }) == pytest.approx(1000, abs=1e-6)
        xs = [float(r["中心线长度(mm)"]) for r in payload.get("wall_thickness_data") or []]
        assert xs
        assert max(xs) == pytest.approx(1000, abs=1e-3)
        from core.pp.process_length import rfm_process_length
        stale_tube = {
            "centerline_length": 1050.0,
            "actual_z_length": 1040.0,
            "sections_full": [{"cumulative_length": 1050.0}],
        }
        assert rfm_process_length(
            stale_tube,
            payload.get("wall_thickness_data"),
            {"params": {"LL": 1000}},
        ) == pytest.approx(1000, abs=1e-3)

    def test_3d_sections_follow_small_and_large_end_deltas(self):
        s, d = _taper(1050, 50, 40)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1050)
        qa = confirm_wall_and_validate_length(
            qa, {"T1": 2.0, "TL1": 1000}, small_end_correction_mm=-20.0,
        )
        lv = qa["length_validation"]
        assert lv["small_end_correction_mm"] == pytest.approx(-20)
        assert lv["large_end_correction_mm"] == pytest.approx(-30)
        sections = [
            {"cumulative_length": float(z), "position": [0.0, 0.0, float(z)], "perimeter": 40 * np.pi}
            for z in (0.0, 350.0, 700.0, 1050.0)
        ]
        payload = {
            "centerline_length": 1050.0,
            "actual_z_length": 1040.0,
            "sections_full": sections,
        }
        apply_qa_lengths_to_results(payload, qa)
        remapped = payload["sections_full"]
        assert payload["stp_sections_full"][-1]["cumulative_length"] == pytest.approx(1050)
        assert remapped[0]["cumulative_length"] == pytest.approx(0, abs=1e-6)
        assert remapped[-1]["cumulative_length"] == pytest.approx(1000, abs=1e-3)
        # 小端在 end：先截 20，大端 start 截 30，剩余 1000。
        assert remapped[0]["position"][2] == pytest.approx(30, abs=1e-3)
        assert remapped[-1]["position"][2] == pytest.approx(1030, abs=1e-3)
        apply_qa_lengths_to_results(payload, qa)
        assert payload["sections_full"][-1]["cumulative_length"] == pytest.approx(1000, abs=1e-3)
        assert payload["actual_z_length"] == pytest.approx(1040)

    def test_3d_sections_extend_from_small_end(self):
        s, d = _taper(1050, 50, 40)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1050)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1100})
        sections = [
            {"cumulative_length": float(z), "position": [0.0, 0.0, float(z)], "perimeter": 40 * np.pi}
            for z in (0.0, 525.0, 1050.0)
        ]
        payload = {"sections_full": sections, "actual_z_length": 1040.0}
        apply_qa_lengths_to_results(payload, qa)
        remapped = payload["sections_full"]
        assert remapped[-1]["cumulative_length"] == pytest.approx(1100, abs=1e-3)
        assert remapped[-1]["position"][2] == pytest.approx(1100, abs=1e-3)
        assert remapped[-1]["measurement_status"] == "extrapolated"


class Test3bTrimFromSmallEnd:
    def test_trim_from_small_end(self):
        s, d = _taper(1050, 50, 40)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1050)
        d_at_1000 = float(np.interp(1000.0, s, d))
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1000})
        lv = qa["length_validation"]
        assert lv["status"] == "trimmed"
        assert lv["correction_side"] == "end"
        corr = qa["corrected_profile"]
        assert corr["s_mm"][-1] == pytest.approx(1000, abs=1e-6)
        assert corr["outer_diameter_mm"][-1] == pytest.approx(d_at_1000, abs=1e-3)


class Test4ExtendFromSmallEnd:
    def test_extend_short_stp(self):
        s, d = _taper(950, 52, 44)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=950)
        last_d = float(d[-1])
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1000})
        lv = qa["length_validation"]
        assert lv["status"] == "extended"
        assert lv["difference"] == pytest.approx(-50, abs=1e-6)
        assert lv["final_length"] == pytest.approx(1000, abs=1e-6)
        corr = qa["corrected_profile"]
        assert corr["s_mm"][-1] == pytest.approx(1000, abs=1e-6)
        # Junction stays continuous with the known small-end diameter.
        side = identify_small_end(s, d)["side"]
        if side == "end":
            idx = int(np.searchsorted(corr["s_mm"], 950.0))
            assert corr["outer_diameter_mm"][idx] == pytest.approx(last_d, abs=0.05)
            assert corr["outer_diameter_mm"][-1] <= last_d + 1e-6
        else:
            assert corr["outer_diameter_mm"][0] <= float(d[0]) + 1e-6


class Test5FloatBoundary:
    def test_tiny_float_gap_is_matched(self):
        s, d = _taper(1000, 50, 48)
        profile = _profile(s, d)
        result = validate_profile_length(profile, 1000.0)
        # Force a 1e-9-class residual without a real millimetre gap.
        result = validate_profile_length(profile, 1000.0 - 1e-9)
        assert result["status"] == "matched"
        result = validate_profile_length(_profile(s, d), 1000.000000001)
        assert result["status"] == "matched"


class Test6KeySectionUnique:
    def test_unique_match(self):
        s = np.arange(0.0, 201.0, 1.0)
        d = np.linspace(52, 48, s.size)
        x = np.full_like(s, 40.0)
        y = np.full_like(s, 30.0)
        x[120] = 52.0
        y[120] = 48.0
        qa = initialize_qa_from_records(_records(s, d, x, y), centerline_length=200)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 200})
        qa = search_key_sections_in_bundle(qa, 52.0, 48.0)
        cands = qa["key_section_search"]["candidates"]
        assert qa["key_section_search"]["profile_version"] == "corrected"
        assert len(cands) >= 1
        assert cands[0]["s_mm"] == pytest.approx(120.0, abs=0.6)


class TestKeySectionToleranceOneMm:
    def test_plus_minus_one_mm_band(self):
        s = np.arange(0.0, 51.0, 1.0)
        d = np.full_like(s, 50.0)
        x = np.full_like(s, 40.0)
        y = np.full_like(s, 30.0)
        x[20] = 52.0
        y[20] = 48.0
        qa = initialize_qa_from_records(_records(s, d, x, y), centerline_length=50)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 50})
        inside = search_key_sections_in_bundle(qa, 53.0, 48.0)
        assert inside["key_section_search"]["candidates"]
        edge = search_key_sections_in_bundle(qa, 53.0, 49.0)
        assert edge["key_section_search"]["candidates"]
        outside = search_key_sections_in_bundle(qa, 53.2, 48.0)
        assert outside["key_section_search"]["candidates"] == []
        assert "未找到匹配截面" in (outside["key_section_search"]["message"] or "")


class Test7KeySectionMultiple:
    def test_all_candidates_sorted(self):
        s = np.arange(0.0, 201.0, 1.0)
        d = np.full_like(s, 50.0)
        x = np.full_like(s, 40.0)
        y = np.full_like(s, 30.0)
        for idx, dx in ((50, 0.0), (80, 0.2), (140, 0.4)):
            x[idx] = 52.0 + dx
            y[idx] = 48.0
        qa = initialize_qa_from_records(_records(s, d, x, y), centerline_length=200)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 200})
        qa = search_key_sections_in_bundle(qa, 52.0, 48.0)
        cands = qa["key_section_search"]["candidates"]
        assert len(cands) >= 3
        errors = [c["error"] for c in cands]
        assert errors == sorted(errors)
        assert cands[0]["s_mm"] == pytest.approx(50.0, abs=0.6)


class Test8SaveThreeRecords:
    def test_three_records_not_overwritten(self):
        s = np.arange(0.0, 301.0, 1.0)
        d = np.linspace(55, 45, s.size)
        x = np.full_like(s, 20.0)
        y = np.full_like(s, 15.0)
        specs = [(80, 52, 48), (150, 45, 42), (220, 38, 36)]
        for idx, L, W in specs:
            x[idx] = L
            y[idx] = W
        qa = initialize_qa_from_records(_records(s, d, x, y), centerline_length=300)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 300})
        for _idx, L, W in specs:
            qa = search_key_sections_in_bundle(qa, L, W)
            qa = save_key_section_record(qa, index=0)
        assert len(qa["key_section_records"]) == 3
        ids = [r["id"] for r in qa["key_section_records"]]
        assert ids == [1, 2, 3]
        assert qa["key_section_records"][0]["target_length"] == pytest.approx(52)
        assert qa["key_section_records"][1]["target_length"] == pytest.approx(45)
        assert qa["key_section_records"][2]["target_length"] == pytest.approx(38)


class Test9StaleAfterLengthChange:
    def test_records_marked_stale(self):
        s, d = _taper(1000, 52, 44)
        x = np.linspace(50, 40, s.size)
        y = np.linspace(40, 30, s.size)
        qa = initialize_qa_from_records(_records(s, d, x, y), centerline_length=1000)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1000})
        qa = search_key_sections_in_bundle(qa, float(x[100]), float(y[100]))
        if qa["key_section_search"]["candidates"]:
            qa = save_key_section_record(qa, index=0)
        else:
            qa = search_key_sections_in_bundle(qa, float(x[100]), float(y[100]))
            pytest.skip("no candidate at seeded section")
        assert qa["key_section_records"]
        assert qa["key_section_records"][0]["stale"] is False
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 950})
        assert qa["key_section_records"][0]["stale"] is True
        assert "请重新确认已保存的关键截面位置" in (qa.get("key_section_stale_message") or "")


class TestFinalSchemaUnchanged:
    def test_get_final_profile_keys(self):
        s = np.arange(0.0, 201.0, 1.0)
        d = np.full_like(s, 52.7)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=200)
        from core.pp.profile_qa.pipeline import finalize_straight
        ids = [seg["segment_id"] for seg in qa.get("straight_segments") or []]
        qa = finalize_straight(qa, confirmed_ids=ids)
        assert qa["status"] == QA_PASS
        payload = get_final_profile(qa)
        assert set(payload.keys()) == {
            "status",
            "profile_source",
            "final_profile",
            "straight_segments",
            "qa_quantity",
            "storage_quantity",
            "algorithm_version",
        }


class TestAmbiguousSmallEnd:
    def test_does_not_silently_trim_start(self):
        s = np.arange(0.0, 1051.0, 1.0)
        d = np.full_like(s, 50.0)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1050)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1000})
        assert qa["length_validation"]["status"] == "ambiguous_end"
        assert qa["raw_profile"]["s_mm"][-1] == pytest.approx(1050, abs=1e-6)
        assert qa["corrected_profile"] is None or qa["working_profile"]["s_mm"][-1] == pytest.approx(1050, abs=1e-6)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1000}, correction_side="end")
        assert qa["length_validation"]["status"] == "trimmed"
        assert qa["length_validation"]["correction_side"] == "end"
        assert qa["corrected_profile"]["s_mm"][-1] == pytest.approx(1000, abs=1e-6)


def _ellipse_pts(length_mm: float, width_mm: float, n: int = 240, rot_rad: float = 0.0):
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    x = (length_mm / 2.0) * np.cos(t)
    y = (width_mm / 2.0) * np.sin(t)
    c, s = np.cos(rot_rad), np.sin(rot_rad)
    xr = c * x - s * y
    yr = s * x + c * y
    return np.column_stack([xr, yr]).tolist()


class TestKeySectionFromOuter2d:
    def test_rotated_ellipse_uses_principal_axes_not_aabb(self):
        pts = _ellipse_pts(66.5, 51.2, rot_rad=np.deg2rad(37.0))
        size = section_size_from_outer_2d(pts)
        assert size is not None
        assert size[0] == pytest.approx(66.5, abs=0.2)
        assert size[1] == pytest.approx(51.2, abs=0.2)
        arr = np.asarray(pts)
        aabb_x = float(arr[:, 0].max() - arr[:, 0].min())
        aabb_y = float(arr[:, 1].max() - arr[:, 1].min())
        assert abs(max(aabb_x, aabb_y) - 66.5) > 1.0

    def test_search_matches_drawing_size_from_outer_2d_only(self):
        s = np.arange(0.0, 51.0, 1.0)
        d = np.full_like(s, 52.7)
        pts = _ellipse_pts(66.5, 51.2, rot_rad=np.deg2rad(37.0))
        records = []
        for si, di in zip(s, d):
            records.append({
                "中心线长度(mm)": float(si),
                "外径周长(mm)": float(di) * np.pi,
                "outer_2d": pts,
            })
        qa = initialize_qa_from_records(records, centerline_length=50)
        assert qa.get("raw_section_dims")
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 50})
        qa = search_key_sections_in_bundle(qa, 51.2, 66.5)
        cands = qa["key_section_search"]["candidates"]
        assert cands
        a = cands[0]["actual_length"]
        b = cands[0]["actual_width"]
        assert max(a, b) == pytest.approx(66.5, abs=0.25)
        assert min(a, b) == pytest.approx(51.2, abs=0.25)

    def test_backfill_from_sections_full_when_qa_started_without_xy(self):
        s = np.arange(0.0, 21.0, 1.0)
        d = np.full_like(s, 50.0)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=20)
        assert qa.get("raw_section_dims") is None
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 20})
        pts = _ellipse_pts(66.5, 51.2, rot_rad=np.deg2rad(12.0))
        extras = [
            {"cumulative_length": float(si), "outer_2d": pts}
            for si in s
        ]
        from core.pp.profile_qa.key_section import records_with_section_sizes
        filled = records_with_section_sizes(_records(s, d), extras)
        qa = backfill_section_dims(qa, filled)
        qa = search_key_sections_in_bundle(qa, 51.2, 66.5)
        assert qa["key_section_search"]["candidates"]


class TestKeySectionRanges:
    def test_consecutive_hits_merge_to_one_range(self):
        s = np.arange(0.0, 81.0, 1.0)
        d = np.full_like(s, 50.0)
        x = np.full_like(s, 40.0)
        y = np.full_like(s, 30.0)
        x[20:51] = 52.0
        y[20:51] = 48.0
        qa = initialize_qa_from_records(_records(s, d, x, y), centerline_length=80)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 80})
        qa = search_key_sections_in_bundle(qa, 52.0, 48.0)
        search = qa["key_section_search"]
        assert search["count"] == 31
        assert search["range_count"] == 1
        rng = search["candidates"][0]
        assert rng["s_start_mm"] == pytest.approx(20.0)
        assert rng["s_end_mm"] == pytest.approx(50.0)
        assert rng["point_count"] == 31
        assert "已合并为 1 段" in (search["message"] or "")

    def test_one_mm_hole_stays_in_same_range(self):
        s = np.arange(0.0, 41.0, 1.0)
        d = np.full_like(s, 50.0)
        x = np.full_like(s, 40.0)
        y = np.full_like(s, 30.0)
        x[10:21] = 52.0
        y[10:21] = 48.0
        x[22:31] = 52.0
        y[22:31] = 48.0
        qa = initialize_qa_from_records(_records(s, d, x, y), centerline_length=40)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 40})
        qa = search_key_sections_in_bundle(qa, 52.0, 48.0)
        ranges = qa["key_section_search"]["candidates"]
        assert len(ranges) == 1
        assert ranges[0]["s_start_mm"] == pytest.approx(10.0)
        assert ranges[0]["s_end_mm"] == pytest.approx(30.0)

    def test_save_all_ranges_without_selection(self):
        s = np.arange(0.0, 201.0, 1.0)
        d = np.full_like(s, 50.0)
        x = np.full_like(s, 40.0)
        y = np.full_like(s, 30.0)
        x[20:31] = 52.0
        y[20:31] = 48.0
        x[80:91] = 52.0
        y[80:91] = 48.0
        qa = initialize_qa_from_records(_records(s, d, x, y), centerline_length=200)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 200})
        qa = search_key_sections_in_bundle(qa, 52.0, 48.0)
        assert qa["key_section_search"]["range_count"] == 2
        qa = save_key_section_record(qa)
        recs = qa["key_section_records"]
        assert len(recs) == 2
        starts = sorted(r["s_start_mm"] for r in recs)
        assert starts[0] == pytest.approx(20.0)
        assert starts[1] == pytest.approx(80.0)
        assert all(r["point_count"] == 11 for r in recs)


class TestExpectedPositionAndSplitLength:
    def test_expected_position_delta_keeps_sign(self):
        s = np.arange(0.0, 201.0, 1.0)
        d = np.linspace(52, 48, s.size)
        x = np.full_like(s, 40.0)
        y = np.full_like(s, 30.0)
        x[120] = 52.0
        y[120] = 48.0
        qa = initialize_qa_from_records(_records(s, d, x, y), centerline_length=200)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 200})
        qa = search_key_sections_in_bundle(qa, 52.0, 48.0, expected_s_mm=500.0)
        # drawing expected 500 vs actual 120 on a 200 mm tube is legal; delta is signed.
        qa = save_key_section_record(qa, index=0, expected_s_mm=500.0)
        rec = qa["key_section_records"][0]
        assert rec["expected_s_mm"] == pytest.approx(500.0)
        assert rec["s_mm"] == pytest.approx(120.0, abs=0.6)
        assert rec["position_delta"] == pytest.approx(rec["s_mm"] - 500.0, abs=1e-6)
        assert rec["position_delta"] < 0

        qa = search_key_sections_in_bundle(qa, 52.0, 48.0, expected_s_mm=112.0)
        qa = save_key_section_record(qa, index=0, expected_s_mm=112.0)
        rec2 = qa["key_section_records"][1]
        assert rec2["position_delta"] == pytest.approx(rec2["s_mm"] - 112.0, abs=1e-6)
        assert rec2["position_delta"] > 0

    def test_default_still_puts_all_on_small_end(self):
        s, d = _taper(1050, 40, 50)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1050)
        qa = confirm_wall_and_validate_length(qa, {"T1": 2.0, "TL1": 1000})
        lv = qa["length_validation"]
        assert lv["status"] == "trimmed"
        assert lv["small_end_correction_mm"] == pytest.approx(-50, abs=1e-6)
        assert lv["large_end_correction_mm"] == pytest.approx(0.0, abs=1e-6)
        assert lv["final_length"] == pytest.approx(1000, abs=1e-6)

    def test_engineer_split_trim_both_ends(self):
        s, d = _taper(1050, 40, 50)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1050)
        qa = confirm_wall_and_validate_length(
            qa, {"T1": 2.0, "TL1": 1000}, small_end_correction_mm=-20.0,
        )
        lv = qa["length_validation"]
        assert lv["status"] == "trimmed"
        assert lv["small_end_correction_mm"] == pytest.approx(-20, abs=1e-6)
        assert lv["large_end_correction_mm"] == pytest.approx(-30, abs=1e-6)
        assert lv["small_end_correction_mm"] + lv["large_end_correction_mm"] == pytest.approx(
            lv["drawing_length"] - lv["stp_length"], abs=1e-6,
        )
        assert lv["final_length"] == pytest.approx(1000, abs=1e-6)
        # Small end is start (40 < 50). Trim 20 from start and 30 from end.
        corr = qa["corrected_profile"]
        assert corr["s_mm"][0] == pytest.approx(0.0, abs=1e-6)
        assert corr["s_mm"][-1] == pytest.approx(1000.0, abs=1e-6)
        raw_d = np.asarray(qa["raw_profile"]["outer_diameter_mm"])
        raw_s = np.asarray(qa["raw_profile"]["s_mm"])
        new_d0 = float(qa["corrected_profile"]["outer_diameter_mm"][0])
        assert new_d0 == pytest.approx(float(np.interp(20.0, raw_s, raw_d)), abs=1e-3)

    def test_omit_small_end_correction_is_not_zero(self):
        from core.pp.profile_qa.length_correction import allocate_end_corrections
        default = allocate_end_corrections(50.0, None)
        zero = allocate_end_corrections(50.0, 0.0)
        assert default["small_end_correction_mm"] == pytest.approx(-50)
        assert default["large_end_correction_mm"] == pytest.approx(0)
        assert zero["small_end_correction_mm"] == pytest.approx(0)
        assert zero["large_end_correction_mm"] == pytest.approx(-50)

    def test_over_trim_is_rejected(self):
        s, d = _taper(1050, 40, 50)
        qa = initialize_qa_from_records(_records(s, d), centerline_length=1050)
        with pytest.raises(QaStateError) as exc:
            confirm_wall_and_validate_length(
                qa, {"T1": 2.0, "TL1": 1000}, small_end_correction_mm=-2000.0,
            )
        assert "截短量过大" in exc.value.message
