"""把 ``sections_full`` 转成工艺规划/形线 QA 需要的逐站记录。

测量结果（STP 原生截面 / STL 切片）只保证有 ``sections_full``；而
``wall_thickness_data`` / ``centerline_perimeter_data`` 这套「老字段」被
工艺规划（Tab2/Tab3）、关键截面校验、硬模成型和 QA 状态机读取。两边字段
名不同（``cumulative_length`` vs ``中心线长度(mm)``），一旦某条链路（例如
一体化流水线的上传入库）只写了 ``sections_full``，页面就会在「数据库加载」
之后报 ``QA_INVALID_INPUT / empty_profile``。

这里集中做一次转换，避免每个入口各写一份循环。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

S_LENGTH_KEY = '中心线长度(mm)'
PERIMETER_KEY = '外径周长(mm)'
WALL_KEY = '壁厚(mm)'


def _section_length(section: dict) -> Any:
    value = section.get('cumulative_length')
    if value is None:
        value = section.get(S_LENGTH_KEY)
    return value if value is not None else 0


def _section_perimeter(section: dict) -> Any:
    value = section.get('perimeter')
    if value is None:
        value = section.get(PERIMETER_KEY)
    return value if value is not None else 0


def _densified_sections(sections: Sequence[dict]) -> Sequence[dict]:
    """归档网格比 1 mm 粗时，先把站位插值回 1 mm 再出记录。

    ``sections_full`` 是「归档层」，可以按 ``GTAPP_STATION_GRID`` 放粗（3 mm）；
    而 ``wall_thickness_data`` 是「计算层」，形线质检、壁厚分段、工艺优化、
    硬模成型都按 1 mm 口径消费它，所以这里必须还原成 1 mm。
    """
    rows = [s for s in sections if isinstance(s, dict)]
    if len(rows) < 4:
        return sections
    try:
        import numpy as np

        lengths = sorted(float(r.get('cumulative_length') or 0.0) for r in rows)
        diffs = np.diff(lengths)
        diffs = diffs[diffs > 1e-9]
        if diffs.size == 0:
            return sections
        spacing = float(np.median(diffs))
    except Exception:  # noqa: BLE001 - 判不出站距就按原样用
        return sections
    if spacing <= 1.5:
        return sections
    try:
        from core.pp.tube.station_plan import expand_rows
        dense, _ = expand_rows(rows, grid_step=1.0)
        return dense or sections
    except Exception:  # noqa: BLE001 - 插值失败不阻塞，退回粗记录
        return sections


def records_from_sections(sections: Optional[Sequence[dict]]) -> List[dict]:
    """``sections_full`` → ``[{中心线长度(mm), 外径周长(mm), 壁厚(mm), x_length, y_length, x,y,z}]``.

    与 ``api/pp.py`` 上传分析后的构造保持一致（同一套列名、同一套 x_length /
    y_length 口径），这样从数据库加载出来的记录和刚上传分析的记录等价。
    """
    if not sections:
        return []
    sections = _densified_sections(sections)
    try:  # 延迟 import：只有真的需要算截面尺寸时才拉几何依赖
        from core.pp.profile_qa.key_section import xy_from_section_row
    except Exception:  # noqa: BLE001 - 几何依赖缺失时仍然给出长度/周长/壁厚
        xy_from_section_row = None  # type: ignore[assignment]

    out: List[dict] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        entry: Dict[str, Any] = {
            S_LENGTH_KEY: _section_length(section),
            PERIMETER_KEY: _section_perimeter(section),
            WALL_KEY: section.get('wall_thickness'),
            'measurement_status': section.get('measurement_status') or 'measured',
            'station_source': section.get('station_source') or 'measured',
        }
        size = None
        if xy_from_section_row is not None:
            try:
                size = xy_from_section_row(section)
            except Exception:  # noqa: BLE001
                size = None
        if size:
            try:
                entry['x_length'] = round(float(size[0]), 4)
                entry['y_length'] = round(float(size[1]), 4)
            except (TypeError, ValueError):
                pass
        position = section.get('position')
        if position is not None:
            try:
                entry.update(zip(('x', 'y', 'z'), position))
            except TypeError:
                pass
        out.append(entry)
    return out


def _last_length(records: Sequence[dict]) -> float:
    for rec in reversed(list(records or [])):
        try:
            value = float(rec.get(S_LENGTH_KEY) or 0.0)
        except (TypeError, ValueError):
            continue
        if value:
            return value
    return 0.0


def ensure_results_records(results: dict) -> List[dict]:
    """补齐 ``results`` 里的逐站记录，返回最终使用的那份列表。

    已经有 ``wall_thickness_data`` / ``centerline_perimeter_data`` 的会话原样
    返回，不重算、不覆盖用户确认过的手工数据；两者都缺时才用
    ``sections_full`` 重建，并把 ``centerline_length`` 补上（旧记录这一列
    可能是 0，导致 QA 的长度校验失败）。
    """
    if not isinstance(results, dict):
        return []
    existing = results.get('wall_thickness_data') or results.get('centerline_perimeter_data') or []
    if existing:
        if not results.get('wall_thickness_data'):
            results['wall_thickness_data'] = existing
        if not results.get('centerline_perimeter_data'):
            results['centerline_perimeter_data'] = existing
    else:
        existing = records_from_sections(results.get('sections_full') or [])
        if existing:
            results['wall_thickness_data'] = existing
            results['centerline_perimeter_data'] = existing

    if not results.get('centerline_length'):
        length = _last_length(existing)
        if length:
            results['centerline_length'] = round(float(length), 4)
    return list(existing or [])
