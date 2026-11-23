"""STEP tube analysis using native boundary-representation plane intersections."""
from __future__ import annotations

import copy
import logging
import os
import time
from collections import Counter
from concurrent.futures import as_completed

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import savgol_filter

from .brep_sections import read_shape, shape_points, section_shape
from .geometry import pca_long_axis, orthonormal_basis, sparse_indices_by_arc_length
from .section_pool import measurement_workers, section_pool, section_task
from .station_plan import (
    coarse_step_mm,
    correction_rounds,
    correction_tolerance_mm,
    decimate_rows,
    expand_rows,
    refine_positions,
    station_grid_mm,
    station_step_mm,
    straight_curvature_radius_mm,
    straight_end_fine_mm,
    straight_fill_mode,
    straight_probe_count,
    straight_tolerances,
)

logger = logging.getLogger(__name__)

#: 粗定位/校正（details=False）的轮廓偏转。这一段只要形心，读数不进最终结果。
#: 精测固定 0.003 不动：偏转直接决定精测的壁厚读数（实测 3 µm→8 µm 壁厚掉 4%）。
_COARSE_DEFLECTION = float(os.environ.get('GTAPP_COARSE_DEFLECTION', '0.01'))
#: 精测偏转，与历史口径一致（缓存里 contour_deflection_mm = 0.003）。
_FINE_DEFLECTION = 0.003
#: 端部投机并行的批次大小（0.25 mm 步进，64 个 = 16 mm）。
_PROBE_BATCH = 64


def _progress(cb, pct, msg):
    if cb:
        cb(int(pct), str(msg))


def _arc(points):
    return np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]


def _run_sections(pool, shape, tasks, *, details, deflection, progress_callback=None,
                  progress_lo=0., progress_hi=100., label='截面测量', progress_every=20):
    """按站位切一批截面：有池走并行，没池走串行，返回与 tasks 同序的截面字典。

    并行不改变读数——每个站位是 ``(shape, 平面)`` 的纯函数，结果按索引回填；
    单站失败（子进程挂了、OCC 抛异常）会在本进程里串行补算，绝不让某站静默变空。
    """
    n = len(tasks)
    if not n:
        return []
    if pool is None:
        results = []
        for i, (origin, normal) in enumerate(tasks):
            results.append(section_shape(shape, origin, normal, details=details,
                                         deflection=deflection))
            if progress_callback and (i % progress_every == 0 or i == n - 1):
                _progress(progress_callback,
                          progress_lo + (progress_hi - progress_lo) * (i + 1) / n,
                          f'{label} {i + 1}/{n}')
        return results

    futures = [pool.submit(section_task, (i, origin, normal, details, deflection))
               for i, (origin, normal) in enumerate(tasks)]
    results = [None] * n
    failures = 0
    done = 0
    broken = False
    for future in as_completed(futures):
        try:
            index, result = future.result()
        except Exception as exc:  # noqa: BLE001 - 单站失败回退串行
            failures += 1
            logger.warning('[分片] 并行站位失败（第 %d 次）：%s', failures, exc)
            if done == 0 and failures >= 3:
                broken = True  # 池整体不可用，不必一座座等下去
                break
            continue
        results[index] = result
        done += 1
        if progress_callback and (done % progress_every == 0 or done == n):
            _progress(progress_callback,
                      progress_lo + (progress_hi - progress_lo) * done / n,
                      f'{label} {done}/{n}')
    if broken:
        logger.warning('[分片] 进程池连续失败，改串行补算全部站位')
    if any(item is None for item in results):
        for future in futures:
            future.cancel()
        for i, (origin, normal) in enumerate(tasks):
            if results[i] is None:
                results[i] = section_shape(shape, origin, normal, details=details,
                                           deflection=deflection)
    return results


def _probe_ends(pool, shape, spline, lo, hi, start, end, progress_callback=None):
    """端部探测：保留「第一个无效截面就停」的语义，候选一次投机发出。

    越界的候选结果本来就会被丢弃，所以并行不改变 start/end 的取值。
    """
    for side in (-1, 1):
        boundary = start if side < 0 else end
        limit = lo if side < 0 else hi
        step = side * .25
        probe = boundary + step
        while (probe - limit) * side > 0:
            candidates, cursor = [], probe
            while len(candidates) < _PROBE_BATCH and (cursor - limit) * side > 0:
                candidates.append(float(cursor))
                cursor += step
            results = _run_sections(pool, shape,
                                    [(spline(x), spline(x, 1)) for x in candidates],
                                    details=False, deflection=_FINE_DEFLECTION)
            stopped = False
            for value, sec in zip(candidates, results):
                if not sec["valid"]:
                    stopped = True
                    break
                if side < 0:
                    start = value
                else:
                    end = value
            if stopped:
                break
            probe = cursor
    if progress_callback:
        _progress(progress_callback, 55, f'端部探测完成（{start:.3f} … {end:.3f} mm）')
    return start, end


def _fit(points, axis, origin):
    """Smooth only at the local 15-25 mm scale; never flatten either end."""
    t = (points - origin) @ axis
    order = np.argsort(t)
    t, points = t[order], points[order]
    keep = np.r_[True, np.diff(t) > 1e-5]
    t, points = t[keep], points[keep]
    if len(t) < 4:
        raise ValueError("有效闭合截面不足，无法构建中心线")
    w = min(5, len(t) if len(t) % 2 else len(t) - 1)
    smoothed = savgol_filter(points, w, 3, axis=0) if w >= 5 else points
    # Keep the independent axial coordinate unchanged after smoothing.
    smoothed += ((t - (smoothed - origin) @ axis)[:, None]) * axis
    return CubicSpline(t, smoothed), t


def verify_section_normals(sections):
    """Measure normal consistency without feeding dense derivative noise back.

    The fit window is physical (about ±4 mm), not a fixed number of samples, so
    a coarser measurement station does not silently average a bend away.
    """
    pts = np.array([s['position'] for s in sections])
    arc = _arc(pts)
    spacing = float(np.median(np.diff(arc))) if len(arc) > 2 else 1.
    half = max(2, int(round(4. / max(spacing, 1e-6))))
    width = 2 * half + 1
    degree = 3 if width >= 6 else max(2, width - 2)
    tangent = np.zeros_like(pts)
    for i in range(len(pts)):
        start = max(0, min(i - half, len(pts) - width))
        stop = min(len(pts), start + width)
        tangent[i] = np.polyfit(arc[start:stop] - arc[i], pts[start:stop], degree)[-2]
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:, None], 1e-12)
    angles = np.degrees(np.arccos(np.clip(np.abs(np.sum(np.array([s['normal'] for s in sections]) * tangent, axis=1)), 0, 1)))
    for s, angle in zip(sections, angles):
        s['normal_deviation_deg'] = float(angle)
    return {'max_normal_deviation_deg': float(max(angles)),
            'p95_normal_deviation_deg': float(np.percentile(angles, 95))}


def _section_needs_review(section):
    return (section.get('normal_deviation_deg', 0) > 2.
            or section['wall_thickness_min'] < .1 * section['wall_thickness']
            or section['centroid_residual_mm'] > .2)


def trim_unreliable_ends(sections):
    """Do not report a cap-clipped/unstable end as a normal tube measurement."""
    removed = []
    for side in (0, -1):
        # Only a local boundary correction; never repeat the old 20% flattening.
        for _ in range(10):
            if len(sections) < 10 or not _section_needs_review(sections[side]):
                break
            removed.append(sections.pop(side))
    for s in sections:
        if _section_needs_review(s):
            s['measurement_status'] = 'needs_review'
    return removed


def finalize_section_quality(sections, rejected, axis):
    """Quality applies to the retained measurement interval after end trimming.

    Failures beyond that interval remain in the diagnostic record, but are not
    internal missing sections. Recompute endpoint derivatives using only the
    retained centers so discarded end geometry cannot taint their normals.
    """
    quality = verify_section_normals(sections)
    for section in sections:
        section['measurement_status'] = 'needs_review' if _section_needs_review(section) else 'measured'
    projection = np.asarray([s['position'] for s in sections]) @ np.asarray(axis)
    low, high = projection.min(), projection.max()
    internal, excluded = [], []
    for failure in rejected:
        t = np.asarray(failure['position']) @ np.asarray(axis)
        (internal if low-1e-6 <= t <= high+1e-6 else excluded).append(failure)
    return quality, internal, excluded


def section_record(sec, predicted):
    thickness = sec["thickness"]
    return {
        "position": sec["center"].tolist(), "normal": sec["normal"].tolist(),
        "basis_u": sec["basis_u"].tolist(), "basis_v": sec["basis_v"].tolist(),
        "perimeter": float(sec["perimeter"]), "inner_perimeter": float(sec["inner_perimeter"]),
        "equivalent_diameter": float(sec["perimeter"] / np.pi),
        "wall_thickness": thickness["median_mm"],
        "wall_thickness_min": thickness["min_mm"], "wall_thickness_max": thickness["max_mm"],
        "wall_thickness_mean": thickness["mean_mm"],
        "wall_area": float(sec["outer_area"] - sec["inner_area"]),
        "centroid_residual_mm": float(np.linalg.norm(sec["center"] - predicted)),
        "measurement_status": "measured", "thickness_sample_count": thickness["sample_count"],
        "contour_repairs": sec["contour_repairs"],
        "plane_offset_mm": sec.get("plane_offset_mm", 0.),
        "intersection_fuzzy_mm": sec.get("intersection_fuzzy_mm", 0.),
        "outer_2d": sec["outer_2d"].tolist(), "inner_2d": sec["inner_2d"].tolist(),
        "outline_3d": sec["outline_3d"].tolist(), "inner_outline_3d": sec["inner_outline_3d"].tolist(),
    }


def _radial_profile(contour, samples=192):
    """把 2D 闭环轮廓转成等角度半径签名（相对轮廓质心），用于形状对比与填充。"""
    xy = np.asarray(contour, dtype=float)
    if len(xy) < 3:
        return np.zeros(samples, dtype=float)
    delta = xy - xy.mean(axis=0)
    radius = np.linalg.norm(delta, axis=1)
    theta = np.arctan2(delta[:, 1], delta[:, 0])
    order = np.argsort(theta)
    theta, radius = theta[order], radius[order]
    theta = np.r_[theta[-1] - 2. * np.pi, theta, theta[0] + 2. * np.pi]
    radius = np.r_[radius[-1], radius, radius[0]]
    grid = np.linspace(-np.pi, np.pi, samples, endpoint=False)
    return np.interp(grid, theta, radius)


def _contour_signature(contour, samples=192):
    """轮廓的等角度半径签名：先按弧长重采样，消除 CAD 顶点疏密带来的质心偏差。"""
    resampled = _resample_closed_contour(contour, samples)
    if resampled is None:
        return _radial_profile(contour, samples)
    return _radial_profile(resampled, samples)


def _contour_in_frame(row, frame_row, key='outer_2d'):
    """把 row 的 2D 轮廓点变换到 frame_row 的 (u, v) 坐标系（消除两站局部基的旋转差）。"""
    xy = np.asarray(row[key], dtype=float)
    u = np.asarray(row.get('basis_u', (1., 0., 0.)), dtype=float)
    v = np.asarray(row.get('basis_v', (0., 1., 0.)), dtype=float)
    offset = u[None, :] * xy[:, :1] + v[None, :] * xy[:, 1:]
    fu = np.asarray(frame_row['basis_u'], dtype=float)
    fv = np.asarray(frame_row['basis_v'], dtype=float)
    return np.column_stack([offset @ fu, offset @ fv])


def _signed_area(contour):
    """2D 闭环有向面积（>0 逆时针，<0 顺时针）。"""
    xy = np.asarray(contour, dtype=float)
    if len(xy) < 3:
        return 0.
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _normalize_orientation(contour):
    """统一轮廓绕向为逆时针，避免两站点序相反导致逐点混合自相抵消。"""
    xy = np.asarray(contour, dtype=float)
    if len(xy) < 3:
        return xy
    return xy[::-1].copy() if _signed_area(xy) < 0. else xy


def _resample_closed_contour(contour, samples=192, start_angle=0.):
    """按弧长等距重采样闭环轮廓，并从最接近 start_angle 的顶点起采样，便于两站逐点对应。"""
    xy = np.asarray(contour, dtype=float)
    if len(xy) < 3:
        return None
    if np.allclose(xy[0], xy[-1]):
        xy = xy[:-1]
    xy = _normalize_orientation(xy)
    centered = xy - xy.mean(axis=0)
    angles = np.arctan2(centered[:, 1], centered[:, 0])
    delta = (angles - start_angle + np.pi) % (2. * np.pi) - np.pi
    start = int(np.argmin(np.abs(delta)))
    xy = np.r_[xy[start:], xy[:start]]
    closed = np.r_[xy, xy[:1]]
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    total = float(seg.sum())
    if total <= 1e-9:
        return None
    s = np.r_[0., np.cumsum(seg)]
    t = np.linspace(0., total, samples, endpoint=False)
    return np.column_stack([np.interp(t, s, closed[:, 0]), np.interp(t, s, closed[:, 1])])


def _blend_contour(contour_a, contour_b, weight, target_perimeter, samples=192):
    """两根（已对齐到同一局部基的）2D 轮廓按权重混合，再缩放到目标周长。"""
    a = _resample_closed_contour(contour_a, samples)
    b = _resample_closed_contour(contour_b, samples)
    if a is None or b is None:
        raise ValueError("无法对截面轮廓做等弧长重采样")
    blended = (1. - weight) * a + weight * b
    blended = blended - blended.mean(axis=0)
    closed = np.r_[blended, blended[:1]]
    length = float(np.sum(np.linalg.norm(np.diff(closed, axis=0), axis=1)))
    if length > 1e-9:
        blended = blended * (float(target_perimeter) / length)
    return blended


def _max_curvature(spline, start_mm, end_mm, arc, dense_t, samples=9):
    """区间内中心线最大曲率（1/mm）。"""
    if end_mm - start_mm <= 1e-9:
        return 0.
    t = np.interp(np.linspace(start_mm, end_mm, samples), arc, dense_t)
    first = spline(t, 1)
    second = spline(t, 2)
    speed = np.linalg.norm(first, axis=1)
    cross = np.linalg.norm(np.cross(first, second), axis=1)
    return float(np.max(cross / np.maximum(speed, 1e-12) ** 3))


def _blend_scalar(row_a, row_b, key, weight):
    return float((1. - weight) * row_a[key] + weight * row_b[key])


def _frame_from_reference(ref_row, normal):
    """用参考实测站的局部基连续延伸出当前法向对应的 (u, v)，避免正负号翻转。"""
    n = np.asarray(normal, dtype=float)
    n = n / max(float(np.linalg.norm(n)), 1e-12)
    u = np.asarray(ref_row.get('basis_u', (1., 0., 0.)), dtype=float)
    u = u - float(np.dot(u, n)) * n
    length = float(np.linalg.norm(u))
    if length < 1e-9:
        _unused, u, _unused_v = orthonormal_basis(n)
        length = float(np.linalg.norm(u))
    u = u / max(length, 1e-12)
    v = np.cross(n, u)
    v = v / max(float(np.linalg.norm(v)), 1e-12)
    return u, v


def _filled_section(row_a, row_b, weight, *, cumulative_length, position, normal):
    """等截面段内的一行填充读数：标量按两端（恒定/线性），轮廓按平均形状重建。"""
    entry = copy.deepcopy(row_a)
    normal = np.asarray(normal, dtype=float)
    normal = normal / max(float(np.linalg.norm(normal)), 1e-12)
    basis_u, basis_v = _frame_from_reference(row_a, normal)
    position = np.asarray(position, dtype=float)
    perimeter = _blend_scalar(row_a, row_b, 'perimeter', weight)
    inner_perimeter = _blend_scalar(row_a, row_b, 'inner_perimeter', weight)
    frame_i = {'basis_u': basis_u, 'basis_v': basis_v}
    outer = _blend_contour(_contour_in_frame(row_a, frame_i), _contour_in_frame(row_b, frame_i),
                           weight, perimeter)
    inner = _blend_contour(_contour_in_frame(row_a, frame_i, 'inner_2d'),
                           _contour_in_frame(row_b, frame_i, 'inner_2d'),
                           weight, inner_perimeter)
    entry.update({
        'position': position.tolist(), 'normal': normal.tolist(),
        'basis_u': basis_u.tolist(), 'basis_v': basis_v.tolist(),
        'perimeter': perimeter, 'inner_perimeter': inner_perimeter,
        'equivalent_diameter': perimeter / np.pi,
        'wall_thickness': _blend_scalar(row_a, row_b, 'wall_thickness', weight),
        'wall_thickness_min': _blend_scalar(row_a, row_b, 'wall_thickness_min', weight),
        'wall_thickness_max': _blend_scalar(row_a, row_b, 'wall_thickness_max', weight),
        'wall_thickness_mean': _blend_scalar(row_a, row_b, 'wall_thickness_mean', weight),
        'wall_area': _blend_scalar(row_a, row_b, 'wall_area', weight),
        'centroid_residual_mm': 0., 'normal_deviation_deg': 0.,
        'measurement_status': 'interpolated', 'station_source': 'straight_fill',
        'straight_fill': True, 'thickness_sample_count': 0,
        'contour_repairs': [], 'plane_offset_mm': 0., 'intersection_fuzzy_mm': 0.,
        'outer_2d': outer.tolist(), 'inner_2d': inner.tolist(),
        'outline_3d': (position + outer[:, :1] * basis_u + outer[:, 1:] * basis_v).tolist(),
        'inner_outline_3d': (position + inner[:, :1] * basis_u + inner[:, 1:] * basis_v).tolist(),
        'cumulative_length': float(cumulative_length),
        'fill_from_mm': float(row_a['cumulative_length']),
        'fill_to_mm': float(row_b['cumulative_length']),
    })
    for stale in ('refined_station', 'recovered_station', 'contour_inherited_from_mm'):
        entry.pop(stale, None)
    return entry


def _plan_straight_fill(rows_by_index, probe_idx, stations, spline, arc, dense_t, tolerances, radius_min):
    """逐段判定：中心线曲率 + 周长/壁厚/轮廓径向差。返回 {(左,右): 是否等截面}。"""
    result = {}
    for left, right in zip(probe_idx[:-1], probe_idx[1:]):
        left, right = int(left), int(right)
        row_a, row_b = rows_by_index.get(left), rows_by_index.get(right)
        if right <= left + 1 or row_a is None or row_b is None:
            result[(left, right)] = False
            continue
        curvature = _max_curvature(spline, float(stations[left]), float(stations[right]), arc, dense_t)
        straight = curvature <= (1. / max(float(radius_min), 1e-9))
        radial = float(np.max(np.abs(_contour_signature(row_a['outer_2d'])
                                     - _contour_signature(_contour_in_frame(row_b, row_a)))))
        result[(left, right)] = bool(
            straight
            and abs(row_a['perimeter'] - row_b['perimeter']) <= tolerances['perimeter_mm']
            and abs(row_a['wall_thickness'] - row_b['wall_thickness']) <= tolerances['wall_mm']
            and radial <= tolerances['radial_mm'])
    return result


def recover_missing_sections(shape, sections, step_mm=1.):
    """Re-cut gaps with finer CAD contours; never interpolate measurements.

    The gap threshold follows the planned station pitch: a fixed 1.6 mm would
    re-cut every normal gap of a coarse grid and cancel its cost saving.
    """
    step = max(float(step_mm), 1.)
    threshold = max(1.6, 1.6 * step)
    recovered, failed, rows = 0, [], []
    for i, row in enumerate(sections):
        if i:
            previous = sections[i - 1]
            gap = np.linalg.norm(np.array(row["position"]) - previous["position"])
            if gap > threshold:
                count = max(2, int(np.ceil(gap / step)) + 1)
                for ratio in np.linspace(0., 1., count)[1:-1]:
                    p = (1 - ratio) * np.array(previous["position"]) + ratio * np.array(row["position"])
                    n = (1 - ratio) * np.array(previous["normal"]) + ratio * np.array(row["normal"])
                    sec = section_shape(shape, p, n, deflection=.0005)
                    if sec["valid"]:
                        value = section_record(sec, p)
                        if 'cumulative_length' in previous and 'cumulative_length' in row:
                            value['cumulative_length'] = float((1-ratio)*previous['cumulative_length']+ratio*row['cumulative_length'])
                        value["recovered_station"] = True
                        rows.append(value)
                        recovered += 1
                    else:
                        failed.append({"position": p.tolist(), "normal": n.tolist(), "reason": sec["reason"]})
        rows.append(row)
    return rows, failed, recovered


def analyze_brep_tube(file_path, *, algorithm="A", sparse_step_mm=25., progress_callback=None, end_treatment=None):
    """STEP 管件测量：站位之间并行（见 :mod:`section_pool`），其余步骤与历史一致。"""
    logger.info('[分片] 站内并行进程数：%d（1 表示串行）', measurement_workers())
    with section_pool(file_path) as pool:
        return _analyze_brep_tube_impl(file_path, algorithm=algorithm,
                                       sparse_step_mm=sparse_step_mm,
                                       progress_callback=progress_callback,
                                       end_treatment=end_treatment, pool=pool)


def _analyze_brep_tube_impl(file_path, *, algorithm="A", sparse_step_mm=25., progress_callback=None,
                            end_treatment=None, pool=None):
    started = time.monotonic()
    timings, cuts = {}, {}
    clock = {'t': started}

    def mark(name):
        now = time.monotonic()
        timings[name] = round(now - clock['t'], 3)
        clock['t'] = now

    _progress(progress_callback, 2, "读取 STP 原生曲面...")
    shape = read_shape(file_path)
    pts = shape_points(shape)
    origin = pts.mean(axis=0)
    axis = pca_long_axis(pts) if algorithm.upper() == "A" else np.array([0., 0., 1.])
    # Deterministic orientation; large-perimeter end is selected after measurement.
    if axis[np.argmax(abs(axis))] < 0:
        axis = -axis
    projection = (pts - origin) @ axis
    lo, hi = float(projection.min()), float(projection.max())
    if hi - lo < 5:
        raise ValueError("模型轴向范围不足 5 mm；B 算法需要模型主轴沿 Z 轴")

    # 站位策略：step_mm 决定实测布点（默认 1 mm），coarse_mm 决定找中心线的种子网格，
    # rounds/tol 决定法向校正轮数与收敛判据，grid_step 决定归档表格的抽稀步距。
    step_mm = station_step_mm()
    grid_step = station_grid_mm()
    coarse_mm = coarse_step_mm(step_mm)
    rounds_total = correction_rounds(step_mm)
    converge_tol = correction_tolerance_mm(step_mm)
    mark('read')

    def _seed_grid(step_value):
        return np.linspace(lo + .05, hi - .05, max(8, int(np.ceil((hi - lo) / step_value)) + 1))

    grid = _seed_grid(coarse_mm)

    def _seed_batch(grid_points):
        planes = [(origin + t * axis, axis) for t in grid_points]
        result = _run_sections(pool, shape, planes, details=False, deflection=_COARSE_DEFLECTION,
                               progress_callback=progress_callback, progress_lo=5., progress_hi=25.,
                               label='原生截面定位', progress_every=10)
        return [(i, sec["center"]) for i, sec in enumerate(result) if sec["valid"]], len(planes)

    seed, count = _seed_batch(grid)
    cuts['seeds'] = cuts.get('seeds', 0) + count
    if len(seed) < 4 and coarse_mm > 5. + 1e-9:
        # 粗网格在急弯/短过渡处可能取不到足够种子；退回 5 mm 网格重算，绝不拿 3 个点拟合整根中心线。
        logger.warning('[粗定位] %g mm 网格只得到 %d 个有效种子，退回 5 mm 网格重算',
                       coarse_mm, len(seed))
        coarse_mm = 5.
        grid = _seed_grid(coarse_mm)
        seed, count = _seed_batch(grid)
        cuts['seeds'] += count
        seed_retry = True
    else:
        seed_retry = False
    if len(seed) < 4:
        raise ValueError("找不到足够的双闭环截面；请检查管件中空、封闭或轴向选择是否正确")
    positions = {i: np.asarray(center, dtype=float) for i, center in seed}
    spline, ts = _fit(np.asarray([positions[i] for i in sorted(positions)]), axis, origin)
    mark('seeds')

    # 中心线法向校正：每轮都必须重切全部定位站位。实测证据（1324-1，相对 1 mm 基线的周长最大差）：
    #   5 mm 网格 × 3 轮全切 = 0.0052 mm（可用）；× 2 轮全切 = 4.39 mm；按残差「按需重切」= 1.36 mm。
    # 斜切椭圆的质心偏置是沿管面方向的，样条残差看不见它，少切一轮就再也回不来。
    iteration_shift = []
    corrections = []
    for rnd in range(rounds_total):
        round_started = time.monotonic()
        coarse_planes = [(spline(t), spline(t, 1)) for t in grid]
        probes = _run_sections(pool, shape, coarse_planes, details=False,
                               deflection=_COARSE_DEFLECTION,
                               progress_callback=progress_callback,
                               progress_lo=min(25. + rnd * 8., 50.),
                               progress_hi=min(33. + rnd * 8., 58.),
                               label=f"中心线法向校正 {rnd + 1}/{rounds_total}")
        round_shifts = []
        round_positions = {}
        for index, ((predicted, _normal), sec) in enumerate(zip(coarse_planes, probes)):
            if sec["valid"]:
                round_positions[index] = np.asarray(sec["center"], dtype=float)
                round_shifts.append(float(np.linalg.norm(sec["center"] - predicted)))
        if len(round_positions) >= 4:
            positions = round_positions
            spline, ts = _fit(np.asarray([positions[i] for i in sorted(positions)]), axis, origin)
        shift = float(max(round_shifts)) if round_shifts else None
        iteration_shift.append(shift)
        cuts['corrections'] = cuts.get('corrections', 0) + len(grid)
        corrections.append({
            'round': rnd + 1,
            'grid_mm': round(float(grid[1] - grid[0]), 3) if len(grid) > 1 else 0.,
            'sections': len(grid),
            'stations': len(positions),
            'seconds': round(time.monotonic() - round_started, 3),
            'max_shift_mm': shift,
        })
        if converge_tol > 0 and shift is not None and shift <= converge_tol:
            break
    mark('corrections')

    # First measure complete rings; the final end-treatment stage explicitly
    # restores missing end stations and labels their values as extrapolated.
    start, end = _probe_ends(pool, shape, spline, lo, hi, float(ts[0]), float(ts[-1]),
                             progress_callback=progress_callback)
    mark('probe')

    dense_t = np.linspace(start, end, max(10, int((end - start) * 4) + 1))
    dense_pts = spline(dense_t)
    arc = _arc(dense_pts)
    stations = np.r_[np.arange(0., arc[-1], step_mm), arc[-1]]
    target_t = np.interp(stations, arc, dense_t)
    sections = []
    rejected = []
    straight_info = None
    measured_count = 0

    # S5 精测：偏转固定 0.003（进最终读数）。粗测判定模式下先只切「判定站位」，
    # 相邻判定站读数一致（且中心线够直）的区间就跳过 1 mm 切刀，按两端读数填充。
    def _measure(indices, label, lo_pct, hi_pct):
        indices = np.asarray(indices, dtype=int)
        planes = [(spline(target_t[i]), spline(target_t[i], 1)) for i in indices]
        secs = _run_sections(pool, shape, planes, details=True,
                             deflection=_FINE_DEFLECTION,
                             progress_callback=progress_callback,
                             progress_lo=lo_pct, progress_hi=hi_pct, label=label)
        rows = {}
        for i, (predicted, normal), sec in zip(indices, planes, secs):
            if not sec["valid"]:
                rejected.append({"requested_length_mm": float(stations[i]), "reason": sec["reason"],
                                 "position": predicted.tolist(),
                                 "normal": (normal / np.linalg.norm(normal)).tolist()})
                continue
            entry = section_record(sec, predicted)
            entry['cumulative_length'] = float(stations[i])
            rows[int(i)] = entry
        return rows

    probe_count = straight_probe_count()
    fill_mode = straight_fill_mode()
    tolerances = straight_tolerances()
    probe_mode = (step_mm <= 1. + 1e-9 and fill_mode != 'off'
                  and probe_count >= 4 and len(stations) >= probe_count + 8)
    if probe_mode:
        probe_idx = np.unique(np.rint(np.linspace(0, len(stations) - 1, probe_count)).astype(int))
        end_mm = straight_end_fine_mm()
        forced = (np.where((stations <= end_mm) | (stations >= stations[-1] - end_mm))[0]
                  if end_mm > 0 else np.zeros(0, dtype=int))
        first = np.unique(np.r_[probe_idx, forced]).astype(int)
        rows = _measure(first, "粗测判定站位", 56., 70.)
        measured_count += int(len(first))
        probe_rows = {int(i): rows[int(i)] for i in probe_idx if int(i) in rows}
        interval_ok = _plan_straight_fill(probe_rows, probe_idx, stations, spline, arc, dense_t,
                                          tolerances, straight_curvature_radius_mm())
        measured_set = set(int(i) for i in first)
        fill_indices, need_indices = [], []
        for (left, right), ok in interval_ok.items():
            interior = [i for i in range(left + 1, right) if i not in measured_set]
            if ok:
                fill_indices.extend(interior)
            else:
                need_indices.extend(interior)
        remaining = np.array(sorted(set(need_indices)), dtype=int)
        if len(remaining):
            rows.update(_measure(remaining, "精确截面测量", 70., 96.))
            measured_count += int(len(remaining))
        for i in fill_indices:
            left = int(max(k for k in probe_idx if k < i))
            right = int(min(k for k in probe_idx if k > i))
            row_a, row_b = rows.get(left), rows.get(right)
            if row_a is None or row_b is None:
                continue
            if fill_mode == 'linear':
                weight = (float(stations[i]) - float(stations[left])) / max(float(stations[right]) - float(stations[left]), 1e-9)
            else:
                weight = 0.5
            t = target_t[i]
            sections.append(_filled_section(row_a, row_b, weight,
                                            cumulative_length=stations[i],
                                            position=spline(t),
                                            normal=spline(t, 1)))
        sections.extend(rows[i] for i in sorted(rows))
        sections.sort(key=lambda s: s['cumulative_length'])
        straight_info = {
            'enabled': True, 'probe_stations': int(len(probe_idx)),
            'probe_spacing_mm': (float(np.median(np.diff(stations[probe_idx])))
                                 if len(probe_idx) > 1 else 0.),
            'fill_mode': fill_mode, 'thresholds': tolerances,
            'curvature_radius_mm': float(straight_curvature_radius_mm()),
            'planned_stations': int(len(stations)),
            'measured_fine_stations': int(measured_count),
            'filled_stations': int(len(fill_indices)),
            'intervals': int(len(interval_ok)),
            'passed_intervals': int(sum(1 for value in interval_ok.values() if value)),
            'saved_cuts': int(len(fill_indices)),
        }
    else:
        rows = _measure(np.arange(len(stations)), "精确截面测量", 56., 98.)
        sections = list(rows.values())
        sections.sort(key=lambda s: s['cumulative_length'])
        measured_count = len(sections)
    cuts['fine'] = int(measured_count)
    if len(sections) < 4:
        raise ValueError("法向截面测量失败：有效截面不足")
    mark('fine')

    # 过渡段加密：粗站距只在曲率变化处补 1 mm，直段/等斜率锥段靠回填插值。
    refined_count = 0
    if step_mm > 1. + 1e-9:
        ordered = sorted(sections, key=lambda s: s['cumulative_length'])
        planned = refine_positions([r['cumulative_length'] for r in ordered],
                                   [r['perimeter'] for r in ordered],
                                   [r['wall_thickness'] for r in ordered],
                                   step_mm=step_mm)
        if len(planned):
            extra_planes = [(spline(t), spline(t, 1)) for t in np.interp(np.asarray(planned), arc, dense_t)]
            extra_secs = _run_sections(pool, shape, extra_planes, details=True,
                                       deflection=_FINE_DEFLECTION,
                                       progress_callback=progress_callback,
                                       progress_lo=96., progress_hi=98.,
                                       label='过渡段加密')
            for length, (predicted, normal), sec in zip(planned, extra_planes, extra_secs):
                if not sec["valid"]:
                    rejected.append({"requested_length_mm": float(length), "reason": sec["reason"],
                                     "stage": "refine",
                                     "position": predicted.tolist(),
                                     "normal": (normal / np.linalg.norm(normal)).tolist()})
                    continue
                entry = section_record(sec, predicted)
                entry['cumulative_length'] = float(length)
                entry['station_source'] = 'refined'
                entry['refined_station'] = True
                sections.append(entry)
                refined_count += 1
            sections.sort(key=lambda s: s['cumulative_length'])
            cuts['refine'] = len(planned)
    mark('refine')

    _progress(progress_callback, 98, "校核局部中心线法向与实际截面...")
    original_rejected = rejected
    sections, rejected, recovered = recover_missing_sections(shape, sections, step_mm=step_mm)
    for failure in original_rejected:
        if min(np.linalg.norm(np.array(s['position'])-failure['position']) for s in sections) > .55:
            if not any(np.linalg.norm(np.array(r['position'])-failure['position']) < .55 for r in rejected):
                rejected.append(failure)
    normal_quality = verify_section_normals(sections)
    for section in sections:
        if section.get('straight_fill'):
            continue  # 填充行不是实测读数，保留 interpolated 标记
        section['measurement_status'] = 'needs_review' if _section_needs_review(section) else 'measured'
    from .end_treatment import complete_ends, planar_end_faces
    planes = planar_end_faces(shape, axis)
    if planes is None:
        planes = [dict(point=(origin+t*axis).tolist(), normal=axis.tolist(), source='axial_support_plane') for t in (lo, hi)]
    if np.mean([s["perimeter"] for s in sections[-10:]]) > np.mean([s["perimeter"] for s in sections[:10]]) + 1e-5:
        reference_last = sections[-1]['cumulative_length']
        sections.reverse()
        planes.reverse()
        for s in sections:
            s['cumulative_length'] = reference_last-s['cumulative_length']
            s["normal"] = (-np.array(s["normal"])).tolist()
            s["basis_v"] = (-np.array(s["basis_v"])).tolist()
            for key in ("outer_2d", "inner_2d"):
                s[key] = [[x, -y] for x, y in s[key]]
    sections, end_info = complete_ends(sections, planes, end_treatment)
    mark('ends')

    # 网格：先把实测层铺成 1 mm 计算层（计算层永远 1 mm），再按 GTAPP_STATION_GRID 抽稀成归档层。
    grid_info = None
    if step_mm > 1. + 1e-9 or grid_step > 1. + 1e-9:
        _progress(progress_callback, 98, f"重建 {grid_step:g} mm 站位网格...")
        dense_rows, grid_info = expand_rows(sections, grid_step=1.0)
        if grid_step > 1. + 1e-9:
            dense_rows, archived = decimate_rows(dense_rows, grid_step=grid_step)
            grid_info = dict(grid_info,
                             archive_step_mm=float(archived.get('archive_step_mm', grid_step)),
                             archive_rows=int(len(dense_rows)))
        sections = dense_rows
    mark('grid')
    dense_sections = sections if grid_step > 1. + 1e-9 else None

    centerline = np.array([s["position"] for s in sections])
    lengths = np.array([s['cumulative_length'] for s in sections])
    measured = [s for s in sections
                if s['measurement_status'] != 'extrapolated' and not s.get('straight_fill')]
    limits = sorted([np.dot(measured[0]['position'], axis), np.dot(measured[-1]['position'], axis)])
    excluded_rejections = [r for r in rejected if not limits[0]-1e-6 <= np.dot(r['position'], axis) <= limits[1]+1e-6]
    rejected = [r for r in rejected if limits[0]-1e-6 <= np.dot(r['position'], axis) <= limits[1]+1e-6]
    normal_quality['max_normal_deviation_deg'] = max(s.get('normal_deviation_deg', 0) for s in measured)
    normal_quality['p95_normal_deviation_deg'] = float(np.percentile([s.get('normal_deviation_deg', 0) for s in measured], 95))
    # 稀疏截面只从有真实轮廓的站里选：插值站的轮廓是借来的，不能当独立读数点。
    contoured = [s for s in sections if s.get("outline_3d") and not s.get("straight_fill")]
    contoured_lengths = np.array([s['cumulative_length'] for s in contoured])
    sparse = []
    for i in sparse_indices_by_arc_length(contoured_lengths, step_mm=sparse_step_mm):
        s = contoured[i]
        sparse.append({k: s[k] for k in ("position", "perimeter", "wall_thickness", "cumulative_length", "index")}
                      | {"outline": s["outline_3d"] + [s["outline_3d"][0]]})
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    prop = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, prop)
    bbox = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bbox)
    wt = np.array([s["wall_thickness"] for s in sections])
    residual = max(s["centroid_residual_mm"] for s in measured)
    excluded = [e['trim_length_mm'] for e in end_info['ends']]
    warnings = [f"两端舍弃并线性外推 {excluded[0]:.2f} / {excluded[1]:.2f} mm，补齐 {end_info['extrapolated_station_count']} 个站位；长度坐标保持完整，外推值已单独标记。"]
    if straight_info:
        mode_text = '两端平均后恒定' if straight_info['fill_mode'] == 'const' else '按弧长线性插值'
        warnings.insert(0, f"粗测判定：{straight_info['probe_stations']} 个判定站位、"
                           f"{straight_info['passed_intervals']}/{straight_info['intervals']} 段判为等截面，"
                           f"{straight_info['filled_stations']} 个 1 mm 站位按{mode_text}填充（非实测读数，已单独标记）；"
                           f"实际精测 {straight_info['measured_fine_stations']}/{straight_info['planned_stations']} 站。")
    if grid_info and grid_info.get('interpolated'):
        warnings.insert(0, f"实测站位按 {step_mm:g} mm 布点、过渡段补 1 mm：{grid_info['interpolated']} 个 1 mm 站位由 PCHIP 插值（周长/壁厚），其截面轮廓沿用最近实测站，不能当作独立读数点。")
    if grid_step > 1. + 1e-9:
        warnings.insert(0, f"归档站位按 {grid_step:g} mm 抽稀（{len(sections)} 行，GTAPP_STATION_GRID）：表格与入库 payload 只保留这一层；形线质检、壁厚分段、工艺优化仍按 1 mm 网格计算，读数不受影响。")
    if rejected:
        warnings.append(f"有效范围内 {len(rejected)} 个截面测量失败；保留缺测记录，不填造数值。")
    if residual > .2:
        warnings.append(f"中心线法向迭代最大残差 {residual:.3f} mm，需复核局部造型。")
    review_count = sum(s['measurement_status'] == 'needs_review' for s in sections)
    if review_count:
        warnings.append(f"{review_count} 个截面的法向或最薄壁位置需人工复核，不能直接用于工艺计算。")
    timings['assemble'] = round(time.monotonic() - clock['t'], 3)
    timings['total_seconds'] = round(time.monotonic() - started, 3)
    quality = {
        "engine": "native_brep_v1", "center_definition": "outer_area_centroid_of_normal_sections",
        "post_trim_quality_version": 3,
        "end_treatment": end_info,
        "centroid_method": "CAD_intersection_contour_area_at_0.003mm_deflection",
        "thickness_definition": "section_plane_shortest_distance_outer_to_inner",
        "thickness_summary": "median_of_192_uniform_outer_arc_samples",
        "contour_deflection_mm": .003, "station_step_mm": 1.,
        "measurement_station_step_mm": float(step_mm),
        "station_grid_step_mm": float(grid_step),
        "station_grid": grid_info,
        "refined_station_count": int(refined_count),
        "coarse_step_mm": float(coarse_mm),
        "seed_retry": bool(seed_retry),
        "correction_rounds": int(len(corrections)),
        "correction_tolerance_mm": float(converge_tol),
        "corrections": corrections,
        "stage_timings": timings,
        "cuts": cuts,
        "straight_fill": straight_info,
        "extrapolated_end_lengths_mm": excluded, "axial_span_mm": hi - lo,
        "coverage_ratio": 1., "rejected_sections": rejected,
        "excluded_end_rejections": excluded_rejections,
        "recovered_stations": recovered,
        "rejection_counts": dict(Counter(s["reason"] for s in rejected)),
        "max_centroid_residual_mm": residual, "iteration_max_shift_mm": iteration_shift,
        "warnings": warnings, "requires_review": bool(review_count or rejected),
        "unreliable_end_stations_removed": 0,
        "extrapolated_station_count": end_info['extrapolated_station_count'],
        "geometric_centerline_length_mm": float(_arc(centerline)[-1]),
        "review_station_count": review_count,
        "elapsed_seconds": time.monotonic() - started,
        **normal_quality,
    }
    _progress(progress_callback, 100, "STP 原生截面分析完成")
    payload = {"algorithm": algorithm.upper(), "source_format": "stp", "analysis_quality": quality,
               "sections_full": sections, "sections": sparse, "center_line_points": centerline.tolist(),
               "center_line_length": float(lengths[-1]),
               "centerline_endpoint_length": float(np.linalg.norm(centerline[-1] - centerline[0])),
               "actual_z_length": float(np.linalg.norm(centerline[-1] - centerline[0])),
               "bounding_box": list(bbox.Get()), "volume": abs(float(prop.Mass())),
               "wall_thickness_stats": {"median_mm": float(np.median(wt)), "mean_mm": float(wt.mean()),
                                        "min_mm": float(wt.min()), "max_mm": float(wt.max()), "count": len(wt)}}
    if dense_sections is not None:
        payload["sections_dense"] = dense_sections
    return payload
