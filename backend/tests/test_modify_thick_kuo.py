"""扩口段壁厚：T1×D1/Dn，无 Dn 时用 D0。"""
from __future__ import annotations

import os
import sys

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.modify_thick import apply_modify_thick  # noqa: E402


def _wt(n=80, t=1.7):
    return [{"中心线长度(mm)": float(i), "壁厚(mm)": t} for i in range(n)]


def test_flaring_uses_dn_when_present():
    sol = {
        "D0": 50, "Dn": 40, "D1": 48, "D3": 32, "D2": 36,
        "L1": 20, "L2": 30, "L3": 0,
    }
    df, _ = apply_modify_thick(_wt(), sol, "普通抽+Taper+扩口")
    expected = round(1.7 * 48 / 40, 3)
    assert abs(float(df.iloc[0]["壁厚(mm)"]) - expected) < 1e-6
    assert abs(float(df.iloc[20]["壁厚(mm)"]) - expected) < 1e-6


def test_flaring_falls_back_to_d0_without_dn():
    sol = {
        "D0": 50, "D1": 48, "D3": 32, "D2": 36,
        "L1": 20, "L2": 30, "L3": 0,
    }
    df, _ = apply_modify_thick(_wt(), sol, "Taper+TP抽+扩口")
    expected = round(1.7 * 48 / 50, 3)
    assert abs(float(df.iloc[0]["壁厚(mm)"]) - expected) < 1e-6


def test_flaring_ignores_zero_dn():
    sol = {
        "D0": 50, "Dn": 0, "D1": 48, "D3": 32, "D2": 36,
        "L1": 20, "L2": 30, "L3": 0,
    }
    df, _ = apply_modify_thick(_wt(), sol, "普通抽+扩口")
    expected = round(1.7 * 48 / 50, 3)
    assert abs(float(df.iloc[0]["壁厚(mm)"]) - expected) < 1e-6


def test_taper_uses_d2_for_nor_taper_kuo():
    sol = {
        "D0": 50, "Dn": 40, "D1": 48, "D3": 32, "D2": 36,
        "L1": 20, "L2": 0, "L3": 40,
    }
    df, _ = apply_modify_thick(_wt(), sol, "普通抽+Taper+扩口", Taper_thick=True)
    got = float(df.iloc[-1]["壁厚(mm)"])
    expected_d2 = 1.7 * 32 / 36
    expected_d0 = 1.7 * 32 / 50
    assert abs(got - expected_d2) < abs(got - expected_d0)
    assert abs(got - expected_d2) < 0.03


def test_taper_uses_d2_for_nor_taper():
    sol = {
        "D0": 50, "D1": 48, "D3": 32, "D2": 48,
        "L1": 0, "L3": 60,
    }
    df, _ = apply_modify_thick(_wt(), sol, "普通抽+Taper", Taper_thick=False)
    expected = round(1.7 * 32 / 48, 3)
    assert abs(float(df.iloc[10]["壁厚(mm)"]) - expected) < 1e-6


def test_taper_falls_back_to_d0_without_d2():
    sol = {
        "D0": 50, "D1": 48, "D3": 32,
        "L1": 0, "L3": 60,
    }
    df, _ = apply_modify_thick(_wt(), sol, "普通抽+Taper", Taper_thick=False)
    expected = round(1.7 * 32 / 50, 3)
    assert abs(float(df.iloc[10]["壁厚(mm)"]) - expected) < 1e-6


def test_ga_taper_still_uses_d2():
    sol = {
        "D0": 50, "D1": 48, "D3": 32, "D2": 36,
        "L1": 20, "L2": 30, "L3": 20,
    }
    df, _ = apply_modify_thick(_wt(), sol, "Taper+TP抽+扩口", Taper_thick=False)
    # 扩口段仍 T1*D1/D0（无 Dn）；Taper 段从 L2 起 × D3/D2
    expected_flare = round(1.7 * 48 / 50, 3)
    expected_taper = round(1.7 * 32 / 36, 3)
    assert abs(float(df.iloc[0]["壁厚(mm)"]) - expected_flare) < 1e-6
    assert abs(float(df.iloc[70]["壁厚(mm)"]) - expected_taper) < 1e-6


def test_taper_thick_writes_l3_as_tlm_nor_taper_kuo():
    """勾选 Taper 作过渡后，总表 TLM1 用方案 L3（不再为 0）。"""
    sol = {
        "D0": 46, "Dn": 45, "D1": 47.67, "D3": 32.88, "D2": 45,
        "L1": 20, "L2": 0, "L3": 59,
    }
    _, seg = apply_modify_thick(_wt(n=80), sol, "普通抽+Taper+扩口", Taper_thick=True)
    assert float(seg.get("TL1") or 0) == 20
    assert float(seg.get("TLM1") or 0) == 59
    assert float(seg.get("TL2") or 0) == 0


def test_taper_unchecked_keeps_50mm_blend_tlm():
    sol = {
        "D0": 46, "Dn": 45, "D1": 47.67, "D3": 32.88, "D2": 45,
        "L1": 20, "L2": 0, "L3": 59,
    }
    _, seg = apply_modify_thick(_wt(n=80), sol, "普通抽+Taper+扩口", Taper_thick=False)
    assert float(seg.get("TLM1") or 0) == 50
    assert float(seg.get("TL1") or 0) == 20


def test_taper_only_thick_writes_l3_as_tlm():
    sol = {
        "D0": 50, "D1": 48, "D3": 32, "D2": 48,
        "L1": 0, "L3": 79,
    }
    _, seg = apply_modify_thick(_wt(n=80), sol, "普通抽+Taper", Taper_thick=True)
    assert float(seg.get("TLM1") or 0) == 79


def test_ga_taper_thick_writes_l3_as_tlm():
    sol = {
        "D0": 50, "Dn": 40, "D1": 48, "D3": 32, "D2": 36,
        "L1": 20, "L2": 30, "L3": 29,
    }
    _, seg = apply_modify_thick(_wt(n=80), sol, "普通抽+TP抽+Taper+扩口", Taper_thick=True)
    assert float(seg.get("TLM1") or 0) == 29
    assert float(seg.get("TLM2") or 0) == 0


def test_tp_only_keeps_confirmed_single_segment_tl1():
    """仅TP抽单段时不得用 STP 形线重测覆盖图纸 TL1。"""
    sol = {"D0": 40, "D1": 40, "D3": 32, "D2": 32, "L1": 0, "L2": 100}
    wt = [{"中心线长度(mm)": float(i), "壁厚(mm)": 1.7} for i in range(200)]
    _, seg = apply_modify_thick(
        wt, sol, "仅TP抽", segments={"T1": 1.7, "TL1": 180.0},
    )
    assert float(seg.get("TL1") or 0) == 180.0
    assert float(seg.get("T1") or 0) == 1.7
