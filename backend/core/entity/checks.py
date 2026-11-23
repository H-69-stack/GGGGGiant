"""统一版捷安特 · 实体化流水线：几何体检（S3）。

本模块只做一件事：**把几何量出来、记下来**，绝不替调用方判成败：

    inspect_shape(shape) -> CheckRecord
        面 / 边 / 实体 / 自由边 / 有效性 / 体积 / 包围盒 / 分型边 / 平面端盖。

    inspect_step(step_path) -> CheckRecord
        读 STEP（``STEPControl_Reader``）后转 ``inspect_shape``，顺带记
        ``bytes`` / ``read_seconds`` / ``seconds``。

    merge_check(result, rec, ref_volume=None, ref_bbox=None) -> RouteResult
        把 ``CheckRecord`` 填进 ``RouteResult``，并算 ``volume_error_pct`` /
        ``bbox_dev_mm``。**只记录：不回写 status / error / problems。**

    summarize(reports) -> dict
        按「件 × 类 × 路线」汇总 ok/error/skipped 与最大偏差，供 report 与前端用。

规范值（``ENTITY_PIPELINE.md`` §8）：``faces == 4``、``solids == 1``、
``free_edges == 0``、分型边 2 条、``valid is True``。不合规由调用方写 ``problems``，
本模块只提供 :func:`conformance_issues` 这个「读结论」的小工具。

实现约定：

* 只用 OCP（``D:\\python\\envs\\occ`` 的 7.7.2），不依赖 FreeCAD；
* OCP 一律在函数内部延迟 import —— 没装 OCP 的环境里也能 import 本模块；
* 单个指标量测失败只记进 ``extra["errors"]``，不让整件体检崩掉
  （例外：STEP 读不进来时 :func:`inspect_step` 会抛 ``RuntimeError``，
  要「绝不抛」请用 :func:`safe_inspect_step`）；
* ``CheckRecord`` 没有的字段（平面端盖数等）一律塞 ``extra``，
  键名见下表：

==========================  ==================================================
``planar_caps``             平面端盖数（同时以 ``planar_faces`` 别名再存一份，
                            对齐 ``vendor/c2/c2_routes.py`` 的叫法）
``bbox_diagonal_mm``        包围盒对角线长度
``parting_threshold_mm``    分型边长度阈值 = 0.6 × 对角线
``parting_edge_lengths_mm`` 命中的分型边长（降序，最多 8 条）
``shared_edges``            被 2 个面共享的边数；``shared_edges_multi`` 被 ≥3 面共享
``unique_edges``            按 ``IsSame`` 归并后的唯一边数（``edges`` 是
                            ``TopExp_Explorer`` 口径，共享边会在每个面下各计一次，
                            4 面规范里 ``edges == 12`` 而 ``unique_edges == 6``）
``errors``                  体检过程中被吞掉的异常文本
==========================  ==================================================
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Iterator, Optional

from .contracts import CheckRecord, PartReport, RouteResult, json_safe, now_iso

# ---------------------------------------------------------------- 规范值与阈值

#: 分型边：长度必须 > 该系数 × 包围盒对角线，且恰好被 2 个面共享。
PARTING_RATIO = 0.6
#: 4 面模具实体的规范值（只用于 ``conformance_issues``，不影响任何写回）。
EXPECT_FACES = 4
EXPECT_SOLIDS = 1
EXPECT_FREE_EDGES = 0
EXPECT_PARTING_EDGES = 2
#: 平面端盖下限（只记录在 extra，不参与 conformance）。
MIN_PLANAR_CAPS = 2

_STATUS_OK = frozenset({"ok", "success", "done", "passed"})
_STATUS_ERROR = frozenset({"failed", "fail", "error", "exception", "timeout"})
_STATUS_SKIPPED = frozenset({"skipped", "skip"})

__all__ = [
    "PARTING_RATIO",
    "EXPECT_FACES",
    "EXPECT_SOLIDS",
    "EXPECT_FREE_EDGES",
    "EXPECT_PARTING_EDGES",
    "MIN_PLANAR_CAPS",
    "read_step",
    "shape_bbox",
    "bbox_diagonal",
    "inspect_shape",
    "inspect_step",
    "safe_inspect_step",
    "merge_check",
    "compare",
    "conformance_issues",
    "summarize",
]


# ---------------------------------------------------------------- OCP 延迟加载

_OCC: Optional[SimpleNamespace] = None


def _occ() -> SimpleNamespace:
    """把用到的 OCP 名字收进一个命名空间（第一次调用时才 import）。"""
    global _OCC
    if _OCC is not None:
        return _OCC

    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.Bnd import Bnd_Box
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    from OCP.TopoDS import TopoDS

    _OCC = SimpleNamespace(
        BRepAdaptor_Surface=BRepAdaptor_Surface,
        BRepBndLib=BRepBndLib,
        BRepCheck_Analyzer=BRepCheck_Analyzer,
        BRepGProp=BRepGProp,
        Bnd_Box=Bnd_Box,
        GeomAbs_Plane=GeomAbs_Plane,
        GProp_GProps=GProp_GProps,
        IFSelect_RetDone=IFSelect_RetDone,
        STEPControl_Reader=STEPControl_Reader,
        TopAbs_EDGE=TopAbs_EDGE,
        TopAbs_FACE=TopAbs_FACE,
        TopAbs_SOLID=TopAbs_SOLID,
        TopExp=TopExp,
        TopExp_Explorer=TopExp_Explorer,
        TopTools_IndexedDataMapOfShapeListOfShape=TopTools_IndexedDataMapOfShapeListOfShape,
        TopoDS=TopoDS,
    )
    return _OCC


# ---------------------------------------------------------------- 小工具


def _finite(value: Any) -> Optional[float]:
    """转成有限 float，转不动就给 None（nan/inf 一律当没有）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rd(value: Any, nd: int = 3) -> Optional[float]:
    number = _finite(value)
    return None if number is None else round(number, nd)


def _as_int(value: Any) -> Optional[int]:
    number = _finite(value)
    return None if number is None else int(number)


# ---------------------------------------------------------------- 几何量测


def read_step(step_path: str | Path):
    """读 STEP 并返回 ``TopoDS_Shape``（失败抛 ``RuntimeError``）。"""
    occ = _occ()
    path = Path(step_path)
    reader = occ.STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != occ.IFSelect_RetDone:
        raise RuntimeError(f"无法读取 STEP 文件（状态 {status}）：{path}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise RuntimeError(f"STEP 文件不包含可用的几何体：{path}")
    return shape


def shape_bbox(shape) -> tuple[float, float, float, float, float, float]:
    """最优包围盒 ``(xmin, ymin, zmin, xmax, ymax, zmax)``（不加间隙）。"""
    occ = _occ()
    box = occ.Bnd_Box()
    occ.BRepBndLib.AddOptimal_s(shape, box, False, False)
    if box.IsVoid():
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return tuple(float(v) for v in box.Get())


def bbox_diagonal(bbox) -> float:
    """包围盒对角线长度；入参可以是 6 元素 bbox 或 3 元素尺寸。"""
    values = [_finite(v) for v in (bbox or ())]
    if len(values) >= 6 and all(v is not None for v in values[:6]):
        dx = values[3] - values[0]
        dy = values[4] - values[1]
        dz = values[5] - values[2]
    elif len(values) >= 3 and all(v is not None for v in values[:3]):
        dx, dy, dz = values[0], values[1], values[2]
    else:
        return 0.0
    return float(math.sqrt(dx * dx + dy * dy + dz * dz))


def _count_subshapes(shape, kind) -> int:
    occ = _occ()
    explorer = occ.TopExp_Explorer(shape, kind)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def _face_edge_map(shape):
    occ = _occ()
    mapping = occ.TopTools_IndexedDataMapOfShapeListOfShape()
    occ.TopExp.MapShapesAndAncestors_s(shape, occ.TopAbs_EDGE, occ.TopAbs_FACE, mapping)
    return mapping


def _edge_length(edge) -> float:
    occ = _occ()
    props = occ.GProp_GProps()
    occ.BRepGProp.LinearProperties_s(edge, props)
    return float(props.Mass())


def _planar_cap_count(shape) -> int:
    """平面面数（4 面规范里就是 2 个平面端盖）。"""
    occ = _occ()
    count = 0
    explorer = occ.TopExp_Explorer(shape, occ.TopAbs_FACE)
    while explorer.More():
        face = occ.TopoDS.Face_s(explorer.Current())
        try:
            if occ.BRepAdaptor_Surface(face).GetType() == occ.GeomAbs_Plane:
                count += 1
        except Exception:  # noqa: BLE001 - 个别面取不到曲面就跳过
            pass
        explorer.Next()
    return count


def _distinct_face_count(ancestors) -> int:
    """祖先列表里真正不同的面有几个（``IsSame`` 忽略朝向）。"""
    faces: list = []
    try:
        for item in ancestors:
            if any(item.IsSame(seen) for seen in faces):
                continue
            faces.append(item)
    except Exception:  # noqa: BLE001 - 迭代不了就退回条数
        try:
            return int(ancestors.Extent())
        except Exception:  # noqa: BLE001
            return 0
    return len(faces)


def _planar_face_edges(shape) -> list:
    """平面面（端盖）上的边——这些是端盖轮廓，不算纵向分型边。"""
    occ = _occ()
    edges: list = []
    explorer = occ.TopExp_Explorer(shape, occ.TopAbs_FACE)
    while explorer.More():
        face = occ.TopoDS.Face_s(explorer.Current())
        try:
            if occ.BRepAdaptor_Surface(face).GetType() == occ.GeomAbs_Plane:
                sub = occ.TopExp_Explorer(face, occ.TopAbs_EDGE)
                while sub.More():
                    edges.append(occ.TopoDS.Edge_s(sub.Current()))
                    sub.Next()
        except Exception:  # noqa: BLE001 - 个别面取不到曲面就跳过
            pass
        explorer.Next()
    return edges


def _parting_edges(shape, mapping, diagonal: float):
    """分型边：长度 > ``PARTING_RATIO`` × 包围盒对角线，且被 2 个面共享。

    端盖（平面面）上的边不计入——短件（如扩口芯轴）的端盖圆弧也可能超过
    长度阈值，但它们是端盖轮廓，不是纵向分型线。

    返回 ``(edges, lengths)``；``edges`` 按长度降序。同一条缝边在
    ``MapShapesAndAncestors`` 里可能按两个朝向各出现一次，用 ``IsSame`` 去重。
    """
    occ = _occ()
    if not diagonal or diagonal <= 0.0:
        return [], []
    threshold = PARTING_RATIO * diagonal
    cap_edges = _planar_face_edges(shape)
    found: list[tuple[float, Any]] = []
    for index in range(1, mapping.Extent() + 1):
        ancestors = mapping.FindFromIndex(index)
        if ancestors.Extent() < 2:
            continue
        if _distinct_face_count(ancestors) != 2:
            continue
        edge = occ.TopoDS.Edge_s(mapping.FindKey(index))
        if any(edge.IsSame(cap) for cap in cap_edges):
            continue
        try:
            length = _edge_length(edge)
        except Exception:  # noqa: BLE001 - 个别边量不出长度就跳过
            continue
        if length > threshold:
            found.append((length, edge))

    found.sort(key=lambda pair: pair[0], reverse=True)
    unique: list[tuple[float, Any]] = []
    for length, edge in found:
        if any(edge.IsSame(seen) for _, seen in unique):
            continue
        unique.append((length, edge))
    return [edge for _, edge in unique], [round(length, 3) for length, _ in unique[:8]]


# ---------------------------------------------------------------- 体检


def inspect_shape(shape) -> CheckRecord:
    """量一个 ``TopoDS_Shape`` 的拓扑/体积/包围盒/分型边，返回 ``CheckRecord``。

    单个指标量测失败时该字段留 ``None``，错误文本进 ``extra["errors"]``；
    只有 ``shape`` 本身不合法（None / Null）才会抛异常。
    """
    occ = _occ()
    if shape is None:
        raise TypeError("inspect_shape 需要一个 TopoDS_Shape，收到 None")
    try:
        if shape.IsNull():
            raise ValueError("inspect_shape 收到空形状（IsNull）")
    except AttributeError as exc:  # 不是 TopoDS_Shape
        raise TypeError(f"inspect_shape 需要 TopoDS_Shape：{exc}") from None

    record = CheckRecord()
    extra: dict = {}
    errors: list[str] = []

    # 1) 拓扑计数
    try:
        record.faces = _count_subshapes(shape, occ.TopAbs_FACE)
        record.edges = _count_subshapes(shape, occ.TopAbs_EDGE)
        record.solids = _count_subshapes(shape, occ.TopAbs_SOLID)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"拓扑计数失败：{exc}")

    # 2) 边 → 面 归属：自由边 / 共享边
    mapping = None
    try:
        mapping = _face_edge_map(shape)
        free_edges = 0
        shared_two = 0
        shared_multi = 0
        for index in range(1, mapping.Extent() + 1):
            used = mapping.FindFromIndex(index).Extent()
            if used == 1:
                free_edges += 1
            elif used == 2:
                shared_two += 1
            elif used > 2:
                shared_multi += 1
        record.free_edges = free_edges
        extra["shared_edges"] = shared_two
        extra["shared_edges_multi"] = shared_multi
        extra["unique_edges"] = int(mapping.Extent())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"边-面归属统计失败：{exc}")

    # 3) 包围盒（最优盒，不加间隙）
    diagonal = 0.0
    try:
        box = shape_bbox(shape)
        record.bbox = [round(value, 4) for value in box]
        diagonal = bbox_diagonal(box)
        extra["bbox_diagonal_mm"] = round(diagonal, 3)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"包围盒计算失败：{exc}")

    # 4) 体积
    try:
        props = occ.GProp_GProps()
        occ.BRepGProp.VolumeProperties_s(shape, props)
        record.volume_mm3 = round(float(props.Mass()), 3)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"体积计算失败：{exc}")

    # 5) OCC 有效性
    try:
        record.valid = bool(occ.BRepCheck_Analyzer(shape).IsValid())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"有效性检查失败：{exc}")

    # 6) 平面端盖
    try:
        caps = _planar_cap_count(shape)
        extra["planar_caps"] = caps
        # 对齐 vendor/c2 的叫法，两个键同值
        extra["planar_faces"] = caps
    except Exception as exc:  # noqa: BLE001
        errors.append(f"平面端盖统计失败：{exc}")

    # 7) 分型边（依赖 mapping + 对角线，放最后）
    try:
        if mapping is not None:
            edges, lengths = _parting_edges(shape, mapping, diagonal)
            record.parting_edges = len(edges)
            extra["parting_threshold_mm"] = round(PARTING_RATIO * diagonal, 3)
            extra["parting_edge_lengths_mm"] = lengths
    except Exception as exc:  # noqa: BLE001
        errors.append(f"分型边统计失败：{exc}")

    if errors:
        extra["errors"] = errors
    record.extra = extra
    return record


def inspect_step(step_path: str | Path) -> CheckRecord:
    """读一个 STEP 文件并体检；文件读不进来直接抛 ``RuntimeError``。"""
    path = Path(step_path)
    if not path.is_file():
        raise FileNotFoundError(f"STEP 文件不存在：{path}")

    started = time.perf_counter()
    shape = read_step(path)
    read_seconds = time.perf_counter() - started
    record = inspect_shape(shape)
    total_seconds = time.perf_counter() - started

    record.extra["path"] = str(path)
    record.extra["bytes"] = path.stat().st_size
    record.extra["read_seconds"] = round(read_seconds, 3)
    record.extra["seconds"] = round(total_seconds, 3)
    return record


def safe_inspect_step(step_path: str | Path) -> CheckRecord:
    """``inspect_step`` 的「绝不抛」版本：读失败返回空记录 + ``extra["error"]``。

    读不进来时几何字段一律留 ``None``（「没体检」而不是「体检出问题」），
    不会让 :func:`conformance_issues` 误报无效；失败原因看 ``extra["error"]``。
    """
    try:
        return inspect_step(step_path)
    except Exception as exc:  # noqa: BLE001 - 体检模块不许把流水线带崩
        record = CheckRecord()
        record.extra = {
            "path": str(step_path),
            "error": f"{type(exc).__name__}: {exc}",
            "read_failed": True,
            "seconds": None,
        }
        return record


# ---------------------------------------------------------------- 合并进 RouteResult


def merge_check(
    result: RouteResult,
    rec: CheckRecord,
    ref_volume: Optional[float] = None,
    ref_bbox: Optional[Iterable[float]] = None,
) -> RouteResult:
    """把体检结果填进 ``RouteResult``，只记录，不判失败。

    * ``volume_mm3`` / ``faces`` / ``edges`` / ``solids`` / ``free_edges`` /
      ``valid`` / ``bbox`` / ``parting_edges`` 取自 ``rec``（``rec`` 的对应字段
      为 ``None`` 时保留 ``result`` 原值）；
    * ``ref_volume`` 缺省时用 ``result.ref_volume_mm3``，算 ``volume_error_pct``
      （绝对偏差百分比，4 位小数）；
    * ``ref_bbox`` 缺省时用 ``result.ref_bbox``，算 ``bbox_dev_mm`` =
      6 个边界坐标差的绝对值最大值（4 位小数）；
    * **不改** ``status`` / ``error`` / ``problems`` / ``seconds``；
    * ``rec.extra`` 合并进 ``result.artifacts["checks"]``（``artifacts`` 里别的键不动）。
    """
    if result is None:
        return result

    if rec is not None:
        if rec.faces is not None:
            result.faces = _as_int(rec.faces)
        if rec.edges is not None:
            result.edges = _as_int(rec.edges)
        if rec.solids is not None:
            result.solids = _as_int(rec.solids)
        if rec.free_edges is not None:
            result.free_edges = _as_int(rec.free_edges)
        if rec.parting_edges is not None:
            result.parting_edges = _as_int(rec.parting_edges)
        if rec.valid is not None:
            result.valid = bool(rec.valid)
        if rec.volume_mm3 is not None:
            result.volume_mm3 = _rd(rec.volume_mm3, 3)
        if rec.bbox:
            result.bbox = [_rd(value, 4) for value in rec.bbox]

    # 体积偏差
    reference_volume = ref_volume if ref_volume is not None else result.ref_volume_mm3
    reference_volume = _finite(reference_volume)
    if reference_volume is not None and reference_volume > 0:
        result.ref_volume_mm3 = reference_volume
        if result.volume_mm3 is not None:
            error = abs(result.volume_mm3 - reference_volume) / reference_volume * 100.0
            result.volume_error_pct = round(error, 4)

    # 包围盒偏差
    reference_box = ref_bbox if ref_bbox is not None else result.ref_bbox
    reference_values = [_finite(value) for value in (reference_box or ())]
    if len(reference_values) >= 6 and all(value is not None for value in reference_values[:6]):
        result.ref_bbox = [float(value) for value in reference_values[:6]]
        if result.bbox and len(result.bbox) >= 6:
            deviation = max(
                abs(float(here) - float(there))
                for here, there in zip(result.bbox[:6], result.ref_bbox)
            )
            result.bbox_dev_mm = round(deviation, 4)

    # 体检明细（平面端盖数、耗时等）挂到 artifacts，别的键不碰
    if rec is not None and rec.extra:
        try:
            checks = result.artifacts.get("checks")
            if not isinstance(checks, dict):
                checks = {}
            checks.update(json_safe(rec.extra))
            result.artifacts["checks"] = checks
        except Exception:  # noqa: BLE001 - 记录不影响主流程
            pass

    return result


#: 契约（``ENTITY_PIPELINE.md`` §9）里这个名字，保留别名。
compare = merge_check


# ---------------------------------------------------------------- 读结论（不写回）

_FLAG_ORDER = ("faces_ne_4", "solids_ne_1", "free_edges_ne_0", "parting_ne_2", "invalid")


def _check_view(obj: Any) -> dict:
    """把 ``RouteResult`` / ``CheckRecord`` / dict 统一成「有那几个字段的 dict」。"""
    if obj is None:
        return {}
    if isinstance(obj, RouteResult):
        return obj.to_dict()
    if isinstance(obj, CheckRecord):
        return {
            "faces": obj.faces,
            "edges": obj.edges,
            "solids": obj.solids,
            "free_edges": obj.free_edges,
            "parting_edges": obj.parting_edges,
            "valid": obj.valid,
            "volume_mm3": obj.volume_mm3,
            "bbox": obj.bbox,
            "extra": obj.extra,
        }
    if isinstance(obj, dict):
        return obj
    return {}


def _flags(view: dict) -> dict[str, Optional[bool]]:
    faces = _as_int(view.get("faces"))
    solids = _as_int(view.get("solids"))
    free_edges = _as_int(view.get("free_edges"))
    parting_edges = _as_int(view.get("parting_edges"))
    valid = view.get("valid")
    return {
        "faces_ne_4": None if faces is None else faces != EXPECT_FACES,
        "solids_ne_1": None if solids is None else solids != EXPECT_SOLIDS,
        "free_edges_ne_0": None if free_edges is None else free_edges != EXPECT_FREE_EDGES,
        "parting_ne_2": None if parting_edges is None else parting_edges != EXPECT_PARTING_EDGES,
        "invalid": None if valid is None else (not bool(valid)),
    }


def conformance_issues(obj: Any) -> list[str]:
    """对照 §8 规范值列出问题（空列表 = 合规；字段缺失不算问题）。"""
    view = _check_view(obj)
    issues: list[str] = []
    for flag, value in _flags(view).items():
        if not value:
            continue
        if flag == "faces_ne_4":
            issues.append(f"面数 {view.get('faces')} ≠ {EXPECT_FACES}")
        elif flag == "solids_ne_1":
            issues.append(f"实体数 {view.get('solids')} ≠ {EXPECT_SOLIDS}")
        elif flag == "free_edges_ne_0":
            issues.append(f"自由边 {view.get('free_edges')} ≠ {EXPECT_FREE_EDGES}")
        elif flag == "parting_ne_2":
            issues.append(f"共享分型边 {view.get('parting_edges')} ≠ {EXPECT_PARTING_EDGES}")
        elif flag == "invalid":
            issues.append("OCC 有效性检查未通过")
    return issues


#: 只记录不判失败的问题前缀（用户口径：验收只记录，不因此中断流水线）。
SOFT_ISSUE_PREFIXES = ("共享分型边",)


def hard_issues(obj: Any) -> list[str]:
    """致命问题（面数/实体数/自由边/有效性）；分型边条数不算，只记录。"""
    return [item for item in conformance_issues(obj)
            if not str(item).startswith(SOFT_ISSUE_PREFIXES)]


def soft_issues(obj: Any) -> list[str]:
    """软问题（当前只有分型边条数）：记进 problems，不改状态。"""
    return [item for item in conformance_issues(obj)
            if str(item).startswith(SOFT_ISSUE_PREFIXES)]


# ---------------------------------------------------------------- 汇总


def _new_counter() -> dict:
    return {
        "total": 0,
        "ok": 0,
        "error": 0,
        "skipped": 0,
        "other": 0,
        "max_volume_error_pct": None,
        "max_bbox_dev_mm": None,
        "worst": None,
        "seconds": 0.0,
        "conformance": {flag: 0 for flag in _FLAG_ORDER},
        "checked": 0,
    }


def _bump(counter: dict, view: dict) -> None:
    counter["total"] += 1
    status = str(view.get("status") or "").strip().lower()
    if status in _STATUS_OK:
        counter["ok"] += 1
    elif status in _STATUS_ERROR:
        counter["error"] += 1
    elif status in _STATUS_SKIPPED:
        counter["skipped"] += 1
    else:
        counter["other"] += 1

    seconds = _finite(view.get("seconds"))
    if seconds is not None:
        counter["seconds"] = round(counter["seconds"] + seconds, 3)

    volume_error = _finite(view.get("volume_error_pct"))
    if volume_error is not None and (
        counter["max_volume_error_pct"] is None or volume_error > counter["max_volume_error_pct"]
    ):
        counter["max_volume_error_pct"] = round(volume_error, 4)
        counter["worst"] = {
            "part": view.get("part"),
            "key": view.get("key"),
            "route": view.get("route"),
            "volume_error_pct": round(volume_error, 4),
        }

    bbox_dev = _finite(view.get("bbox_dev_mm"))
    if bbox_dev is not None and (
        counter["max_bbox_dev_mm"] is None or bbox_dev > counter["max_bbox_dev_mm"]
    ):
        counter["max_bbox_dev_mm"] = round(bbox_dev, 4)

    flags = _flags(view)
    if any(value is not None for value in flags.values()):
        counter["checked"] += 1
    for flag, violated in flags.items():
        if violated:
            counter["conformance"][flag] += 1


def _iter_reports(reports: Any) -> Iterator[Any]:
    """把 ``reports`` 归一成「有 results 的报告对象」迭代出来。

    支持：单个 ``PartReport`` / 报告 dict / ``{件: 报告}`` / 以上任意列表，
    也容忍直接给一串 ``RouteResult``（当成无头报告）。
    """
    if reports is None:
        return
    if isinstance(reports, PartReport):
        yield reports
        return
    if isinstance(reports, dict):
        if "results" in reports:
            yield reports
            return
        for name, value in reports.items():
            if isinstance(value, PartReport) or (isinstance(value, dict) and "results" in value):
                yield value
            elif isinstance(value, (list, tuple)):
                yield {"part": str(name), "results": list(value)}
        return
    if isinstance(reports, (list, tuple, set)):
        for item in reports:
            if isinstance(item, PartReport) or (isinstance(item, dict) and "results" in item):
                yield item
            elif isinstance(item, RouteResult) or isinstance(item, dict):
                yield {"results": [item]}
        return
    if isinstance(reports, RouteResult):
        yield {"results": [reports]}


def _results_of(report: Any) -> list:
    if isinstance(report, PartReport):
        return list(report.results or [])
    if isinstance(report, dict):
        return list(report.get("results") or [])
    return []


def _report_part(report: Any) -> str:
    if isinstance(report, PartReport):
        return str(report.part or "")
    if isinstance(report, dict):
        return str(report.get("part") or "")
    return ""


def _ordered_unique(values: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def summarize(reports: Any, *, max_problems: int = 200) -> dict:
    """按「件 × 类 × 路线」汇总，供 ``report.py`` 与前端共用。

    ``reports`` 可以是 ``PartReport`` / ``report.to_dict()`` / 它们组成的
    list，或 ``{件: 报告}``。返回纯 dict（``json.dumps`` 得动）：

    ``totals`` / ``by_part`` / ``by_model`` / ``by_route``
        各自的 ``total`` / ``ok`` / ``error`` / ``skipped`` / ``other`` 计数，
        ``max_volume_error_pct`` / ``max_bbox_dev_mm`` 最大偏差，
        ``worst``（产生最大体积偏差的那条产出），``conformance`` 违规计数。
    ``cells``
        「件 × 类 × 路线」明细列表，含同样计数与 ``violations`` 文字。
    ``problems``
        逐条 ``件/类/路线: 问题``（超过 ``max_problems`` 截断并置
        ``problems_truncated``）。
    """
    cells: dict[tuple[str, str, str], dict] = {}
    by_part: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_route: dict[str, dict] = {}
    totals = _new_counter()
    problems: list[str] = []
    truncated = False

    parts: list[str] = []
    models: list[str] = []
    routes: list[str] = []

    for report in _iter_reports(reports):
        report_part = _report_part(report)
        for raw in _results_of(report):
            view = raw.to_dict() if isinstance(raw, RouteResult) else dict(raw or {})
            part = str(view.get("part") or report_part or "")
            key = str(view.get("key") or "")
            route = str(view.get("route") or "")
            parts.append(part)
            models.append(key)
            routes.append(route)

            cell_key = (part, key, route)
            cell = cells.get(cell_key)
            if cell is None:
                cell = _new_counter()
                cell.update({"part": part, "key": key, "route": route, "violations": []})
                cells[cell_key] = cell

            _bump(cell, view)
            _bump(totals, view)
            _bump(by_part.setdefault(part, _new_counter()), view)
            _bump(by_model.setdefault(key, _new_counter()), view)
            _bump(by_route.setdefault(route, _new_counter()), view)

            for issue in conformance_issues(view):
                if issue not in cell["violations"]:
                    cell["violations"].append(issue)
                if len(problems) < max_problems:
                    problems.append(f"{part}/{key}/{route}: {issue}")
                else:
                    truncated = True

    cell_list = [
        {
            "part": cell["part"],
            "key": cell["key"],
            "route": cell["route"],
            "total": cell["total"],
            "ok": cell["ok"],
            "error": cell["error"],
            "skipped": cell["skipped"],
            "other": cell["other"],
            "max_volume_error_pct": cell["max_volume_error_pct"],
            "max_bbox_dev_mm": cell["max_bbox_dev_mm"],
            "worst": cell["worst"],
            "seconds": cell["seconds"],
            "checked": cell["checked"],
            "conformance": cell["conformance"],
            "violations": cell["violations"],
        }
        for cell in sorted(cells.values(), key=lambda c: (c["part"], c["key"], c["route"]))
    ]

    return {
        "generated": now_iso(),
        "expectations": {
            "faces": EXPECT_FACES,
            "solids": EXPECT_SOLIDS,
            "free_edges": EXPECT_FREE_EDGES,
            "parting_edges": EXPECT_PARTING_EDGES,
            "valid": True,
            "planar_caps_min": MIN_PLANAR_CAPS,
        },
        "parts": _ordered_unique(parts),
        "models": _ordered_unique(models),
        "routes": _ordered_unique(routes),
        "totals": totals,
        "conformance": totals["conformance"],
        "by_part": by_part,
        "by_model": by_model,
        "by_route": by_route,
        "cells": cell_list,
        "problems": problems,
        "problems_truncated": truncated,
    }
