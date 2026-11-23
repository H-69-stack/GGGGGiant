"""分型方向判定：镜像对称评分 + 4 条类别规则（纯 numpy，顶层不 import OCP / FreeCAD）。

契约见 ``Giant-Unified/ENTITY_PIPELINE.md`` §7（规则）与 §9（签名）。
本模块只做数值计算：不读文件、不碰 CAD、不写文件，输入就是截面环。

输入 ``rings``
--------------
``list[dict]``，每项至少含 ``points``::

    {"points": (N,3) ndarray,   # 截面平面上闭合环的采样点（首尾不必重合）
     "centroid": (3,) ndarray,  # 环心（可缺，缺了取点均值）
     "normal": (3,) ndarray}    # 截面法向（可缺，缺了取拟合平面法向）

也接受 ``list[(N,3) ndarray]`` 这种只带点的写法（S3 的 ``sections_from_step``
和 S4 的 pipe 路都能直接喂）。

类别规则（§7）
--------------
====================  ====================================================
``shaping`` / 其它     三向镜像评分取最小；并列（差 < 1e-4）按 Z > Y > X；
                      最佳分 > :data:`SYMMETRY_TOL`（0.02×尺度）→ ``ValueError``
``belling``            回转体任意方向 → 评分最小的轴；并列取 Y
``flattening``         截面长轴：逐截面轮廓 PCA 长轴、符号对齐、平均归一化
``bending``            弯曲平面法向：质心点集 PCA 最小主成分 n 优先（= 压弯件的
                      镜像对称轴、且恒 ⊥ 每个截面法向 → 投影恒为 1）；
                      仅当 n 在每个截面里的投影不足时，才退到弯曲平面内的
                      normalize(span_axis - (span_axis·n)n) 等候选（见
                      :func:`_resolve_bending` 的候选表）
====================  ====================================================

评分口径（与 ``PipeSolidStudio/PipeSolidBridge/geometry.auto_direction_scores``
一致）：把点云按某个全局平面（过质心）镜像，算「反射点到原点云的最近距离均值」，
再除以点云尺度 ``mean(|p - centroid|)``。**无量纲，越小越对称**。

返回值的双兼容
--------------
§9 契约要求 ``resolve_for_category`` 返回 ``dict``（S3 的 ``routes/selfref.py``
就是这么用的：``resolved.get("vector")``），而本次任务的验收要求返回
``(vector, label, rule)`` 三元组。这里返回 :class:`DirectionResult`（dict 子类，
``__iter__`` / 整数下标按三元组走），两种写法同时成立::

    res = resolve_for_category("shaping", rings)
    res["vector"], res.get("rule")        # dict 风格（契约）
    vector, label, rule = res             # 元组风格（任务书）
"""
from __future__ import annotations

import math
import re
from typing import Any, Sequence

import numpy as np

__all__ = [
    "AXES",
    "MIN_PROJECTION",
    "SYMMETRY_TOL",
    "TIE_TOL",
    "DirectionResult",
    "angle_direction",
    "angle_of_xy",
    "axis_label",
    "bending_plane",
    "direction_label",
    "normalize",
    "parse_direction",
    "per_section_axes",
    "planar_bend_normal",
    "resolve_for_category",
    "score_axes",
    "section_frames",
]

AXES: tuple[str, str, str] = ("X", "Y", "Z")

#: 对称门限：最佳镜像评分（已按点云尺度归一化）超过它 → 认定「不对称」
SYMMETRY_TOL: float = 0.02
#: 并列判定阈值（§7：差 < 1e-4 视为并列）
TIE_TOL: float = 1e-4
#: 单截面采样点数（PipeSolidStudio ``_symmetry_cloud(samples=96)`` 同口径）
DEFAULT_SAMPLES: int = 96
#: 参与评分云的最大截面数（PipeSolidStudio ``_symmetry_cloud(nsections=12)`` 同口径；
#: 138 环全量喂进来时按序号等间距取 12 个，保证评分稳定且 < 2 s）
MAX_CLOUD_SECTIONS: int = 12
#: 分型方向在截面内的最小投影长度。与 ``routes/selfref.py:43`` 的
#: ``MIN_AXIS_PROJECTION = 0.15`` 对齐：低于它下游 ``_apply_direction`` 会直接报错，
#: 所以压弯规则在这里就换成不退化的候选方向，而不是把错误留给下游。
MIN_PROJECTION: float = 0.15

_OP_BY_KEY = {
    "成型_外径形腔": "shaping",
    "扩口_扩口腔体": "belling",
    "扩口_扩芯轴": "belling",
    "扩口_扩口芯轴": "belling",
    "压扁_压扁模型": "flattening",
    "压弯_压弯模型": "bending",
}
_CATEGORIES = ("shaping", "belling", "flattening", "bending")


# ---------------------------------------------------------------------------
# 1) 小工具
# ---------------------------------------------------------------------------


def normalize(v: Any) -> np.ndarray:
    """单位化；零向量 / NaN 抛 ``ValueError``（不静默返回零）。"""
    arr = np.asarray(v, dtype=float).reshape(-1)
    if arr.size != 3:
        raise ValueError(f"分型方向必须是 3 维向量，收到 shape={np.shape(v)}")
    norm = float(np.linalg.norm(arr))
    if not np.isfinite(norm) or norm < 1e-12:
        raise ValueError(f"分型方向是零向量 / 非有限值：{arr.tolist()}")
    return arr / norm


def axis_label(v: Any, tol: float = 1e-6) -> str:
    """方向标签：``'X'`` / ``'Y'`` / ``'Z'`` / ``'custom'``（与坐标轴夹角 < tol 视为该轴）。"""
    try:
        unit = normalize(v)
    except ValueError:
        return "custom"
    for i, name in enumerate(AXES):
        if abs(abs(float(unit[i])) - 1.0) <= tol:
            return name
    return "custom"


def _unit(v: Any, fallback: np.ndarray | None = None) -> np.ndarray | None:
    """单位化的宽松版：退化时返回 fallback（不抛错，内部用）。"""
    try:
        return normalize(v)
    except ValueError:
        return fallback


def _canonical(v: np.ndarray) -> np.ndarray:
    """符号规范化：绝对值最大的分量取正，保证 SVD / 特征分解结果可复现。"""
    unit = normalize(v)
    i = int(np.argmax(np.abs(unit)))
    return -unit if unit[i] < 0 else unit


def _axis_vector(name: str) -> np.ndarray:
    vec = np.zeros(3)
    vec[AXES.index(name)] = 1.0
    return vec


#: 手动角度写法：``angle:-27`` / ``angle=-27`` / ``angle_z:-27`` / ``angle:-27°``
#: 角度约定：绕 +Z 轴、从 +X 逆时针为正（右手系），即方向 = (cosθ, sinθ, 0)。
_ANGLE_RE = re.compile(
    r"^angle(?:\s*[_ -]?\s*z)?\s*[:=]\s*([-+]?\d+(?:\.\d+)?)\s*°?$",
    re.IGNORECASE,
)


def angle_direction(degrees: Any) -> np.ndarray:
    """「绕 Z 轴的角度（度）」→ 单位方向 ``(cosθ, sinθ, 0)``。"""
    value = float(degrees)
    if not math.isfinite(value):
        raise ValueError(f"角度必须是有限数，收到 {degrees!r}")
    rad = math.radians(value)
    return np.array([math.cos(rad), math.sin(rad), 0.0], dtype=float)


def _parse_angle(text: str) -> float | None:
    match = _ANGLE_RE.match(str(text).strip())
    if not match:
        return None
    return float(match.group(1))


def angle_of_xy(vector: Any) -> float | None:
    """方向在 XY 平面内的角度（度）；接近竖直（|z| 主导）时返回 None。"""
    unit = _unit(vector)
    if unit is None:
        return None
    if abs(float(unit[2])) > 0.999:
        return None
    return round(math.degrees(math.atan2(float(unit[1]), float(unit[0]))), 4)


def planar_bend_normal(rings: Any, *, straight_tol: float = 1e-4,
                       planar_tol: float = 5e-3) -> np.ndarray | None:
    """平面弯的弯曲平面法向；直管 / 空间弯返回 None。

    * 截面质心 PCA 的 ``s1/s0 <= straight_tol`` → 直管（无弯曲平面）；
    * ``s2/s0 > planar_tol`` → 空间弯（不是单一平面），交给别的规则；
    * 其余 → 返回 ``s2`` 对应主成分（弯曲平面法向），符号已规范化。
    """
    items = _ring_items(rings)
    if len(items) < 3:
        return None
    centers = np.array(
        [_centroid_of(item, pts) for item, pts in zip(items, _point_list(items))],
        dtype=float,
    )
    q = centers - centers.mean(axis=0)
    try:
        _, sing, vh = np.linalg.svd(q, full_matrices=False)
    except np.linalg.LinAlgError:
        return None
    if len(sing) < 3 or sing[0] <= 0:
        return None
    if float(sing[1]) <= max(float(sing[0]) * straight_tol, 1e-9):
        return None
    if float(sing[2]) > float(sing[0]) * planar_tol:
        return None
    # 质心共面还不够：截面环必须是按弧长排序的闭合轮廓。随机点云 / 散点
    # 的质心也可能近似共面，但相邻点间距与截面尺度同量级；真实截面约为
    # 周长/点数，比尺度小一个数量级。
    if not _rings_ordered_loops(rings):
        return None
    return _canonical(vh[-1])


def _rings_ordered_loops(rings: Any, *, max_edge_ratio: float = 0.15) -> bool:
    """截面环是否像按弧长排序的闭合轮廓（用于排除散点云）。"""
    ratios: list[float] = []
    for pts in _point_list(rings):
        pts = np.asarray(pts, dtype=float)
        if pts.ndim != 2 or pts.shape[0] < 8 or pts.shape[1] != 3:
            return False
        span = float(np.ptp(pts, axis=0).max())
        if span <= 0 or not np.isfinite(span):
            return False
        steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        if not steps.size:
            return False
        ratios.append(float(np.median(steps)) / span)
    return bool(ratios) and float(np.median(ratios)) <= max_edge_ratio



def _format_vector(v: Any) -> str:
    return "custom(%.3f,%.3f,%.3f)" % (float(v[0]), float(v[1]), float(v[2]))


def _label_for(vector: Any) -> str:
    name = axis_label(vector)
    return name if name != "custom" else _format_vector(vector)


def parse_direction(requested: Any) -> np.ndarray | None:
    """公开的方向解析：``auto``/``None`` → None；轴 / ``[x,y,z]`` / ``angle:<度>`` → 单位向量。"""
    return _explicit(requested)


def direction_label(requested: Any) -> str:
    """给 ``parse_direction`` 的结果配一个可读标签（``angle:`` 保留角度）。"""
    if isinstance(requested, str):
        degrees = _parse_angle(requested)
        if degrees is not None:
            return "angle:%g°" % degrees
    vec = _explicit(requested)
    return "auto" if vec is None else _label_for(vec)


def _normalized_category(category: Any) -> str:
    """把 op / 中文模型键 / 中文工序名 归一成 4 个类别之一；不认识的返回 ``''``。"""
    raw = str(category or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low in _CATEGORIES:
        return low
    if raw in _OP_BY_KEY:
        return _OP_BY_KEY[raw]
    for prefix, op in (("成型", "shaping"), ("扩口", "belling"), ("压扁", "flattening"), ("压弯", "bending")):
        if raw.startswith(prefix) or prefix in raw:
            return op
    return ""


# ---------------------------------------------------------------------------
# 2) rings 读取与采样
# ---------------------------------------------------------------------------


def _points_of(item: Any) -> np.ndarray:
    """取一个环的 (N,3) 点数组（吃 dict / 带属性的对象 / 裸点数组）。"""
    if isinstance(item, dict):
        raw = item.get("points")
        if raw is None:
            raw = item.get("pts")
    else:
        raw = getattr(item, "points", None)
        if raw is None:
            raw = getattr(item, "pts", None)
        if raw is None and not hasattr(item, "__len__"):
            raw = item
    if raw is None:
        raise ValueError("rings 里有一项既不是 dict['points'] 也不是点数组")
    arr = np.asarray(raw, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"截面环点数组必须是 (N,3)，收到 shape={arr.shape}")
    return arr


def _ring_items(rings: Any) -> list[Any]:
    """归一成 rings 条目列表（单环 / 单点数组 / list 都吃）。"""
    if rings is None:
        raise ValueError("rings 为空，无法判定分型方向")
    if isinstance(rings, dict):
        return [rings]
    if isinstance(rings, np.ndarray):
        return [rings] if rings.ndim == 2 else list(rings)
    items = list(rings)
    if not items:
        raise ValueError("rings 为空，无法判定分型方向")
    return items


def _point_list(rings: Any) -> list[np.ndarray]:
    return [_points_of(item) for item in _ring_items(rings)]


def _centroid_of(item: Any, pts: np.ndarray) -> np.ndarray:
    cen = item.get("centroid") if isinstance(item, dict) else getattr(item, "centroid", None)
    if cen is None:
        cen = item.get("center") if isinstance(item, dict) else getattr(item, "center", None)
    if cen is not None:
        arr = np.asarray(cen, dtype=float).reshape(-1)
        if arr.size == 3 and np.all(np.isfinite(arr)):
            return arr
    return pts.mean(axis=0)


def _normal_of(item: Any, pts: np.ndarray) -> np.ndarray | None:
    """截面法向：优先条目自带的 ``normal``，否则用最小主成分拟合平面。"""
    nrm = item.get("normal") if isinstance(item, dict) else getattr(item, "normal", None)
    if nrm is not None:
        arr = np.asarray(nrm, dtype=float).reshape(-1)
        if arr.size == 3:
            unit = _unit(arr)
            if unit is not None:
                return unit
    if len(pts) < 3:
        return None
    q = pts - pts.mean(axis=0)
    try:
        _, _, vh = np.linalg.svd(q, full_matrices=False)
    except np.linalg.LinAlgError:  # pragma: no cover - 数值异常兜底
        return None
    return _unit(vh[-1])


def _resample_loop(points: np.ndarray, n: int) -> np.ndarray:
    """闭合环按弧长等距重采样成 ``n`` 点（不重复首点，与 PSS 的 k/samples 同口径）。"""
    n = max(int(n), 3)
    pts = np.asarray(points, dtype=float)
    if len(pts) == 0:
        raise ValueError("截面环没有点")
    if len(pts) == 1:
        return np.repeat(pts, n, axis=0)
    # 去掉相邻重复点，避免累计弧长出现零段
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(pts, axis=0), axis=1) > 1e-12
    pts = pts[keep]
    if len(pts) < 3:
        return np.repeat(pts[:1], n, axis=0)
    loop = np.vstack([pts, pts[:1]])
    seg = np.linalg.norm(np.diff(loop, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if not np.isfinite(total) or total <= 1e-12:
        return np.repeat(pts[:1], n, axis=0)
    targets = np.linspace(0.0, total, n, endpoint=False)
    return np.column_stack([np.interp(targets, cum, loop[:, k]) for k in range(3)])


def _mean_nearest_distance(points: np.ndarray, reflected: np.ndarray, chunk: int | None = None) -> float:
    """反射点到原点云的最近距离均值（分块 + 增广矩阵一次 gemm，省掉一整趟内存扫描）。

    ``d² = |b|² + |p|² - 2 b·p``：把 ``[|p|²]`` 拼成矩阵的最后一行，一次
    ``(m,4) @ (4,n)`` 就同时得到 ``|p|²-2b·p``，再补 ``|b|²`` 即得距离平方，
    只对每行的最小值开方。float64 全程，几何量精度不受损。
    """
    n = len(points)
    if n == 0 or len(reflected) == 0:
        return 0.0
    p2 = np.einsum("ij,ij->i", points, points)
    mat = np.vstack([-2.0 * points.T, p2[None, :]])
    if chunk is None:
        chunk = max(64, min(2048, int(2_000_000 // max(n, 1))))
    total = 0.0
    for start in range(0, len(reflected), chunk):
        blk = reflected[start : start + chunk]
        aug = np.empty((len(blk), 4))
        aug[:, :3] = blk
        aug[:, 3] = 1.0
        grid = aug @ mat
        row_min = grid.min(axis=1)
        row_min += np.einsum("ij,ij->i", blk, blk)
        np.maximum(row_min, 0.0, out=row_min)
        total += float(np.sqrt(row_min).sum())
    return total / float(len(reflected))


# ---------------------------------------------------------------------------
# 3) 镜像评分
# ---------------------------------------------------------------------------


def _symmetry_cloud(rings: Any, samples: int = DEFAULT_SAMPLES,
                    max_sections: int | None = MAX_CLOUD_SECTIONS) -> np.ndarray:
    """评分点云：按序号等间距取 ``max_sections`` 个截面 × 每截面 ``samples`` 点。"""
    pts_list = _point_list(rings)
    total = len(pts_list)
    limit = total if not max_sections else max(1, min(int(max_sections), total))
    idx = np.unique(np.linspace(0, total - 1, limit).astype(int)) if limit < total else np.arange(total)
    cloud = np.vstack([_resample_loop(pts_list[int(i)], samples) for i in idx])
    return cloud


def score_axes(rings: Any, samples: int = DEFAULT_SAMPLES,
               max_sections: int | None = MAX_CLOUD_SECTIONS) -> dict[str, float]:
    """X / Y / Z 三个全局平面（过点云质心）的镜像对称评分。

    Parameters
    ----------
    rings:
        截面环列表（见模块 docstring）。
    samples:
        每个截面重采样的点数，默认 96。
    max_sections:
        参与点云的截面数，默认 12（``None`` = 全部；138 环全量约 1.4 s）。

    Returns
    -------
    dict ``{'X': float, 'Y': float, 'Z': float}``，已除以点云尺度
    ``mean(|p - 质心|)``，无量纲，**越小越对称**。
    """
    cloud = _symmetry_cloud(rings, samples=samples, max_sections=max_sections)
    if len(cloud) < 8:
        return {name: 1.0 for name in AXES}
    center = cloud.mean(axis=0)
    scale = float(np.linalg.norm(cloud - center, axis=1).mean())
    if not np.isfinite(scale) or scale <= 1e-9:
        scale = 1.0
    scores: dict[str, float] = {}
    for i, name in enumerate(AXES):
        reflected = cloud.copy()
        reflected[:, i] = 2.0 * center[i] - reflected[:, i]
        scores[name] = _mean_nearest_distance(cloud, reflected) / scale
    return scores


def _rounded(scores: dict[str, float]) -> dict[str, float]:
    return {k: round(float(v), 6) for k, v in scores.items()}


def _tied_min(scores: dict[str, float], tol: float = TIE_TOL) -> tuple[str, list[str]]:
    """最小分轴 + 并列集合；并列按 Z > Y > X 取（§7）。"""
    best = min(float(scores[name]) for name in AXES)
    tied = [name for name in ("Z", "Y", "X") if float(scores[name]) <= best + tol]
    return tied[0], tied


# ---------------------------------------------------------------------------
# 4) 类别规则所需的几何量
# ---------------------------------------------------------------------------


def per_section_axes(rings: Any, vector: Any = None, tol: float = 1e-3) -> list[Any]:
    """逐截面主轴。

    * 不给 ``vector``（本次任务书用法）：返回 ``list[(3,) ndarray]`` —— 每个截面
      轮廓 PCA 的长轴单位向量，已按「与上一截面 ``dot < 0`` 取反」做符号对齐；
    * 给 ``vector``（§9 契约用法，自参考保形用）：返回 :func:`section_frames`
      的结果，即 ``list[dict]``，每截面 ``n`` / ``a`` / ``up``。
    """
    if vector is not None:
        return section_frames(rings, vector, tol=tol)

    items = _ring_items(rings)
    axes: list[np.ndarray] = []
    prev: np.ndarray | None = None
    for item, pts in zip(items, _point_list(items)):
        axis = _long_axis(pts, _normal_of(item, pts))
        if prev is not None and float(np.dot(axis, prev)) < 0.0:
            axis = -axis
        axes.append(axis)
        prev = axis
    return axes


def _long_axis(pts: np.ndarray, normal: np.ndarray | None = None) -> np.ndarray:
    """单个截面的长轴：轮廓 PCA 最大主成分（投影到截面平面后再算，抗噪）。"""
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 3:
        return np.array([1.0, 0.0, 0.0])
    q = pts - pts.mean(axis=0)
    if normal is not None:
        q = q - np.outer(q @ normal, normal)  # 压到截面平面内，消除厚度/噪声分量
    try:
        _, _, vh = np.linalg.svd(q, full_matrices=False)
    except np.linalg.LinAlgError:  # pragma: no cover - 数值异常兜底
        return np.array([1.0, 0.0, 0.0])
    axis = _unit(vh[0])
    if axis is None:  # 退化成一点
        return np.array([1.0, 0.0, 0.0])
    return _canonical_soft(axis)


def _canonical_soft(v: np.ndarray) -> np.ndarray:
    """符号按最大分量取正（仅用于稳定的初始符号，随后仍会按相邻截面翻转）。"""
    i = int(np.argmax(np.abs(v)))
    return -v if v[i] < 0 else v


def section_frames(rings: Any, vector: Any, tol: float = 1e-3) -> list[dict]:
    """自参考保形用：每截面的 ``n``（法向）/ ``a``（分型方向在截面内的投影）/ ``up``。

    ``a`` 与 ``n`` 直交；``up = n × a``。``a`` 与上一截面 ``dot < 0`` 时整帧取反，
    保证 U=0 一侧在整件上连续（与 PipeSolidStudio ``build_section_curve`` 同构）。
    """
    direction = normalize(vector)
    items = _ring_items(rings)
    frames: list[dict] = []
    prev_a: np.ndarray | None = None
    prev_up: np.ndarray | None = None
    for index, (item, pts) in enumerate(zip(items, _point_list(items))):
        normal = _normal_of(item, pts)
        if normal is None:
            raise ValueError(f"第 {index} 个截面退化，拟合不出法向")
        a = direction - float(np.dot(direction, normal)) * normal
        norm = float(np.linalg.norm(a))
        if norm < tol:
            # 分型方向与截面法向几乎平行 → 退化，换一个与 n 直交的方向
            helper = np.array([0.0, 0.0, 1.0]) if abs(float(normal[2])) < 0.9 else np.array([1.0, 0.0, 0.0])
            fb = _unit(np.cross(normal, helper))
            if fb is None:
                raise ValueError(f"第 {index} 个截面：分型方向与截面法向平行，无可用 a 轴")
            a = fb
        else:
            a = a / norm
        if prev_a is not None and float(np.dot(a, prev_a)) < 0.0:
            a = -a
        up = _unit(np.cross(normal, a))
        if up is None:
            raise ValueError(f"第 {index} 个截面：n × a 退化，无法建立截面标架")
        if prev_up is not None and float(np.dot(up, prev_up)) < 0.0:
            a = -a
            up = -up
        prev_a, prev_up = a, up
        frames.append(
            {
                "index": index,
                "n": normal,
                "a": a,
                "up": up,
                "normal": normal,
                "vector": a,
                "centroid": _centroid_of(item, pts),
                "degenerate": norm < tol,
            }
        )
    return frames


def bending_plane(rings: Any, warnings: list[str] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """弯曲平面：截面质心点集 PCA → ``(平面法向 n, 平面内主轴 plane_axis)``。

    法向 ``n`` = 最小主成分（弯曲平面法向）；``plane_axis`` = 最大主成分（弦方向，
    必在平面内）。质心近似共线（直管）时弯曲平面退化：``n`` 取与长轴最不平行的
    全局坐标轴（Z > Y > X 优先），并往 ``warnings`` 里写一条说明。
    """
    items = _ring_items(rings)
    pts_list = _point_list(items)
    if len(items) < 3:
        raise ValueError(f"截面只有 {len(items)} 个（< 3），无法确定弯曲平面")
    centers = np.array([_centroid_of(item, pts) for item, pts in zip(items, pts_list)], dtype=float)
    mean = centers.mean(axis=0)
    q = centers - mean
    try:
        _, sing, vh = np.linalg.svd(q, full_matrices=False)
    except np.linalg.LinAlgError as exc:  # pragma: no cover - 数值异常兜底
        raise ValueError(f"质心点集 PCA 失败：{exc}") from exc
    plane_axis = _canonical(vh[0])
    noise = float(sing[0]) * 1e-4
    if sing[1] <= max(noise, 1e-9):
        # 质心共线 → 没有弯曲平面；取与长轴最不平行的坐标轴当占位法向
        order = sorted(AXES, key=lambda name: (abs(float(np.dot(_axis_vector(name), plane_axis))),
                                               -AXES.index(name)))
        normal = _axis_vector(order[0])
        if warnings is not None:
            warnings.append(
                f"截面质心近似共线（无弯曲），弯曲平面退化；法向暂取 {order[0]} 轴"
            )
        return normal, plane_axis
    normal = _canonical(vh[-1])
    return normal, plane_axis


def _span_axis(rings: Any) -> np.ndarray:
    """全件最长跨度方向（包围盒最长边的坐标轴方向）。"""
    pts = np.vstack(_point_list(rings))
    ext = pts.max(axis=0) - pts.min(axis=0)
    return _axis_vector(AXES[int(np.argmax(ext))])


# ---------------------------------------------------------------------------
# 5) 结果容器（dict 契约 + 元组解包双兼容）
# ---------------------------------------------------------------------------


class DirectionResult(dict):
    """``{'vector', 'label', 'rule', ...}``，同时支持 ``vector, label, rule = res``。

    ``res["vector"]`` 是 ``[x, y, z]``（JSON 安全）；``res[0]`` / 解包给出同一份
    三元组。额外键（``scores`` / ``category`` / ``angle_deg`` …）只做诊断用，
    下游按需取，改它们不影响契约三键。
    """

    __slots__ = ()

    def __iter__(self):
        yield self["vector"]
        yield self["label"]
        yield self["rule"]

    def __getitem__(self, key):
        if isinstance(key, (int, np.integer)):
            return (self["vector"], self["label"], self["rule"])[int(key)]
        return dict.__getitem__(self, key)

    def as_tuple(self) -> tuple[list[float], str, str]:
        """显式取 ``(vector, label, rule)``。"""
        return (self["vector"], self["label"], self["rule"])


def _make(vector: Any, label: str, rule: str, **extra: Any) -> DirectionResult:
    vec = normalize(vector)
    payload = {
        "vector": [float(c) for c in vec],
        "label": label,
        "rule": rule,
    }
    payload.update(extra)
    return DirectionResult(payload)


def _explicit(requested: Any) -> Any:
    """把 ``requested`` 解析成显式方向；``auto`` / ``''`` / ``None`` 返回 None。"""
    if requested is None:
        return None
    if isinstance(requested, (list, tuple, np.ndarray)):
        return normalize(requested)
    text = str(requested).strip()
    if not text or text.lower() in {"auto", "automatic", "none", "自动"}:
        return None
    if text.upper() in AXES:
        return _axis_vector(text.upper())
    degrees = _parse_angle(text)
    if degrees is not None:
        return angle_direction(degrees)
    if text.startswith("custom(") and text.endswith(")"):
        text = text[len("custom(") : -1]
    parts = [p for p in text.replace(";", ",").split(",") if p.strip()]
    if len(parts) == 3:
        try:
            return normalize([float(p) for p in parts])
        except ValueError as exc:
            raise ValueError(f"无法解析 requested={requested!r}：{exc}") from exc
    raise ValueError(f"无法解析 requested={requested!r}；请给 'auto' / 'X'/'Y'/'Z' / [x,y,z]")


# ---------------------------------------------------------------------------
# 6) 类别规则
# ---------------------------------------------------------------------------


def resolve_for_category(category: Any, rings: Any, requested: Any = "auto",
                         tol: float = 1e-3) -> DirectionResult:
    """按类别给分型方向。

    Parameters
    ----------
    category:
        ``'shaping'`` / ``'belling'`` / ``'flattening'`` / ``'bending'``（也吃
        中文模型键 ``'成型_外径形腔'`` 与工序名 ``'压弯'``）；不认识的按
        「shaping / 其它」处理。
    rings:
        截面环列表（见模块 docstring）。
    requested:
        ``'auto'`` 走类别规则；``'X'``/``'Y'``/``'Z'`` 或 ``[x,y,z]`` 是人工指定，
        直接采用（``rule='fixed'``，不再做对称自检）。
    tol:
        数值容差，用于退化保护（并列阈值固定为 :data:`TIE_TOL` = 1e-4）。

    Returns
    -------
    :class:`DirectionResult` —— ``vector`` / ``label`` / ``rule`` 三键 +
    ``scores`` 等诊断键；``rule ∈ {'symmetry','major_axis','bend_plane','fixed'}``
    （可能带 ``warning(...)`` 后缀）。压弯结果额外带 ``direction_source``
    （``span_projection`` / ``in_plane_perp`` / ``bend_axis``）说明实际用的候选，
    以及 ``parting_sin_min``（分型方向在各截面内的最小投影长度，下游
    ``MIN_AXIS_PROJECTION=0.15`` 会据此判退化）。

    Raises
    ------
    ValueError
        成型类（shaping / 其它）最佳镜像评分超过 :data:`SYMMETRY_TOL`（不满足
        「分型方向必须是对称方向」的硬约束），或输入退化。
    """
    op = _normalized_category(category)
    explicit = _explicit(requested)
    if explicit is not None:
        manual_deg = None
        if not isinstance(requested, (list, tuple, np.ndarray)):
            manual_deg = _parse_angle(str(requested))
        return _make(
            explicit,
            f"angle:{manual_deg:g}°" if manual_deg is not None else _label_for(explicit),
            "fixed",
            category=op or "other",
            requested=str(requested),
            angle_deg=manual_deg if manual_deg is not None else angle_of_xy(explicit),
            note="人工指定方向，未做对称自检",
        )

    items = _ring_items(rings)
    if not items:
        raise ValueError("rings 为空，无法判定分型方向")
    scores = score_axes(rings)
    rounded = _rounded(scores)

    if op == "belling":
        best = min(float(scores[name]) for name in AXES)
        near = [name for name in ("Y", "Z", "X") if float(scores[name]) <= best + TIE_TOL]
        name = "Y" if "Y" in near else near[0]
        return _make(
            _axis_vector(name),
            name,
            "fixed",
            category="belling",
            scores=rounded,
            note="回转体任意方向；取评分最小的轴，并列取 Y",
        )

    elif op == "flattening":
        return _resolve_flattening(items, rings, scores, rounded, tol)

    elif op == "bending":
        return _resolve_bending(items, rings, scores, rounded, tol)

    # shaping / 其它：优先「平面弯 → 弯曲平面法向」；直管 / 空间弯再回退三轴评分
    normal = planar_bend_normal(rings)
    if normal is not None:
        degrees = angle_of_xy(normal)
        angle_text = "竖直" if degrees is None else f"绕 Z {degrees:+.2f}°"
        return _make(
            normal,
            _label_for(normal),
            "bend_plane",
            category=op or "other",
            scores=rounded,
            angle_deg=degrees,
            note=f"平面弯：分型方向取弯曲平面法向（{angle_text}），分型面=弯曲平面",
        )

    name, tied = _tied_min(scores)
    best = float(scores[name])
    if best > SYMMETRY_TOL:
        raise ValueError(
            "分型方向必须是对称方向，但三向镜像评分都超过容差 "
            f"{SYMMETRY_TOL:g}：X={scores['X']:.4f} Y={scores['Y']:.4f} Z={scores['Z']:.4f}"
            f"（最佳 {name}={best:.4f}，评分=反射点最近距离均值/点云尺度，越小越对称）；"
            f"类别={op or '其它'}，{len(items)} 个截面。"
        )
    return _make(
        _axis_vector(name),
        name,
        "symmetry",
        category=op or "other",
        scores=rounded,
        tied=tied,
    )


def _resolve_flattening(items: Sequence[Any], rings: Any, scores: dict[str, float],
                        rounded: dict[str, float], tol: float) -> DirectionResult:
    """压扁：分型线沿截面长轴（逐截面 PCA 长轴 → 符号对齐 → 平均）。"""
    axes = per_section_axes(rings)
    if not axes:
        raise ValueError("压扁规则：rings 为空")
    mean = np.mean(np.array(axes, dtype=float), axis=0)
    direction = _unit(mean)
    warnings: list[str] = []
    if direction is None:
        direction = axes[0]
        warnings.append("逐截面长轴互相抵消，退回首截面长轴")

    # 按 §7 表：再投影到首截面平面（首截面法向已知时）
    normal0 = _normal_of(items[0], _points_of(items[0]))
    if normal0 is not None:
        projected = direction - float(np.dot(direction, normal0)) * normal0
        if float(np.linalg.norm(projected)) > 0.5:
            direction = normalize(projected)
        else:
            warnings.append("长轴与首截面法向近似平行，跳过首截面投影")

    # 对称自检：与最小镜像分轴夹角 > 30° 写 warning。椭圆截面 X/Y 两个镜像面常常
    # 并列（分数都≈0），所以只在「并列集合里最接近长轴的那根轴」仍差 >30° 时才告警。
    tie_name, tied = _tied_min(scores)
    best_dot, sym_name = -1.0, tie_name
    for cand in tied:
        dod = abs(float(np.dot(direction, _axis_vector(cand))))
        if dod > best_dot:
            best_dot, sym_name = dod, cand
    angle = float(np.degrees(np.arccos(max(-1.0, min(1.0, best_dot)))))
    if angle > 30.0:
        warnings.append(f"截面长轴与最小镜像分轴 {sym_name} 夹角 {angle:.1f}°（>30°）")

    rule = "major_axis"
    if warnings:
        rule += "(" + "; ".join(f"warning: {w}" for w in warnings) + ")"
    return _make(
        direction,
        _label_for(direction),
        rule,
        category="flattening",
        scores=rounded,
        angle_deg=round(angle, 3),
        symmetry_axis=sym_name,
        symmetry_tied=tied,
        per_section=len(axes),
    )


def _projection_profile(direction: np.ndarray, items: Sequence[Any]) -> list[float]:
    """每个截面里 ``direction`` 的投影长度 = |sin∠(direction, 截面法向)|。"""
    sins: list[float] = []
    for item, pts in zip(items, _point_list(items)):
        nrm = _normal_of(item, pts)
        if nrm is None:
            continue
        sins.append(float(np.sqrt(max(0.0, 1.0 - float(np.dot(direction, nrm)) ** 2))))
    return sins


def _resolve_bending(items: Sequence[Any], rings: Any, scores: dict[str, float],
                     rounded: dict[str, float], tol: float) -> DirectionResult:
    """压弯：优先取**弯曲平面法向 n**，n 退化时才退到弯曲平面内的候选。

    几何事实（实测 24TK_1324-1 压弯模型 760 环 + 合成圆弧）：压弯件关于弯曲平面
    镜像对称，所以平面法向 ``n`` 才是真正的对称轴（三轴评分里 n 恒为 0.0000，
    例如 760 环实测 Z=0.0000 / X=0.0087 / Y=0.0104）；而截面法向 = 管道切线（躺在
    弯曲平面内）⊥ n，于是 ``n`` 在每个截面里的投影恒为 1，下游
    ``routes/selfref.py:206 _apply_direction`` 的 ``MIN_AXIS_PROJECTION`` 不会触发。
    反过来，契约写的「span 在弯曲平面内的投影」对**回转圆弧**天然退化：圆弧的弦
    平行于中段截面的切线，于是中段截面里 d 与法向平行、投影长度 = 0（实测第
    488/760 截面 sin=0.000），硬用它会让所有压弯件在下游炸掉。

    候选按优先级取第一个「所有截面投影 ≥ :data:`MIN_PROJECTION`」的：

    1. ``bend_axis``：``n`` 本身 —— 弯曲平面法向（对称轴 = 物理上的开模方向）；
    2. ``span_projection``：``normalize(span - (span·n)n)`` —— 契约公式（span 取
       包围盒最长边的**坐标轴**）；
    3. ``plane_axis``：``bending_plane`` 的最大主成分（质心点集的实际弦方向），
       与契约公式同向但不受「span 必须与坐标轴对齐」限制；
    4. ``in_plane_perp``：``normalize(n × plane_axis)`` —— 仍在平面内、与弦垂直。

    常态走 1 号（``direction_source='bend_axis'``、不带 warning）；只有 1 号在截面里
    退化（例如质心近似共线的直管）时才走 2/3/4，并附 warning 说明偏离原因。
    """
    warnings: list[str] = []
    normal, plane_axis = bending_plane(rings, warnings=warnings)
    span = _span_axis(rings)

    candidates: list[tuple[str, np.ndarray]] = [("bend_axis", normal)]
    raw = span - float(np.dot(span, normal)) * normal
    d_span = _unit(raw)
    if d_span is not None and float(np.linalg.norm(raw)) >= tol:
        candidates.append(("span_projection", d_span))
    chord = _unit(plane_axis)
    if chord is not None:
        candidates.append(("plane_axis", chord))
        d_perp = _unit(np.cross(normal, chord))
        if d_perp is not None:
            candidates.append(("in_plane_perp", d_perp))

    best: tuple[float, str, np.ndarray] | None = None
    for name, vec in candidates:
        profile = _projection_profile(vec, items)
        lo = min(profile) if profile else 0.0
        if best is None or lo > best[0]:
            best = (lo, name, vec)
        if lo >= MIN_PROJECTION:
            best = (lo, name, vec)
            break
    if best is None:  # pragma: no cover - candidates 至少含 bend_axis
        raise ValueError("压弯规则：rings 为空，无法确定分型方向")
    sin_min, source, direction = best
    n_proj = min(_projection_profile(normal, items)) if items else 0.0
    span_proj = min(_projection_profile(d_span, items)) if d_span is not None else float("nan")

    if source != "bend_axis":
        warnings.append(
            f"弯曲平面法向 n 在截面内的投影不足（最小 |sin|={n_proj:.3f}<{MIN_PROJECTION:g}），"
            f"改用弯曲平面内的候选 {source}"
        )
        if np.isfinite(span_proj) and span_proj < MIN_PROJECTION:
            warnings.append(
                f"契约公式（span 在弯曲平面内的投影）同样退化"
                f"（最小 |sin|={span_proj:.3f}<{MIN_PROJECTION:g}，圆弧的弦平行于中段切线）"
            )
    if sin_min < MIN_PROJECTION:
        warnings.append(
            f"所有候选的投影都低于 {MIN_PROJECTION:g}（最佳 |sin|={sin_min:.3f}），分型方向可能不稳"
        )

    rule = "bend_plane"
    if warnings:
        rule += "(" + "; ".join(f"warning: {w}" for w in warnings) + ")"
    return _make(
        direction,
        _label_for(direction),
        rule,
        category="bending",
        scores=rounded,
        direction_source=source,
        plane_normal=[float(c) for c in normal],
        bend_axis=[float(c) for c in normal],
        span_axis=[float(c) for c in span],
        plane_axis=[float(c) for c in plane_axis],
        parting_sin_min=None if not np.isfinite(sin_min) else round(sin_min, 4),
        angle_to_normal_deg=round(float(np.degrees(np.arccos(
            min(1.0, abs(float(np.dot(direction, normal))))))), 3),
    )
