"""C1 外推路线：把 ``<件>_measured.json`` 的实测站位重建成 4 面实体。

口径（与 ``vendor/c2/c2_routes.build_c1`` 逐项一致，两个金标准就是按它复现的）::

    sections_full
      → 2 mm 站位重采样（cumulative_length 相差 >0.1 mm 去重，中心跳变 ≥0.5 mm
        才算新站，末站按规则补）                  # giant_sections.load_giant_sections
      → 两端外推（giant_ends.smooth_extrapolated_ends，smooth_passes=settings.SMOOTH_PASSES，
        keep_all_extrapolated=False → 每端只留最外层外推站）
      → prepare_sections(half_samples=settings.HALF_SAMPLES)（按 ±side 取极点、按弧长重采）
      → assemble.build_from_closed_sections → 1 Solid / 4 faces / 2 条共享分型边 / 0 自由边

轮廓：``成型_外径形腔`` 用 ``outline_3d`` / ``outer_2d`` / ``perimeter``。
（早期版本的 ``成型_实心内径`` 已按需求下线，本模块只处理外轮廓；Giant 主程序的
「成型 → 实心内径」业务功能不受影响。）

分型方向（A6）：**分型缝按「方向 ±a 极点」落位**，与 selfref 同口径 ——
``routes.selfref._apply_direction(sections, vec)`` 把方向投影进每个截面平面当 a 轴
（``Section.side``），再 ``geometry.prepare_sections(sections, half_samples)``，
±a 极值点即 U=0 / U=0.5 的缝端点。

* 手动 ``spec.direction``（``"X"``/``"Y"``/``"Z"``，也吃 ``"-Y"``/``[x,y,z]``）：
  **以手动为准** —— 即使与实测截面帧不一致也重排 a 轴，并在 ``notes`` /
  ``artifacts.direction_override`` 里说明；
* ``"auto"``（缺省）：沿用 ``directions.resolve_for_category`` 的规则方向；规则方向与
  实测帧冲突时按既有策略**回落实测帧**（不掰几何，保证 1 mm³ 级 c1 金标准）；
* 拿不到方向（``None``）时跳过分型准备、记 ``problems``，保持旧的实测点序分型。

``vendor.giant_sections.load_giant_sections`` 其实已经用实测帧 ``Section.side``
（= measured.json 的 ``basis_u``）跑过一次 ``prepare_sections``；所以「目标方向与实测帧
同轴」时重跑**没有收益**，还会把 ``giant_ends.smooth_extrapolated_ends`` 改写过的端部
形貌覆盖回原始平移轮廓（它只改 ``top_pts``/``points``，不改 ``edge``），破坏金标准体积。
因此只有目标方向真的换轴时才重排 + 重跑 prepare，实际动作记在
``artifacts.seam_anchor`` / ``artifacts.prepared`` / ``artifacts.parting_prepare.action``。

参考路线：``contracts.uses_reference_route(part, key)`` 为真时（本流水线里是
2052-2 的 ``成型_外径形腔``）转调 :mod:`core.entity.routes.selfref`，用本件刚导出的
形腔 STEP（``spec.reference_step or spec.step``）做保形重建；本模块**不**产出该件的
C1 外推件。

契约：``run(spec: ModelSpec, ctx: PipelineContext) -> RouteResult``，不抛异常
（失败 ``status="error"`` 且 ``error`` 带原因）；``available() -> bool``。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

try:  # 包内正常用法（cwd = <repo>/backend）
    from .. import contracts, settings
    from ..contracts import ModelSpec, PipelineContext, RouteResult
except Exception:  # pragma: no cover - 直接按文件跑的兜底
    from core.entity import contracts, settings  # type: ignore
    from core.entity.contracts import ModelSpec, PipelineContext, RouteResult  # type: ignore

__all__ = ["ROUTE", "CONTOURS", "run", "available"]

ROUTE = "c1"

STATUS_OK = "ok"
STATUS_ERROR = "error"

#: 轮廓口径 → measured.json 的数据键（+ 日志/报告用的中文名）
CONTOURS: dict[str, dict[str, str]] = {
    "outer": {
        "label": "外径形腔",
        "outline3d": "outline_3d",
        "contour2d": "outer_2d",
        "perimeter": "perimeter",
        "loader": "vendor.giant_sections.load_giant_sections",
    },
}

#: RouteResult 里需要从 selfref 结果搬过来的字段（参考路线用）
_RESULT_FIELDS = (
    "status",
    "step",
    "stl",
    "volume_mm3",
    "ref_volume_mm3",
    "volume_error_pct",
    "faces",
    "edges",
    "solids",
    "free_edges",
    "parting_edges",
    "valid",
    "bbox",
    "ref_bbox",
    "bbox_dev_mm",
    "direction",
    "direction_label",
    "rule",
    "sections_used",
    "sections_total",
    "attempts",
    "logs",
    "error",
    "artifacts",
    "problems",
)


# ------------------------------------------------------------------ 公开入口


def available() -> bool:
    """放样依赖 OCP；导入不了就别派活给这条路线。"""
    try:
        import OCP  # noqa: F401
        from ..vendor.pipe_smoother.geometry import BuildOptions  # noqa: F401

        return True
    except Exception:
        return False


def run(spec: ModelSpec, ctx: PipelineContext) -> RouteResult:
    """C1 外推：measured.json → ``spec.out_path("c1")`` 的 4 面实体。"""
    started = time.perf_counter()
    sink = _logger(ctx)
    logs: list[str] = []

    def log(message: Any) -> None:
        text = str(message)
        logs.append(text)
        try:
            sink(text)
        except Exception:
            pass

    result = RouteResult(
        part=str(getattr(spec, "part", "")),
        key=str(getattr(spec, "key", "")),
        route=ROUTE,
    )
    result.route = ROUTE
    result.logs = logs
    try:
        _execute(result, spec, ctx, log, started)
    except KeyboardInterrupt:  # pragma: no cover - 让 Ctrl+C 正常上抛
        raise
    except Exception as exc:  # noqa: BLE001 - 契约要求不抛，收成 status="error"
        _fail(result, exc, log)
    finally:
        result.seconds = round(time.perf_counter() - started, 3)
    return result


# ------------------------------------------------------------------ 主流程


def _execute(
    result: RouteResult,
    spec: ModelSpec,
    ctx: PipelineContext,
    log: Callable[[Any], None],
    started: float,
) -> None:
    # 0) 参考路线（2052-2 的成型_外径形腔）：转调 selfref，本模块不外推
    if _uses_reference_route(spec):
        _run_reference(result, spec, ctx, log)
        return

    options = _options()
    contour = _contour(spec)
    keys = CONTOURS[contour]
    defn = _defn(spec)
    category = str(getattr(defn, "op", "") or "") or getattr(spec, "key", "")

    measured = _measured_path(spec, ctx)
    if not measured:
        raise FileNotFoundError(
            "拿不到该件的 measured.json：spec.measured_path 为空，且按 out_dir / ctx.measured 也推不出缓存路径"
        )
    if not os.path.isfile(measured):
        raise FileNotFoundError(f"measured.json 不存在：{measured}")
    log(f"  [c1] {spec.part} / {spec.key}：{keys['label']} ← {os.path.basename(measured)}")

    load_started = time.perf_counter()
    sections, info = _load_sections(measured, options, log)
    load_seconds = time.perf_counter() - load_started
    if len(sections) < 4:
        raise ValueError(
            f"可用站位只有 {len(sections)} 站（< MIN_SECTIONS=4），无法放样；"
            f"源站 {info.get('raw')}，外推标记 {info.get('extrapolated')} 站"
        )
    log(
        f"  [c1] {spec.part} / {spec.key}：{len(sections)} 站放样"
        f"（源 {info.get('raw') if info.get('raw') is not None else '?'} 站）"
    )

    # 分型方向：手动 spec.direction 优先；auto 用规则方向（冲突仍回落实测帧）
    direction = _resolve_direction(category, sections, log)
    parting = _prepare_parting(sections, spec, direction, options, log)
    log(
        f"  [c1] {spec.part} / {spec.key}：分型方向 {direction.get('label') or '?'}"
        f"（{'对称轴极点' if parting['seam_anchor'] == 'symmetry_poles' else '实测点序，未落在对称轴极点'}）"
    )

    BuildOptions, _, _, write_step = _geometry()
    build_from_closed_sections = _assemble()
    build_started = time.perf_counter()
    built = build_from_closed_sections(
        sections, BuildOptions(half_samples=options["half_samples"])
    )
    build_seconds = time.perf_counter() - build_started

    out_path = str(spec.out_path(ROUTE))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    write_step(built.solid, out_path)
    log(f"  [c1] {spec.part} / {spec.key}：STEP → {out_path}")

    result.step = out_path
    result.sections_used = len(sections)
    result.sections_total = int(info.get("raw") or _cached_station_count(ctx, spec) or len(sections))
    result.ref_volume_mm3 = round(float(_prism_volume(sections)), 3)
    result.ref_bbox = _bbox_of_points(sections)
    result.direction = direction["label"]
    result.direction_label = direction["pair"]
    result.rule = direction["rule"]
    result.artifacts = {
        "route": ROUTE,
        "contour": contour,
        "contour_label": keys["label"],
        "end_treatment": getattr(spec, "end_treatment", None),
        "measured": measured,
        "measured_bytes": _file_size(measured),
        "loader": info.get("loader") or keys["loader"],
        "stations": len(sections),
        "stations_raw": info.get("raw"),
        "stations_extrapolated": info.get("extrapolated"),
        "half_samples": options["half_samples"],
        "smooth_passes": options["smooth_passes"],
        "station_step_mm": options["step_mm"],
        "keep_all_extrapolated": False,
        "u_poles": getattr(built, "u_poles", None),
        "v_poles": getattr(built, "v_poles", None),
        "sew_tolerance": getattr(built, "sew_tolerance", None),
        "seam_max_angle_deg": getattr(built, "seam_max_angle_deg", None),
        "warnings": list(getattr(built, "warnings", None) or []),
        "direction": direction,
        "seam_anchor": parting["seam_anchor"],
        "direction_override": parting["override"],
        "prepared": bool(parting["prepared"]),
        "parting_prepare": {
            "action": parting["action"],
            "applier": parting["applier"],
        },
        "ref_volume_mm3": result.ref_volume_mm3,
        "bytes": _file_size(out_path),
        "sha256": _sha256_file(out_path),
        "seconds": {
            "load": round(load_seconds, 3),
            "build": round(build_seconds, 3),
            "total": round(time.perf_counter() - started, 3),
        },
    }

    # 体检（checks 缺失/失败只记 problems）+ 四条硬指标
    problems: list[str] = list(getattr(built, "warnings", None) or [])
    problems.extend(direction["notes"])
    problems.extend(parting["notes"])
    _apply_checks(result, out_path, log)
    problems.extend(_conformance(result))
    result.problems = list(dict.fromkeys(problems))
    result.status = STATUS_OK
    log(
        f"  [c1] {spec.part} / {spec.key}：体积 {result.volume_mm3} mm³"
        f"  面={result.faces} 实体={result.solids} 分型边={result.parting_edges}"
        f" 自由边={result.free_edges} valid={result.valid}"
    )


# ------------------------------------------------------------------ 参考路线


def _uses_reference_route(spec: ModelSpec) -> bool:
    """契约判据：2052-2 的成型_外径形腔走「形腔 STEP → 保形」而不是 C1 外推。"""
    return bool(contracts.uses_reference_route(spec.part, spec.key))


def _run_reference(
    result: RouteResult,
    spec: ModelSpec,
    ctx: PipelineContext,
    log: Callable[[Any], None],
) -> None:
    """转调 :mod:`core.entity.routes.selfref`；缺模块/缺输入都抛成清晰错误。"""
    try:
        from . import selfref
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"参考路线需要 routes/selfref.py（STEP 自参考保形），导入失败：{type(exc).__name__}: {exc}"
        ) from exc

    target = getattr(spec, "reference_step", None) or getattr(spec, "step", None)
    if not target:
        raise FileNotFoundError("参考路线缺少输入 STEP：spec.reference_step 与 spec.step 都为空")
    target = str(target)
    if not os.path.isfile(target):
        raise FileNotFoundError(f"参考路线的形腔 STEP 不存在：{target}")
    log(f"  [c1→selfref] {spec.part} / {spec.key}：保形重建 ← {os.path.basename(target)}")

    runner = getattr(selfref, "run", None)
    if callable(runner):
        produced = runner(spec, ctx)
        if isinstance(produced, RouteResult):
            for name in _RESULT_FIELDS:
                setattr(result, name, getattr(produced, name, None))
            result.route = ROUTE
            result.part = spec.part
            result.key = spec.key
            artifacts = dict(getattr(result, "artifacts", None) or {})
            artifacts["delegated_to"] = "routes.selfref.run"
            artifacts["reference_step"] = target
            result.artifacts = artifacts
            result.logs = list(getattr(result, "logs", None) or [])
            if result.status in (None, "", "pending"):
                result.status = STATUS_OK if result.error is None else STATUS_ERROR
            return
        log(f"  ! selfref.run 返回 {type(produced).__name__}（不是 RouteResult），改用 build_from_step")

    builder = getattr(selfref, "build_from_step", None)
    if not callable(builder):
        raise AttributeError("routes/selfref.py 既没有 run() 也没有 build_from_step()，参考路线无法执行")
    out_path = str(spec.out_path(ROUTE))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    payload = builder(
        target,
        out_path,
        part=spec.part,
        key=spec.key,
        category=spec.key,
        log=log,
    )
    _fill_from_payload(result, payload, out_path, log)


def _fill_from_payload(
    result: RouteResult,
    payload: Any,
    out_path: str,
    log: Callable[[Any], None],
) -> None:
    """selfref.build_from_step 的 dict → RouteResult（参考路线的兜底路径）。"""
    data = dict(payload or {})
    data.setdefault("step", out_path)
    for name in _RESULT_FIELDS:
        if name in ("status", "step"):
            continue
        value = data.get(name)
        if value is not None:
            setattr(result, name, value)
    result.step = str(data.get("step") or out_path)
    result.route = ROUTE
    artifacts = dict(data.get("artifacts") or {})
    artifacts.setdefault("delegated_to", "routes.selfref.build_from_step")
    artifacts.setdefault("reference_step", data.get("reference") or None)
    result.artifacts = artifacts
    result.status = str(data.get("status") or STATUS_OK)
    result.logs = list(data.get("logs") or [])
    if result.faces is None:
        _apply_checks(result, out_path, log)
    result.problems = list(result.problems or []) + list(data.get("warnings") or [])


# ------------------------------------------------------------------ 数据加载


def _load_sections(
    measured: str,
    options: dict,
    log: Callable[[Any], None],
) -> tuple[list, dict]:
    """外轮廓加载（``vendor.giant_sections.load_giant_sections``）。"""
    sections = _giant().load_giant_sections(
        str(measured),
        step_mm=options["step_mm"],
        half_samples=options["half_samples"],
        smooth_passes=options["smooth_passes"],
        keep_extrapolated=True,
        keep_all_extrapolated=False,
    )
    sections = list(sections)
    return sections, {
        "loader": CONTOURS["outer"]["loader"],
        "raw": None,
        "extrapolated": None,
        "stations": len(sections),
    }


# ------------------------------------------------------------------ 报告字段


def _resolve_direction(
    category: str,
    sections: list,
    log: Callable[[Any], None],
) -> dict:
    """规则方向（auto 用）+ 与实测截面帧的一致性校验。

    返回的方向只决定「分型缝应该落在哪条对称轴上」：``auto`` 与实测帧冲突时这里
    会回落到实测帧（几何不掰），手动 ``spec.direction`` 的覆盖在
    :func:`_prepare_parting` 里做。
    """
    frame = np.asarray(sections[0].side, dtype=float) if sections else None
    frame_axis = _axis_of(frame)
    info: dict[str, Any] = {
        "vector": None,
        "label": None,
        "pair": None,
        "rule": None,
        "scores": None,
        "frame": None if frame is None else [round(float(c), 6) for c in frame],
        "frame_axis": frame_axis,
        "conflict": False,
        "notes": [],
    }

    rings = _rings_for_direction(sections)
    vector: Optional[list] = None
    label: Optional[str] = None
    rule: Optional[str] = None
    rules_error: Optional[str] = None

    try:
        from .. import directions
    except Exception as exc:  # noqa: BLE001 - S5 可能还没落地
        directions = None  # type: ignore
        rules_error = f"{type(exc).__name__}: {exc}"

    if directions is not None:
        try:
            scores = directions.score_axes(rings)
            info["scores"] = {str(k): round(float(v), 6) for k, v in dict(scores).items()}
        except Exception as exc:  # noqa: BLE001
            info["notes"].append(f"directions.score_axes 失败：{type(exc).__name__}: {exc}")
        try:
            resolved = directions.resolve_for_category(category, rings, "auto")
            value = resolved.get("vector")
            vector = None if value is None else [float(c) for c in value]
            label = resolved.get("label")
            rule = resolved.get("rule") or "symmetry"
        except Exception as exc:  # noqa: BLE001 - 方向不确定就不猜
            rules_error = f"{type(exc).__name__}: {exc}"

    if vector is None:
        if rules_error:
            info["notes"].append(f"方向规则不可用（{rules_error}），回落实测截面帧")
        vector = None if frame is None else [float(c) for c in frame]
        label = frame_axis
        rule = "measured_frame"
    elif frame is not None and not _same_axis(vector, frame):
        if rule == "bend_plane":
            # 平面弯：规则方向就是弯曲平面法向，必须真的换轴（不能留实测帧），
            # 否则分型线会偏离弯曲平面（H0A3Z2 世界 X 与真实法向差 27°）。
            info["notes"].append(
                f"自动弯曲平面法向 {label or _axis_of(vector)} 与实测截面帧 {frame_axis} 不同轴："
                "按弯曲平面重排截面 a 轴（体积会相对实测帧金标准小幅变化）"
            )
            log(
                f"  [c1] 分型方向取弯曲平面法向 {label}（与实测帧 {frame_axis} 不同轴，重排 a 轴）"
            )
        else:
            info["conflict"] = True
            info["notes"].append(
                f"规则方向 {label or _axis_of(vector)} 与实测截面帧 {frame_axis} 不一致："
                "几何以实测帧为准（掰方向会移动分型点、体积必然偏离金标准）"
            )
            vector = [float(c) for c in frame]
            label = frame_axis
            rule = f"{rule}→measured_frame"

    axis = label if label in ("X", "Y", "Z") else _axis_of(vector)
    info["vector"] = vector
    info["label"] = axis
    info["rule"] = rule
    info["pair"] = f"{axis}+/{axis}-" if axis in ("X", "Y", "Z") else None
    return info


def _rings_for_direction(sections: list, limit: int = 24) -> list:
    """抽稀成 directions 需要的 ring 描述（点 / 质心 / 法向）。"""
    if not sections:
        return []
    stride = max(1, len(sections) // max(1, limit))
    rings: list[dict] = []
    for section in sections[::stride]:
        points = np.asarray(section.points, dtype=float)
        if len(points) < 8:
            continue
        rings.append(
            {
                "points": points,
                "centroid": np.asarray(section.center, dtype=float),
                "normal": np.asarray(section.normal, dtype=float),
            }
        )
    return rings


def _axis_of(vector: Any, tol: float = 1e-3) -> Optional[str]:
    unit = _unit(vector)
    if unit is None:
        return None
    for name, index in (("X", 0), ("Y", 1), ("Z", 2)):
        if abs(abs(float(unit[index])) - 1.0) <= tol:
            return name
    return "custom"


def _same_axis(left: Any, right: Any) -> bool:
    a, b = _unit(left), _unit(right)
    if a is None or b is None:
        return False
    return abs(float(np.dot(a, b))) >= 1.0 - 1e-3


def _unit(vector: Any) -> Optional[np.ndarray]:
    if vector is None:
        return None
    array = np.asarray(vector, dtype=float).reshape(-1)
    if array.size != 3 or not np.all(np.isfinite(array)):
        return None
    norm = float(np.linalg.norm(array))
    if norm < 1e-12:
        return None
    return array / norm


# ------------------------------------------------------------------ 分型极点准备


#: 截面 a 轴与目标方向的点积阈值：≥ 该值即视为「缝已落在该方向的对称轴极点」
_ALIGNED_DOT = 1.0 - 1e-6

#: 方向在截面平面内的最短投影长度（与 selfref.MIN_AXIS_PROJECTION 同值）
_MIN_AXIS_PROJECTION = 0.15

#: 重跑 prepare_sections 前要快照、失败后要回滚的坐标系 / 极点字段
_FRAME_FIELDS = (
    "normal",
    "side",
    "up",
    "axis_name",
    "symmetry_scores",
    "t_right",
    "t_left",
    "right",
    "left",
    "tangent_right",
    "tangent_left",
    "top_pts",
    "bottom_pts",
)


def _prepare_parting(
    sections: list,
    spec: ModelSpec,
    direction: dict,
    options: dict,
    log: Callable[[Any], None],
) -> dict:
    """把分型缝挪到「方向 ±a 极点」（与 selfref 同口径）。

    ``load_giant_sections`` 已经用实测帧 ``Section.side``（= ``basis_u``）跑过一次
    ``prepare_sections``，缝落在 ±实测帧上。本函数只在与目标方向**不同轴**时才重排
    a 轴并重跑 prepare：

    * 重跑会把 ``giant_ends.smooth_extrapolated_ends`` 改写过的端部形貌覆盖回原始
      平移轮廓（它只改 ``top_pts``/``points``、不改 ``edge``），体积随之偏离 1 mm³
      级 c1 金标准；同轴时重跑零收益，故跳过；
    * ``auto`` 的规则方向与实测帧冲突时按既有策略回落实测帧，因此 auto 永远不会
      换轴（金标准保持 bit 级一致），只有手动 ``spec.direction`` 才真正重排 a 轴；
    * 换轴失败（方向几乎平行于截面法向等）时用快照回滚，保持旧的实测点序分型。

    返回 ``{"prepared", "seam_anchor", "override", "action", "applier", "notes"}``，
    并回填 ``direction`` 的 ``label / pair / rule / vector / source`` 供报告使用。
    """
    notes: list[str] = []
    manual, manual_label, manual_note = _manual_direction(spec)
    if manual_note:
        notes.append(manual_note)

    rule_auto = str(direction.get("rule") or "")
    frame_axis = direction.get("frame_axis")
    override: Optional[str] = None
    vector: Optional[np.ndarray] = None
    axis: Optional[str] = direction.get("label")
    source = "auto"

    if manual is not None:
        vector = _unit(manual)
        axis = _axis_of(manual) or "custom"
        source = "manual"
        override = axis
        if not _same_axis(manual, direction.get("frame")):
            direction["conflict"] = False
            note = (
                f"手动分型方向 {manual_label or axis} 与实测截面帧 {frame_axis} 不一致："
                f"以手动为准，重排截面 a 轴使分型缝落在 ±{axis} 极点"
            )
            notes.append(note)
            direction["notes"].append(note)
            log(f"  [c1] {spec.part} / {spec.key}：{note}")
    else:
        vector = _unit(direction.get("vector"))

    direction["vector"] = None if vector is None else [round(float(c), 6) for c in vector]
    direction["label"] = axis
    direction["pair"] = f"{axis}+/{axis}-" if axis in ("X", "Y", "Z") else None
    direction["rule"] = "manual" if source == "manual" else (rule_auto or None)
    direction["source"] = source
    direction["seam_anchor"] = "raw"
    direction["prepared"] = "skipped"

    parting: dict[str, Any] = {
        "prepared": False,
        "seam_anchor": "raw",
        "override": override,
        "action": "skipped",
        "applier": None,
        "notes": notes,
    }

    if vector is None:
        note = (
            "拿不到可用的分型方向（规则方向与实测帧都缺失）：跳过分型准备，"
            "保持实测点序分型（旧行为）"
        )
        notes.append(note)
        direction["notes"].append(note)
        return parting

    if source != "manual" and "measured_frame" in rule_auto:
        # auto 冲突 → 已回落实测帧：几何一律不动，只报问题
        parting["action"] = "measured_frame"
        return parting

    if _aligned(sections, vector):
        parting.update({"prepared": True, "seam_anchor": "symmetry_poles", "action": "aligned"})
        direction["seam_anchor"] = "symmetry_poles"
        direction["prepared"] = "aligned"
        return parting

    prepare_sections = _geometry()[2]
    snapshot = [_frame_snapshot(section) for section in sections]
    try:
        applier = _apply_seam_direction(sections, vector)
        prepare_sections(sections, int(options["half_samples"]))
    except Exception as exc:  # noqa: BLE001 - 失败就退回实测帧，不能让 c1 崩
        _restore_frames(sections, snapshot)
        note = (
            f"分型准备失败（{type(exc).__name__}: {exc}）：已还原实测帧，"
            "保持实测点序分型"
        )
        notes.append(note)
        direction["notes"].append(note)
        log(f"  [c1] {spec.part} / {spec.key}：{note}")
        return parting

    parting.update(
        {
            "prepared": True,
            "seam_anchor": "symmetry_poles",
            "action": "reapplied",
            "applier": applier,
        }
    )
    direction["seam_anchor"] = "symmetry_poles"
    direction["prepared"] = "reapplied"
    log(
        f"  [c1] {spec.part} / {spec.key}：按分型方向 {axis} 重排截面 a 轴并重算 ±极点"
        f"（{applier}）"
    )
    return parting


def _manual_direction(
    spec: ModelSpec,
) -> tuple[Optional[np.ndarray], Optional[str], Optional[str]]:
    """``spec.direction`` → ``(单位向量 | None, 标签, 备注)``；向量 None 表示 auto。

    解析优先复用 :func:`routes.selfref._resolve_direction`（``"auto"``/``None`` →
    auto，``"X"``/``"-Y"``/``[x,y,z]`` → 单位向量 + 标签），不可用时退化为本文件
    的极简轴解析。
    """
    value = getattr(spec, "direction", "auto")
    if value is None:
        return None, None, None
    if not isinstance(value, str):
        unit = _unit(value)
        if unit is None:
            return None, None, f"spec.direction={value!r} 不是合法方向向量，按 auto 处理"
        return unit, _axis_of(unit) or "custom", None

    text = value.strip()
    if not text or text.lower() == "auto":
        return None, None, None

    parsed: Any = None
    label: Optional[str] = None
    try:
        from . import selfref  # 与参考路线共用同一套方向解析

        parsed, label = selfref._resolve_direction(value)
    except Exception:  # noqa: BLE001 - selfref 未落地时用极简解析
        parsed, label = None, None

    if parsed is None:
        body = text.replace(" ", "")
        sign = 1.0
        if body[:1] in ("-", "−", "+"):
            sign = -1.0 if body[0] in ("-", "−") else 1.0
            body = body[1:]
        head = body[:1].upper()
        tail = body[1:]
        if head in ("X", "Y", "Z") and tail in ("", "+", "-", "−", "+1", "-1", "−1"):
            if tail in ("-", "−", "-1", "−1"):
                sign = -sign
            unit = np.zeros(3)
            unit["XYZ".index(head)] = sign
            parsed, label = unit, f"{head}{'+' if sign > 0 else '-'}"

    unit = _unit(parsed)
    if unit is None:
        return None, None, f"手动分型方向 {value!r} 无法解析，按 auto 处理"
    return unit, label or (_axis_of(unit) or "custom"), None


def _aligned(sections: list, vector: np.ndarray, tol: float = _ALIGNED_DOT) -> bool:
    """目标方向是否已经就是每个截面的 a 轴（±vector 极点 ⇔ ±a 极点）。"""
    for section in sections:
        normal = _unit(getattr(section, "normal", None))
        side = _unit(getattr(section, "side", None))
        if normal is None or side is None:
            return False
        projected = vector - float(np.dot(vector, normal)) * normal
        unit = _unit(projected)
        if unit is None:
            return False
        if abs(float(np.dot(unit, side))) < tol:
            return False
    return True


def _apply_seam_direction(sections: list, vector: np.ndarray) -> str:
    """把分型方向投影成每个截面的 a 轴；返回实际使用的实现名（便于报告）。"""
    handler = None
    try:
        from . import selfref

        handler = getattr(selfref, "_apply_direction", None)
    except Exception:  # noqa: BLE001 - 相对 import 失败就用自己的等价实现
        handler = None
    if callable(handler):
        handler(sections, vector)
        return "routes.selfref._apply_direction"
    _apply_direction_local(sections, vector)
    return "c1._apply_direction_local"


def _apply_direction_local(sections: list, vector: np.ndarray) -> None:
    """``selfref._apply_direction`` 的等价实现（兜底用）。

    投影方向到每个截面平面得到 a 轴（``side``），符号沿截面链连续；``up`` 取
    ``cross(normal, side)``。之后 ``prepare_sections`` 的 ±a 极值点即 U=0/0.5。
    """
    previous_side: Optional[np.ndarray] = None
    previous_up: Optional[np.ndarray] = None
    for index, section in enumerate(sections):
        normal = _unit(getattr(section, "normal", None))
        if normal is None:
            raise ValueError(f"第 {index} 个截面的法向退化，无法建立截面坐标系")
        side = vector - float(np.dot(vector, normal)) * normal
        length = float(np.linalg.norm(side))
        if length < _MIN_AXIS_PROJECTION:
            raise ValueError(
                f"第 {index} 个截面上分型方向的投影长度仅 {length:.3f}，"
                "说明方向与截面法向几乎平行（分型方向必须落在截面平面内）"
            )
        side = side / length
        if previous_side is not None and float(np.dot(side, previous_side)) < 0.0:
            side = -side
        up = _unit(np.cross(normal, side))
        if up is None:
            raise ValueError(f"第 {index} 个截面的 up 轴退化")
        if previous_up is not None and float(np.dot(up, previous_up)) < 0.0:
            side, up = -side, -up
        section.normal = normal
        section.side = side
        section.up = up
        section.axis_name = "DIR"
        previous_side, previous_up = side, up


def _frame_snapshot(section) -> dict:
    """快照一个截面的坐标系 / 极点字段（用于换轴失败时回滚）。"""
    snap: dict[str, Any] = {}
    for name in _FRAME_FIELDS:
        value = getattr(section, name, None)
        if isinstance(value, np.ndarray):
            snap[name] = value.copy()
        elif isinstance(value, dict):
            snap[name] = dict(value)
        else:
            snap[name] = value
    return snap


def _restore_frames(sections: list, snapshots: list) -> None:
    for section, snap in zip(sections, snapshots):
        for name, value in snap.items():
            if isinstance(value, np.ndarray):
                setattr(section, name, value.copy())
            else:
                setattr(section, name, value)


# ------------------------------------------------------------------ 体检


def _apply_checks(
    result: RouteResult,
    step_path: str,
    log: Callable[[Any], None],
) -> RouteResult:
    """用 S6 的 checks 补齐 面数/实体数/自由边/体积/bbox/分型边/valid；缺了就留 None。"""
    try:
        from .. import checks
    except Exception as exc:  # noqa: BLE001
        log(f"  ! checks 模块不可用（{type(exc).__name__}: {exc}），体检字段留空")
        result.problems.append("checks 模块不可用，实体体检未做（faces/valid 等字段为 None）")
        return result

    record = None
    for name in ("safe_inspect_step", "inspect_step"):
        probe = getattr(checks, name, None)
        if not callable(probe):
            continue
        try:
            record = probe(step_path)
            break
        except Exception as exc:  # noqa: BLE001
            log(f"  ! checks.{name} 失败：{type(exc).__name__}: {exc}")
    if record is None:
        result.problems.append("STEP 体检失败，faces/valid 等字段为 None")
        return result

    merge = getattr(checks, "merge_check", None) or getattr(checks, "compare", None)
    if callable(merge):
        try:
            merged = merge(result, record)
        except Exception as exc:  # noqa: BLE001
            log(f"  ! checks.merge_check 失败（{type(exc).__name__}: {exc}），改手工填")
            merged = None
        if isinstance(merged, RouteResult):
            return merged

    for name in (
        "faces",
        "edges",
        "solids",
        "free_edges",
        "valid",
        "volume_mm3",
        "bbox",
        "parting_edges",
    ):
        value = getattr(record, name, None)
        if value is not None:
            setattr(result, name, value)
    extra = getattr(record, "extra", None)
    if extra:
        artifacts = dict(result.artifacts or {})
        merged_extra = dict(artifacts.get("checks") or {})
        merged_extra.update(extra)
        artifacts["checks"] = merged_extra
        result.artifacts = artifacts
    return result


def _conformance(result: RouteResult) -> list[str]:
    """§6.1 四条硬指标 + 有效性：不过只记串，不中止。"""
    issues: list[str] = []
    try:
        if result.faces is not None and int(result.faces) != 4:
            issues.append(f"面数 {result.faces} ≠ 4")
        if result.solids is not None and int(result.solids) != 1:
            issues.append(f"实体数 {result.solids} ≠ 1")
        if result.parting_edges is not None and int(result.parting_edges) != 2:
            issues.append(f"共享分型边 {result.parting_edges} ≠ 2")
        if result.free_edges is not None and int(result.free_edges) != 0:
            issues.append(f"自由边 {result.free_edges} ≠ 0")
    except (TypeError, ValueError):
        issues.append("体检字段类型异常，无法判定 4 面 / 1 实体 / 2 分型边 / 0 自由边")
    if result.valid is False:
        issues.append("OCC 有效性检查未通过")
    if result.volume_mm3 is None:
        issues.append("体积未体检")
    return issues


# ------------------------------------------------------------------ 小工具


def _fail(result: RouteResult, exc: BaseException, log: Callable[[Any], None]) -> None:
    message = f"{type(exc).__name__}: {exc}"
    trace = traceback.format_exc(limit=12)
    result.status = STATUS_ERROR
    result.error = message
    result.problems = list(result.problems or []) + [message]
    artifacts = dict(result.artifacts or {})
    artifacts["traceback"] = trace
    result.artifacts = artifacts
    log(f"  ✗ {result.part} / {result.key} / {ROUTE} 失败：{message}")
    lines = [line.strip() for line in trace.strip().splitlines() if line.strip()]
    if lines:
        log(f"      {lines[-1]}")


def _logger(ctx: PipelineContext) -> Callable[[Any], None]:
    log = getattr(ctx, "log", None)
    return log if callable(log) else print


def _options() -> dict:
    """settings 口径（HALF_SAMPLES=65 / SMOOTH_PASSES=2 / STATION_STEP_MM=2.0）。"""
    half = int(getattr(settings, "HALF_SAMPLES", 65) or 65)
    return {
        "step_mm": float(getattr(settings, "STATION_STEP_MM", 2.0) or 2.0),
        "half_samples": half,
        "smooth_passes": int(getattr(settings, "SMOOTH_PASSES", 2) or 2),
    }


def _defn(spec: ModelSpec):
    define = getattr(contracts, "model_def", None)
    if not callable(define):
        return None
    try:
        return define(spec.key)
    except Exception:  # noqa: BLE001 - 目录外的键按启发式判轮廓
        return None


def _contour(spec: ModelSpec) -> str:
    """本流水线只保留外轮廓；``成型_实心内径`` 已下线。"""
    return "outer"


def _measured_path(spec: ModelSpec, ctx: Optional[PipelineContext] = None) -> Optional[str]:
    """``spec.measured_path`` 优先；其次按 settings.measured_path 从 out_dir 推；再退 ctx。"""
    path = getattr(spec, "measured_path", None)
    if path:
        return str(path)
    try:
        out_root = os.path.dirname(os.path.abspath(str(spec.out_dir)))
        resolve = getattr(settings, "measured_path", None)
        if callable(resolve):
            return str(resolve(spec.part, getattr(spec, "end_treatment", "c1"), out_root))
    except Exception:  # noqa: BLE001
        pass
    try:
        cached = (getattr(ctx, "measured", None) or {}).get(spec.part)
        cached_path = getattr(cached, "path", None)
        if cached_path:
            return str(cached_path)
    except Exception:  # noqa: BLE001
        pass
    return None


def _cached_station_count(ctx: Optional[PipelineContext], spec: ModelSpec) -> Optional[int]:
    """ctx.measured 里有该件的 sections_full 时给出源站数（外轮廓 loader 不返回它）。"""
    try:
        cached = (getattr(ctx, "measured", None) or {}).get(spec.part)
        rows = getattr(cached, "sections_full", None)
        if not rows:
            return None
        caliber = getattr(cached, "end_treatment", None)
        if caliber and caliber != getattr(spec, "end_treatment", caliber):
            return None
        return int(len(rows))
    except Exception:  # noqa: BLE001
        return None


def _prism_volume(sections: list) -> float:
    """截面棱柱积分 ``∫(A_i+A_{i+1})/2·d_i``（Shape.Volume 会重复计重叠面）。"""
    if len(sections) < 2:
        return 0.0
    total = 0.0
    for left, right in zip(sections[:-1], sections[1:]):
        step = float(
            np.linalg.norm(np.asarray(right.center, dtype=float) - np.asarray(left.center, dtype=float))
        )
        total += 0.5 * (_loop_area(left) + _loop_area(right)) * step
    return total


def _loop_area(section) -> float:
    points = np.asarray(section.points, dtype=float)
    if len(points) < 3:
        return 0.0
    local = points - np.asarray(section.center, dtype=float)
    x = local @ np.asarray(section.side, dtype=float)
    y = local @ np.asarray(section.up, dtype=float)
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _bbox_of_points(sections: list) -> Optional[list[float]]:
    """截面点云的 bbox（参考体积口径用；正式 bbox 由 checks 体检覆盖）。"""
    clouds = [np.asarray(section.points, dtype=float) for section in sections if len(section.points)]
    if not clouds:
        return None
    cloud = np.vstack(clouds)
    low = cloud.min(axis=0)
    high = cloud.max(axis=0)
    return [round(float(v), 4) for v in (*low, *high)]


def _file_size(path: str) -> Optional[int]:
    try:
        return int(os.path.getsize(path))
    except OSError:
        return None


def _sha256_file(path: str) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


# ------------------------------------------------------------------ 延迟导入


def _giant():
    from ..vendor.pipe_smoother import giant_sections as gs

    return gs


def _geometry():
    from ..vendor.pipe_smoother.geometry import (
        BuildOptions,
        Section,
        prepare_sections,
        write_step,
    )

    return BuildOptions, Section, prepare_sections, write_step


def _assemble():
    from ..vendor.pipe_smoother.assemble import build_from_closed_sections

    return build_from_closed_sections
