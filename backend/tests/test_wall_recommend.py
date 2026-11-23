"""壁厚曲线分段推荐，以及平台吸附和过渡直线重建。"""
import os
import sys

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.wall_thickness_segments import (  # noqa: E402
    analyze_uploaded_wall,
    recommend_wall_fields,
)


def _sections(thicknesses):
    return [
        {"cumulative_length": float(index), "wall_thickness": thickness, "perimeter": 80.0}
        for index, thickness in enumerate(thicknesses)
    ]


def test_near_constant_curve_recommends_one_plateau():
    sections = _sections([2.026 if index < 15 else 2.001 for index in range(756)])
    original = sections[0]["wall_thickness"]
    recommended = recommend_wall_fields(sections)

    assert sections[0]["wall_thickness"] == original
    assert recommended["T1"] == 2.0
    assert recommended["TL1"] == 755
    for key in ("T2", "T3", "T4", "TL2", "TL3", "TL4", "TLM1", "TLM2", "TLM3"):
        assert recommended[key] is None


def test_two_plateaus_keep_transition_and_leave_unused_fields_empty():
    thicknesses = [2.04] * 100 + [2.0 - 0.03 * step for step in range(20)] + [1.46] * 200
    recommended = recommend_wall_fields(_sections(thicknesses))

    assert recommended["T1"] == 2.0
    assert recommended["T2"] == 1.5
    assert recommended["TL1"] > 0
    assert recommended["TLM1"] > 0
    assert recommended["TL2"] > 0
    assert recommended["TL1"] + recommended["TLM1"] + recommended["TL2"] == 319
    for key in ("T3", "T4", "TL3", "TL4", "TLM2", "TLM3"):
        assert recommended[key] is None


def test_small_ripple_on_plateau_is_snapped_back():
    thicknesses = [1.40] * 100 + [1.46] * 8 + [1.41] * 100
    sections = _sections(thicknesses)
    prepared = analyze_uploaded_wall(sections)

    assert prepared["fields"]["T1"] == 1.4
    assert prepared["fields"]["T2"] is None
    assert prepared["fields"]["TLM1"] is None
    snapped = [point["wall_thickness"] for point in prepared["curve"][100:108]]
    assert snapped == [1.4] * 8
    assert sections[100]["wall_thickness"] == 1.46


def test_whole_plateau_near_1_46_keeps_rounded_median():
    prepared = analyze_uploaded_wall(_sections([1.46] * 80))
    assert prepared["fields"]["T1"] == 1.5
    assert prepared["curve"][0]["wall_thickness"] == 1.5


def test_sustained_deviation_becomes_its_own_plateau():
    prepared = analyze_uploaded_wall(_sections([1.40] * 100 + [1.60] * 30 + [1.40] * 100))
    fields = prepared["fields"]
    assert fields["T1"] == 1.4
    assert fields["T2"] == 1.6
    assert fields["T3"] == 1.4
    assert fields["TL2"] >= 20
    middle = [point["wall_thickness"] for point in prepared["curve"][110:120]]
    assert middle == [1.6] * 10


def test_transition_is_a_straight_line():
    ramp = [1.40 - 0.50 * step / 60 for step in range(61)]
    prepared = analyze_uploaded_wall(_sections([1.40] * 80 + ramp + [0.90] * 80))
    fields = prepared["fields"]
    assert fields["T1"] == 1.4
    assert fields["T2"] == 0.9
    assert fields["TLM1"] > 20
    transition = [
        point["wall_thickness"]
        for point in prepared["curve"]
        if fields["TL1"] < point["cumulative_length"] < fields["TL1"] + fields["TLM1"]
    ]
    assert len(transition) > 10
    steps = [round(right - left, 2) for left, right in zip(transition, transition[1:])]
    assert max(steps) <= 0.02
    assert min(steps) >= -0.02
    assert transition[0] > transition[-1]


def _gentle_taper_rows(length=568.0):
    """1.24 → 0.94 over 137 mm, i.e. ~0.0022 mm/mm — below the old 0.005 threshold."""
    rows = []
    position = 0.0
    while position <= length + 1e-9:
        if position <= 125.0:
            thickness = 1.241
        elif position < 262.0:
            thickness = 1.241 + (0.94 - 1.241) * (position - 125.0) / 137.0
        else:
            thickness = 0.94
        rows.append({"cumulative_length": round(position, 3), "wall_thickness": round(thickness, 4)})
        position += 1.0
    return rows


def test_gentle_taper_is_a_transition_not_a_vertical_step():
    prepared = analyze_uploaded_wall(_gentle_taper_rows())
    fields = prepared["fields"]

    assert fields["T1"] == 1.2
    assert fields["T2"] == 0.9
    assert fields["TL1"] > 0
    assert fields["TLM1"] > 100, "缓坡必须保留真实跨度，不能被切成两个平台"
    assert fields["TLM1"] < 200
    assert fields["TL1"] + fields["TLM1"] + fields["TL2"] == 568

    curve = prepared["curve"]
    tallest = 0.0
    for left, right in zip(curve, curve[1:]):
        if right["cumulative_length"] > left["cumulative_length"]:
            tallest = max(tallest, abs(right["wall_thickness"] - left["wall_thickness"]))
    assert tallest < 0.05, "重建曲线上不应再出现竖直台阶"


def test_taper_keeps_plateau_medians_after_rounding():
    rows = _gentle_taper_rows()
    prepared = analyze_uploaded_wall(rows)
    fields = prepared["fields"]
    # 高平台 1.241 → 1.2；低平台 0.94 → 0.9；1 位小数取整不是测量误差
    assert fields["T1"] == 1.2
    assert fields["T2"] == 0.9
    assert prepared["curve"][0]["wall_thickness"] == 1.2
    assert prepared["curve"][-1]["wall_thickness"] == 0.9
