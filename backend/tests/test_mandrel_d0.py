"""普通抽素材外径由修改后壁厚和芯轴间隙反推，Dn 保持原算法。"""
from __future__ import annotations

import math
import os
import sys

import pandas as pd

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.legacy_opt.opt_nor_kuo import OPT_Nor_Kuo  # noqa: E402
from core.pp.legacy_opt.opt_tp import OPT_TP  # noqa: E402
from core.pp.mandrel_d0 import (  # noqa: E402
    d0_for_mandrel_clearance,
    modified_draw_thicknesses,
    scale_blank_length,
    solve_mandrel_d0,
)
from core.pp.optimization import recalc_solution_by_method  # noqa: E402


def _curve(n=30, thickness=2.0, diameter=40.0):
    perimeter = math.pi * diameter
    return [
        {
            "中心线长度(mm)": float(i),
            "壁厚(mm)": thickness,
            "外径周长(mm)": perimeter,
        }
        for i in range(n)
    ]


def _analysis(curve_thickness, input_thickness, d1=40, d3=30, n=40, length=39):
    return {
        "wall_thickness_data": pd.DataFrame(_curve(n, curve_thickness, (d1 + d3) / 2)),
        "segments": {
            "T1": input_thickness,
            "T2": input_thickness,
            "T3": input_thickness,
            "TL1": 10,
            "D1": d1,
            "D3": d3,
        },
        "volume": 200000,
        "centerline_length": length,
    }


def test_d0_uses_modified_curve_thickness_not_input_wall():
    rows = _curve(thickness=2.0)
    entered = {"T1": 9, "T2": 8, "T3": 7, "TL1": 10, "D1": 40, "D3": 30}
    blank = solve_mandrel_d0(
        rows,
        pp_method="普通抽+TP抽",
        dn=40,
        t0=3,
        d1=40,
        d3=30,
        d2=30,
        segments=entered,
        precision=1,
        provisional_d0=50,
    )
    assert blank == 43.8
    assert blank == d0_for_mandrel_clearance(40, 3, 2.0, 1)
    assert blank != d0_for_mandrel_clearance(40, 3, 7.0, 1)
    clearance = (blank - 2 * 3) - (40 - 2 * 2.0 + 0.3)
    assert abs(clearance - 1.5) < 1e-9


def test_d0_rejected_when_not_above_draw_diameter_or_cap():
    rows = _curve(thickness=2.0)
    too_small = solve_mandrel_d0(
        rows,
        pp_method="普通抽+TP抽",
        dn=40,
        t0=0.4,
        d1=40,
        d3=30,
        precision=1,
        provisional_d0=50,
    )
    over_cap = solve_mandrel_d0(
        rows,
        pp_method="普通抽+TP抽",
        dn=40,
        t0=3,
        d1=40,
        d3=30,
        precision=1,
        max_d0=43,
        provisional_d0=50,
    )
    assert too_small is None
    assert over_cap is None


def test_taper_without_d2_iterates_to_one_blank_diameter():
    rows = _curve(n=40, thickness=2.0)
    common = dict(
        wall_rows=rows,
        pp_method="普通抽+Taper",
        dn=30,
        t0=3,
        d1=40,
        d3=20,
        d2=0,
        l3=100,
        precision=1,
        max_d0=150,
    )
    from_high = solve_mandrel_d0(**common, provisional_d0=80)
    from_low = solve_mandrel_d0(**common, provisional_d0=35)
    assert from_high == from_low
    assert from_high is not None
    thicknesses = modified_draw_thicknesses(
        rows,
        {
            "D0": from_high,
            "Dn": 30,
            "D1": 40,
            "D2": 0,
            "D3": 20,
            "L1": 0,
            "L2": 0,
            "L3": 100,
            "T0": 3,
        },
        "普通抽+Taper",
    )
    assert from_high == d0_for_mandrel_clearance(30, 3, min(thicknesses), 1)
    seed_thickness = modified_draw_thicknesses(
        rows,
        {
            "D0": 80,
            "Dn": 30,
            "D1": 40,
            "D2": 0,
            "D3": 20,
            "L3": 100,
            "T0": 3,
        },
        "普通抽+Taper",
    )
    assert from_high != d0_for_mandrel_clearance(30, 3, min(seed_thickness), 1)


def test_normal_tp_search_keeps_dn_and_sets_d0_from_curve():
    optimizer = OPT_TP(_analysis(2.0, 1.2), d_precision=1, t_precision=1, l_precision=0, L4=0)
    solutions = optimizer.brute_force_optimize2()
    blank = solutions[0]
    assert blank["Dn"] == 40
    assert blank["D0"] == d0_for_mandrel_clearance(40, blank["T0"], 2.0, 1)
    assert blank["D0"] != d0_for_mandrel_clearance(40, blank["T0"], 1.2, 1)
    assert blank["L0"] > 0
    assert len({item["T0"] for item in solutions}) == len(solutions)


def test_normal_flaring_stores_d0_from_modified_thickness():
    analysis = _analysis(1.2, 1.5, d1=36, d3=24, n=80, length=79)
    optimizer = OPT_Nor_Kuo(
        analysis, d_precision=1, t_precision=1, l_precision=0, add3=0, L4=50,
    )
    solutions = optimizer.brute_force_optimize()
    blank = solutions[0]
    assert blank["Dn"] == 24
    rows = analysis["wall_thickness_data"].to_dict("records")
    thicknesses = modified_draw_thicknesses(
        rows, blank, "普通抽+扩口", segments=analysis["segments"],
    )
    assert thicknesses
    assert blank["D0"] == d0_for_mandrel_clearance(
        blank["Dn"], blank["T0"], min(thicknesses), 1,
    )
    assert blank["L0"] > 0


def test_recalc_replaces_hand_edited_d0():
    analysis = _analysis(2.0, 1.2)
    analysis["wall_thickness_data"] = analysis["wall_thickness_data"].to_dict("records")
    result = recalc_solution_by_method(
        analysis,
        pp_method="普通抽+TP抽",
        params={"D0": 80, "T0": 3, "Dn": 40},
        d_precision=1,
        t_precision=1,
        l_precision=0,
    )
    assert result["success"]
    assert result["updates"]["D0"] == 43.8
    assert result["updates"]["L0"] > 0


def test_preview_uses_modified_thickness_and_rewrites_blank_diameter():
    from flask import Flask
    from api import pp as pp_api

    app = Flask(__name__)
    app.register_blueprint(pp_api.pp_bp, url_prefix="/api/pp")
    client = app.test_client()
    session_id = "mandrel-d0-preview"
    pp_api._pp_sessions[session_id] = {
        "results": {
            "wall_thickness_data": _curve(thickness=2.0),
            "segments": {"T1": 9, "T2": 8, "T3": 7, "TL1": 12, "D1": 40, "D3": 30},
            "centerline_length": 29,
        }
    }
    try:
        response = client.post("/api/pp/design/preview", json={
            "session_id": session_id,
            "pp_method": "普通抽+TP抽",
            "taper_thick": False,
            "solution": {
                "D0": 50, "T0": 3, "Dn": 40, "D1": 40, "D3": 30, "D2": 30,
                "L0": 100, "L1": 0, "L2": 20, "L3": 0,
            },
        })
        body = response.get_json()
        assert body["success"]
        # 传入的 D0 是整数，沿用 0 位精度，43.8 四舍五入为 44
        assert body["solution_updates"]["D0"] == 44
        expected_l0 = round(scale_blank_length(100, 50, 44, 3), 0)
        assert body["solution_updates"]["L0"] == expected_l0
        values = {row["参数"]: row["值"] for row in body["all_data"]}
        assert float(values["T1"]) == 2.0
        assert float(values["TL1"]) == 29
        assert float(values["D0"]) == 44

        finer = client.post("/api/pp/design/preview", json={
            "session_id": session_id,
            "pp_method": "普通抽+TP抽",
            "taper_thick": False,
            "solution": {
                "D0": 50.5, "T0": 3, "Dn": 40, "D1": 40, "D3": 30, "D2": 30,
                "L0": 100, "L1": 0, "L2": 20, "L3": 0,
            },
        })
        finer_body = finer.get_json()
        assert finer_body["solution_updates"]["D0"] == 43.8

        kept = client.post("/api/pp/design/preview", json={
            "session_id": session_id,
            "pp_method": "仅TP抽",
            "taper_thick": False,
            "solution": {
                "D0": 40, "T0": 3, "Dn": 40, "D1": 40, "D3": 30,
                "L0": 100, "L1": 0, "L2": 20,
            },
        })
        kept_body = kept.get_json()
        kept_values = {row["参数"]: row["值"] for row in kept_body["all_data"]}
        assert float(kept_values["T1"]) == 9.0
        assert "D0" not in (kept_body.get("solution_updates") or {})
    finally:
        pp_api._pp_sessions.pop(session_id, None)
