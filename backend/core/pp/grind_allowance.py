"""把读取到的壁厚和外周长改成预留研磨后的值。

内径不变：壁厚加上余量，外径增加两倍余量。
保存的仍是外径周长，等于 π ×（原直径 + 2×余量）。
"""
import math

_THICKNESS_KEYS = ('T1', 'T2', 'T3', 'T4')


def _positive_number(value):
    if value is None or value == '':
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def apply_grind_allowance(results: dict, allowance_mm) -> None:
    """就地修改即将保存并传给后续工艺的周长和壁厚。字段名不变。"""
    allowance = _positive_number(allowance_mm)
    if allowance is None or not isinstance(results, dict):
        return
    delta_perimeter = 2.0 * math.pi * allowance

    def thicker(value):
        number = _positive_number(value)
        if number is None:
            return value
        return round(number + allowance, 3)

    def wider(value):
        number = _positive_number(value)
        if number is None:
            return value
        return round(number + delta_perimeter, 3)

    seen = set()
    for key in ('wall_thickness_data', 'centerline_perimeter_data'):
        rows = results.get(key)
        if not isinstance(rows, list) or id(rows) in seen:
            continue
        seen.add(id(rows))
        for row in rows:
            if not isinstance(row, dict):
                continue
            if '壁厚(mm)' in row:
                row['壁厚(mm)'] = thicker(row.get('壁厚(mm)'))
            if '外径周长(mm)' in row:
                row['外径周长(mm)'] = wider(row.get('外径周长(mm)'))

    manual = results.get('manual_segments')
    if isinstance(manual, dict):
        thicknesses = manual.get('thicknesses')
        if isinstance(thicknesses, list):
            manual['thicknesses'] = [thicker(value) for value in thicknesses]
        for point in manual.get('final_curve') or []:
            if isinstance(point, dict) and 'wall_thickness' in point:
                point['wall_thickness'] = thicker(point.get('wall_thickness'))
        for point in manual.get('perimeter_curve') or []:
            if not isinstance(point, dict):
                continue
            if 'wall_thickness' in point:
                point['wall_thickness'] = thicker(point.get('wall_thickness'))
            if 'perimeter' in point:
                point['perimeter'] = wider(point.get('perimeter'))

    qa = results.get('qa')
    wall_info = qa.get('wall_thickness') if isinstance(qa, dict) else None
    if isinstance(wall_info, dict):
        for bucket in ('parsed', 'values'):
            data = wall_info.get(bucket)
            if not isinstance(data, dict):
                continue
            for key in _THICKNESS_KEYS:
                if key in data:
                    data[key] = thicker(data.get(key))
