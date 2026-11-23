"""站位规划：粗测 + 曲率处加密 + 1 mm 网格重建。

设计约束（改之前先读）：

  * 下游 QA / 工艺优化 / RFM / 一体化流水线全部按 **1 mm 站位表**消费，所以输出仍是
    1 mm 网格，只是逐点标注 ``station_source``：``measured`` / ``refined`` /
    ``interpolated`` / ``extrapolated``；
  * 「缺测不插值造数」是既有铁律：这里只插值「设计上连续」的标量（周长、壁厚、壁厚统计），
    截面轮廓不凭空生成——插值站沿用最近实测站的轮廓（标 ``contour_inherited_from_mm``），
    并在测量平面内按法向平移到位；
  * 曲率判据用**二阶差分**而不是斜率：长锥面 ``Δ²≡0``、线性插值本就零误差；真正吃站距的是
    过渡弧、台阶、端部收口这类二阶不连续处；
  * 端部外推窗口在 :mod:`end_treatment` 里，那里的拟合至少要 3-6 个实测站，
    所以两端各 ``end_fine_mm`` 强制 1 mm。

时间账（24TK_1324-1，744 站基准）：3 mm 基准约 255 站，S5 19.32 s → 约 6.6 s；
局部加密后实测站约占 1 mm 方案的 25%～40%，误差（相对 1 mm 基准）周长 ≤0.04 mm、
壁厚 ≤0.003 mm。
"""
from __future__ import annotations

import os
from collections import Counter

import numpy as np

#: 下游统一消费的网格步长（保持历史 1 mm 口径，不要改）。
GRID_STEP_MM = 1.0
#: 归档网格缺省步长：``GTAPP_STATION_GRID`` 未设时与 GRID_STEP_MM 相同（1 mm）。
DEFAULT_GRID_STEP_MM = 1.0
#: 归档网格允许的最大步长（再大前端表格/稀疏截面就失去意义）。
MAX_GRID_STEP_MM = 10.0
#: 实测基准步长：环境变量缺省值（1.0 = 与历史逐位一致）。
DEFAULT_STATION_MM = 1.0
#: 允许的最大实测步长（再大过渡段就看不见了）。
MAX_STATION_MM = 10.0
#: 二阶差分容差（周长 mm / 壁厚 mm）。3 mm 基准下实测最大误差分别为 0.037 / 0.003。
DEFAULT_TOL_PERIMETER_MM = 0.05
DEFAULT_TOL_WALL_MM = 0.02
#: 端部强制 1 mm 的长度（端部拟合窗口 10 mm，至少要 6 个实测站）。
DEFAULT_END_FINE_MM = 15.0
#: 粗定位种子间距：0/未设 = 随实测站距自动（1 mm→5 mm、3 mm→10 mm、10 mm→15 mm）。
DEFAULT_COARSE_STEP_MM = 0.0
MIN_COARSE_STEP_MM = 3.0
MAX_COARSE_STEP_MM = 30.0
#: 法向校正提前收敛阈值（mm）：单轮最大位移小于它就不再迭代；0 = 关闭（跑满轮数）。
DEFAULT_CORRECTION_TOL_MM = 0.02

#: 参与插值的标量字段。
SCALAR_KEYS = (
    "perimeter", "inner_perimeter", "wall_thickness",
    "wall_thickness_min", "wall_thickness_max", "wall_thickness_mean", "wall_area",
)


def station_step_mm(default: float = DEFAULT_STATION_MM) -> float:
    """实测基准步长：``GTAPP_STATION_MM``，夹在 [1, 10]；非法值回落默认。"""
    raw = os.environ.get("GTAPP_STATION_MM")
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(value):
        return float(default)
    return max(1.0, min(MAX_STATION_MM, value))


def station_grid_mm(default: float = DEFAULT_GRID_STEP_MM) -> float:
    """归档网格步长：``GTAPP_STATION_GRID``，夹在 [1, 10]；非法值回落默认。

    与 ``station_step_mm`` 相互独立：

      * ``GTAPP_STATION_MM`` 控制「在哪几个位置真做截面」；
      * ``GTAPP_STATION_GRID`` 控制「结果表按多密的网格归档」。
    """
    raw = os.environ.get("GTAPP_STATION_GRID")
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(value):
        return float(default)
    return max(1.0, min(MAX_GRID_STEP_MM, value))


def coarse_step_mm(station_mm: float | None = None) -> float:
    """粗定位种子间距：``GTAPP_COARSE_STEP_MM``，0/未设 = 固定 5 mm。

    为什么自动值不跟着站距放大：1324-1 实测把种子放到 10 mm 后，急缩锥段（斜率约
    −1.08 mm/mm）的中心线被样条抹平，s 轴偏 0.26 mm、周长最大差 0.37 mm；即使第 2 轮
    回到 5 mm 网格也只剩 0.26 mm——粗种子一旦带偏就回不来。所以自动档保持 5 mm 不放大，
    省时间改由「按位移按需重切」承担（见 :func:`correction_tolerance_mm`）。

    显式设置时夹在 [3, 30]；非法/非有限值回落自动（5 mm）。
    """
    raw = os.environ.get("GTAPP_COARSE_STEP_MM")
    if raw is not None and str(raw).strip():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        if np.isfinite(value) and value > 0:
            return max(MIN_COARSE_STEP_MM, min(MAX_COARSE_STEP_MM, value))
    return 5.0


def correction_rounds(station_mm: float | None = None) -> int:
    """中心线法向校正轮数上限：``GTAPP_CORRECTION_ROUNDS``，缺省 3 轮。

    粗测档不再是「少跑几轮」，而是「每轮只重切还没收敛的站位」
    （见 :func:`correction_tolerance_mm`）——少跑整轮会把锥面中心线留在 0.35 mm 的偏差上。
    """
    raw = os.environ.get("GTAPP_CORRECTION_ROUNDS")
    if raw is not None and str(raw).strip():
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return max(1, min(5, value))
    return 3


def correction_tolerance_mm(station_mm: float | None = None) -> float:
    """中心线校正的提前收敛阈值（``GTAPP_CORRECTION_TOL_MM``）：单轮最大位移小于它才跳过剩余轮。

    缺省 **0 = 关闭**（每轮都重切全部定位站位）。这不是保守，是实测结论：
    1324-1 相对 1 mm 基线的周长最大差——3 轮全切 0.0052 mm、2 轮全切 4.39 mm、
    「只重切没收敛的站位」1.36 mm。斜切椭圆的质心偏置沿管面方向，样条残差看不见它，
    少切一轮中心线就再也纠不回来。仅当用户明确接受精度下降时才调这个值。
    """
    raw = os.environ.get("GTAPP_CORRECTION_TOL_MM")
    fallback = 0.0
    if raw is None or not str(raw).strip():
        return float(fallback)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(fallback)
    if not np.isfinite(value) or value < 0:
        return float(fallback)
    return min(2.0, value)


def coarse_positions(total_length_mm: float, step_mm: float = GRID_STEP_MM) -> np.ndarray:
    """沿弧长的实测站位（含两端点）。"""
    total = float(total_length_mm)
    if total <= 0:
        return np.zeros(1)
    step = max(float(step_mm), 1e-3)
    pos = np.arange(0.0, total, step)
    if pos.size == 0 or pos[-1] < total - 1e-9:
        pos = np.r_[pos, total]
    return pos


def refine_positions(
    measured_s,
    perimeter,
    wall_thickness,
    *,
    step_mm: float,
    fine_mm: float = GRID_STEP_MM,
    tol_perimeter_mm: float = DEFAULT_TOL_PERIMETER_MM,
    tol_wall_mm: float = DEFAULT_TOL_WALL_MM,
    end_fine_mm: float = DEFAULT_END_FINE_MM,
) -> np.ndarray:
    """按已测站位的二阶差分，给出**需要补测**的 1 mm 位置（已测位置不在结果里）。"""
    s = np.asarray(measured_s, dtype=float)
    p = np.asarray(perimeter, dtype=float)
    t = np.asarray(wall_thickness, dtype=float)
    if s.size < 3 or step_mm <= fine_mm + 1e-9:
        return np.zeros(0)
    order = np.argsort(s)
    s, p, t = s[order], np.nan_to_num(p[order]), np.nan_to_num(t[order])
    total = float(s[-1])
    spans: list[tuple[float, float]] = []
    for i in range(1, s.size - 1):
        d2_p = abs(p[i + 1] - 2.0 * p[i] + p[i - 1])
        d2_t = abs(t[i + 1] - 2.0 * t[i] + t[i - 1])
        if d2_p > tol_perimeter_mm or d2_t > tol_wall_mm:
            spans.append((float(s[i - 1]), float(s[i + 1])))
    if end_fine_mm > 0:
        spans.append((0.0, min(float(end_fine_mm), total)))
        spans.append((max(0.0, total - float(end_fine_mm)), total))
    wanted: list[np.ndarray] = []
    for lo, hi in spans:
        first = int(np.ceil((lo - 1e-9) / fine_mm))
        last = int(np.floor((hi + 1e-9) / fine_mm))
        if last >= first:
            wanted.append(np.arange(first, last + 1) * fine_mm)
    if not wanted:
        return np.zeros(0)
    grid = np.unique(np.round(np.concatenate(wanted), 6))
    existing = set(np.round(s, 6).tolist())
    return np.array([v for v in grid if v not in existing and -1e-9 <= v <= total + 1e-9])


def _nearest_contour(rows: list[tuple[float, dict]], station: float):
    """最近实测轮廓（插值站的截面形状来源）。"""
    if not rows:
        return None, None
    stations = np.array([s for s, _ in rows])
    index = int(np.argmin(np.abs(stations - station)))
    return rows[index]


def _interpolated_row(query, fpos, fnrm, fscalars, contour, *, label=None):
    """构造一个插值站：标量按 PCHIP，轮廓借最近实测站并沿法向平移。

    ``query`` 是插值采样位置；``label`` 是这行要登记的长度坐标（网格点夹到数据区间
    端点时会与采样位置不同）。
    """
    from .geometry import orthonormal_basis

    station = float(query if label is None else label)
    normal = np.asarray(fnrm(query), dtype=float)
    norm = float(np.linalg.norm(normal))
    normal = normal / norm if norm > 1e-12 else np.array([0.0, 0.0, 1.0])
    normal, u, v = orthonormal_basis(normal)
    values = {key: float(f(query)) for key, f in fscalars.items()}
    perimeter = float(values.get("perimeter", float("nan")))
    row = {
        "cumulative_length": float(station),
        "position": np.asarray(fpos(query), dtype=float).tolist(),
        "normal": normal.tolist(), "basis_u": u.tolist(), "basis_v": v.tolist(),
        "perimeter": perimeter,
        "inner_perimeter": float(values.get("inner_perimeter", float("nan"))),
        "equivalent_diameter": float(perimeter / np.pi),
        "wall_thickness": float(values.get("wall_thickness", float("nan"))),
        "wall_thickness_min": float(values.get("wall_thickness_min", float("nan"))),
        "wall_thickness_max": float(values.get("wall_thickness_max", float("nan"))),
        "wall_thickness_mean": float(values.get("wall_thickness_mean", float("nan"))),
        "wall_area": float(values.get("wall_area", float("nan"))),
        "centroid_residual_mm": 0.0, "normal_deviation_deg": 0.0,
        "measurement_status": "interpolated", "station_source": "interpolated",
        "thickness_sample_count": 0, "contour_repairs": [],
        "plane_offset_mm": 0.0, "intersection_fuzzy_mm": 0.0,
        "outer_2d": None, "inner_2d": None, "outline_3d": None, "inner_outline_3d": None,
    }
    if contour is not None:
        source_s, source = contour
        row["outer_2d"] = source.get("outer_2d")
        row["inner_2d"] = source.get("inner_2d")
        row["contour_inherited_from_mm"] = float(source_s)
        offset = np.asarray(normal, dtype=float) * float(station - source_s)
        outline = source.get("outline_3d")
        if outline:
            row["outline_3d"] = (np.asarray(outline, dtype=float) + offset[None, :]).tolist()
        inner_outline = source.get("inner_outline_3d")
        if inner_outline:
            row["inner_outline_3d"] = (np.asarray(inner_outline, dtype=float) + offset[None, :]).tolist()
    return row


def expand_rows(rows, *, grid_step: float = GRID_STEP_MM):
    """把「实测站位」重建到 1 mm 网格，返回 ``(rows, info)``。

    * 已存在的站位（实测 / 加密 / 端部外推）原样保留，只补 ``station_source``；
    * 缺的 1 mm 位置用 PCHIP 插值标量，轮廓沿用最近实测站；
    * ``index`` 重新编号，供 ``sections_full`` / 稀疏截面定位。
    """
    from scipy.interpolate import PchipInterpolator

    rows = list(rows)
    if len(rows) < 2:
        return rows, {"grid_step_mm": grid_step, "interpolated": 0, "sources": {}}
    s = np.array([float(r["cumulative_length"]) for r in rows])
    order = np.argsort(s)
    rows = [rows[i] for i in order]
    s = s[order]
    total = float(s[-1])
    # 端部修正会把 s 轴整体平移，实测站位因此落在 10.2439 这种偏移量上（1 mm 链路
    # 也有同样的偏移）。这里用「实测站位的中位偏移量」做网格相位，再让每个网格点吸附
    # 最近的真实行——同一网格点只留一行（优先实测），既不重复也不丢读数。
    step = float(grid_step)
    measured = [float(v) for v, r in zip(s, rows) if r.get("measurement_status") == "measured"]
    phase = float(np.median([v - np.floor(v) for v in measured])) if measured else 0.0
    if step > 0 and phase > 0.75 * step:
        phase -= step  # 相位接近整毫米时改用等价的前一格晶格，避免与整数头段挤在一起
    tol = 0.45 * step if step > 0 else 0.0
    if step > 0:
        # 端部外推段是历史留下的整数站位（0,1,2...），实测段从带相位的第一站开始。
        head_end = float(np.floor(measured[0])) if measured else float(s[0])
        head = np.arange(0.0, head_end + 1e-9, step) if head_end > 1e-9 else np.zeros(0)
        base = head_end + phase  # 实测段从第一个带相位的实测站位接着铺
        body = base + np.arange(0.0, max(total - base, 0.0) + 1e-9, step)
        grid = np.unique(np.r_[head, body])
        if grid.size and total - grid[-1] > tol:
            grid = np.r_[grid, total]  # 尾部残差超过半个站距才单列一站
    else:
        grid = s.copy()
    priority = {"measured": 0, "needs_review": 1, "extrapolated": 2}
    snapped = {}
    kept = []
    for i, value in enumerate(s):
        if grid_step <= 0:
            snapped[round(float(value), 6)] = i
            continue
        g = float(grid[int(np.argmin(np.abs(grid - value)))])
        if abs(g - value) > tol:
            kept.append(i)  # 偏离网格太远（端部残差/异常补测），原样保留
            continue
        row = rows[i]
        if abs(g - value) > tol:
            kept.append(i)  # 偏离网格太远（端部残差/异常补测），原样保留
            continue
        rank = (priority.get(row.get("measurement_status", "measured"), 3), abs(g - value))
        if g in snapped:
            current = snapped[g]
            current_rank = (priority.get(rows[current].get("measurement_status", "measured"), 3),
                            abs(g - s[current]))
            if rank >= current_rank:
                continue
        snapped[g] = i

    for g, i in snapped.items():
        rows[i]["cumulative_length"] = float(g)
        if not rows[i].get("station_source"):
            status = rows[i].get("measurement_status", "measured")
            rows[i]["station_source"] = "extrapolated" if status == "extrapolated" else "measured"
    aligned = [rows[i] for i in sorted(set(snapped.values()) | set(kept))]
    for i in kept:  # 保留下来的离网点也要有来源标记，供前端展示
        row = rows[i]
        if not row.get("station_source"):
            status = row.get("measurement_status", "measured")
            row["station_source"] = "extrapolated" if status == "extrapolated" else "measured"
    aligned.sort(key=lambda r: float(r["cumulative_length"]))
    aligned_s = np.array([r["cumulative_length"] for r in aligned], dtype=float)

    contours = [(float(r["cumulative_length"]), r) for r in aligned if r.get("outer_2d")]
    pos = np.array([r["position"] for r in aligned], dtype=float)
    nrm = np.array([r["normal"] for r in aligned], dtype=float)
    fpos = PchipInterpolator(aligned_s, pos, axis=0, extrapolate=False)
    fnrm = PchipInterpolator(aligned_s, nrm, axis=0, extrapolate=False)
    fscalars = {}
    for key in SCALAR_KEYS:
        values = [r.get(key) for r in aligned]
        if any(v is None for v in values):
            continue
        arr = np.asarray(values, dtype=float)
        if not np.all(np.isfinite(arr)):
            continue
        fscalars[key] = PchipInterpolator(aligned_s, arr, extrapolate=False)

    out = []
    lo_s, hi_s = float(aligned_s[0]), float(aligned_s[-1])
    for station in grid:
        key = round(float(station), 6)
        i = snapped.get(float(station), snapped.get(key))
        if i is not None and abs(float(rows[i]["cumulative_length"]) - float(station)) < 1e-6:
            out.append(rows[i])
        else:
            # 网格点落在数据区间外（端部补点被合并时会出现）时按端点值补齐，不外推出 NaN。
            out.append(_interpolated_row(float(np.clip(station, lo_s, hi_s)), fpos, fnrm, fscalars,
                                         _nearest_contour(contours, float(station)),
                                         label=float(station)))
    out.extend(rows[i] for i in kept if rows[i] not in out)
    out.sort(key=lambda r: float(r["cumulative_length"]))
    for index, row in enumerate(out):
        row["index"] = index
    counts = Counter(str(r.get("station_source") or "measured") for r in out)
    return out, {"grid_step_mm": float(grid_step),
                 "interpolated": int(counts.get("interpolated", 0)),
                 "sources": dict(counts)}


def decimate_rows(rows, *, grid_step: float = GRID_STEP_MM):
    """把 1 mm 网格行抽稀成归档行（隔 ``grid_step`` 取一行，保留首末行）。

    归档层（``sections_full`` / 前端表格 / 入库 payload）可以比计算层稀；抽稀只做
    「少几行」，绝不再做一次插值——否则会叠加第二次数值误差（3 mm 归档实测过
    周长 0.42 mm 偏差，原因就是拿稀疏节点重新插值）。
    """
    rows = list(rows)
    step = float(grid_step)
    if step <= 1. + 1e-9 or len(rows) < 3:
        return rows, {"archive_step_mm": 1.0, "archive_rows": len(rows)}
    stride = max(1, int(round(step)))
    if stride <= 1:
        return rows, {"archive_step_mm": 1.0, "archive_rows": len(rows)}
    real = ("measured", "refined", "needs_review")
    picked = []
    n = len(rows)
    for start in range(0, n, stride):
        if str(rows[start].get("measurement_status")) in real:
            picked.append(start)
            continue
        # 目标点本身是插值行（实测站落在半毫米相位上）时，1 mm 邻域内有真实截面就
        # 优先归档真实截面——否则「来源」列整表都是插值，读数也不如实测可信。
        window = [i for i in range(max(0, start - 1), min(n, start + 2))
                  if str(rows[i].get("measurement_status")) in real]
        picked.append(window[0] if window else start)
    picked = sorted(set(picked))
    out = [rows[i] for i in picked]
    if out and out[-1] is not rows[-1]:
        out.append(rows[-1])
    return out, {"archive_step_mm": float(stride), "archive_rows": len(out)}
