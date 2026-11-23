"""保存前的研磨余量：壁厚加余量，周长按外径增加两倍余量。"""
import math
import os
import sys

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.grind_allowance import apply_grind_allowance  # noqa: E402


def test_grind_allowance_keeps_inner_diameter_and_field_names():
    perimeter = 100.0
    thickness = 2.0
    allowance = 0.3
    results = {
        "wall_thickness_data": [{
            "中心线长度(mm)": 10,
            "外径周长(mm)": perimeter,
            "壁厚(mm)": thickness,
        }],
        "manual_segments": {
            "thicknesses": [2.0, 0, 1.5],
            "final_curve": [{"s": 0, "wall_thickness": 2.0}],
            "perimeter_curve": [{"s": 0, "perimeter": perimeter, "wall_thickness": 2.0}],
        },
        "qa": {"wall_thickness": {"parsed": {"T1": 2.0, "T2": 0, "TL1": 80}, "values": {"T1": 2.0}}},
    }
    results["centerline_perimeter_data"] = results["wall_thickness_data"]

    apply_grind_allowance(results, allowance)

    row = results["wall_thickness_data"][0]
    expected_perimeter = round(perimeter + 2 * math.pi * allowance, 3)
    assert row["外径周长(mm)"] == expected_perimeter
    assert row["壁厚(mm)"] == 2.3
    assert "中心线长度(mm)" in row
    old_inner = perimeter / math.pi - 2 * thickness
    new_inner = row["外径周长(mm)"] / math.pi - 2 * row["壁厚(mm)"]
    assert abs(new_inner - old_inner) < 0.001
    assert results["manual_segments"]["thicknesses"] == [2.3, 0, 1.8]
    assert results["manual_segments"]["final_curve"][0]["wall_thickness"] == 2.3
    assert results["qa"]["wall_thickness"]["parsed"]["T1"] == 2.3
    assert results["qa"]["wall_thickness"]["parsed"]["T2"] == 0
    assert results["qa"]["wall_thickness"]["parsed"]["TL1"] == 80
    assert results["centerline_perimeter_data"][0]["壁厚(mm)"] == 2.3

    apply_grind_allowance(results, 0)
    assert results["wall_thickness_data"][0]["壁厚(mm)"] == 2.3
