"""RFM geometry from measured sections and explicitly supplied process dimensions.

Lengths are station arc lengths in mm. x_length/y_length and ellipse_x/y are
FULL axes, not radii. Every exported model is a checked CAD solid; the display
mesh is tessellated from that very solid.
"""
import logging

import numpy as np
from scipy.special import ellipe
from scipy.optimize import brentq
from core.common.solid_geometry import (
    ellipse_wire, contour_wire, loft, mesh_from_shape, step_bytes, revolve_profile,
)

logger = logging.getLogger(__name__)


def perimeter_series(rows):
    if len(rows) < 2:
        raise ValueError('至少需要两条中心线周长数据')
    rows = sorted(rows, key=lambda r: float(r['中心线长度(mm)']))
    s = np.array([r['中心线长度(mm)'] for r in rows], float)
    p = np.array([r['外径周长(mm)'] for r in rows], float)
    if not np.all(np.isfinite(s)) or not np.all(np.isfinite(p)) or np.any(p <= 0):
        raise ValueError('中心线长度和外径周长必须为有限正数')
    if np.any(np.diff(s) <= 0):
        raise ValueError('截面站位重复或逆序，请重新确认工艺数据')
    return rows, s-s[0], p


def ellipse_perimeter(a, b):
    major, minor = max(a, b), min(a, b)
    return 4*major*ellipe(1-(minor/major)**2)


def ellipse_params_from_perimeter(perimeter, short_axis=None):
    if short_axis is None:
        return perimeter/(2*np.pi), perimeter/(2*np.pi)
    b = float(short_axis)/2
    if b <= 0 or 2*np.pi*b > perimeter+1e-7:
        raise ValueError('椭圆短轴与周长不相容')
    if abs(2*np.pi*b-perimeter) <= 1e-7:
        return b, b
    a = brentq(lambda a: ellipse_perimeter(a, b)-perimeter, b, perimeter/4+1e-7)
    return a, b


def transport_frames(points):
    tangent = np.gradient(np.asarray(points, float), axis=0)
    tangent /= np.linalg.norm(tangent, axis=1)[:, None]
    u = np.array([1., 0., 0.])
    if abs(np.dot(u, tangent[0])) > .9:
        u = np.array([0., 1., 0.])
    frames = []
    for n in tangent:
        u = u-n*np.dot(u, n)
        u /= np.linalg.norm(u)
        frames.append((n.copy(), u.copy(), np.cross(n, u)))
    return frames


def _grid(length, step):
    samples = np.arange(0, length, step)
    return np.r_[samples[samples < length-1e-7], length]


def _stations(values):
    ordered = np.sort(values)
    # Floating-point arange/linspace can place two nominally equal stations
    # 1e-13 mm apart, creating a valid but unmeshable zero-width CAD face.
    return ordered[np.r_[np.diff(ordered) > 1e-7, True]]


def _thin_stations(grid, min_gap=0.2):
    """Keep stations at least ``min_gap`` apart.

    The perimeter table and the centre-line stations both contribute sections;
    when the two grids land 0.003 mm apart the loft still "works" but the band
    between them is a zero-width sliver whose surface normal flips, which shows
    up later as a bogus 180 deg crease at that station. Dropping the duplicates
    keeps the geometry identical to the eye and the surface continuous.
    """
    grid = np.asarray(grid, float)
    if grid.size < 3 or min_gap <= 0:
        return grid
    keep = [grid[0]]
    for value in grid[1:-1]:
        if value - keep[-1] >= min_gap:
            keep.append(value)
    if grid[-1] - keep[-1] < min_gap and len(keep) > 1:
        keep[-1] = grid[-1]
    else:
        keep.append(grid[-1])
    return np.asarray(keep, float)


def _resample_contour(points, n=192):
    """CCW, stable +X seam, then uniform closed arc sampling (concavities kept)."""
    p = np.asarray(points, float)
    if np.linalg.norm(p[0]-p[-1]) < 1e-8:
        p = p[:-1]
    if len(p) < 4 or not np.all(np.isfinite(p)):
        raise ValueError('截面轮廓点无效')
    p = p[np.r_[True, np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-8]]
    if np.sum(p[:, 0]*np.roll(p[:, 1], -1)-p[:, 1]*np.roll(p[:, 0], -1)) < 0:
        p = p[::-1]
    hits = []
    for i, (a, b) in enumerate(zip(p, np.roll(p, -1, axis=0))):
        if (a[1] <= 0 < b[1]) or (b[1] <= 0 < a[1]):
            hit = a+(b-a)*(-a[1]/(b[1]-a[1]))
            hits.append((hit[0], i, hit))
    if not hits:
        raise ValueError('截面局部坐标未包含中心，无法对齐截面')
    _, i, hit = max(hits, key=lambda item: item[0])
    p = np.vstack((hit, p[i+1:], p[:i+1], hit))
    keep = np.r_[True, np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-8]
    p = p[keep]
    lengths = np.r_[0, np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))]
    samples = np.arange(n)*lengths[-1]/n
    return np.column_stack([np.interp(samples, lengths, p[:, k]) for k in range(2)])


def _ring_frames(rings):
    """One continuously transported frame per section ring.

    A section carries its own ``basis_u``/``basis_v``; those bases are computed
    per station and may rotate or flip between neighbouring stations.  The old
    seam rule ("largest x where the local y=0 line crosses the contour") is
    absolute in that local basis, so on an oval contour it can land on the
    opposite end of the long axis from one ring to the next.  The loft then
    pairs two rings roughly half a turn apart and the band folds.

    These frames depend only on the ring centroids, which form the ridge line,
    so they follow the tube instead of the local section basis.
    """
    from core.pp.tube.geometry import rotation_between_vectors

    centroids = np.array([np.asarray(r, float).mean(axis=0) for r in rings])
    if len(centroids) < 2:
        raise ValueError('成型至少需要两个截面')
    tangents = np.empty_like(centroids)
    tangents[0] = centroids[1]-centroids[0]
    tangents[-1] = centroids[-1]-centroids[-2]
    if len(centroids) > 2:
        tangents[1:-1] = centroids[2:]-centroids[:-2]
    lengths = np.linalg.norm(tangents, axis=1)
    if not np.all(np.isfinite(lengths)) or np.any(lengths < 1e-9):
        raise ValueError('截面中心重合或无效，无法确定成型方向')
    tangents /= lengths[:, None]

    seed = np.array([0., 0., 1.])
    if abs(float(np.dot(seed, tangents[0]))) > .9:
        seed = np.array([1., 0., 0.])
    axis = seed-float(np.dot(seed, tangents[0]))*tangents[0]
    axes = [axis/np.linalg.norm(axis)]
    for i in range(1, len(centroids)):
        axis = rotation_between_vectors(tangents[i-1], tangents[i]) @ axes[-1]
        axis = axis-float(np.dot(axis, tangents[i]))*tangents[i]
        norm = np.linalg.norm(axis)
        if norm < 1e-9:
            alt = np.array([1., 0., 0.]) if abs(tangents[i][0]) < .9 else np.array([0., 1., 0.])
            axis = alt-float(np.dot(alt, tangents[i]))*tangents[i]
            norm = np.linalg.norm(axis)
        axes.append(axis/norm)
    return centroids, tangents, np.array(axes)


def _align_ring_sequence(rings):
    """Re-seam every ring in its transported frame; the point set is kept.

    All rings come out with the same winding about the local tangent and with
    their parameter origin on the same side of the contour, so ``ThruSections``
    cannot pair two rings half a turn apart.

    The sample distribution is deliberately left as ``_resample_contour``
    produced it: re-resampling here perturbs the periodic interpolant enough
    that OpenCASCADE returns an inside-out shell (valid topology, volume ~0).
    """
    centroids, tangents, axes = _ring_frames(rings)
    aligned = []
    for ring, origin, tangent, axis in zip(rings, centroids, tangents, axes):
        up = np.cross(tangent, axis)
        q = np.asarray(ring, float)-origin
        x, y = q@axis, q@up
        if np.sum(x*np.roll(y, -1)-y*np.roll(x, -1)) < 0:
            q, x, y = q[::-1], x[::-1], y[::-1]
        hits = []
        for i in range(len(q)):
            j = (i+1) % len(q)
            if (y[i] <= 0 < y[j]) or (y[j] <= 0 < y[i]):
                t = -y[i]/(y[j]-y[i])
                if x[i]+t*(x[j]-x[i]) > 0:
                    hits.append((x[i]+t*(x[j]-x[i]), i))
        if hits:
            _, i = max(hits, key=lambda item: item[0])
            shift = (i+1) % len(q)
        else:
            # Centroid fell outside the contour: fall back to the nearest sample.
            shift = int(np.argmin(np.abs(np.arctan2(y, x))))
        aligned.append(np.roll(q, -shift, axis=0)+origin)
    return aligned


def _select_rings(rings, stations, tolerance=.01, max_span=10.):
    """Retain ends and shape changes; bound every sampled correspondence error."""
    rings, stations = np.asarray(rings), np.asarray(stations)
    if np.any(np.diff(stations) <= 0):
        raise ValueError('成型截面站位必须严格递增')
    selected = {0, len(rings)-1}
    stack = [(0, len(rings)-1)]
    while stack:
        a, b = stack.pop()
        if b-a <= 1:
            continue
        t = (stations[a+1:b]-stations[a])/(stations[b]-stations[a])
        interp = rings[a]+t[:, None, None]*(rings[b]-rings[a])
        errors = np.linalg.norm(rings[a+1:b]-interp, axis=2).max(axis=1)
        k = int(np.argmax(errors))+a+1
        if errors.max() <= tolerance:
            if stations[b]-stations[a] <= max_span:
                continue
            k = (a+b)//2
        selected.add(k)
        stack.extend(((a,k),(k,b)))
    return sorted(selected)


def _drop_sliver_bands(selected, stations, min_gap=.5):
    """Remove rings that sit closer than `min_gap` to their neighbour.

    The end-treatment pass inserts extrapolated stations on the full-length
    axis. Two of them can land ~0.2 mm apart while carrying the same contour,
    and a ruled loft turns that pair into a sliver face — the kind of face that
    makes the mould boolean and the tessellation fragile.

    Both physical ends are kept: the first ring always stays, and a too-close
    last ring replaces its neighbour instead of being dropped, so the model
    still spans the drawing length.
    """
    if len(selected) < 3:
        return list(selected)
    kept = [selected[0]]
    for pos in range(1, len(selected)):
        index = selected[pos]
        if stations[index]-stations[kept[-1]] >= min_gap:
            kept.append(index)
        elif pos == len(selected)-1:
            kept[-1] = index
    return kept


class BaseProcessor:
    def __init__(self):
        self.centerline = None
        self.perimeter_data = []
        self.sections_full = []
        self.mesh = self.shape = None
        self.ellipse_x = self.ellipse_y = None
        self.section_points = 384
        self.num_sections = 152
        self.loft_axial_subdiv = 1

    def set_session_data(self, centerline, perimeter_data):
        self.centerline = None if centerline is None else np.asarray(centerline, float)
        self.perimeter_data = list(perimeter_data)

    def set_sections(self, sections):
        self.sections_full = list(sections or [])
        if self.sections_full:
            self.centerline = np.array([s['position'] for s in self.sections_full], float)

    def _finish(self, shape):
        self.shape = shape
        self.mesh = mesh_from_shape(shape)
        return self.mesh

    def get_step_bytes(self):
        if self.shape is None:
            raise ValueError('请先生成模型')
        return step_bytes(self.shape)

    def export_stl(self, output_path):
        self.mesh.export(output_path)
        return output_path

    def _axes(self, positions, rows, s, perimeters):
        if (self.ellipse_x is None) != (self.ellipse_y is None):
            raise ValueError('请同时填写 X/Y 两个全轴长，或同时留空使用前序轴长')
        if self.ellipse_x and self.ellipse_y:
            x = np.full(len(positions), float(self.ellipse_x))
            y = np.full(len(positions), float(self.ellipse_y))
        elif all(r.get('x_length') and r.get('y_length') for r in rows):
            x = np.interp(positions, s, [r['x_length'] for r in rows])
            y = np.interp(positions, s, [r['y_length'] for r in rows])
        else:
            raise ValueError('缺少已确认的椭圆长短轴，请填写 X/Y 全轴长或重新保存含轴长的工艺数据')
        if np.any(x <= 0) or np.any(y <= 0) or not np.all(np.isfinite([x, y])):
            raise ValueError('椭圆轴长必须是有限正数')
        # The axes coming from the process sheet are rounded to 0.01 mm, so a
        # legitimate section can miss its perimeter by a fraction of a percent.
        # Rather than refusing the whole part we scale both axes by the exact
        # factor that restores the measured perimeter (ellipse perimeter is
        # homogeneous in the axes), keeping the measured aspect ratio.
        corrections = []
        for index, (pi, xi, yi) in enumerate(zip(perimeters, x, y)):
            deviation = ellipse_perimeter(xi/2, yi/2) - pi
            if abs(deviation) <= max(.05, pi*.005):
                continue
            scale = pi/ellipse_perimeter(xi/2, yi/2)
            if not np.isfinite(scale) or not 0.8 <= scale <= 1.25:
                raise ValueError('椭圆轴长与前序周长不一致（容差 0.5% 或 0.05 mm），请确认参数')
            corrections.append({
                'station_mm': float(positions[index]),
                'before_mm': [round(float(xi), 4), round(float(yi), 4)],
                'after_mm': [round(float(xi*scale), 4), round(float(yi*scale), 4)],
                'perimeter_mm': round(float(pi), 4),
                'scale': round(float(scale), 6),
            })
            x[index] = xi*scale
            y[index] = yi*scale
        self.axis_corrections = corrections
        if corrections:
            logger.info(
                '椭圆轴长按周长自动匹配：%d/%d 个站位被缩放（最大 %.3f%%），首个 %s',
                len(corrections), len(perimeters),
                max(abs(c['scale']-1) for c in corrections)*100, corrections[0])
        return x/2, y/2


class BellingProcessor(BaseProcessor):
    def __init__(self):
        super().__init__()
        self.belling_length = 200.
        self.wall_thickness = 2.
        self.wall_thickness_source = 'constant'
        self.length_allowance = 0.
        self.resample_step_mm = 1.
        self.revolve_segments = 96
        self.cavity_mesh = self.cavity_shape = None

    def _build(self, mandrel=False):
        rows, s, p = perimeter_series(self.perimeter_data)
        length = float(self.belling_length) if mandrel else float(s[-1])
        if length <= 0 or length > s[-1]+1e-6:
            raise ValueError('扩口长度必须在已确认的周长数据范围内')
        grid = np.unique(np.r_[0, s[(s > 0) & (s < length)], length])
        radii = np.interp(grid, s, p)/(2*np.pi)
        outer_diameters = 2*radii.copy()
        wall = None
        if mandrel:
            if self.wall_thickness_source == 'data':
                try:
                    values = np.array([row['壁厚(mm)'] for row in rows], float)
                except (KeyError, TypeError, ValueError):
                    raise ValueError('前序型线缺少壁厚，请补齐数据或指定统一壁厚')
                if not np.all(np.isfinite(values)) or np.any(values <= 0):
                    raise ValueError('前序壁厚必须为有限正数')
                wall = np.interp(grid, s, values)
            elif self.wall_thickness_source == 'constant':
                wall = np.full(len(grid), float(self.wall_thickness))
                if not np.all(np.isfinite(wall)) or np.any(wall <= 0):
                    raise ValueError('指定壁厚必须为有限正数')
            else:
                raise ValueError('未知芯轴壁厚来源')
            radii -= wall
            allowance = float(self.length_allowance)
            if allowance < 0:
                raise ValueError('长度余量不能为负')
            if allowance:
                grid = np.r_[grid, length+allowance]
                radii = np.r_[radii, radii[-1]]
                outer_diameters = np.r_[outer_diameters, outer_diameters[-1]]
                wall = np.r_[wall, wall[-1]]
        if np.any(radii <= 0):
            raise ValueError('壁厚过大，芯轴半径必须大于零')
        # A planar profile revolved by CAD keeps true circles and fractional ends.
        shape = revolve_profile(np.r_[grid, grid[-1], grid[0]], np.r_[radii, 0, 0])
        summary = {
            'profile_interval_mm': [0., length], 'model_length_mm': float(grid[-1]),
            'stations_mm': grid.tolist(), 'diameters_mm': (2*radii).tolist(),
            'outer_diameters_mm': outer_diameters.tolist(),
            'wall_thickness_mm': wall.tolist() if wall is not None else None,
            'wall_thickness_source': self.wall_thickness_source if mandrel else None,
        }
        setattr(self, 'mandrel_profile' if mandrel else 'cavity_profile', summary)
        theta = np.arange(96)*2*np.pi/96
        rings = [np.column_stack((r*np.cos(theta), r*np.sin(theta), np.full(96, z))) for z, r in zip(grid, radii)]
        return shape, rings

    def generate_model(self):
        return self.generate_mandrel()

    def generate_mandrel(self):
        shape, self._mandrel_rings_3d = self._build(True)
        return self._finish(shape)

    def generate_cavity_model(self):
        self.cavity_shape, self._cavity_rings_3d = self._build(False)
        self.cavity_mesh = mesh_from_shape(self.cavity_shape)
        return self.cavity_mesh

    def get_cavity_step_bytes(self):
        return step_bytes(self.cavity_shape)


class FlatteningProcessor(BaseProcessor):
    def __init__(self):
        super().__init__()
        self.flattening_length = 200.
        self.transition_length = 150.
        self.flattening_allowance = 0.
        self.total_length = None

    def section_dimensions(self):
        rows, s, p = perimeter_series(self.perimeter_data)
        total = float(self.total_length or s[-1])
        fl, tl = float(self.flattening_length), float(self.transition_length)
        if min(total, fl) <= 0 or tl < 0 or total > s[-1]+1e-6 or fl+tl > total+1e-6:
            raise ValueError('压扁段和过渡段必须落在模型总长内；总长不能超出周长数据范围')
        grid = _stations(np.r_[s[s < total], _grid(total, min(5., total/max(2, self.num_sections-1))), fl, fl+tl, total])
        per = np.interp(grid, s, p)
        a, b = self._axes(grid, rows, s, per)
        allowance = float(self.flattening_allowance)
        if allowance < 0:
            raise ValueError('压扁余量不能为负')
        # Preserve P while moving toward a circle; linear interpolation of both
        # axes would change circumference. Preserve the orientation of long axis.
        dims = []
        for z, pi, ai, bi in zip(grid, per, a, b):
            t = 0 if z <= fl else (1 if tl == 0 else min(1., (z-fl)/tl))
            t = t*t*(3-2*t)
            short = min(ai, bi)-allowance/2
            short += (pi/(2*np.pi)-short)*t
            major, minor = ellipse_params_from_perimeter(pi, 2*short)
            dims.append((major, minor) if ai >= bi else (minor, major))
        return grid, np.array(dims)

    def generate_model(self):
        grid, dims = self.section_dimensions()
        wires = [ellipse_wire([0, 0, z], [0, 0, 1], [1, 0, 0], a, b) for z, (a, b) in zip(grid, dims)]
        self.generated_stations, self.generated_axes = grid, dims
        return self._finish(loft(wires))


class BendingProcessor(FlatteningProcessor):
    def __init__(self):
        super().__init__()
        self.flattening_executed = False

    def generate_model(self):
        rows, s, p = perimeter_series(self.perimeter_data)
        cl = self.centerline
        if cl is None or len(cl) < 2 or not np.all(np.isfinite(cl)):
            raise ValueError('压弯缺少有效中心线，请重新读取管件')
        if self.sections_full and len(self.sections_full) == len(cl):
            arc = np.array([row['cumulative_length'] for row in self.sections_full], float)
            arc -= arc[0]
        else:
            arc = np.r_[0, np.cumsum(np.linalg.norm(np.diff(cl, axis=0), axis=1))]
        if not np.all(np.isfinite(arc)) or np.any(np.diff(arc) < 1e-8):
            raise ValueError('中心线站位必须有限且严格递增')
        if np.any(np.linalg.norm(np.diff(cl,axis=0),axis=1) < 1e-8):
            raise ValueError('中心线存在重复点')
        if arc[-1] > s[-1]+max(.05, s[-1]*.001):
            raise ValueError('中心线长度超出工艺截面数据范围')
        if self.flattening_executed:
            grid, dims = self.section_dimensions()
            if abs(grid[-1]-arc[-1]) > max(.05, arc[-1]*.001):
                raise ValueError('压弯前的压扁模型总长与中心线不一致')
        else:
            grid = _thin_stations(_stations(np.r_[arc, s[s < arc[-1]], arc[-1]]))
            dims = np.repeat((np.interp(grid, s, p)/(2*np.pi))[:, None], 2, axis=1)
        points = np.column_stack([np.interp(grid, arc, cl[:, i]) for i in range(3)])
        frames = transport_frames(points)
        self.generated_stations, self.generated_axes = grid, dims
        wires = [ellipse_wire(c, n, u, a, b) for c, (n, u, v), (a, b) in zip(points, frames, dims)]
        return self._finish(loft(wires))


class ShapingProcessor(BaseProcessor):
    def __init__(self):
        super().__init__()
        self.inner_mesh = self.inner_shape = None
        self.inner_rings_3d = None

    def _measured_shape(self, inner=False):
        if len(self.sections_full) < 2:
            raise ValueError('成型需要完整的原始截面轮廓，请重新读取并保存 STP 管件')
        key = 'inner_2d' if inner else 'outer_2d'
        n = max(384, int(self.section_points))
        rings = []
        for sec in self.sections_full:
            if not sec.get(key) or not sec.get('basis_u') or not sec.get('basis_v'):
                raise ValueError('截面缺少局部轮廓或坐标基，请重新分析 STP')
            points = _resample_contour(sec[key], n)
            world = np.asarray(sec['position'])+points[:, :1]*sec['basis_u']+points[:, 1:]*sec['basis_v']
            rings.append(world)
        # The per-section basis can flip between stations; re-seam in a
        # transported frame so the loft cannot pair rings half a turn apart.
        rings = _align_ring_sequence(rings)
        stations = [float(s['cumulative_length']) for s in self.sections_full]
        selected = _drop_sliver_bands(_select_rings(rings, stations), stations)
        chosen = [rings[i] for i in selected]
        self.selected_station_indices = selected
        if inner:
            self.inner_rings_3d = chosen
        self.generated_rings = chosen
        return loft([contour_wire(r) for r in chosen])

    def generate_model(self):
        return self._finish(self._measured_shape())

    def generate_inner_mold(self):
        self.inner_shape = self._measured_shape(True)
        self.inner_mesh = mesh_from_shape(self.inner_shape)
        return self.inner_mesh

    def get_inner_step_bytes(self):
        return step_bytes(self.inner_shape)


PROCESSOR_MAP = {'belling': BellingProcessor, 'flattening': FlatteningProcessor,
                 'bending': BendingProcessor, 'shaping': ShapingProcessor}


def get_processor(operation):
    if operation not in PROCESSOR_MAP:
        raise ValueError(f'未知操作: {operation}')
    return PROCESSOR_MAP[operation]()
