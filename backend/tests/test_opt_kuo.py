"""仅扩口：固定赋值，且不改修改后壁厚。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.legacy_opt.opt_kuo import OPT_Kuo  # noqa: E402
from core.pp.modify_thick import apply_modify_thick  # noqa: E402
from core.pp.optimization import (  # noqa: E402
    optimize_process_parameters_by_method,
    recommend_pp_method,
    recalc_solution_by_method,
)


def _analysis():
    s = np.arange(0, 121, 1.0)
    d = 30.0 + 4.0 * s / 120.0
    records = [
        {
            "中心线长度(mm)": float(si),
            "外径周长(mm)": float(di * np.pi),
            "壁厚(mm)": 1.2 if si < 60 else 1.8,
        }
        for si, di in zip(s, d)
    ]
    return {
        "wall_thickness_data": records,
        "segments": {
            "D1": 34.0, "D3": 30.0,
            "T1": 1.2, "T2": 1.8, "T3": 0, "T4": 1.5,
            "TL1": 120,
        },
        "centerline_length": 120.0,
        "volume": 20000.0,
        "L4": 20.0,
    }


def _ready():
    data = _analysis()
    data["wall_thickness_data"] = pd.DataFrame(data["wall_thickness_data"])
    return data


def test_only_flaring_assigns_d0_d1_and_max_wall():
    optimizer = OPT_Kuo(_ready(), d_precision=1, t_precision=1, l_precision=0, L4=20)
    solution = optimizer.brute_force_optimize()[0]
    assert solution["D0"] == 30.0
    assert solution["D3"] == 30.0
    assert solution["D1"] == 34.0
    assert solution["T0"] == 1.8
    assert solution["Ln"] == 0
    assert solution["L1"] == 100
    assert solution["L0"] > 0


def test_recalc_restores_fixed_diameters():
    out = recalc_solution_by_method(
        _analysis(),
        pp_method="仅扩口",
        params={"D0": 99, "T0": 9.9},
        d_precision=1,
        t_precision=1,
        l_precision=0,
    )
    assert out["success"] is True
    assert out["updates"]["D0"] == 30.0
    assert out["updates"]["T0"] == 1.8
    assert out["updates"]["D1"] == 34.0
    assert out["updates"]["Ln"] == 0


def test_optimize_route_returns_one_solution():
    result = optimize_process_parameters_by_method(
        _analysis(), pp_method="仅扩口", d_precision=1, t_precision=1, l_precision=0,
    )
    assert result["status"] == "success"
    assert len(result["solutions"]) == 1
    assert result["solutions"][0]["D0"] == result["solutions"][0]["D3"]


def test_modified_wall_table_is_unchanged():
    raw = _analysis()
    solution = OPT_Kuo(_ready(), d_precision=1, t_precision=1, l_precision=0, L4=20).brute_force_optimize()[0]
    original = [row["壁厚(mm)"] for row in raw["wall_thickness_data"]]
    modified, _ = apply_modify_thick(raw["wall_thickness_data"], solution, "仅扩口", segments=raw["segments"])
    assert list(modified["壁厚(mm)"]) == original


def test_recommend_marks_only_flaring_without_changing_choice():
    short = recommend_pp_method(120, 34, 30, L4=0)
    assert short["recommended"] == "tp"
    flaring = next(item for item in short["all_options"] if item["method"] == "only-flaring")
    assert flaring["feasible"] is True

    long_change = recommend_pp_method(120, 50, 30, L4=0)
    flaring = next(item for item in long_change["all_options"] if item["method"] == "only-flaring")
    assert flaring["feasible"] is False
    assert long_change["recommended"] == "normal+flaring"

    longer = recommend_pp_method(200, 34, 30, L4=0)
    flaring = next(item for item in longer["all_options"] if item["method"] == "only-flaring")
    assert flaring["feasible"] is False


def test_missing_wall_raises():
    analysis = _ready()
    analysis["segments"]["T1"] = 0
    analysis["segments"]["T2"] = 0
    analysis["segments"]["T4"] = 0
    analysis["wall_thickness_data"] = analysis["wall_thickness_data"].assign(**{"壁厚(mm)": 0})
    try:
        OPT_Kuo(analysis, L4=20)
    except ValueError as exc:
        assert "壁厚" in str(exc)
    else:
        raise AssertionError("无效壁厚应报错")
