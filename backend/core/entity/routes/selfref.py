"""S3 · STEP 自参考保形 → 4 面实体（c1 路的 selfref 分支）。

路线（见 ``ENTITY_PIPELINE.md`` §6.1「自参考保形」）：

1. 读该件自己的模型 STEP，找出一组**闭合且平面**的截面环，按管件长轴排序；
2. 每个截面把调用方给的显式分型方向 ``direction`` 投影到截面平面得 ``a`` 轴，
   ``±a`` 极值点即 U=0 / U=0.5 的分型端点（上下两条半截面）；
3. 交给 ``vendor/pipe_smoother`` 的 ``assemble.build_from_closed_sections``
   拟一张全周周期 B 样条曲面、在两条分型缝处切成 2 个侧面，加 2 个平面端盖，
   缝合为 1 个 Solid（4 面 / 2 条共享分型边 / 0 自由边）。

所有几何算法都在 vendor 内核里，本模块只做「STEP → Section 列表 → 参数化 →
调用 → 体检/回报」，不重写任何曲面/缝合逻辑。

对外两个函数（签名固定，S4 的 pipe 路会复用 ``sections_from_step``）：

* :func:`sections_from_step` —— STEP → 截面环 dict 列表（points/centroid/normal）
* :func:`build_from_step` —— STEP → 4 面实体 STEP + 体检 dict
"""
from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

# 相对 import：本文件是 core.entity.routes 的成员（backend 在 sys.path 上即可）。
from ..contracts import CheckRecord, ModelSpec, PipelineContext, RouteResult, model_def

__all__ = ["sections_from_step", "build_from_step", "run", "available"]

Log = Callable[[str], None]

#: 截面数超过这个值时按等弧长抽稀（沿用 pipe 路 40/64/96/160 的自适应思路）。
DEFAULT_MAX_SECTIONS = 160

#: 与 c1 路一致的半截面点数（settings.HALF_SAMPLES）。
DEFAULT_HALF_SAMPLES = 65

#: 显式方向在截面平面内的最短投影长度（相对单位向量）；再短就说明方向≈法向。
MIN_AXIS_PROJECTION = 0.15

#: 分型边判定阈值，与 ``geometry._parting_edges`` 保持一致。
PARTING_LENGTH_RATIO = 0.2
PARTING_SPAN_RATIO = 0.5


# ---------------------------------------------------------------------------
# 延迟 import（OCP 只在真正做几何时进来，contracts 规则 §「模块顶层只 import 标准库」）
# ---------------------------------------------------------------------------


def _geometry():
    from ..vendor.pipe_smoother import geometry as G

    return G


def _assemble():
    from ..vendor.pipe_smoother import assemble as A

    return A


def _logger(log: Log | None) -> Log:
    """把调用方的 log 包成「绝不抛」的日志函数。

    调用方的 sink 可能是 GBK 控制台（写 ``mm³``/``×`` 会 UnicodeEncodeError）、
    也可能自己写文件时抛 OSError——日志失败绝不能把几何构建带崩。
    """
    if log is None:
        return lambda _msg: None
    if not callable(log):
        raise TypeError("log 必须是可调用对象（None 或 f(str)）")

    def emit(message: str) -> None:
        try:
            log(message)
        except Exception:  # noqa: BLE001 - 日志失败不得影响实体
            try:
                print(message.encode("ascii", "replace").decode("ascii"))
            except Exception:  # noqa: BLE001
                pass

    return emit


# ---------------------------------------------------------------------------
# 1) STEP → 截面环
# ---------------------------------------------------------------------------


def _normalise(v: Sequence[float], *, what: str = "向量") -> np.ndarray:
    arr = np.asarray(v, dtype=float).reshape(-1)
    if arr.size != 3:
        raise ValueError(f"{what}必须是长度 3 的 [x, y, z]，收到 {arr.size} 个分量")
    norm = float(np.linalg.norm(arr))
    if not np.isfinite(norm) or norm < 1e-9:
        raise ValueError(f"{what}不能是零向量")
    return arr / norm


def _ring_from_section(sec, t: float) -> dict:
    points = np.array(sec.points, dtype=float, copy=True)
    centroid = np.array(sec.center, dtype=float, copy=True)
    normal = np.array(sec.normal, dtype=float, copy=True)
    return {
        "points": points,
        "centroid": centroid,
        "normal": normal,
        "t": float(t),
        "perimeter": float(sec.length),
        "index": int(sec.index),
        "side": np.array(sec.side, dtype=float, copy=True),
        "up": np.array(sec.up, dtype=float, copy=True),
        "axis": str(sec.axis_name or ""),
    }


def _arc_params(rings: list[dict]) -> list[float]:
    """沿质心序列的累计弧长，用作截面顺序参数 t。"""
    if not rings:
        return []
    centers = np.array([r["centroid"] for r in rings], dtype=float)
    steps = np.linalg.norm(np.diff(centers, axis=0), axis=1)
    return [float(t) for t in np.concatenate([[0.0], np.cumsum(steps)])]


def sections_from_step(step_path: str | Path, *, max_sections: int | None = None) -> list[dict]:
    """读 STEP，返回按长轴排序的闭合平面截面环。

    Parameters
    ----------
    step_path:
        待分析的 STEP/STP 文件。
    max_sections:
        可选上限；超过时按序号等间距抽稀（保留首末截面）。``None`` 保留全部。

    Returns
    -------
    list[dict]，每项::

        {"points": (N,3) ndarray,     # 截面平面上闭合环的 3D 采样点
         "centroid": (3,) ndarray,    # 环心
         "normal": (3,) ndarray,      # 截面法向（沿长轴，朝下游）
         "t": float, "perimeter": float, "index": int,
         "side": (3,), "up": (3,), "axis": str}

    Raises
    ------
    ValueError
        文件不存在 / 读不出几何 / 闭合平面截面环不足 4 个。
    """
    path = Path(step_path)
    if not path.is_file():
        raise ValueError(f"STEP 文件不存在：{path}")

    G = _geometry()
    try:
        shape = G.read_step(path)
    except Exception as exc:  # noqa: BLE001 - 统一转成可读 ValueError
        raise ValueError(f"读取 STEP 失败（{path.name}）：{exc}") from exc

    warnings: list[str] = []
    try:
        sections = G.extract_sections(shape, parting_axis="auto", warnings=warnings)
    except Exception as exc:  # noqa: BLE001
        reason = "；".join(warnings) if warnings else str(exc)
        raise ValueError(
            f"未能从 {path.name} 提取足够的闭合平面截面环（需要 ≥{G.MIN_SECTIONS} 个）：{reason}"
        ) from exc
    if not sections:
        raise ValueError(f"{path.name} 里没有找到闭合平面截面环")

    sections = _decimate(sections, max_sections)
    rings = [_ring_from_section(sec, 0.0) for sec in sections]
    for ring, t in zip(rings, _arc_params(rings)):
        ring["t"] = t
    return rings


def _decimate(sections: Sequence, max_sections: int | None) -> list:
    limit = int(max_sections) if max_sections else 0
    if limit <= 0 or len(sections) <= limit:
        return list(sections)
    if limit < 4:
        raise ValueError(f"max_sections 太小（{limit}）；至少需要 4 个截面")
    idx = np.unique(np.round(np.linspace(0, len(sections) - 1, limit)).astype(int))
    return [sections[i] for i in idx]


# ---------------------------------------------------------------------------
# 2) 分型方向 → 每截面的 a 轴
# ---------------------------------------------------------------------------


def _resolve_direction(direction: Any) -> tuple[np.ndarray | None, str]:
    """把调用方的 ``direction`` 归一成 (单位向量 | None, 标签)。

    ``None`` / ``"auto"`` 表示交给 vendor 的对称轴检测（用于没有显式方向的场景）。
    字符串支持 ``X``/``Y``/``Z``（可带 ``+``/``-``）、``[x,y,z]``、``0.89,-0.45,0``
    与 ``angle:-27``（绕 +Z、从 +X 逆时针为正）。
    """
    if direction is None:
        return None, "auto"
    if isinstance(direction, str):
        text = direction.strip()
        if not text or text.lower() in {"auto", "none", "自动"}:
            return None, "auto"
        try:
            from .. import directions
            vec = directions.parse_direction(text)
            if vec is not None:
                return vec, directions.direction_label(text)
        except Exception:  # noqa: BLE001 - 解析失败再走旧的轴写法
            pass
        vec = np.zeros(3)
        axis = text[0].upper()
        if axis not in "XYZ" or len(text) > 2:
            raise ValueError(f"无法解析 direction={direction!r}；请给 [x, y, z] 或 'X'/'Y'/'Z'")
        vec["XYZ".index(axis)] = -1.0 if text[1:] in {"-", "-1"} else 1.0
        return vec, axis + ("-" if vec["XYZ".index(axis)] < 0 else "+")
    vec = _normalise(direction, what="分型方向 direction")
    label = "[" + ", ".join(f"{c:.4g}" for c in vec) + "]"
    return vec, label


def _apply_direction(sections: Sequence, direction: np.ndarray | None) -> None:
    """把 ``direction`` 投影进每个截面平面，得到 a 轴（side）与 up 轴。

    与 ``geometry.extract_sections`` 内部的处理一致（那用的是检测出来的全局
    对称轴），这里只是把轴换成调用方给的显式向量，并保证相邻截面同向，
    避免上下半截面翻转。
    """
    if direction is None:
        return
    prev_side = None
    prev_up = None
    for i, sec in enumerate(sections):
        normal = np.asarray(sec.normal, dtype=float)
        norm = float(np.linalg.norm(normal))
        if norm < 1e-9:
            raise ValueError(f"第 {i} 个截面的法向退化，无法建立截面坐标系")
        normal = normal / norm
        side = direction - float(np.dot(direction, normal)) * normal
        length = float(np.linalg.norm(side))
        if length < MIN_AXIS_PROJECTION:
            raise ValueError(
                f"第 {i} 个截面（t≈{i}）上分型方向的投影长度仅 {length:.3f}，"
                "说明 direction 与截面法向几乎平行（方向必须落在截面平面内）"
            )
        side = side / length
        if prev_side is not None and float(np.dot(side, prev_side)) < 0.0:
            side = -side
        up = np.cross(normal, side)
        up_norm = float(np.linalg.norm(up))
        if up_norm < 1e-9:
            raise ValueError(f"第 {i} 个截面的 up 轴退化")
        up = up / up_norm
        if prev_up is not None and float(np.dot(up, prev_up)) < 0.0:
            side, up = -side, -up
        sec.normal = normal
        sec.side = side
        sec.up = up
        sec.axis_name = "DIR"
        prev_side, prev_up = side, up


# ---------------------------------------------------------------------------
# 3) 体检
# ---------------------------------------------------------------------------


def _polygon_area(points: np.ndarray, centroid: np.ndarray, side: np.ndarray, up: np.ndarray) -> float:
    local = np.column_stack([(points - centroid) @ side, (points - centroid) @ up])
    x, y = local[:, 0], local[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def prism_volume(sections: Sequence) -> float:
    """截面棱柱体积 ∫(A_i+A_{i+1})/2·d_i（ENTITY_PIPELINE §12.2 规定的体积基准）。"""
    if len(sections) < 2:
        return 0.0
    areas = [
        _polygon_area(np.asarray(s.points, dtype=float), np.asarray(s.center, dtype=float),
                      np.asarray(s.side, dtype=float), np.asarray(s.up, dtype=float))
        for s in sections
    ]
    centers = np.array([np.asarray(s.center, dtype=float) for s in sections])
    steps = np.linalg.norm(np.diff(centers, axis=0), axis=1)
    return float(np.sum(0.5 * (np.asarray(areas[:-1]) + np.asarray(areas[1:])) * steps))


def _shape_volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _parting_edge_count(solid, extent: float) -> int:
    """数纵向共享边（两个侧面之间的缝）。

    ``geometry._parting_edges`` 只返回排序后的前 2 条，这里为了体检做一次
    不截断的计数，判据与其完全一致：被 2 个面共享、长度 > 0.2×extent、
    自身跨度 > 0.5×extent。
    """
    from OCP.TopoDS import TopoDS

    G = _geometry()
    mapping = G.face_edge_map(solid)
    seen = []
    for i in range(1, mapping.Extent() + 1):
        if mapping.FindFromIndex(i).Extent() != 2:
            continue
        edge = TopoDS.Edge_s(mapping.FindKey(i))
        if G.edge_length(edge) < PARTING_LENGTH_RATIO * extent:
            continue
        if float(max(G.bbox_size(G.shape_bbox(edge)))) <= PARTING_SPAN_RATIO * extent:
            continue
        if any(edge.IsSame(other) for other in seen):
            continue
        seen.append(edge)
    return len(seen)


def _inspect(solid) -> dict:
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID

    G = _geometry()
    bbox = G.shape_bbox(solid)
    extent = float(max(G.bbox_size(bbox)))
    return {
        "faces": int(G.count_subshapes(solid, TopAbs_FACE)),
        "edges": int(G.count_subshapes(solid, TopAbs_EDGE)),
        "solids": int(G.count_subshapes(solid, TopAbs_SOLID)),
        "free_edges": int(G.free_edge_count(solid)),
        "valid": bool(BRepCheck_Analyzer(solid).IsValid()),
        "volume_mm3": _shape_volume(solid),
        "bbox": [float(v) for v in bbox],
        "parting_edges_measured": _parting_edge_count(solid, extent),
    }


def _bbox_dev(a: Sequence[float], b: Sequence[float]) -> float:
    """包围盒偏差 = 6 个分量差的最大绝对值（mm）。"""
    return float(np.max(np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float))))


# ---------------------------------------------------------------------------
# 4) STEP → 4 面实体
# ---------------------------------------------------------------------------


def build_from_step(
    step_path: str | Path,
    out_path: str | Path,
    *,
    part: str = "",
    key: str = "",
    op: str = "",
    direction: Any = None,
    log: Log | None = None,
    category: str | None = None,
    half_samples: int | None = None,
    max_sections: int | None = None,
) -> dict:
    """把一件模型 STEP 保形重建成 4 面实体并写出 STEP。

    Parameters
    ----------
    step_path / out_path:
        输入模型 STEP / 输出 STEP。
    part, key, op:
        调用方上下文（件号 / 模型键 / 工序），只用于日志与回报。
    direction:
        调用方算好的**显式**分型方向 ``[x, y, z]``（也接受 ``'X'``/``'Z'``）。
        会投影到每个截面平面得到 a 轴，±a 极值点就是 U=0 / 0.5 分型端点。
        ``None``/``'auto'`` 时退回 vendor 的对称轴检测（仅供独立测试）。
    log:
        进度回调 ``f(str)``。
    category:
        ``ENTITY_PIPELINE.md`` 旧签名的兼容别名；给 ``key`` 时以 ``key`` 为准。
    half_samples:
        每个半截面的采样点数，默认 65（= ``settings.HALF_SAMPLES``）。
    max_sections:
        截面数上限，默认 160；超出按等间距抽稀（保留首末）。

    Returns
    -------
    dict：``step / volume_mm3 / faces / edges / solids / free_edges / valid /
    bbox / parting_edges / direction / seconds / logs``，另附
    ``ref_volume_mm3 / volume_error_pct / ref_bbox / bbox_dev_mm /
    sections_used / sections_total / warnings / check``。

    Raises
    ------
    ValueError
        读不出几何、截面不足、分型方向退化、缝合失败等，消息可直接展示。
    """
    started = time.time()
    say = _logger(log)
    logs: list[str] = []

    def note(text: str) -> None:
        logs.append(text)
        say(text)

    part = part or Path(step_path).stem
    key = key or (category or "")
    op = op or ""
    if category and not key:
        key = category

    path = Path(step_path)
    out = Path(out_path)
    if not path.is_file():
        raise ValueError(f"输入 STEP 不存在：{path}")

    G = _geometry()
    A = _assemble()
    half = int(half_samples or DEFAULT_HALF_SAMPLES)
    limit = DEFAULT_MAX_SECTIONS if max_sections is None else int(max_sections)
    label = f"{part}/{key or '?'}"

    # --- 读 STEP -----------------------------------------------------------
    t_read = time.time()
    try:
        shape = G.read_step(path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"[{label}] 读取 STEP 失败（{path.name}）：{exc}") from exc
    seconds_read = time.time() - t_read
    ref_bbox = [float(v) for v in G.shape_bbox(shape)]
    note(f"[{label}] 读 STEP {path.name} {path.stat().st_size / 1e6:.1f} MB，{seconds_read:.2f}s")

    # --- 截面环 -----------------------------------------------------------
    t_sec = time.time()
    warnings: list[str] = []
    try:
        sections = G.extract_sections(shape, parting_axis="auto", warnings=warnings)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"[{label}] 无法提取闭合平面截面环（需要 ≥{G.MIN_SECTIONS} 个）：{exc}"
        ) from exc
    total_sections = len(sections)
    sections = _decimate(sections, limit)
    seconds_sections = time.time() - t_sec
    note(
        f"[{label}] 截面环 {total_sections} 个（已抽稀到 {len(sections)}，上限 {limit}），"
        f"{seconds_sections:.2f}s；检测对称轴 {sections[0].axis_name} {sections[0].symmetry_scores}"
    )
    if len(sections) < G.MIN_SECTIONS:
        raise ValueError(
            f"[{label}] 截面环不足（{len(sections)} < {G.MIN_SECTIONS}），无法重建 4 面实体"
        )

    # --- 显式分型方向 ------------------------------------------------------
    vec, direction_label = _resolve_direction(direction)
    try:
        _apply_direction(sections, vec)
    except ValueError as exc:
        raise ValueError(f"[{label}] {exc}") from exc
    if vec is not None:
        note(f"[{label}] 分型方向 {direction_label}（投影为每截面 a 轴，±a 极值点 = U=0/0.5）")

    # 体积基准：截面棱柱法（在最终使用的截面上算，和缝出来的实体同口径）
    ref_volume = prism_volume(sections)
    ref_volume_brep = _shape_volume(shape)
    note(
        f"[{label}] 体积基准（截面棱柱）{ref_volume:.1f} mm³；"
        f"源实体 BRepGProp {ref_volume_brep:.1f} mm³"
    )

    # --- 拟合 + 缝合 -------------------------------------------------------
    t_build = time.time()
    try:
        G.prepare_sections(sections, half)
        options = G.BuildOptions(half_samples=half, parting_axis="auto")
        result = A.build_from_closed_sections(sections, options)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"[{label}] 4 面实体重建失败：{exc}") from exc
    seconds_build = time.time() - t_build
    warnings.extend(result.warnings or [])
    note(
        f"[{label}] 曲面/缝合 {seconds_build:.2f}s（u×v 极点数 "
        f"{result.u_poles}×{result.v_poles}，缝合容差 {result.sew_tolerance:.2e}）"
    )

    # --- 写出 -------------------------------------------------------------
    t_write = time.time()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        G.write_step(result.solid, out)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"[{label}] STEP 写出失败（{out}）：{exc}") from exc
    seconds_write = time.time() - t_write
    note(f"[{label}] 写出 {out.name}，{seconds_write:.2f}s")

    # --- 体检 -------------------------------------------------------------
    t_check = time.time()
    try:
        info = _inspect(result.solid)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"[{label}] 结果体检失败：{exc}") from exc
    bbox_dev = _bbox_dev(info["bbox"], ref_bbox)
    volume_error = (
        100.0 * (info["volume_mm3"] - ref_volume) / ref_volume if ref_volume > 0 else float("nan")
    )
    parting = len(result.parting_edges)
    check = CheckRecord(
        faces=info["faces"],
        edges=info["edges"],
        solids=info["solids"],
        free_edges=info["free_edges"],
        valid=info["valid"],
        volume_mm3=info["volume_mm3"],
        bbox=info["bbox"],
        parting_edges=parting,
        extra={
            "parting_edges_measured": info["parting_edges_measured"],
            "parting_axis": result.parting_axis,
            "u_poles": result.u_poles,
            "v_poles": result.v_poles,
            "sew_tolerance": result.sew_tolerance,
            "ref_volume_prism_mm3": ref_volume,
            "ref_volume_brep_mm3": ref_volume_brep,
            "ref_bbox": ref_bbox,
            "sections_used": len(sections),
            "sections_total": total_sections,
            "direction": None if vec is None else [float(c) for c in vec],
            "direction_label": direction_label,
        },
    )
    seconds_check = time.time() - t_check
    if info["parting_edges_measured"] != 2:
        warnings.append(f"实测纵向分型边 {info['parting_edges_measured']} 条，期望 2 条")
    if info["faces"] != 4:
        warnings.append(f"面数 {info['faces']}，期望 4")
    if ref_volume_brep > 0 and abs(ref_volume - ref_volume_brep) / ref_volume_brep > 0.01:
        warnings.append(
            f"截面棱柱基准 {ref_volume:.0f} mm³ 与源实体 BRepGProp {ref_volume_brep:.0f} mm³ 相差 "
            f"{100.0 * (ref_volume - ref_volume_brep) / ref_volume_brep:+.2f}%（斜截面/端部区域"
            "按棱柱法会少算，体积口径按 ENTITY_PIPELINE §12 取棱柱）"
        )
    note(
        f"[{label}] 体检 {info['faces']} 面 / {info['solids']} 实体 / "
        f"{info['parting_edges_measured']} 分型边 / {info['free_edges']} 自由边 / "
        f"valid={info['valid']}；体积 {info['volume_mm3']:.1f} mm³（偏差 {volume_error:+.3f}%），"
        f"bbox 偏差 {bbox_dev:.3f} mm"
    )
    if warnings:
        note(f"[{label}] 警告：" + "；".join(warnings))

    seconds = time.time() - started
    return {
        "step": str(out),
        "part": part,
        "key": key,
        "op": op,
        "volume_mm3": info["volume_mm3"],
        "ref_volume_mm3": ref_volume,
        "ref_volume_brep_mm3": ref_volume_brep,
        "volume_error_pct": volume_error,
        "volume_error_vs_source_pct": (
            100.0 * (info["volume_mm3"] - ref_volume_brep) / ref_volume_brep
            if ref_volume_brep > 0
            else float("nan")
        ),
        "faces": info["faces"],
        "edges": info["edges"],
        "solids": info["solids"],
        "free_edges": info["free_edges"],
        "valid": info["valid"],
        "bbox": info["bbox"],
        "ref_bbox": ref_bbox,
        "bbox_dev_mm": bbox_dev,
        "parting_edges": parting,
        "parting_edges_measured": info["parting_edges_measured"],
        "direction": None if vec is None else [float(c) for c in vec],
        "direction_label": direction_label,
        "sections_used": len(sections),
        "sections_total": total_sections,
        "seconds": seconds,
        "seconds_read": seconds_read,
        "seconds_sections": seconds_sections,
        "seconds_build": seconds_build,
        "seconds_write": seconds_write,
        "seconds_check": seconds_check,
        "logs": logs,
        "warnings": warnings,
        "check": dataclasses.asdict(check),
    }


# ---------------------------------------------------------------------------
# 5) 路线入口（orchestrator 调用）
# ---------------------------------------------------------------------------


def _fallback_direction(rings: list[dict]) -> dict:
    """``directions.py`` 缺席时的兜底：三向镜像评分取最小（并列时 Z > Y > X）。

    只判断「分型方向必须是对称方向」这一条硬约束；截面长轴 / 弯曲平面这类
    类别规则属于 S5 的 ``directions.py``，这里不越界实现。
    """
    G = _geometry()
    if not rings:
        return {"vector": [0.0, 0.0, 1.0], "label": "Z+", "rule": "symmetry(fallback)"}
    cloud = np.vstack([np.asarray(r["points"], dtype=float)[::4] for r in rings])
    if len(cloud) > 4000:
        cloud = cloud[np.linspace(0, len(cloud) - 1, 4000).astype(int)]
    lo, hi = cloud.min(axis=0), cloud.max(axis=0)
    center = (lo + hi) / 2.0
    scores = {}
    for i, name in enumerate("XYZ"):
        reflected = cloud.copy()
        reflected[:, i] = 2.0 * center[i] - reflected[:, i]
        scores[name] = float(G._mean_nearest_distance(cloud, reflected))

    # 与长轴平行的轴不能当分型方向（分型线沿长轴走）。
    centers = np.array([r["centroid"] for r in rings], dtype=float)
    spine = np.zeros(3)
    if len(centers) >= 2:
        _, _, vh = np.linalg.svd(centers - centers.mean(axis=0), full_matrices=False)
        spine = vh[0]
    best = min(scores.values())
    tied = [n for n in ("Z", "Y", "X") if scores[n] <= best + 1e-3 * max(abs(best), 1e-9)]
    transverse = [n for n in tied if abs(float(np.dot(np.eye(3)["XYZ".index(n)], spine))) <= 0.92]
    axis = (transverse or tied)[0]
    vec = np.zeros(3)
    vec["XYZ".index(axis)] = 1.0
    return {
        "vector": [float(c) for c in vec],
        "label": axis + "+",
        "rule": "symmetry(fallback)",
        "scores": {k: round(v, 4) for k, v in scores.items()},
    }


def _usable_vector(vector: Any, rings: Sequence[dict], tol: float = MIN_AXIS_PROJECTION) -> str:
    """方向能不能当分型方向：必须落在每个截面平面内。可用返回空串，否则返回原因。"""
    try:
        arr = np.asarray(vector, dtype=float).reshape(-1)
    except Exception:  # noqa: BLE001
        return f"不是数值向量（{vector!r}）"
    if arr.size != 3 or not np.all(np.isfinite(arr)):
        return f"不是有限的 3 维向量（{vector!r}）"
    norm = float(np.linalg.norm(arr))
    if norm < 1e-9:
        return "是零向量"
    arr = arr / norm
    step = max(1, len(rings) // 12)
    worst, worst_index = 1.0, -1
    for i in range(0, len(rings), step):
        normal = np.asarray(rings[i]["normal"], dtype=float)
        n_norm = float(np.linalg.norm(normal))
        if n_norm < 1e-9:
            continue
        normal = normal / n_norm
        projection = float(np.linalg.norm(arr - float(np.dot(arr, normal)) * normal))
        if projection < worst:
            worst, worst_index = projection, i
    if worst < tol:
        return (
            f"在第 {worst_index} 个截面平面内的投影只有 {worst:.3f}"
            "（与截面法向几乎平行，分型方向必须落在截面平面内）"
        )
    return ""


def _direction_from_rules(rings: list[dict], categories: Sequence[str], log: Log, tag: str) -> dict:
    """用 ``directions.resolve_for_category`` 算分型方向；不可用就兜底。"""
    try:
        from .. import directions  # S5 的模块，可能尚未落地
    except Exception as exc:  # noqa: BLE001
        log(f"{tag} directions 不可用（{exc}），用三向镜像评分兜底")
        return _fallback_direction(rings)

    last_error: Exception | None = None
    for category in categories:
        if not category:
            continue
        try:
            resolved = directions.resolve_for_category(category, rings, "auto")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        if isinstance(resolved, dict):
            vector = resolved.get("vector")
            if vector is None:
                last_error = ValueError(f"resolve_for_category({category!r}) 没给 vector")
                continue
        elif resolved is not None:
            resolved = {"vector": list(resolved), "label": "auto", "rule": "symmetry"}
            vector = resolved["vector"]
        else:
            last_error = ValueError(f"resolve_for_category({category!r}) 返回 None")
            continue
        problem = _usable_vector(vector, rings)
        if problem:
            last_error = ValueError(f"{category} 给的方向不可用：{problem}")
            log(f"{tag} directions({category}) 的方向 {vector} 不可用：{problem}；换下一条规则")
            continue
        return resolved

    fallback = _fallback_direction(rings)
    problem = _usable_vector(fallback["vector"], rings)
    if problem:
        raise ValueError(
            f"没有可用的分型方向（directions 全部失败：{last_error}；"
            f"兜底 {fallback['label']} 也{problem}）"
        )
    log(f"{tag} directions 全部不可用（{last_error}），改用兜底方向 {fallback['label']}")
    return fallback
    log(f"{tag} directions 解析失败（{last_error}），用三向镜像评分兜底")
    return _fallback_direction(rings)


def _max_sections(ctx: PipelineContext) -> int:
    try:
        value = (getattr(ctx, "options", None) or {}).get("max_sections")
        return int(value) if value else DEFAULT_MAX_SECTIONS
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_SECTIONS


def _inspect_with_checks(step_path: str, log: Log, tag: str):
    """S6 的 ``checks.inspect_step`` 若已落地就用它复核；否则返回 None。"""
    try:
        from .. import checks
    except Exception as exc:  # noqa: BLE001
        log(f"{tag} checks 不可用（{exc}），用自测值")
        return None
    try:
        return checks.inspect_step(step_path)
    except Exception as exc:  # noqa: BLE001
        log(f"{tag} checks.inspect_step 失败（{exc}），用自测值")
        return None


def _merge_check(result: RouteResult, rec, info: dict) -> None:
    def pick(field: str):
        value = getattr(rec, field, None)
        return info.get(field) if value is None else value

    for field in ("faces", "edges", "solids", "free_edges", "valid", "parting_edges"):
        setattr(result, field, pick(field))
    volume = pick("volume_mm3")
    bbox = pick("bbox")
    result.volume_mm3 = volume
    result.bbox = bbox
    ref_volume = info.get("ref_volume_mm3") or 0.0
    result.volume_error_pct = (
        100.0 * (volume - ref_volume) / ref_volume if ref_volume else info.get("volume_error_pct")
    )
    if bbox and info.get("ref_bbox"):
        result.bbox_dev_mm = _bbox_dev(bbox, info["ref_bbox"])
    result.ref_bbox = info.get("ref_bbox")


def available() -> bool:
    try:
        import OCP  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def run(spec: ModelSpec, ctx: PipelineContext) -> RouteResult:
    """c1 路的 selfref 分支：``reference_step or step`` → ``10_c1/<key>__c1.step``。

    这是 c1 路「保形」的统一实现：orchestrator 对 ``c1_kind == "selfref"`` 的模型
    与 2052-2 的外径形腔都直接调它；``c1.py`` 在 ``uses_reference_route`` 时转调。
    """
    log = _logger(getattr(ctx, "log", None))
    result = RouteResult(part=spec.part, key=spec.key, route="c1")
    started = time.time()
    tag = f"[{spec.part}/{spec.key}]"
    try:
        defn = model_def(spec.key)
    except Exception as exc:  # noqa: BLE001
        defn = None
        log(f"{tag} 模型键 {spec.key!r} 不在 MODELS 里（{exc}）")
    op = getattr(defn, "op", "") if defn is not None else ""

    try:
        if defn is None:
            raise ValueError(f"模型键 {spec.key!r} 不在 MODELS 契约里，selfref 无从选规则")
        step = spec.reference_step or spec.step
        out_path = spec.out_path("c1")
        result.rule = "selfref"

        # 手动分型方向（路由偏好 / CLI --direction）优先：给了就跳过自动规则。
        # 121 保形与 2052 走同一条 _apply_direction 路径（投影为每截面 a 轴）。
        direction = None
        manual = getattr(spec, "direction", None)
        if manual is not None and str(manual).strip().lower() not in ("", "auto", "none"):
            try:
                direction, manual_label = _resolve_direction(manual)
                result.direction_label = manual_label
                result.rule = "selfref/manual"
                log(f"{tag} 分型方向 {manual_label}（手动指定，rule=manual）")
            except Exception as exc:  # noqa: BLE001
                log(f"{tag} 手动分型方向 {manual!r} 不可用（{exc}），改用自动规则")
                direction = None
        if direction is None:
            try:
                rings = sections_from_step(step)
                resolved = _direction_from_rules(rings, (op, spec.key), log, tag)
                direction = resolved.get("vector")
                result.direction_label = resolved.get("label")
                result.rule = f"selfref/{resolved.get('rule', '?')}"
                log(f"{tag} 分型方向 {result.direction_label}（rule={resolved.get('rule')}）")
            except Exception as exc:  # noqa: BLE001
                log(f"{tag} 分型方向解析失败（{exc}），交给 vendor 自动对称轴")

        info = build_from_step(
            step,
            out_path,
            part=spec.part,
            key=spec.key,
            op=op,
            direction=direction,
            log=log,
            max_sections=_max_sections(ctx),
        )
        result.status = "ok"
        result.step = info["step"]
        result.artifacts = {"step": info["step"]}
        result.ref_volume_mm3 = info["ref_volume_mm3"]
        chosen = info["direction"] if direction is None else direction
        if chosen is not None:
            result.direction = [float(v) for v in np.asarray(chosen, dtype=float).reshape(-1)]
        result.direction_label = info["direction_label"] or result.direction_label
        result.sections_used = info["sections_used"]
        result.sections_total = info["sections_total"]
        result.logs = list(info["logs"])
        result.problems = list(info["warnings"])
        _merge_check(result, _inspect_with_checks(info["step"], log, tag), info)
        if not result.valid:
            result.problems.append("实体未通过 OCC 有效性检查")
    except Exception as exc:  # noqa: BLE001 - 路线入口绝不外抛
        result.status = "error"
        result.error = str(exc) if isinstance(exc, ValueError) else f"selfref 未预期失败：{exc}"
        result.logs.append(result.error)
        log(f"{tag} selfref 失败：{result.error}")
    finally:
        result.seconds = time.time() - started
    return result
