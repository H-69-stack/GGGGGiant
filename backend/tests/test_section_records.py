"""``core.pp.section_records``：sections_full → 逐站记录的回归测试。

背景：一体化流水线上传入库只写 ``sections_full``，管件模型分析页
「数据库加载」后用空形线跑 QA，报 ``QA_INVALID_INPUT / empty_profile``。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pp.section_records import (  # noqa: E402
    PERIMETER_KEY,
    S_LENGTH_KEY,
    WALL_KEY,
    ensure_results_records,
    records_from_sections,
)


def _sections():
    return [
        {
            'cumulative_length': 0.0,
            'perimeter': 227.485,
            'wall_thickness': 2.02,
            'measurement_status': 'extrapolated',
            'x_length': 74.157,
            'y_length': 59.052,
            'position': [756.231, 5.4, 0.0],
        },
        {
            'cumulative_length': 400.0,
            'perimeter': 200.0,
            'wall_thickness': 2.0,
            'measurement_status': 'measured',
            'x_length': 63.6,
            'y_length': 63.6,
            'position': [10.0, 0.0, 1.0],
        },
        {
            'cumulative_length': 757.268,
            'perimeter': 177.388,
            'wall_thickness': 2.0,
            'measurement_status': 'measured',
            'x_length': 54.682,
            'y_length': 52.497,
            'position': [-0.214, -3.524, 0.0],
        },
    ]


def test_records_map_expected_columns():
    records = records_from_sections(_sections())
    assert len(records) == 3
    first = records[0]
    assert first[S_LENGTH_KEY] == 0.0
    assert first[PERIMETER_KEY] == 227.485
    assert first[WALL_KEY] == 2.02
    assert first['measurement_status'] == 'extrapolated'
    assert first['x_length'] == 74.157 and first['y_length'] == 59.052
    assert (first['x'], first['y'], first['z']) == (756.231, 5.4, 0.0)
    assert records[-1][S_LENGTH_KEY] == 757.268


def test_records_skip_non_dict_and_tolerate_missing_keys():
    records = records_from_sections([{'perimeter': 100.0}, None, 'x'])  # type: ignore[list-item]
    assert len(records) == 1
    assert records[0][S_LENGTH_KEY] == 0.0
    assert records[0][WALL_KEY] is None
    assert records_from_sections([]) == []
    assert records_from_sections(None) == []


def test_ensure_results_records_fills_both_legacy_keys():
    results = {'sections_full': _sections()}
    records = ensure_results_records(results)
    assert len(records) == 3
    assert results['wall_thickness_data'] is records or results['wall_thickness_data'] == records
    assert results['centerline_perimeter_data'] == results['wall_thickness_data']
    # 中心线长度按最后一站补齐（旧记录这一列是 0）
    assert results['centerline_length'] == 757.268


def test_ensure_results_records_keeps_existing_records():
    existing = [{S_LENGTH_KEY: 0.0, PERIMETER_KEY: 198.0, WALL_KEY: 1.8}]
    results = {
        'sections_full': _sections(),
        'wall_thickness_data': existing,
        'centerline_length': 300.0,
    }
    returned = ensure_results_records(results)
    assert returned == existing
    assert results['wall_thickness_data'] is existing
    # 不覆盖已有长度、不重算已有形线
    assert results['centerline_length'] == 300.0
    assert len(returned) == 1


def _long_sections(n=121):
    """够长的合成形线：周长缓慢单调减小，用来喂 QA 状态机。"""
    out = []
    for index in range(n):
        s = index * 5.0
        per = 220.0 - (40.0 * index / (n - 1))
        diameter = per / 3.141592653589793
        out.append({
            'cumulative_length': s,
            'perimeter': per,
            'wall_thickness': 2.0,
            'measurement_status': 'measured',
            'x_length': round(diameter, 3),
            'y_length': round(diameter, 3),
            'position': [0.0, 0.0, s],
        })
    return out


def test_qa_accepts_rebuilt_records():
    from core.pp.profile_qa.pipeline import initialize_qa_from_records
    from core.pp.profile_qa.state import QA_INVALID_INPUT

    results = {'sections_full': _long_sections()}
    records = ensure_results_records(results)
    qa = initialize_qa_from_records(
        records,
        centerline_length=results.get('centerline_length'),
        profile_source='stp_raw',
        original_filename='24TK_1324-1',
    )
    assert qa['status'] != QA_INVALID_INPUT, qa.get('reason')
    assert (qa.get('raw_profile') or {}).get('s_mm')
