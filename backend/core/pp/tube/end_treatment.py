"""Replace unreliable end values on an immutable, full-length station axis.

Measured interior sections are retained. End values are explicitly extrapolated,
including local contours, so process tables and CAD lofts use the same data.
"""
from __future__ import annotations

import copy
import numpy as np
from shapely.geometry import Polygon
from scipy.optimize import brentq

from .brep_sections import contour_thickness


def end_options(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('端部修正参数必须为对象')
    result = {}
    for key in ('start_trim_mm', 'end_trim_mm'):
        v = value.get(key)
        result[key] = None if v in (None, '') else float(v)
        if result[key] is not None and (not np.isfinite(result[key]) or result[key] < 0):
            raise ValueError('端部舍弃长度必须是有限非负数')
    result['fit_window_mm'] = float(value.get('fit_window_mm', 10))
    if not np.isfinite(result['fit_window_mm']) or not 2 <= result['fit_window_mm'] <= 100:
        raise ValueError('端部拟合窗口必须在 2～100 mm 之间')
    result['shape_extrapolation'] = str(value.get('shape_extrapolation', 'c1')).lower()
    if result['shape_extrapolation'] not in ('c1', 'v1'):
        raise ValueError('端部外推形状策略必须为 c1 或 v1')
    return result


def planar_end_faces(shape, axis):
    """Prefer annular CAD end faces; curved/non-planar ends use support planes."""
    from OCP.TopAbs import TopAbs_FACE, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    axis = np.asarray(axis, float)
    candidates = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face_s(ex.Current())
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Plane:
            plane = surface.Plane()
            normal = np.array(plane.Axis().Direction().Coord())
            wires, count = TopExp_Explorer(face, TopAbs_WIRE), 0
            while wires.More():
                count += 1
                wires.Next()
            if count >= 2 and abs(normal @ axis) > .35:
                prop = GProp_GProps()
                BRepGProp.SurfaceProperties_s(face, prop)
                point = np.array(prop.CentreOfMass().Coord())
                candidates.append({'point': point.tolist(), 'normal': normal.tolist(),
                                   'source': 'cad_planar_end_face'})
        ex.Next()
    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda p: np.dot(p['point'], axis))
    return [candidates[0], candidates[-1]]


def _fit(rows, anchor, side, window):
    s0 = rows[anchor]['cumulative_length']
    available = [i for i in range(len(rows))
                 if (i-anchor)*side >= 0 and abs(rows[i]['cumulative_length']-s0) <= window+1e-6
                 and rows[i].get('measurement_status') == 'measured']
    if len(available) < 3:
        raise ValueError('端部可靠拟合数据不足，请调整舍弃长度或拟合窗口')
    x = np.array([rows[i]['cumulative_length']-s0 for i in available])
    denom = float(x @ x)
    if denom < 1e-8:
        raise ValueError('端部拟合站位重复')
    def slope(key):
        values = np.array([rows[i][key] for i in available], float)
        return np.tensordot(x, values-np.array(rows[anchor][key]), axes=1)/denom
    return {'indices': available, 'position': slope('position'),
            'perimeter': float(slope('perimeter')), 'wall_thickness': float(slope('wall_thickness'))}


def _stable_candidates(rows, side, window):
    result=[]
    for i,row in enumerate(rows):
        if row.get('measurement_status') != 'measured':
            continue
        neighbours=[r for j,r in enumerate(rows) if (j-i)*side >= 0
                    and abs(r['cumulative_length']-row['cumulative_length']) <= window+1e-6]
        if len(neighbours) >= 3 and all(r.get('measurement_status') == 'measured' for r in neighbours):
            result.append(i)
    return result


def _boundary_station(row, slope, plane):
    normal = np.asarray(plane['normal'], float)
    denom = float(slope @ normal)
    if abs(denom) < 1e-5:
        raise ValueError('端部拟合方向与端面近似平行，无法线性外推')
    return row['cumulative_length'] + float((np.array(plane['point'])-row['position']) @ normal)/denom


def _scaled_contours(anchor, perimeter, thickness):
    """Keep the nearest reliable non-circular form; match P and median wall."""
    outer = np.array(anchor['outer_2d'], float)
    poly = Polygon(outer)
    outer = (outer-np.array(poly.centroid.coords)[0]) * (perimeter/poly.length)
    inner0 = (np.array(anchor['inner_2d'], float)-np.array(poly.centroid.coords)[0]) * (perimeter/poly.length)
    boundary = Polygon(outer)
    inner_poly = Polygon(inner0)
    if boundary.convex_hull.area-boundary.area > 1e-5:
        # Homothetic shrinkage around a centroid outside a concavity is invalid.
        # Offset the existing inner wall instead; retain a single nested loop.
        def offset_shape(distance):
            candidate = inner_poly.buffer(distance, join_style=2)
            if candidate.geom_type != 'Polygon' or candidate.is_empty or not candidate.is_valid or not boundary.contains(candidate):
                return None
            return np.asarray(candidate.exterior.coords)[:-1]
        def error_offset(distance):
            points = offset_shape(distance)
            if points is None:
                raise ValueError('端部外推内轮廓无法保持单一闭环')
            return contour_thickness(outer, points)['median_mm']-thickness
        base_error = error_offset(0.)
        if abs(base_error) < 1e-7:
            return outer, inner0, contour_thickness(outer, inner0)
        sign = 1 if base_error > 0 else -1
        bound = sign*max(thickness*2, 1.)
        if offset_shape(bound) is None:
            a,b=0.,abs(bound)
            for _ in range(40):
                middle=(a+b)/2
                if offset_shape(sign*middle) is not None:a=middle
                else:b=middle
            bound=sign*a*(1-1e-7)
        if base_error*error_offset(bound) > 0:
            raise ValueError('外推壁厚超出该凹形截面的可用范围')
        distance=brentq(error_offset,min(0.,bound),max(0.,bound),xtol=1e-9)
        inner=offset_shape(distance)
        return outer,inner,contour_thickness(outer,inner)
    center = np.array(Polygon(inner0).centroid.coords)[0]
    if not boundary.contains(Polygon(inner0)):
        center = np.array(boundary.representative_point().coords)[0]
    def inner_at(factor):
        return center+(inner0-center)*factor
    # For concave/eccentric contours, only evaluate strictly nested polygons.
    top = 1.
    while boundary.contains(Polygon(inner_at(top))) and top < 16:
        top *= 1.1
    low = .0001
    for _ in range(45):
        mid = (low+top)/2
        if boundary.contains(Polygon(inner_at(mid))):
            low = mid
        else:
            top = mid
    maximum = low*(1-1e-8)
    def error(factor):
        return contour_thickness(outer, inner_at(factor))['median_mm']-thickness
    if error(.0001) <= 0 or error(maximum) >= 0:
        raise ValueError('外推壁厚无法形成有效内轮廓，请调整端部舍弃范围')
    factor = brentq(error, .0001, maximum, xtol=1e-9)
    inner = inner_at(factor)
    if not boundary.is_valid or not Polygon(inner).is_valid or not boundary.contains(Polygon(inner)):
        raise ValueError('端部外推轮廓自交或内外壁相交')
    return outer, inner, contour_thickness(outer, inner)


def _resample_closed(points, count=256):
    """Uniform arc-length resampling of one closed contour."""
    q = np.asarray(points, float)
    if q.ndim != 2 or q.shape[1] != 2 or len(q) < 8 or not np.isfinite(q).all():
        raise ValueError('端部轮廓点数不足或包含非法坐标')
    keep = np.r_[True, np.linalg.norm(np.diff(q, axis=0), axis=1) > 1e-10]
    q = q[keep]
    if np.linalg.norm(q[0]-q[-1]) < 1e-10:
        q = q[:-1]
    if len(q) < 8:
        raise ValueError('端部轮廓有效点不足')
    closed = np.vstack([q, q[:1]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    s = np.r_[0., np.cumsum(seg)]
    if s[-1] < 1e-8:
        raise ValueError('端部轮廓长度为 0')
    target = np.linspace(0., s[-1], count, endpoint=False)
    return np.column_stack([np.interp(target, s, closed[:, 0]),
                            np.interp(target, s, closed[:, 1])])


def _align_cyclic(contour, reference):
    """Align a resampled loop to the anchor start point, preserving direction."""
    best = None
    best_score = float('inf')
    for shift in range(len(contour)):
        shifted = np.roll(contour, -shift, axis=0)
        score = float(np.mean(np.sum((shifted-reference)**2, axis=1)))
        if score < best_score:
            best, best_score = shifted, score
    return best


def _local_contour(row, frame):
    """Outer contour in the local (side, up) frame of ``frame``."""
    points = np.asarray(row['outer_2d'], float)
    p3 = (np.asarray(row['position'], float)
          + points[:, :1]*np.asarray(row['basis_u'], float)
          + points[:, 1:]*np.asarray(row['basis_v'], float))
    d = p3-np.asarray(frame['position'], float)
    return np.column_stack([d@np.asarray(frame['basis_u'], float),
                            d@np.asarray(frame['basis_v'], float)])


def _shape_fit(rows, anchor_idx, side, window):
    """Fit outer-shape modes with a C1 (through-origin) slope at the anchor.

    The anchor contour is the constant term; each shape mode is multiplied by
    its measured axial slope, so the predicted loop has both the anchor outline
    and the anchor shape derivative at ``delta=0``.
    """
    anchor = rows[anchor_idx]
    s0 = float(anchor['cumulative_length'])
    available = [i for i in range(len(rows))
                 if (i-anchor_idx)*side >= 0 and abs(rows[i]['cumulative_length']-s0) <= window+1e-6
                 and rows[i].get('measurement_status') == 'measured']
    if anchor_idx not in available:
        available.append(anchor_idx)
    if len(available) < 4:
        raise ValueError('端部形状拟合数据不足')
    # Nearest stations first; distant stations are useful for slope, not noise.
    available = sorted(set(available), key=lambda i: abs(rows[i]['cumulative_length']-s0))[:8]
    q0 = _resample_closed(anchor['outer_2d'])
    t, displacements = [], []
    for i in available:
        dt = float(rows[i]['cumulative_length'])-s0
        if abs(dt) < 1e-9:
            continue
        q = _align_cyclic(_resample_closed(_local_contour(rows[i], anchor)), q0)
        t.append(dt)
        displacements.append((q-q0).ravel())
    if len(displacements) < 3:
        raise ValueError('端部形状拟合有效站不足')
    t = np.asarray(t, float)
    d = np.asarray(displacements, float)
    _, singular, vt = np.linalg.svd(d, full_matrices=False)
    if singular.size == 0 or singular[0] < 1e-9:
        raise ValueError('端部形状没有可外推的变化')
    energy = singular**2
    keep = min(6, int(np.searchsorted(np.cumsum(energy)/energy.sum(), 0.995))+1, len(singular))
    modes = vt[:keep]
    scores = d@modes.T
    slopes = (t@scores)/float(t@t)
    return {'anchor_index': int(anchor_idx), 'q0': q0, 'modes': modes,
            'slopes': slopes, 'window': float(window), 'station_count': len(t)+1}


def _predict_shape(shape, delta):
    flat = shape['q0'].ravel() + float(delta)*(shape['slopes']@shape['modes'])
    outer = flat.reshape(-1, 2)
    if not np.isfinite(outer).all() or not Polygon(outer).is_valid:
        raise ValueError('端部 C1 形状外推产生自交轮廓')
    return outer


def _inner_from_outer(outer, wall, anchor):
    """Nest an inner loop at the target wall; fall back to the old offsetter."""
    boundary = Polygon(outer)
    candidate = boundary.buffer(-float(wall), join_style=2)
    if (candidate.geom_type == 'Polygon' and not candidate.is_empty
            and candidate.is_valid and boundary.contains(candidate)):
        inner = np.asarray(candidate.exterior.coords)[:-1]
        return outer, inner, contour_thickness(outer, inner)
    # Concave/offset failure: reuse the proven wall-offset path with the
    # already-C1 outer loop as the shape template. This only affects the inner
    # wall; the outer loop and its C1 derivative are unchanged.
    temp = {'outer_2d': outer.tolist(), 'inner_2d': anchor['inner_2d']}
    return _scaled_contours(temp, float(boundary.length), float(wall))


def _extrapolated(anchor, station, fit, normal, u, v, side, shape=None):
    delta = station-anchor['cumulative_length']
    wall = anchor['wall_thickness'] + delta*fit['wall_thickness']
    if not np.isfinite(wall) or wall <= 0:
        raise ValueError('端部外推产生非正壁厚，请调整舍弃范围')
    if shape is not None:
        outer = _predict_shape(shape, delta)
        perimeter = float(Polygon(outer).length)
        outer, inner, stats = _inner_from_outer(outer, wall, anchor)
        source = 'c1_shape_extrapolation'
    else:
        perimeter = anchor['perimeter'] + delta*fit['perimeter']
        if not np.isfinite(perimeter) or perimeter <= 0:
            raise ValueError('端部线性外推产生非正周长，请调整舍弃范围')
        outer, inner, stats = _scaled_contours(anchor, perimeter, wall)
        source = 'linear_end_extrapolation'
    position = np.array(anchor['position']) + delta*fit['position']
    row = copy.deepcopy(anchor)
    row.update(position=position.tolist(), normal=normal.tolist(), basis_u=u.tolist(), basis_v=v.tolist(),
               cumulative_length=float(station), perimeter=float(perimeter), equivalent_diameter=float(perimeter/np.pi),
               inner_perimeter=float(Polygon(inner).length), wall_thickness=float(wall),
               wall_thickness_min=stats['min_mm'], wall_thickness_max=stats['max_mm'],
               wall_thickness_mean=stats['mean_mm'], wall_area=float(Polygon(outer).area-Polygon(inner).area),
               outer_2d=outer.tolist(), inner_2d=inner.tolist(),
               outline_3d=(position+outer@np.array([u,v])).tolist(),
               inner_outline_3d=(position+inner@np.array([u,v])).tolist(),
               measurement_status='extrapolated', measurement_source=source,
               extrapolation_side=side, extrapolation_anchor_mm=anchor['cumulative_length'],
               centroid_residual_mm=None, normal_deviation_deg=None,
               thickness_sample_count=0, plane_offset_mm=0., intersection_fuzzy_mm=0.)
    row.pop('x_length', None)
    row.pop('y_length', None)
    return row


def complete_ends(sections, planes, options=None):
    """Freeze full station coordinates, then replace end values without re-lengthing.

    ``planes`` are ordered from the first to the last section. Original station
    differences and fractional last stations are retained exactly.
    """
    options = end_options(options)
    rows = copy.deepcopy(sections)
    raw_s = np.array([r['cumulative_length'] for r in rows], float)
    if len(rows) < 6 or not np.all(np.diff(raw_s)>0):
        raise ValueError('端部修正需要至少六个严格递增的有效截面')
    spacing = float(np.median(np.diff(raw_s))) if len(raw_s) > 1 else 1.
    # 粗站距（GTAPP_STATION_MM>1）下 10 mm 窗口只剩 3-4 个实测站，按「至少 4 站」放宽窗口；
    # 1 mm 网格时 window 恒等于 options['fit_window_mm']，行为与历史逐位一致。
    window = float(options['fit_window_mm'])
    if spacing > 1.5:
        window = max(window, 4. * spacing)
    good = [i for i,r in enumerate(rows) if r.get('measurement_status') == 'measured']
    if len(good) < 6:
        raise ValueError('可靠截面不足，无法推算端部')
    stable_start,stable_end=_stable_candidates(rows,1,window),_stable_candidates(rows,-1,window)
    if not stable_start or not stable_end:
        raise ValueError('找不到连续可靠的端部拟合区间')
    first, last = stable_start[0], stable_end[-1]
    fits = [_fit(rows, first, 1, window), _fit(rows, last, -1, window)]
    bounds = [_boundary_station(rows[i], fit['position'], plane) for i,fit,plane in zip((first,last), fits, planes)]
    bounds = [0. if abs(value) < 1e-9 else value for value in bounds]
    if bounds[0] >= raw_s[first] or bounds[1] <= raw_s[last]:
        raise ValueError('端面未包围可靠测量区间，请检查模型端面或轴向')
    total = bounds[1]-bounds[0]
    # The reference length is fixed before requested end exclusions are applied.
    for row in rows:
        row['cumulative_length'] -= bounds[0]
    positions = np.array([r['cumulative_length'] for r in rows])
    wanted_start = positions[first] if options['start_trim_mm'] is None else options['start_trim_mm']
    wanted_end = total-positions[last] if options['end_trim_mm'] is None else options['end_trim_mm']
    start_candidates = [i for i in _stable_candidates(rows,1,window) if positions[i] >= wanted_start-1e-8]
    end_candidates = [i for i in _stable_candidates(rows,-1,window) if positions[i] <= total-wanted_end+1e-8]
    if not start_candidates or not end_candidates:
        raise ValueError('端部舍弃长度超出模型范围')
    first, last = start_candidates[0], end_candidates[-1]
    if last-first < 5 or positions[last]-positions[first] < window*2:
        raise ValueError('两端舍弃后可靠区间不足两个拟合窗口')
    start_fit = _fit(rows[first:last+1], 0, 1, window)
    end_fit = _fit(rows[first:last+1], last-first, -1, window)
    shape_fits, shape_fallbacks = [], []
    for anchor_local, side in ((0, 1), (last-first, -1)):
        shape = None
        reason = None
        if options['shape_extrapolation'] == 'c1':
            try:
                shape = _shape_fit(rows[first:last+1], anchor_local, side, window)
            except ValueError as exc:
                reason = str(exc)
        shape_fits.append(shape)
        shape_fallbacks.append(reason)
    # Add missing end stations. Keep every existing interior station unchanged.
    head = np.r_[0., np.arange(1., max(0.,positions[0]), 1.)]
    head = head[head < positions[0]-1e-7]
    tail = np.r_[np.arange(np.floor(positions[-1])+1, total, 1.), total]
    tail = tail[tail > positions[-1]+1e-7]
    grid = np.r_[head, positions, tail]
    grid = grid[(grid >= -1e-7)&(grid <= total+1e-7)]
    result = []
    details = []
    for end_idx, (anchor_idx, fit, plane, endpoint, name) in enumerate(
            ((first,start_fit,planes[0],0.,'start'),(last,end_fit,planes[1],total,'end'))):
        anchor = rows[anchor_idx]
        shape = shape_fits[end_idx]
        shape_fallback = shape_fallbacks[end_idx]
        slope = fit['position']
        # Preserve the local fitted direction and meet the CAD end plane at the
        # existing endpoint station; never recompute length from adjusted XYZ.
        plane_n = np.asarray(plane['normal'])
        factor = float((np.array(plane['point'])-anchor['position'])@plane_n)/((endpoint-anchor['cumulative_length'])*float(slope@plane_n))
        if not np.isfinite(factor) or factor <= 0:
            raise ValueError('端部线性外推方向反转')
        fit['position'] = slope*factor
        normal = fit['position']/np.linalg.norm(fit['position'])
        u = np.array(anchor['basis_u']);u -= normal*(u@normal);u /= np.linalg.norm(u)
        v = np.cross(normal,u)
        fit.update(normal=normal,u=u,v=v)
        details.append({'side':name,'trim_length_mm':float(anchor['cumulative_length'] if name=='start' else total-anchor['cumulative_length']),
                        'anchor_station_mm':float(anchor['cumulative_length']), 'boundary':plane,
                        'perimeter_slope':fit['perimeter'],'wall_slope':fit['wall_thickness'],
                        'position_slope':fit['position'].tolist(),
                        'shape_extrapolation':'c1' if shape is not None else 'v1',
                        'shape_mode_count':0 if shape is None else int(len(shape['slopes'])),
                        'shape_fallback':shape_fallback})
    source_by_s={r['cumulative_length']:r for r in rows}
    for station in grid:
        if station < positions[first]-1e-7:
            f=start_fit; row=_extrapolated(rows[first],station,f,f['normal'],f['u'],f['v'],'start',shape_fits[0])
        elif station > positions[last]+1e-7:
            f=end_fit; row=_extrapolated(rows[last],station,f,f['normal'],f['u'],f['v'],'end',shape_fits[1])
        else:
            row=source_by_s[float(station)]
        row['index']=len(result)
        result.append(row)
    use_c1 = any(shape is not None for shape in shape_fits)
    metadata={'method':'c1_shape_extrapolation_v1' if use_c1 else 'linear_end_extrapolation_v1',
              'options':options,'reference_length_mm':float(total),
              'fit_window_mm':float(window), 'station_spacing_mm':float(spacing),
              'length_policy':'station_axis_frozen_before_end_value_replacement','ends':details,
              'extrapolated_station_count':sum(r['measurement_status']=='extrapolated' for r in result),
              'shape_mode_count':[0 if s is None else int(len(s['slopes'])) for s in shape_fits],
              'shape_fallback_count':sum(s is None for s in shape_fits),
              'measured_interval_mm':[float(positions[first]),float(positions[last])],
              'reference_offset_mm':float(-bounds[0]),
              'contour_policy':'anchor_shape_plus_measured_shape_gradient_c1' if use_c1
                              else 'nearest_reliable_shape_scaled_to_linear_perimeter_and_wall'}
    return result,metadata
