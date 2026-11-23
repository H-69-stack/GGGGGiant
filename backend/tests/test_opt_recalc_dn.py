"""应用更改：各工艺路线是否缺隐藏决策变量（Dn/D2/D20）。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.optimization import recalc_solution_by_method  # noqa: E402

# 与 Tab3Optimization.vue METHOD_META.editable 对齐
UI_EDITABLE = {
    "Taper+TP抽+扩口": ["D0", "T0", "D2"],
    "普通抽+TP抽+Taper+扩口": ["D0", "T0", "D2"],
    "仅TP抽": ["D0", "T0"],
    "普通抽+TP抽": ["D0", "T0"],
    "仅扩口": ["D0", "T0"],
    "普通抽+扩口": ["D0", "T0"],
    "TP抽+扩口": ["D0", "T0"],
    "普通抽+Taper+扩口": ["D0", "T0"],
    "普通抽+Taper": ["D0", "T0"],
}

# 当前方案里优化器会写出、重算可能要用的字段
SOLUTIONS = {
    "Taper+TP抽+扩口": {"D0": 42, "T0": 2.0, "D2": 36},
    "普通抽+TP抽+Taper+扩口": {"D0": 42, "T0": 2.0, "D2": 36, "Dn": 40},
    "加长Taper+TP抽+扩口": {"D0": 42, "T0": 2.0, "D2": 36, "D20": 39},
    "仅TP抽": {"D0": 49, "T0": 2.0},
    "普通抽+TP抽": {"D0": 49, "T0": 2.0},
    "仅扩口": {"D0": 40, "T0": 2.1},
    "普通抽+扩口": {"D0": 40, "T0": 2.1},
    "TP抽+扩口": {"D0": 42, "T0": 2.0},
    "普通抽+Taper+扩口": {"D0": 46, "T0": 2.1, "Dn": 45, "D2": 45},
    "普通抽+Taper": {"D0": 49, "T0": 2.0},
}

APPLY_HIDDEN = ("D0", "D2", "D20", "Dn", "T0")


def _analysis_input():
    s = np.arange(0, 613, 1.0)
    d = 47.67 - (47.67 - 32.88) * s / 612.0
    records = [
        {
            "中心线长度(mm)": float(si),
            "外径周长(mm)": float(di * np.pi),
            "壁厚(mm)": 1.7,
        }
        for si, di in zip(s, d)
    ]
    return {
        "wall_thickness_data": records,
        "segments": {
            "D1": 47.67, "D3": 32.88,
            "T1": 1.7, "T2": 1.7, "T3": 1.7, "T4": 0, "TL1": 612.39,
        },
        "centerline_length": 612.39,
        "actual_z_length": 611.0,
        "volume": 127058.45,
        "L4": 0,
    }


def _editable_only(method: str) -> dict:
    sol = SOLUTIONS[method]
    keys = UI_EDITABLE.get(method, ["D0", "T0"])
    params = {k: sol[k] for k in keys if k in sol}
    params["T0"] = round(float(params.get("T0", sol["T0"])) + 0.1, 1)
    if "D0" in params:
        params["D0"] = float(params["D0"])
    return params


def _apply_like_ui(method: str) -> dict:
    """与前端 applyRecalc 一致：当前方案 D0/D2/D20/Dn/T0 + 可编辑覆盖。"""
    sol = SOLUTIONS[method]
    params = {}
    for k in APPLY_HIDDEN:
        if sol.get(k) not in (None, ""):
            params[k] = sol[k]
    edited = _editable_only(method)
    params.update(edited)
    return params


def _recalc(method: str, params: dict) -> dict:
    return recalc_solution_by_method(_analysis_input(), pp_method=method, params=params)


def test_taper_kuo_recalc_without_dn_fails():
    out = _recalc("普通抽+Taper+扩口", {"D0": 46, "T0": 2.1})
    assert out["success"] is False
    assert "Dn" in str(out.get("error", ""))


def test_taper_kuo_recalc_keeps_current_dn():
    out = _recalc("普通抽+Taper+扩口", {"D0": 46, "T0": 2.1, "Dn": 45})
    assert out["success"] is True
    assert out["updates"]["Dn"] == 45


def test_taper_kuo_recalc_dn_from_d2():
    out = _recalc("普通抽+Taper+扩口", {"D0": 46, "T0": 2.1, "D2": 45})
    assert out["success"] is True
    assert out["updates"]["Dn"] == 45


@pytest.mark.parametrize("method", list(SOLUTIONS))
def test_apply_like_ui_all_methods(method):
    out = _recalc(method, _apply_like_ui(method))
    assert out["success"] is True, f"{method} UI应用失败: {out.get('error')}"
    assert out.get("fitness") is not None


@pytest.mark.parametrize("method", list(UI_EDITABLE))
def test_editable_only_reports_hidden_key_gaps(method):
    """只发编辑框时：缺隐藏变量的路线应失败，其余应成功。"""
    out = _recalc(method, _editable_only(method))
    needs_hidden = method in ("普通抽+Taper+扩口", "普通抽+TP抽+Taper+扩口")
    if needs_hidden:
        assert out["success"] is False, f"{method} 只发编辑框不应成功"
        assert "Dn" in str(out.get("error", ""))
    else:
        assert out["success"] is True, f"{method} 只发编辑框失败: {out.get('error')}"
