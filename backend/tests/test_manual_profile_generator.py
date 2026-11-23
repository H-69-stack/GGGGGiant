"""Standalone manual-profile generator: sparse knots, plateaus, PCHIP, monotone."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.manual_profile import generate_manual_profile  # noqa: E402
from core.pp.profile_qa.pipeline import (  # noqa: E402
    apply_manual_xlsx,
    get_final_profile,
    initialize_qa_from_records,
)
from core.pp.profile_qa.state import QA_PASS  # noqa: E402


def _s(length=200, step=1.0):
    return np.arange(0.0, length + 1e-12, step)


def _records(s, diameter):
    return [
        {"中心线长度(mm)": float(si), "外径周长(mm)": float(di) * np.pi}
        for si, di in zip(s, diameter)
    ]


def _xlsx_from_rows(rows, headers=("centerline_mm", "outer_circumference_mm")):
    from openpyxl import Workbook
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = "manual_profile"
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestManualProfileGenerator:
    def test_does_not_mutate_raw_xlxs(self):
        raw = [[300, 48.0], [0, 52.0], [150, 51.0]]
        snapshot = [list(p) for p in raw]
        result = generate_manual_profile(raw)
        assert result["ok"] is True
        assert raw == snapshot
        assert result["raw_xlxs"] == snapshot

    def test_sorts_uneven_x(self):
        result = generate_manual_profile([[300, 48], [0, 52], [150, 51]])
        assert result["ok"]
        knots = result["normalized_control_points"]
        xs = [p[0] for p in knots]
        assert xs == sorted(xs)
        assert knots[0][1] == pytest.approx(52)
        assert knots[-1][1] == pytest.approx(48)

    def test_sparse_control_points_are_legal(self):
        xlxs = [[0, 52], [35, 52], [120, 50], [285, 48], [310, 48], [580, 46], [920, 44]]
        result = generate_manual_profile(xlxs)
        assert result["ok"]
        s = np.asarray(result["manual_profile"]["s_mm"])
        assert s[0] == pytest.approx(0.0)
        assert s[-1] == pytest.approx(920.0)
        assert len(s) > len(xlxs)

    def test_straight_segment_is_exactly_constant(self):
        result = generate_manual_profile([[0, 52.7], [120, 52.7], [200, 48.0]])
        assert result["ok"]
        s = np.asarray(result["manual_profile"]["s_mm"])
        d = np.asarray(result["manual_profile"]["outer_diameter_mm"])
        plateau = d[s <= 120.0 + 1e-9]
        assert np.max(plateau) - np.min(plateau) < 1e-9
        assert plateau[0] == pytest.approx(52.7, abs=1e-9)
        kinds = [seg["kind"] for seg in result["segments"]]
        assert kinds[0] == "constant"
        assert kinds[1] == "taper"

    def test_taper_is_monotone_non_increasing(self):
        result = generate_manual_profile([[0, 52.0], [80, 50.0], [200, 44.0]])
        d = np.asarray(result["manual_profile"]["outer_diameter_mm"])
        assert result["monotone"] is True
        assert np.all(np.diff(d) <= 1e-9)

    def test_pchip_uses_true_knot_locations(self):
        early = generate_manual_profile([[0, 52.0], [10, 50.0], [200, 44.0]])
        late = generate_manual_profile([[0, 52.0], [190, 50.0], [200, 44.0]])
        s_e = np.asarray(early["manual_profile"]["s_mm"])
        d_e = np.asarray(early["manual_profile"]["outer_diameter_mm"])
        s_l = np.asarray(late["manual_profile"]["s_mm"])
        d_l = np.asarray(late["manual_profile"]["outer_diameter_mm"])
        d_early_100 = float(np.interp(100.0, s_e, d_e))
        d_late_100 = float(np.interp(100.0, s_l, d_l))
        # Same three y values; if spacing were ignored the curves would match.
        assert d_early_100 != pytest.approx(d_late_100, abs=0.2)
        assert 10.0 in np.round(s_e, 6)
        assert 190.0 in np.round(s_l, 6)

    def test_reverses_small_to_large_input(self):
        result = generate_manual_profile([[0, 40.0], [100, 50.0]])
        assert result["reversed"] is True
        knots = result["normalized_control_points"]
        assert knots[0][1] == pytest.approx(50.0)
        assert knots[-1][1] == pytest.approx(40.0)
        d = np.asarray(result["manual_profile"]["outer_diameter_mm"])
        assert d[0] > d[-1]
        assert np.all(np.diff(d) <= 1e-9)

    def test_rejects_nan_and_too_few_points(self):
        bad = generate_manual_profile([[0, 50.0], [10, float("nan")]])
        assert bad["ok"] is False
        few = generate_manual_profile([[0, 50.0]])
        assert few["ok"] is False
        text = generate_manual_profile([[0, 50.0], [10, "abc"]])
        assert text["ok"] is False

    def test_clips_local_rise(self):
        result = generate_manual_profile([[0, 50.0], [40, 51.0], [80, 48.0]])
        assert result["ok"]
        ys = [p[1] for p in result["constrained_control_points"]]
        assert ys[1] <= ys[0] + 1e-12
        d = np.asarray(result["manual_profile"]["outer_diameter_mm"])
        assert np.all(np.diff(d) <= 1e-9)


class TestManualProfileWiredThroughXlsx:
    def test_sparse_xlsx_becomes_dense_formline(self):
        s = _s(200)
        qa = initialize_qa_from_records(_records(s, np.full_like(s, 50.0)), centerline_length=200)
        qa["status"] = "QA_MANUAL_REQUIRED"
        rows = [(0.0, 52.7 * np.pi), (80.0, 52.7 * np.pi), (200.0, 48.0 * np.pi)]
        qa = apply_manual_xlsx(qa, _xlsx_from_rows(rows), "sparse.xlsx", centerline_length_mm=200)
        prof = qa["manual_profile"]
        assert prof is not None
        assert prof["s_mm"][0] == pytest.approx(0.0)
        assert prof["s_mm"][-1] == pytest.approx(200.0)
        assert prof["point_count"] > 3
        d = np.asarray(prof["outer_diameter_mm"])
        assert np.all(np.diff(d) <= 1e-9)
        gen = qa["manual_generation"]
        assert gen["control_point_count"] == 3
        plateau = d[np.asarray(prof["s_mm"]) <= 80.0 + 1e-9]
        assert np.max(plateau) - np.min(plateau) < 1e-6

    def test_final_profile_keys_unchanged(self):
        s = _s(80)
        qa = initialize_qa_from_records(_records(s, np.full_like(s, 52.7)), centerline_length=80)
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
