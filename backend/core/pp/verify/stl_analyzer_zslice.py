import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import trimesh
from scipy.spatial import ConvexHull
from scipy import stats
import plotly.graph_objects as go


logger = logging.getLogger(__name__)


@dataclass
class _Section2D:
    points_2d: np.ndarray  # (N,2)
    hull: ConvexHull
    hull_perimeter: float
    hull_area: float
    x_length: float
    y_length: float
    centroid_2d: np.ndarray  # (2,)


def _polygon_centroid(vertices_2d: np.ndarray) -> np.ndarray:
    """
    使用鞋带公式计算简单多边形质心。输入点需按顺/逆时针顺序排列，且不要求闭合。
    """
    if vertices_2d is None or len(vertices_2d) < 3:
        return np.mean(vertices_2d, axis=0) if vertices_2d is not None and len(vertices_2d) > 0 else np.array([0.0, 0.0])

    x = vertices_2d[:, 0]
    y = vertices_2d[:, 1]
    # 闭合
    x2 = np.append(x, x[0])
    y2 = np.append(y, y[0])
    cross = x2[:-1] * y2[1:] - x2[1:] * y2[:-1]
    area2 = np.sum(cross)
    if abs(area2) < 1e-12:
        return np.mean(vertices_2d, axis=0)
    cx = np.sum((x2[:-1] + x2[1:]) * cross) / (3.0 * area2)
    cy = np.sum((y2[:-1] + y2[1:]) * cross) / (3.0 * area2)
    return np.array([cx, cy], dtype=float)


def _orthonormal_basis_from_normal(n: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """给定法向 n，返回 (n_unit, u1, u2) 构成正交基，u1/u2 位于切平面内。"""
    n = np.asarray(n, dtype=float)
    n_norm = np.linalg.norm(n) + 1e-12
    n_unit = n / n_norm

    # 选一个不共线的向量
    if abs(float(n_unit[0])) < 0.9:
        a = np.array([1.0, 0.0, 0.0], dtype=float)
    else:
        a = np.array([0.0, 1.0, 0.0], dtype=float)

    u1 = a - np.dot(a, n_unit) * n_unit
    u1 = u1 / (np.linalg.norm(u1) + 1e-12)
    u2 = np.cross(n_unit, u1)
    u2 = u2 / (np.linalg.norm(u2) + 1e-12)
    return n_unit, u1, u2


def _section_to_2d(section: Any, plane_origin: np.ndarray, plane_normal: np.ndarray) -> Optional[_Section2D]:
    if section is None or not hasattr(section, "vertices") or section.vertices is None:
        return None
    verts = np.asarray(section.vertices)
    if verts.ndim != 2 or verts.shape[1] != 3 or len(verts) < 10:
        return None

    n_unit, u1, u2 = _orthonormal_basis_from_normal(plane_normal)
    rel = verts - plane_origin
    pts2d = np.column_stack([rel @ u1, rel @ u2])

    if len(pts2d) < 3:
        return None

    try:
        hull = ConvexHull(pts2d)
    except Exception:
        return None

    hull_area = float(hull.volume)      # 2D: volume=面积
    hull_perimeter = float(hull.area)   # 2D: area=周长
    if not np.isfinite(hull_area) or hull_area <= 1e-8 or not np.isfinite(hull_perimeter) or hull_perimeter <= 1e-8:
        return None

    x_coords = pts2d[:, 0]
    y_coords = pts2d[:, 1]
    x_length = float(np.max(x_coords) - np.min(x_coords))
    y_length = float(np.max(y_coords) - np.min(y_coords))

    hull_pts = pts2d[hull.vertices]
    centroid_2d = _polygon_centroid(hull_pts)

    return _Section2D(
        points_2d=pts2d,
        hull=hull,
        hull_perimeter=hull_perimeter,
        hull_area=hull_area,
        x_length=x_length,
        y_length=y_length,
        centroid_2d=centroid_2d,
    )


class ZSliceSTLAnalyzer:
    """
    B方案（Z切片质心中心线）：
    - 不旋转模型，不做PCA主轴对齐；
    - 先用平行 XY 平面 (Z = 常数) 以 1mm 步长切片，计算每个切面的几何中心(质心)；
    - 将这些中心点按Z连接得到中心线；
    - 再沿中心线按弧长 1mm 取样，取该处切向方向的垂直平面做截面，计算周长；
    - 对两端异常周长做趋势外推平滑，输出字段与A方案保持一致。
    """

    def __init__(self, z_slice_step_mm: float = 1.0):
        self.logger = logging.getLogger(__name__)
        self.z_slice_step_mm = float(z_slice_step_mm)
        self.mesh: Optional[trimesh.Trimesh] = None
        self.original_mesh: Optional[trimesh.Trimesh] = None
        self.bounds: Optional[np.ndarray] = None
        self.centerline_points: Optional[np.ndarray] = None  # (N,3) 原始中心线点（按Z）
        self.smoothed_centerline: Optional[np.ndarray] = None  # (N,3) 平滑中心线点
        self.centerline_length: float = 0.0
        self.last_error: Optional[str] = None

    def load_stl(self, stl_path: str, progress_callback=None) -> bool:
        try:
            self.last_error = None
            if progress_callback:
                progress_callback(0, "正在读取STL文件(新算法B)...")

            loaded = trimesh.load(stl_path)
            if isinstance(loaded, trimesh.Scene):
                geoms = list(getattr(loaded, "geometry", {}).values())
                if len(geoms) == 0:
                    self.logger.error("STL文件为空或无有效几何体(Scene)")
                    return False
                loaded = trimesh.util.concatenate(geoms)

            self.original_mesh = loaded
            if self.original_mesh is None or self.original_mesh.is_empty:
                self.logger.error("STL文件为空或无效")
                return False

            self.mesh = self.original_mesh.copy()
            self.bounds = self.mesh.bounds

            # B方案要求模型主轴基本沿Z，且高度至少4cm；否则直接提示用户检查模型文件
            try:
                z0 = float(self.bounds[0][2])
                z1 = float(self.bounds[1][2])
                # 对齐到1mm步长的有效高度（与切片一致）
                step = float(self.z_slice_step_mm) if self.z_slice_step_mm > 0 else 1.0
                k_last = int(np.floor((z1 - z0) / step))
                z_last = z0 + k_last * step
                height_mm = z_last - z0
                if height_mm < 40.0:
                    self.last_error = "请确认模型文件，检测到高度不足4cm！"
                    return False
            except Exception:
                # 如果边界计算异常，不阻断后续（由切片/中心线逻辑自行判定）
                pass

            if progress_callback:
                progress_callback(20, "正在进行Z向切片提取中心线...")

            self.centerline_points = self._extract_centerline_by_z_slices(step_mm=self.z_slice_step_mm, progress_callback=progress_callback)
            if self.centerline_points is None or len(self.centerline_points) < 2:
                self.logger.error("中心线提取失败（有效切片不足）")
                return False

            if progress_callback:
                progress_callback(60, "正在平滑中心线并计算长度...")

            self.smoothed_centerline = self._smooth_centerline(self.centerline_points)
            # 端部异常段处理（按Z高度）：舍掉首尾各10mm高度范围的点集，并用拟合外推补全该高度范围（1mm步长）
            self.smoothed_centerline = self._replace_centerline_endpoint_anomalies_by_zheight(
                self.smoothed_centerline,
                trim_z_mm=10.0,
                trend_z_mm=10.0,
                step_mm=1.0
            )
            self.centerline_points = self.smoothed_centerline
            self.centerline_length = self._calculate_centerline_length(self.smoothed_centerline)

            if progress_callback:
                progress_callback(80, "新算法B加载完成")

            return True
        except Exception as e:
            self.logger.error(f"新算法B加载STL失败: {e}")
            return False

    def _extract_centerline_by_z_slices(self, step_mm: float = 1.0, progress_callback=None) -> Optional[np.ndarray]:
        if self.mesh is None:
            return None
        step_mm = float(step_mm)
        if step_mm <= 0:
            step_mm = 1.0

        b = self.mesh.bounds
        z_min = float(b[0][2])
        z_max = float(b[1][2])
        if not np.isfinite(z_min) or not np.isfinite(z_max) or z_max <= z_min:
            return None

        # 用整数步进避免浮点误差累计
        z0 = z_min
        z1 = z_max
        n_steps = int(np.ceil((z1 - z0) / step_mm)) + 1
        z_values = z0 + np.arange(n_steps, dtype=float) * step_mm
        z_values = z_values[z_values <= z1 + 1e-9]

        centers: List[np.ndarray] = []

        for idx, z in enumerate(z_values):
            if progress_callback and idx % 50 == 0:
                pct = 20 + (idx / max(1, len(z_values) - 1)) * 35
                progress_callback(pct, f"Z切片: {idx+1}/{len(z_values)}")

            plane_normal = np.array([0.0, 0.0, 1.0], dtype=float)
            plane_origin = np.array([0.0, 0.0, float(z)], dtype=float)
            section = self.mesh.section(plane_normal=plane_normal, plane_origin=plane_origin)
            if section is None or not hasattr(section, "vertices") or section.vertices is None:
                continue

            verts = np.asarray(section.vertices)
            if verts.ndim != 2 or verts.shape[1] != 3 or len(verts) < 10:
                continue

            # 投影到XY，计算凸包多边形质心（更接近“几何中心”）
            pts2d = verts[:, :2]
            try:
                hull = ConvexHull(pts2d)
                hull_pts = pts2d[hull.vertices]
                c2 = _polygon_centroid(hull_pts)
            except Exception:
                # 退化情况：用均值兜底
                c2 = np.mean(pts2d, axis=0)

            cz = float(np.mean(verts[:, 2]))
            centers.append(np.array([float(c2[0]), float(c2[1]), cz], dtype=float))

        if len(centers) < 2:
            return None

        centers_arr = np.array(centers, dtype=float)
        # 按Z排序（确保中心线单调推进）
        order = np.argsort(centers_arr[:, 2])
        centers_arr = centers_arr[order]

        # 简单去重：Z非常接近的点保留一个
        filtered = [centers_arr[0]]
        for p in centers_arr[1:]:
            if abs(float(p[2] - filtered[-1][2])) >= 1e-6:
                filtered.append(p)
        return np.array(filtered, dtype=float)

    def _smooth_centerline(self, points: np.ndarray) -> np.ndarray:
        # 轻量平滑：3点加权滑动平均（保留端点）
        pts = np.asarray(points, dtype=float)
        if len(pts) < 5:
            return pts
        sm = pts.copy()
        for i in range(1, len(pts) - 1):
            sm[i] = 0.25 * pts[i - 1] + 0.5 * pts[i] + 0.25 * pts[i + 1]
        sm[0] = pts[0]
        sm[-1] = pts[-1]
        return sm

    def _calculate_centerline_length(self, points: np.ndarray) -> float:
        if points is None or len(points) < 2:
            return 0.0
        seg = np.linalg.norm(points[1:] - points[:-1], axis=1)
        return float(np.sum(seg))

    def _replace_centerline_endpoint_anomalies_by_zheight(self, points: np.ndarray, trim_z_mm: float = 10.0, trend_z_mm: float = 10.0, step_mm: float = 1.0) -> np.ndarray:
        """
        中心线端部异常段处理（按Z高度，B方案特化）：
        - 舍掉（替换）模型最低 trim_z_mm 和最高 trim_z_mm 高度范围内的中心线点集
        - 使用紧邻的稳定高度段（各 trend_z_mm）对 x(z)、y(z) 做线性拟合
        - 在端部高度范围内以 step_mm=1.0 的步长生成点，并用拟合结果外推补全
        - Z 值保持高度本身不动（仅拟合/修正 X、Y），最终覆盖原中心线
        """
        pts = np.asarray(points, dtype=float)
        if pts.ndim != 2 or pts.shape[1] != 3 or len(pts) < 5:
            return pts

        if self.mesh is None or self.bounds is None:
            # 没有模型边界时无法按高度做端部处理
            return pts

        step = float(step_mm) if step_mm and step_mm > 0 else 1.0
        z0 = float(self.bounds[0][2])
        z1 = float(self.bounds[1][2])
        # 对齐到切片步长：使用与切片一致的最高有效高度 z_last
        k_last = int(np.floor((z1 - z0) / step))
        z_last = z0 + k_last * step

        # 端部高度范围（半开区间）：
        # low: [z0, z0+trim) -> 10个点（1mm步长）
        # high: (z_last-trim, z_last] -> 10个点（1mm步长）
        trim = float(trim_z_mm)
        trend = float(trend_z_mm)

        low_band_end = z0 + trim
        high_band_start = z_last - trim

        # 中间段保留原中心线点（按Z筛选）
        mid_mask = (pts[:, 2] >= low_band_end - 1e-9) & (pts[:, 2] <= high_band_start + 1e-9)
        mid_pts = pts[mid_mask]

        # 拟合窗口：紧邻中间段两侧各 trend mm
        low_fit_start = low_band_end
        low_fit_end = low_band_end + trend
        high_fit_start = high_band_start - trend
        high_fit_end = high_band_start

        low_fit_mask = (pts[:, 2] >= low_fit_start - 1e-9) & (pts[:, 2] < low_fit_end + 1e-9)
        high_fit_mask = (pts[:, 2] > high_fit_start - 1e-9) & (pts[:, 2] <= high_fit_end + 1e-9)

        low_fit_pts = pts[low_fit_mask]
        high_fit_pts = pts[high_fit_mask]

        # 拟合点不足时：直接使用 mid_pts 的前/后若干点（不做复杂兜底）
        if len(low_fit_pts) < 2:
            low_fit_pts = mid_pts[: max(2, int(np.ceil(trend / step)))]
        if len(high_fit_pts) < 2:
            high_fit_pts = mid_pts[-max(2, int(np.ceil(trend / step))):]

        if len(low_fit_pts) < 2 or len(high_fit_pts) < 2:
            # 中间段点不足，无法拟合
            return pts

        try:
            from scipy import stats
        except Exception:
            return pts

        # 线性拟合：x(z)、y(z)
        def _fit_xy(fit_pts: np.ndarray):
            z = fit_pts[:, 2]
            x = fit_pts[:, 0]
            y = fit_pts[:, 1]
            sx, ix, *_ = stats.linregress(z, x)
            sy, iy, *_ = stats.linregress(z, y)
            return (sx, ix, sy, iy)

        sx0, ix0, sy0, iy0 = _fit_xy(low_fit_pts)
        sx1, ix1, sy1, iy1 = _fit_xy(high_fit_pts)

        # 生成端部补全点（严格按1mm步长）
        low_steps = int(np.floor(trim / step))
        high_steps = int(np.floor(trim / step))

        low_zs = z0 + np.arange(0, low_steps, dtype=float) * step
        high_zs = z_last - np.arange(high_steps - 1, -1, -1, dtype=float) * step  # 递增序列：z_last-9..z_last

        low_pts = np.column_stack([
            sx0 * low_zs + ix0,
            sy0 * low_zs + iy0,
            low_zs
        ])

        high_pts = np.column_stack([
            sx1 * high_zs + ix1,
            sy1 * high_zs + iy1,
            high_zs
        ])

        # 合并：低端补全 + 中间段原点 + 高端补全
        # （中间段可能为空；允许）
        new_pts = np.vstack([low_pts, mid_pts, high_pts]) if len(mid_pts) > 0 else np.vstack([low_pts, high_pts])

        # 确保按Z排序
        order = np.argsort(new_pts[:, 2])
        new_pts = new_pts[order]
        return new_pts

    def get_centerline_info(self) -> Optional[Dict]:
        if self.smoothed_centerline is None or len(self.smoothed_centerline) < 2:
            return None
        cumulative = [0.0]
        for i in range(1, len(self.smoothed_centerline)):
            cumulative.append(cumulative[-1] + float(np.linalg.norm(self.smoothed_centerline[i] - self.smoothed_centerline[i - 1])))
        return {
            "points": self.smoothed_centerline,
            "length": self.centerline_length,
            "discarded_start_length": 0.0,
            "discarded_end_length": 0.0,
            "length_with_compensation": self.centerline_length,
            "cumulative_distances": cumulative,
            "point_count": len(self.smoothed_centerline),
        }

    def _interpolate_centerline_by_arclength(self, s: float) -> Tuple[np.ndarray, np.ndarray]:
        """
        给定弧长位置 s（0~L），返回 (point, tangent_direction_unit)
        """
        info = self.get_centerline_info()
        if info is None:
            raise ValueError("中心线未初始化")
        cum = np.asarray(info["cumulative_distances"], dtype=float)
        pts = np.asarray(info["points"], dtype=float)
        L = float(info["length"])
        s = float(np.clip(s, 0.0, L))

        idx = int(np.searchsorted(cum, s, side="right") - 1)
        idx = max(0, min(idx, len(cum) - 2))
        s0 = cum[idx]
        s1 = cum[idx + 1]
        t = 0.0 if abs(s1 - s0) < 1e-12 else (s - s0) / (s1 - s0)
        p = (1 - t) * pts[idx] + t * pts[idx + 1]
        d = pts[idx + 1] - pts[idx]
        dn = np.linalg.norm(d) + 1e-12
        direction = d / dn
        return p, direction

    def get_cross_section_at_position(self, position_mm: float) -> Dict:
        # 为了兼容 pages/12_read_stl.py，保留同名接口
        return self.get_cross_section_at_centerline_position(position_mm)

    def get_cross_section_at_centerline_position(self, position_mm: float) -> Dict:
        if self.mesh is None or self.smoothed_centerline is None:
            return {"error": "模型或中心线未初始化"}
        if position_mm < 0 or position_mm > self.centerline_length:
            return {"error": f"位置 {position_mm}mm 超出中心线范围 [0, {self.centerline_length:.1f}]mm"}

        center_point, direction = self._interpolate_centerline_by_arclength(float(position_mm))
        plane_normal = direction
        plane_origin = center_point

        section = self._get_valid_section_with_search(plane_normal, plane_origin, max_search=5.0, step=0.5)
        if section is None or not hasattr(section, "vertices") or section.vertices is None or len(section.vertices) == 0:
            return {"error": "无法获取有效截面"}

        # 计算周长/面积等（凸包）
        sec2d = _section_to_2d(section, plane_origin=plane_origin, plane_normal=plane_normal)
        if sec2d is None:
            return {"error": "截面投影/凸包计算失败"}

        verts = np.asarray(section.vertices)
        section_bounds = np.array([np.min(verts, axis=0), np.max(verts, axis=0)], dtype=float)
        section_dimensions = section_bounds[1] - section_bounds[0]

        return {
            "position_mm": float(position_mm),
            "center_point": center_point.tolist(),
            "direction": direction.tolist(),
            "vertices": verts.tolist(),
            "bounds": {
                "min": section_bounds[0].tolist(),
                "max": section_bounds[1].tolist(),
                "dimensions": section_dimensions.tolist(),
            },
            "area": sec2d.hull_area,
            "perimeter": sec2d.hull_perimeter,
            "vertex_count": int(len(verts)),
            "x_length": float(sec2d.x_length),
            "y_length": float(sec2d.y_length),
            "short_axis_length": float(max(sec2d.x_length, sec2d.y_length)),
            "ellipse_params": None,
            "if_is_daao": False,
        }

    def _get_valid_section_with_search(self, plane_normal: np.ndarray, plane_origin: np.ndarray, max_search: float = 5.0, step: float = 0.5):
        """截面失败时，沿法向前后小范围搜索一个有效截面（新算法默认模型合规，搜索范围较小）。"""
        n = np.asarray(plane_normal, dtype=float)
        n = n / (np.linalg.norm(n) + 1e-12)
        origin = np.asarray(plane_origin, dtype=float)

        # 先试原位
        sec = self.mesh.section(plane_normal=n, plane_origin=origin)
        if _section_to_2d(sec, origin, n) is not None:
            return sec

        dist = step
        while dist <= max_search + 1e-9:
            for sign in (+1.0, -1.0):
                o2 = origin + sign * n * dist
                s2 = self.mesh.section(plane_normal=n, plane_origin=o2)
                if _section_to_2d(s2, o2, n) is not None:
                    return s2
            dist += step
        return sec

    def get_perimeter_data_along_centerline(self, sample_interval_mm: float = 1.0, progress_callback=None) -> Dict:
        if self.mesh is None or self.smoothed_centerline is None:
            return {"error": "模型或中心线未初始化"}

        interval = float(sample_interval_mm) if sample_interval_mm and sample_interval_mm > 0 else 1.0
        total_length = float(self.centerline_length)
        positions = np.arange(0.0, total_length + 1e-9, interval)
        if len(positions) == 0 or positions[-1] < total_length:
            positions = np.append(positions, total_length)

        # 记录 (中心线弧长位置pos, 周长perim, 对应中心线点的z高度)
        perim_list_xyz: List[Tuple[float, float, float]] = []
        for i, pos in enumerate(positions):
            if progress_callback and i % 20 == 0:
                progress_callback((i / max(1, len(positions) - 1)) * 100.0, f"计算周长: {i+1}/{len(positions)}")
            sec = self.get_cross_section_at_centerline_position(float(pos))
            if "error" in sec:
                continue
            perim = float(sec.get("perimeter", 0.0))
            if np.isfinite(perim) and perim > 0:
                try:
                    z_val = float(sec.get("center_point", [0.0, 0.0, np.nan])[2])
                except Exception:
                    z_val = float("nan")
                perim_list_xyz.append((float(pos), perim, z_val))

        perim_list_xyz.sort(key=lambda x: x[0])
        if len(perim_list_xyz) < 2:
            return {"error": "周长数据生成失败（有效截面不足）"}

        # 方向修正：确保从大头开始
        perim_list_xyz = self._ensure_large_end_first(perim_list_xyz)

        # 两端异常值处理：按“Z高度”处理（首尾各20mm高度范围），用相邻20mm做线性趋势拟合并外推
        if len(perim_list_xyz) > 10:
            perim_list_xyz = self._handle_perimeter_endpoint_anomalies_by_zheight(
                perim_list_xyz,
                trim_z_mm=20.0,
                trend_z_mm=20.0
            )

        return {
            "success": True,
            "total_length": total_length,
            "sample_interval": interval,
            "perimeter_data": [(p, v) for (p, v, _z) in perim_list_xyz],
            "sample_count": len(perim_list_xyz),
        }

    def _ensure_large_end_first(self, perimeter_data: List[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
        if len(perimeter_data) < 2:
            return perimeter_data
        start_p = float(perimeter_data[0][1])
        end_p = float(perimeter_data[-1][1])
        if end_p <= start_p:
            return perimeter_data

        # 翻转：位置从0开始
        end_pos = float(perimeter_data[-1][0])
        reversed_data = [(end_pos - float(pos), float(perim), float(z)) for (pos, perim, z) in perimeter_data]
        reversed_data.sort(key=lambda x: x[0])
        # 归零
        offset = float(reversed_data[0][0])
        if abs(offset) > 1e-9:
            reversed_data = [(float(pos - offset), float(perim), float(z)) for (pos, perim, z) in reversed_data]
        return reversed_data

    def _handle_perimeter_endpoint_anomalies_by_zheight(
        self,
        perimeter_data: List[Tuple[float, float, float]],
        trim_z_mm: float = 20.0,
        trend_z_mm: float = 40.0,
        transition_z_mm: float = 10.0
    ):
        """
        周长端部异常值处理（按Z高度，B方案特化）：
        - 首端/尾端各“替换” trim_z_mm 的高度范围内的周长值
        - 拟合用更宽的稳定段（各 trend_z_mm 高度）做平滑样条拟合 perimeter(z)
        - 在端部与中段交界处使用 transition_z_mm 高度做余弦权重混合，确保连续且更平滑
        """
        if len(perimeter_data) < 10:
            return perimeter_data

        try:
            from scipy.interpolate import UnivariateSpline
        except Exception:
            return perimeter_data

        arr = np.array(perimeter_data, dtype=float)  # columns: pos, perim, z
        pos = arr[:, 0]
        perim = arr[:, 1]
        z = arr[:, 2]

        # z范围基于模型边界，并对齐到1mm步长的最高有效高度
        if self.bounds is None:
            return perimeter_data
        z0 = float(self.bounds[0][2])
        z1 = float(self.bounds[1][2])
        step = 1.0
        k_last = int(np.floor((z1 - z0) / step))
        z_last = z0 + k_last * step

        trim = float(trim_z_mm)
        trend = float(trend_z_mm)
        trans = float(transition_z_mm) if transition_z_mm and transition_z_mm > 0 else 0.0
        low_end = z0 + trim
        high_start = z_last - trim

        # 拟合窗口
        low_fit_mask = (z > low_end - 1e-9) & (z <= (low_end + trend) + 1e-9)
        high_fit_mask = (z >= (high_start - trend) - 1e-9) & (z < high_start + 1e-9)

        if np.count_nonzero(low_fit_mask) < 2 or np.count_nonzero(high_fit_mask) < 2:
            # 拟合点不足则不处理（用户默认模型足够长）
            return perimeter_data

        def _build_spline(z_fit: np.ndarray, p_fit: np.ndarray) -> UnivariateSpline:
            # 确保按z排序，避免样条报错
            order = np.argsort(z_fit)
            zf = z_fit[order]
            pf = p_fit[order]
            # 平滑因子：按数据量线性放大，避免过拟合（经验值）
            # s 越大越平滑；这里使用 n*0.5 作为默认强度
            s = max(1.0, float(len(zf)) * 0.5)
            return UnivariateSpline(zf, pf, s=s)

        try:
            low_spline = _build_spline(z[low_fit_mask], perim[low_fit_mask])
            high_spline = _build_spline(z[high_fit_mask], perim[high_fit_mask])
        except Exception:
            return perimeter_data

        new_perim = perim.copy()

        # 端部替换：按高度范围替换（保持pos不变）
        low_replace = (z <= low_end + 1e-9)
        high_replace = (z >= high_start - 1e-9)

        try:
            new_perim[low_replace] = low_spline(z[low_replace])
            new_perim[high_replace] = high_spline(z[high_replace])
        except Exception:
            return perimeter_data

        # 交界过渡混合：用余弦权重把拟合值与原始值平滑衔接，避免“台阶”
        if trans > 0:
            # 低端过渡带: [low_end, low_end+trans]
            low_trans_start = low_end
            low_trans_end = low_end + trans
            low_trans_mask = (z >= low_trans_start - 1e-9) & (z <= low_trans_end + 1e-9)
            if np.any(low_trans_mask):
                t = (z[low_trans_mask] - low_trans_start) / max(1e-9, trans)
                t = np.clip(t, 0.0, 1.0)
                # w: 1 -> 0 (在low_end更靠近端部，更多使用拟合值)
                w = 0.5 * (1.0 + np.cos(np.pi * t))
                fit_vals = low_spline(z[low_trans_mask])
                orig_vals = perim[low_trans_mask]
                new_perim[low_trans_mask] = w * fit_vals + (1.0 - w) * orig_vals

            # 高端过渡带: [high_start-trans, high_start]
            high_trans_start = high_start - trans
            high_trans_end = high_start
            high_trans_mask = (z >= high_trans_start - 1e-9) & (z <= high_trans_end + 1e-9)
            if np.any(high_trans_mask):
                t = (z[high_trans_mask] - high_trans_start) / max(1e-9, trans)
                t = np.clip(t, 0.0, 1.0)
                # w: 0 -> 1 (越靠近端部越使用拟合值)
                w = 0.5 * (1.0 - np.cos(np.pi * t))
                fit_vals = high_spline(z[high_trans_mask])
                orig_vals = perim[high_trans_mask]
                new_perim[high_trans_mask] = w * fit_vals + (1.0 - w) * orig_vals

        out = [(float(p), float(v), float(zz)) for p, v, zz in zip(pos.tolist(), new_perim.tolist(), z.tolist())]
        return out

    def create_3d_visualization(self, max_faces: int = 15000) -> go.Figure:
        if self.mesh is None:
            return go.Figure()

        # 降采样网格用于可视化
        viz_mesh = self.mesh
        if len(self.mesh.faces) > max_faces:
            try:
                viz_mesh = self.mesh.simplify_quadric_decimation(max_faces)
            except Exception:
                viz_mesh = self.mesh

        vertices = viz_mesh.vertices
        faces = viz_mesh.faces
        fig = go.Figure(data=[
            go.Mesh3d(
                x=vertices[:, 0], y=vertices[:, 1], z=vertices[:, 2],
                i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
                opacity=0.8, color="lightblue", name="STL模型"
            )
        ])
        if self.smoothed_centerline is not None and len(self.smoothed_centerline) >= 2:
            pts = self.smoothed_centerline
            fig.add_trace(go.Scatter3d(
                x=pts[:, 0], y=pts[:, 1], z=pts[:, 2],
                mode="lines", line=dict(color="orange", width=3),
                name="中心线(B方案)"
            ))
        fig.update_layout(
            title="STL模型3D可视化（新算法B）",
            scene=dict(xaxis_title="X轴 (mm)", yaxis_title="Y轴 (mm)", zaxis_title="Z轴 (mm)", aspectmode="data"),
            height=600,
            showlegend=True,
        )
        return fig

    def create_section_visualization(self, position_mm: float) -> go.Figure:
        sec = self.get_cross_section_at_centerline_position(position_mm)
        if "error" in sec:
            fig = go.Figure()
            fig.add_annotation(text=f"错误: {sec['error']}", xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
            return fig
        verts = np.asarray(sec["vertices"], dtype=float)
        # 用简单的二维投影显示（这里直接取XY投影；与A方案一致的视觉习惯）
        v2 = verts[:, [0, 1]]
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=v2[:, 0], y=v2[:, 1],
            mode="markers",
            marker=dict(size=2, color="blue"),
            fill="toself",
            fillcolor="rgba(0,100,255,0.3)",
            showlegend=False,
            name="截面点"
        ))
        c2 = np.mean(v2, axis=0)
        fig.add_trace(go.Scatter(x=[c2[0]], y=[c2[1]], mode="markers", marker=dict(size=10, color="red", symbol="x"), name="截面中心"))
        fig.update_layout(
            title=f"截面视图 (中心线位置: {position_mm:.1f}mm)",
            xaxis_title="X轴 (mm)",
            yaxis_title="Y轴 (mm)",
            height=400,
            showlegend=True,
        )
        fig.update_xaxes(scaleanchor="y", scaleratio=1)
        return fig

    def create_perimeter_visualization(self, df_data: List[Dict], title: str = "周长变化曲线") -> go.Figure:
        if not df_data:
            return go.Figure()
        xs = [float(item.get("中心线长度(mm)", 0.0)) for item in df_data]
        ys = [float(item.get("外径周长(mm)", 0.0)) for item in df_data]
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines",
            name="处理后的周长", line=dict(color="blue", width=2),
        ))
        fig.update_layout(
            title=title,
            xaxis_title="中心线位置 (mm)",
            yaxis_title="截面周长 (mm)",
            height=400,
            hovermode="x unified",
            showlegend=True,
        )
        return fig

    def analyze_stl_comprehensive(self, stl_path: str, sample_interval_mm: float = 1.0, progress_callback=None) -> Dict:
        try:
            if progress_callback:
                progress_callback(0, "开始新算法B综合分析...")
            if not self.load_stl(stl_path, progress_callback=progress_callback):
                return {"error": self.last_error or "STL文件加载失败(新算法B)"}

            if progress_callback:
                progress_callback(80, "正在生成周长数据(新算法B)...")

            perim_result = self.get_perimeter_data_along_centerline(sample_interval_mm=sample_interval_mm)
            if not perim_result.get("success", False):
                return {"error": f"周长数据生成失败(新算法B): {perim_result.get('error', '未知错误')}"}

            perimeter_data = perim_result["perimeter_data"]
            total_length = float(perim_result["total_length"])

            df_data: List[Dict] = []
            for i, (pos, perim) in enumerate(perimeter_data):
                sec = self.get_cross_section_at_centerline_position(float(pos))
                if "error" in sec:
                    # 保守处理：跳过该点
                    continue
                # 截面长宽（基于bounds）
                dims = sec.get("bounds", {}).get("dimensions", [0.0, 0.0, 0.0])
                if isinstance(dims, list) and len(dims) >= 2:
                    width = float(max(dims[0], dims[1]))
                    length = float(min(dims[0], dims[1]))
                else:
                    width = length = 0.0

                x_length = float(sec.get("x_length", 0.0))
                y_length = float(sec.get("y_length", 0.0))

                df_data.append({
                    "序号": i + 1,
                    "中心线长度(mm)": float(pos),
                    "外径周长(mm)": float(perim),
                    "截面宽度(mm)": width,
                    "截面长度(mm)": length,
                    "x_length": x_length,
                    "y_length": y_length,
                    "位置百分比(%)": (float(pos) / total_length) * 100.0 if total_length > 0 else 0.0,
                })

            # 弯曲检测：与A方案保持一致（简化版本）
            bend_angle = 0.0
            is_bended = False
            try:
                pts = self.smoothed_centerline
                if pts is not None and len(pts) > 10:
                    start_vector = pts[len(pts) // 4] - pts[0]
                    end_vector = pts[-1] - pts[-len(pts) // 4]
                    start_vector = start_vector / (np.linalg.norm(start_vector) + 1e-12)
                    end_vector = end_vector / (np.linalg.norm(end_vector) + 1e-12)
                    dot = float(np.dot(start_vector, end_vector))
                    angle = float(np.arccos(np.clip(dot, -1.0, 1.0)))
                    bend_angle = float(np.degrees(angle))
                    is_bended = bend_angle > 2.0
            except Exception:
                pass

            model_info = {
                "total_length": self.centerline_length,
                "bounds": {
                    "min": self.mesh.bounds[0].tolist() if self.mesh is not None else [0, 0, 0],
                    "max": self.mesh.bounds[1].tolist() if self.mesh is not None else [0, 0, 0],
                    "dimensions": (self.mesh.bounds[1] - self.mesh.bounds[0]).tolist() if self.mesh is not None else [0, 0, 0],
                },
                "volume": float(self.mesh.volume) if self.mesh is not None else 0.0,
                "vertices_count": int(len(self.mesh.vertices)) if self.mesh is not None else 0,
                "faces_count": int(len(self.mesh.faces)) if self.mesh is not None else 0,
                "length_axis": "Z轴(假定合规)",
                "centerline_info": self.get_centerline_info(),
                "use_centerline": True,
                "transformation_info": {
                    "rotation_applied": False,
                    "translation_offset": None,
                },
            }

            if progress_callback:
                progress_callback(100, "新算法B分析完成")

            return {
                "success": True,
                "model_info": model_info,
                "centerline_length": float(self.centerline_length),
                "volume": float(self.mesh.volume) if self.mesh is not None else 0.0,
                "processed_mesh": self.mesh,
                "centerline_perimeter_data": df_data,
                "perimeter_result": perim_result,
                "bend_angle": float(bend_angle),
                "is_bended": bool(is_bended),
                "analysis_metadata": {
                    "sample_interval_mm": float(sample_interval_mm),
                    "total_samples": int(len(df_data)),
                    "method": "B_ZSlice_Centroid",
                },
            }
        except Exception as e:
            return {"error": f"新算法B综合分析失败: {e}"}

