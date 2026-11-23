"""普通抽素材外径：按抽管芯轴与素材内径的间隙反推 D0。

Dn 的求法不在这里改。芯轴壁厚用 apply_modify_thick 返回的 T1–T3
（修改后的壁厚），不用工艺规划入口手工填入的壁厚。
模具额外壁厚 0.3 mm、要求间隙 1.5 mm，因此

    D0 = Dn - 2*Tmin + 2*T0 + 1.8
"""
from __future__ import annotations

DRAW_BLANK_METHODS = frozenset({
    '普通抽+扩口',
    '普通抽+Taper',
    '普通抽+Taper+扩口',
    '普通抽+TP抽',
    '普通抽+TP抽+Taper+扩口',
})

MANDREL_WALL_OFFSET_MM = 0.3
BLANK_ID_CLEARANCE_MM = 1.5
MANDREL_INFEASIBLE_PENALTY = 1_000_000.0

_TMIN_CACHE = {}


def decimal_places(value, default=1) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    text = f"{number:.6f}".rstrip('0')
    if '.' not in text or text.endswith('.'):
        return 0
    return min(6, len(text.split('.')[-1]))


def route_max_d0(pp_method, d1, precision, hard_cap=150.0) -> float:
    """路线原有的 D0 上限。遗传算法路线还要求 D0 < D1。"""
    cap = float(hard_cap)
    if pp_method == '普通抽+TP抽+Taper+扩口':
        try:
            prec = max(0, int(precision))
            limit = float(d1) - (10 ** (-prec))
        except (TypeError, ValueError):
            limit = cap
        cap = min(cap, limit)
    return cap


def d0_for_mandrel_clearance(dn, t0, t_min, precision) -> float:
    raw = (
        float(dn)
        - 2.0 * float(t_min)
        + 2.0 * float(t0)
        + MANDREL_WALL_OFFSET_MM
        + BLANK_ID_CLEARANCE_MM
    )
    return round(raw, max(0, int(precision)))


def scale_blank_length(length, old_d0, new_d0, t0):
    """D0 改变后按环形截面积反比缩放已算出的 L0。"""
    import math
    try:
        length = float(length)
        old_d0 = float(old_d0)
        new_d0 = float(new_d0)
        t0 = float(t0)
    except (TypeError, ValueError):
        return length

    def area(diameter):
        radius = diameter / 2.0
        inner = radius - t0
        return math.pi * (radius * radius - inner * inner)

    old_area = area(old_d0)
    new_area = area(new_d0)
    if length <= 0 or old_area <= 0 or new_area <= 0:
        return length
    return length * old_area / new_area


def _positive_thicknesses(segments2):
    values = []
    for key in ('T1', 'T2', 'T3'):
        try:
            thickness = float((segments2 or {}).get(key) or 0)
        except (TypeError, ValueError):
            continue
        if thickness > 0:
            values.append(thickness)
    return values


def _segments_for_modified_thickness(segments):
    """去掉入口手工壁厚，让 no_modify 从曲线识别 T，而不是交回输入值。"""
    if not isinstance(segments, dict):
        return {}
    cleaned = dict(segments)
    for key in ('T1', 'T2', 'T3', 'T4'):
        cleaned.pop(key, None)
    return cleaned


def modified_draw_thicknesses(wall_rows, solution, pp_method, segments=None, taper_thick=False):
    from core.pp.modify_thick import apply_modify_thick
    _, segments2 = apply_modify_thick(
        wall_rows,
        solution,
        pp_method,
        Taper_thick=taper_thick,
        segments=_segments_for_modified_thickness(segments),
    )
    return _positive_thicknesses(segments2)


def _wall_fingerprint(rows):
    count = len(rows)
    if count == 0:
        return (0,)

    def point(row):
        try:
            length = round(float(row.get('中心线长度(mm)') or 0), 2)
        except (TypeError, ValueError, AttributeError):
            length = 0.0
        try:
            thickness = round(float(row.get('壁厚(mm)') or 0), 3)
        except (TypeError, ValueError, AttributeError):
            thickness = 0.0
        return (length, thickness)

    return (count, point(rows[0]), point(rows[count // 2]), point(rows[-1]))


def solve_mandrel_d0(
    wall_rows,
    *,
    pp_method,
    dn,
    t0,
    d1,
    d3,
    d2=0,
    l1=0,
    l2=0,
    l3=0,
    segments=None,
    taper_thick=False,
    precision=1,
    max_d0=150,
    provisional_d0=None,
):
    """返回满足芯轴间隙的 D0。厚度缺失，或不大于 Dn、或超过路线上限时返回 None。"""
    try:
        dn = float(dn)
        t0 = float(t0)
        d1 = float(d1 or 0)
        d3 = float(d3 or 0)
        d2 = float(d2 or 0)
        max_d0 = float(max_d0)
    except (TypeError, ValueError):
        return None
    if dn <= 0 or t0 <= 0 or d1 <= 0 or d3 <= 0 or not wall_rows:
        return None

    def evaluate(d0_value):
        solution = {
            'D0': float(d0_value),
            'Dn': dn,
            'D1': d1,
            'D2': d2,
            'D3': d3,
            'L1': float(l1 or 0),
            'L2': float(l2 or 0),
            'L3': float(l3 or 0),
            'T0': t0,
        }
        thicknesses = modified_draw_thicknesses(
            wall_rows, solution, pp_method, segments, taper_thick,
        )
        if not thicknesses:
            return None
        return min(thicknesses)

    cache_key = (
        _wall_fingerprint(wall_rows),
        pp_method,
        round(dn, 2),
        round(d1, 2),
        round(d2, 2),
        round(d3, 2),
        round(float(l1 or 0), 1),
        round(float(l2 or 0), 1),
        round(float(l3 or 0), 1),
        bool(taper_thick),
    )
    t_min = _TMIN_CACHE.get(cache_key)
    if t_min is None:
        try:
            seed = float(provisional_d0) if provisional_d0 not in (None, '') else 0.0
        except (TypeError, ValueError):
            seed = 0.0
        if seed <= 0:
            seed = dn + 10.0
        t_at_seed = evaluate(seed)
        if t_at_seed is None:
            return None
        if d2 > 0:
            # 有 D2 时 Taper 缩放不看 D0，同一几何只算一次壁厚。
            t_min = t_at_seed
            _TMIN_CACHE[cache_key] = t_min
        else:
            # 无 D2 时壁厚可能随 D0 变。必须迭代到「用该壁厚算出的 D0」
            # 再算回去仍是同一壁厚，不能只比较两个相近的试探直径。
            t_min = t_at_seed
            stable = False
            for _ in range(8):
                trial = d0_for_mandrel_clearance(dn, t0, t_min, precision)
                t_next = evaluate(trial)
                if t_next is None:
                    return None
                if abs(t_next - t_min) < 1e-9:
                    t_min = t_next
                    stable = True
                    break
                t_min = t_next
            if not stable:
                return None
            if abs(t_at_seed - t_min) < 1e-9:
                _TMIN_CACHE[cache_key] = t_min
    new_d0 = d0_for_mandrel_clearance(dn, t0, t_min, precision)
    if new_d0 <= dn or new_d0 > max_d0 or new_d0 <= 2.0 * t0:
        return None
    return new_d0


def _wall_rows(optimizer):
    cached = getattr(optimizer, '_mandrel_wall_rows', None)
    if cached is not None:
        return cached
    frame = getattr(optimizer, 'df', None)
    if hasattr(frame, 'to_dict'):
        rows = frame.to_dict('records')
    else:
        rows = list(frame or [])
    optimizer._mandrel_wall_rows = rows
    return rows


def assign_draw_blank_d0(
    optimizer,
    individual,
    pp_method,
    dn,
    t0,
    *,
    d2=0,
    l1=0,
    l2=0,
    l3=0,
    max_d0=150,
    provisional_d0=None,
    taper_thick=False,
):
    """把可行的 D0 写回 individual。不可行时不改 D0，返回 None。"""
    new_d0 = solve_mandrel_d0(
        _wall_rows(optimizer),
        pp_method=pp_method,
        dn=dn,
        t0=t0,
        d1=getattr(optimizer, 'D1', 0),
        d3=getattr(optimizer, 'D3', 0),
        d2=d2,
        l1=l1,
        l2=l2,
        l3=l3,
        segments=getattr(optimizer, 'segments', None),
        taper_thick=taper_thick,
        precision=getattr(optimizer, 'd_precision', 1),
        max_d0=max_d0,
        provisional_d0=individual.get('D0') if provisional_d0 is None else provisional_d0,
    )
    if new_d0 is None:
        return None
    individual['D0'] = new_d0
    return new_d0
