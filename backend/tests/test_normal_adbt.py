"""普通抽抽管芯轴：壁厚为 0 的外径为 /，%%Cd4 用 T4。"""
from __future__ import annotations

import os
import sys

import pandas as pd

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.normal import _normal_params_from_design, positive_wall_count  # noqa: E402
from core.normal.molds.normal_molds import ADBT, DC0124_AD01, DC0124_AD02, DC0124_SS01  # noqa: E402
from core.tp.molds.tp_molds import DC0128_ADBT  # noqa: E402
from core.normal.shrink_tube import ShrinkTubeCalculator  # noqa: E402

_EXT = {"图号": "DC0124-ADBT0001", "件数": "1", "车种规格": "X", "设计者": "tester"}


def _tube(**values):
    return pd.DataFrame([{"参数": key, "值": value} for key, value in values.items()])


def test_zero_thickness_mandrel_diameter_is_blank_and_cd4_uses_t4():
    mold = ADBT()
    mold.set_params(
        _tube(D=40, L=100, L1=10, T1=2, T2=0, T3=1.5, T4=1.2),
        _EXT,
        normal_add=False,
    )
    assert mold.parameters["%%Cd1"] == "36.0"
    assert mold.parameters["%%Cd2"] == "/"
    assert mold.parameters["%%Cd3"] == "37.0"
    assert mold.parameters["%%Cd4"] == "37.6"
    assert mold.parameters["抽管尺寸"] == "%%C40.0-2.0-1.5-1.2"
    assert mold.parameters["L1"] == "15"
    assert mold.parameters["L2"] == "/"
    assert mold.parameters["L3"] == "/"
    assert mold.parameters["L4"] == "/"

    mold.set_params(
        _tube(D=40, L=100, L1=10, T1=2, T2=1, T3=1.5, T4=0),
        _EXT,
        normal_add=True,
    )
    assert mold.parameters["%%Cd1"] == "36.3"
    assert mold.parameters["%%Cd4"] == "/"
    assert mold.parameters["缩管模直径"] == "40.3"


def test_missing_t4_leaves_cd4_blank():
    mold = ADBT()
    mold.set_params(_tube(D=40, L=80, T1=1, T2=1, T3=1), _EXT)
    assert mold.parameters["%%Cd4"] == "/"
    assert mold.parameters["抽管尺寸"] == "%%C40.0-1.0-1.0-1.0"


def test_l4_is_fourth_segment_not_total_length():
    mold = ADBT()
    mold.set_params(_tube(D=40, L=100, L1=10, L2=20, L3=30, L4=40, T1=1), _EXT)
    assert mold.parameters["L1"] == "15"
    assert mold.parameters["L2"] == "20"
    assert mold.parameters["L3"] == "30"
    assert mold.parameters["L4"] == "40"

    mold.set_params(_tube(D=40, L=100, L1=0, L2=20, L3=0, L4=0, T1=1), _EXT)
    assert mold.parameters["L1"] == "/"
    assert mold.parameters["L2"] == "20"
    assert mold.parameters["L3"] == "/"
    assert mold.parameters["L4"] == "/"


def test_unload_mold_uses_smallest_positive_thickness():
    mold = DC0124_AD01()
    mold.set_params(_tube(D=40, T1=2, T2=0, T3=1.5, T4=1.2, TL1=755), _EXT)
    assert mold.parameters["A"] == "37.8"
    mold.set_params(_tube(D=40, T1=2, T2=0, T3=1.5, T4=1.2), _EXT, normal_add=True)
    assert mold.parameters["A"] == "38.1"


def test_two_wall_segments_do_not_count_zero_or_length():
    rows = [
        {"参数": "T1", "值": 2},
        {"参数": "T2", "值": 1.5},
        {"参数": "T3", "值": 0},
        {"参数": "T4", "值": 0},
        {"参数": "L1", "值": 755},
    ]
    assert positive_wall_count(rows) == 2
    rows[2]["值"] = 1.2
    assert positive_wall_count(rows) == 3


def test_forming_mandrel_and_unload_skip_zero_thickness():
    tube = _tube(D=40, T1=2, T2=0, T3=1.5, T4=1.2, TL1=755)
    mandrel = DC0124_SS01()
    mandrel.set_params(tube, _EXT, normal_add=True)
    assert mandrel.parameters["%%CD"] == "37.3"

    unload = DC0124_AD02()
    unload.set_params(tube, _EXT, normal_add=True)
    assert unload.parameters["%%CA"] == "37.5"


def test_design_mapping_includes_t4():
    rows = _normal_params_from_design({
        "params": {"T1": 1, "T2": 0, "T3": 1.5, "T4": 1.2, "TL4": 25, "Ln": 100, "Dn": 40},
    })
    values = {row["参数"]: row["值"] for row in rows}
    assert values["T4"] == 1.2
    assert values["L4"] == 25
    assert values["L"] == 100
    assert values["D"] == 40


def test_cutting_clip_is_one_mold_and_cd_is_d():
    calc = ShrinkTubeCalculator()
    calc.db.query_max_graph_number = lambda machine_type, base_number: 0
    calc.load_tube_from_params(_tube(D=40, T1=2, T2=1.5, T3=1.2, T4=0))

    calc.calculate("tester", False, ["裁剪夹模"], "DC0124")
    assert set(calc.mold_objects) == {"裁剪夹模"}
    clip = calc.mold_objects["裁剪夹模"]
    assert clip.parameters["图号"] == "DC0124-AD030001"
    assert clip.parameters["件数"] == 4
    assert clip.parameters["%%CD"] == "40.0"

    calc.calculate("tester", False, ["裁剪夹模"], "DC0125")
    clip = calc.mold_objects["裁剪夹模"]
    assert set(calc.mold_objects) == {"裁剪夹模"}
    assert clip.parameters["图号"] == "DC0125-AD070001"
    assert clip.parameters["件数"] == 4
    assert clip.parameters["%%CD"] == "40.0"

    calc.calculate("tester", False, ["裁剪夹模1", "裁剪夹模2"], "DC0121")
    assert calc.mold_objects["裁剪夹模1"].parameters["%%CD"] == "40.0"
    assert calc.mold_objects["裁剪夹模2"].parameters["%%CD"] == "40.0"
    assert calc.mold_objects["裁剪夹模1"].parameters["图号"].startswith("DC0121-AD03")
    assert calc.mold_objects["裁剪夹模2"].parameters["图号"].startswith("DC0121-AD04")


def _value_above(mold, label):
    texts = list(mold._doc.modelspace().query("TEXT"))
    label_text = next(item for item in texts if item.dxf.text.strip() == label)
    x, y = label_text.dxf.insert.x, label_text.dxf.insert.y
    for item in texts:
        pt = item.dxf.insert
        if abs(pt.x - x) <= 5 and abs(pt.y - (y + 18)) <= 5:
            return item.dxf.text
    return None


def test_relief_hole_uses_min_positive_mandrel_diameter():
    mold = ADBT()
    mold.set_params(_tube(D=60, T1=2, T2=0, T3=1, T4=0), _EXT)
    assert mold.parameters["%%Cd1"] == "56.0"
    assert mold.parameters["%%Cd2"] == "/"
    assert mold.parameters["%%CA"] == "30"
    assert mold.parameters["L"] == "400"
    mold.modify_dxf()
    assert _value_above(mold, "%%CA") == "30"
    assert _value_above(mold, "L") == "400"

    mold.set_params(_tube(D=40, T1=2, T2=1.5, T3=1.2, T4=1), _EXT)
    assert mold.parameters["%%CA"] == "0"
    assert mold.parameters["L"] == "0"

    tp = DC0128_ADBT()
    tp.set_params(
        _tube(D=60, T1=1, T2=1, T3=1, T_L=20, L1=10, L2=10, L3=10),
        {"图号": "ADBT0001", "件数": 1, "车种规格": "X", "设计者": "tester"},
    )
    assert float(tp.parameters["%%Cd4"]) > 50
    assert tp.parameters["%%CA"] == "30"
    assert tp.parameters["L"] == "400"
    tp.modify_dxf()
    assert _value_above(tp, "%%CA") == "30"
    assert _value_above(tp, "L") == "400"
