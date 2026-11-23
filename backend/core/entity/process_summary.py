"""一体化流水线 · 工艺摘要内核（A3，冻结接口 §4.6）。

只做一件事：把「测量缓存 + 工艺设计记录 + 路线推荐」揉成一份**前端/报告都能直接读**
的工艺摘要 dict，不碰几何、不碰 OCP。

    build_process_summary(*, part, measured_path, design=None, route_plan=None,
                          out_root=None, max_points=80) -> dict
    write_summary_markdown(part, summary, path) -> str
    attach_summary(report, summary) -> dict

数据来源
--------

* ``entity_out/_cache/<件>_measured.json`` / ``<件>_measured_v1.json``：
  ``center_line_points`` / ``center_line_length`` / ``sections_full`` /
  ``wall_thickness_stats`` / ``analysis_quality`` / ``bounding_box``；
* 工艺设计库 ``pp_design``：``id`` / ``display_name`` / ``pp_method`` / ``fitness`` /
  ``params_json``（解码后是 D0/T0/L0、D1..D3、T1..T4、TL1..TL5、TLM1..TLM4、L1..L4、
  Dn/Ln 等老项目 All_data 扁平参数，见 ``core/pp/design_db.py``）；
* 路线推荐：``core.pp.optimization.recommend_pp_method``（与
  ``api/entity.py::_route_plan_for`` 同规则：需要 length_mm / max_d_mm / min_d_mm /
  L4，D 由 perimeter/π 折算）。

设计约定
--------

* **绝不抛异常**：外层 try/except 兜住，任何异常都返回结构完整的摘要 +
  ``"error": "<异常文本>"``（缺数据的字段是 ``None`` / ``[]``）；
* 抽管道次只从设计参数**读出**，读不到就跳过该道次，缺失字段留 ``None``，
  **不补造数值**；三类抽管全无参数时 ``passes=[]`` 并在 ``drawing.note`` 说明原因；
* 壁厚分段：``sections_full`` 的 ``cumulative_length`` + ``wall_thickness``
  按相邻变化 >5% 切段，最多 40 段（超了按连续区块合并）；
* 纯标准库，Markdown 由本模块统一渲染，``report.py`` 直接复用
  :func:`summary_markdown`，不重复实现。
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "CENTERLINE_METHOD",
    "WALL_METHOD",
    "MAX_SEGMENTS",
    "build_process_summary",
    "write_summary_markdown",
    "attach_summary",
    "summary_markdown",
]

#: 中心线口径（与 step_analyzer / brep_engine 一致，固定文案）。
CENTERLINE_METHOD = "中心线 = 各截面外轮廓面积形心"
#: 壁厚口径（内外轮廓同截面二维最短距离，固定文案）。
WALL_METHOD = "壁厚 = 内外轮廓在同一截面内的二维最短距离"

#: 壁厚分段上限（再长前端/报告读不动）。
MAX_SEGMENTS = 40
#: 相邻壁厚相对变化超过该比例就切段。
_SEGMENT_TOLERANCE = 0.05

_DRAWING_LABELS = {"normal": "普通抽", "tp": "TP抽", "taper": "Taper"}
_EMPTY_DRAWING_NOTE = (
    "未导入工艺设计或设计参数不完整；请人工判断后导入数据库"
    "（流水线只输出推荐路线，暂不推导道次）。"
)


# ---------------------------------------------------------------------------
# 小工具：数值 / JSON 安全 / 时间
# ---------------------------------------------------------------------------


def _num(value: Any) -> Optional[float]:
    """转有限 float；转不动 / nan / inf 一律 None。"""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    return None if number is None else int(number)


def _round(value: Any, digits: int = 3) -> Optional[float]:
    number = _num(value)
    return None if number is None else round(number, digits)


def _p(params: Any, key: str) -> Optional[float]:
    """取设计参数字典里的一个数值（缺 / 空串 / 非法 → None）。"""
    if not isinstance(params, dict):
        return None
    value = params.get(key)
    if value is None or value == "":
        return None
    return _num(value)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(value: Any) -> Any:
    """递归转成 JSON 可序列化的纯 Python 对象（numpy 标量也认）。"""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):  # numpy 标量
        try:
            return _safe(item())
        except Exception:  # noqa: BLE001 - 兜底转字符串
            pass
    return str(value)


def _as_dict(value: Any) -> dict:
    """``dict`` / ``sqlite3.Row`` / 带 ``keys()`` 的对象 → 普通 dict。"""
    if isinstance(value, dict):
        return dict(value)
    try:
        if hasattr(value, "keys"):
            return {str(key): value[key] for key in value.keys()}
    except Exception:  # noqa: BLE001 - 不是映射就当没有
        pass
    return {}


# ---------------------------------------------------------------------------
# 测量缓存
# ---------------------------------------------------------------------------


def _measured_candidates(part: str, measured_path: Any, out_root: Any) -> list[Path]:
    """候选测量缓存路径（传入路径优先，其次 settings 约定，最后常见命名）。"""
    candidates: list[Path] = []
    if measured_path:
        candidates.append(Path(str(measured_path)))
    try:
        from . import settings as _settings

        candidates.append(Path(_settings.measured_path(part, "c1", out_root)))
    except Exception:  # noqa: BLE001 - settings 不可用时只靠传入路径
        pass
    if out_root:
        cache = Path(str(out_root)) / "_cache"
        candidates.append(cache / f"{part}_measured.json")
        candidates.append(cache / f"{part}_measured_v1.json")
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _load_measured(part: str, measured_path: Any, out_root: Any) -> tuple[dict, str, Optional[str]]:
    """读测量缓存，返回 ``(data, path, error)``；读不到也给结构化的错误文本。"""
    tried: list[str] = []
    for path in _measured_candidates(part, measured_path, out_root):
        tried.append(str(path))
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            return {}, str(path), f"测量缓存读取失败：{type(exc).__name__}: {exc}"
        if isinstance(data, dict):
            return data, str(path), None
        return {}, str(path), f"测量缓存格式异常：顶层应为对象，收到 {type(data).__name__}"
    reason = "测量缓存不存在或不可读：" + "、".join(tried[:3]) if tried else "未提供测量缓存路径"
    return {}, "", reason


def _sections(data: dict) -> list[dict]:
    """``sections_full`` 里可用的截面（非 dict 项丢弃）。"""
    raw = data.get("sections_full")
    if not isinstance(raw, list):
        raw = data.get("sections") if isinstance(data.get("sections"), list) else []
    return [item for item in raw or [] if isinstance(item, dict)]


# ---------------------------------------------------------------------------
# 中心线 / 壁厚 / 直径 / 站位
# ---------------------------------------------------------------------------


def _points_from(data: dict, sections: list[dict]) -> list[list[float]]:
    """中心线三维点：优先 ``center_line_points``，缺失时退回各截面 position。"""
    raw = data.get("center_line_points")
    if not isinstance(raw, list) or not raw:
        raw = [item.get("position") for item in sections]
    points: list[list[float]] = []
    for item in raw or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        triple = [_num(item[0]), _num(item[1]), _num(item[2])]
        if any(value is None for value in triple):
            continue
        points.append([round(float(value), 3) for value in triple])  # type: ignore[arg-type]
    return points


def _downsample(items: list, max_points: Any) -> list:
    """等间隔降采样到 ``max_points``（含首尾），原样少于上限时直接返回。"""
    limit = _int(max_points)
    limit = 80 if limit is None else max(2, limit)
    count = len(items)
    if count <= limit:
        return list(items)
    step = (count - 1) / (limit - 1)
    picked: list = []
    seen: set[int] = set()
    for index in range(limit):
        position = min(count - 1, max(0, int(round(index * step))))
        if position in seen:
            continue
        seen.add(position)
        picked.append(items[position])
    if len(picked) < 2:  # 极端上限下也要保住首尾
        return [items[0], items[-1]]
    picked[0] = items[0]
    picked[-1] = items[-1]
    return picked


def _centerline_length(data: dict, sections: list[dict]) -> Optional[float]:
    length = _num(data.get("center_line_length"))
    if length is not None:
        return length
    values = [_num(item.get("cumulative_length")) for item in sections]
    numbers = [value for value in values if value is not None]
    return max(numbers) if numbers else None


def _build_centerline(data: dict, sections: list[dict], max_points: Any) -> dict:
    points = _points_from(data, sections)
    station_count = 0
    raw = data.get("center_line_points")
    if isinstance(raw, list) and raw:
        station_count = len(raw)
    elif sections:
        station_count = len(sections)
    return {
        "length_mm": _round(_centerline_length(data, sections), 3),
        "points": _downsample(points, max_points),
        "point_count": int(station_count),
        "method": CENTERLINE_METHOD,
    }


def _wall_points(sections: list[dict]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in sections:
        station = _num(item.get("cumulative_length"))
        thickness = _num(item.get("wall_thickness"))
        if station is None or thickness is None:
            continue
        points.append((station, thickness))
    points.sort(key=lambda pair: pair[0])
    return points


def _bucket_segments(raw: list[list], target: int) -> list[list]:
    """把过多的连续分段按索引合并成 ``target`` 个区块（覆盖范围不变）。"""
    if target <= 0:
        return []
    if len(raw) <= target:
        return raw
    size = len(raw) / float(target)
    buckets: list[list] = []
    for index in range(target):
        start_index = int(math.floor(index * size))
        end_index = int(math.floor((index + 1) * size))
        if end_index <= start_index:
            continue
        chunk = raw[start_index:end_index]
        values = [value for segment in chunk for value in segment[2]]
        buckets.append([chunk[0][0], chunk[-1][1], values or [chunk[0][2][0]]])
    return buckets


def _wall_segments_from_sections(sections: list[dict]) -> list[dict]:
    """壁厚沿轴线的连续平台分段（相邻相对变化 >5% 切段，最多 40 段）。"""
    points = _wall_points(sections)
    if not points:
        return []

    raw: list[list] = []
    start, values, last = points[0][0], [points[0][1]], points[0][0]
    for station, thickness in points[1:]:
        reference = sum(values) / len(values)
        changed = reference > 0 and abs(thickness - reference) > _SEGMENT_TOLERANCE * reference
        if changed:
            raw.append([start, last, values])
            start, values = station, [thickness]
        else:
            values.append(thickness)
        last = station
    raw.append([start, last, values])

    if len(raw) > MAX_SEGMENTS:
        raw = _bucket_segments(raw, MAX_SEGMENTS)

    segments: list[dict] = []
    for segment_start, segment_end, segment_values in raw:
        segments.append(
            {
                "start_mm": round(segment_start, 3),
                "end_mm": round(segment_end, 3),
                "thickness_mm": round(sum(segment_values) / len(segment_values), 3),
            }
        )
    return segments


def _build_wall(data: dict, sections: list[dict]) -> dict:
    stats = data.get("wall_thickness_stats")
    stats = stats if isinstance(stats, dict) else {}
    values = [
        value
        for value in (_num(item.get("wall_thickness")) for item in sections)
        if value is not None
    ]

    def pick(key: str, fallback: Any) -> Optional[float]:
        value = _num(stats.get(key))
        return value if value is not None else fallback

    count = _int(stats.get("count"))
    return {
        "min_mm": _round(pick("min_mm", min(values) if values else None), 3),
        "max_mm": _round(pick("max_mm", max(values) if values else None), 3),
        "mean_mm": _round(pick("mean_mm", (sum(values) / len(values)) if values else None), 3),
        "median_mm": _round(pick("median_mm", statistics.median(values) if values else None), 3),
        "count": count if count is not None else (len(values) if values else 0),
        "method": WALL_METHOD,
        "segments": _wall_segments_from_sections(sections),
    }


def _build_diameter(sections: list[dict]) -> dict:
    diameters: list[float] = []
    equivalents: list[float] = []
    for item in sections:
        equivalent = _num(item.get("equivalent_diameter"))
        if equivalent is None:
            perimeter = _num(item.get("perimeter"))
            equivalent = perimeter / math.pi if perimeter is not None and perimeter > 0 else None
        else:
            equivalents.append(equivalent)
        if equivalent is not None and equivalent > 0:
            diameters.append(equivalent)
    return {
        "max_mm": _round(max(diameters), 3) if diameters else None,
        "min_mm": _round(min(diameters), 3) if diameters else None,
        "equivalent_max_mm": _round(max(equivalents), 3) if equivalents else None,
    }


def _build_sections(data: dict, sections: list[dict]) -> dict:
    quality = data.get("analysis_quality")
    quality = quality if isinstance(quality, dict) else {}
    step = _num(quality.get("station_step_mm"))
    return {
        "count": len(sections),
        "step_mm": _round(step, 3) if step is not None else 1.0,
    }


# ---------------------------------------------------------------------------
# 设计记录
# ---------------------------------------------------------------------------


def _params_from_design(design: dict) -> dict:
    """设计记录 → 设计参数字典（params / params_json 字符串 / scalars 列表都认）。"""
    params = design.get("params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (ValueError, TypeError):
            params = None
    if isinstance(params, dict) and params:
        return dict(params)

    raw_json = design.get("params_json")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            decoded = json.loads(raw_json)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, dict) and decoded:
            return decoded

    scalars = design.get("scalars")
    if isinstance(scalars, list) and scalars:
        converted: dict = {}
        for row in scalars:
            row = _as_dict(row)
            key = row.get("key")
            if key is not None:
                converted[str(key)] = row.get("value")
        if converted:
            return converted
    return {}


def _build_design(design: Any) -> dict:
    record = _as_dict(design)
    if not record:
        return {
            "id": None,
            "display_name": "",
            "pp_method": "",
            "fitness": None,
            "updated_at": "",
            "params": {},
            "steps": [],
        }
    steps = record.get("steps")
    steps = list(steps) if isinstance(steps, (list, tuple)) else []
    updated = record.get("updated_at") or record.get("created_at") or ""
    return {
        "id": record.get("id"),
        "display_name": str(record.get("display_name") or ""),
        "pp_method": str(record.get("pp_method") or ""),
        "fitness": _num(record.get("fitness")),
        "updated_at": str(updated or ""),
        "params": _safe(_params_from_design(record)),
        "steps": _safe(steps),
    }


# ---------------------------------------------------------------------------
# 抽管道次（只从设计参数读，缺就跳过，不造数）
# ---------------------------------------------------------------------------


def _pass_entry(no: int, *, d_in: Any, d_out: Any, t_in: Any, t_out: Any, length: Any) -> dict:
    return {
        "no": int(no),
        "d_in": _round(d_in, 3),
        "d_out": _round(d_out, 3),
        "t_in": _round(t_in, 3),
        "t_out": _round(t_out, 3),
        "length": _round(length, 3),
    }


def _wall_segments(params: dict) -> list[dict]:
    """设计参数里的壁厚分段：TL1..TL5 平台 + TLM1..TLM4 过渡（长短都读）。"""
    segments: list[dict] = []
    for index in range(1, 6):
        length = _p(params, f"TL{index}")
        if length is not None:
            thickness = _p(params, f"T{index}")
            segments.append(
                {"t_in": thickness, "t_out": thickness, "length": length, "kind": "flat"}
            )
        if index <= 4:
            transition = _p(params, f"TLM{index}")
            if transition is not None:
                segments.append(
                    {
                        "t_in": _p(params, f"T{index}"),
                        "t_out": _p(params, f"T{index + 1}"),
                        "length": transition,
                        "kind": "transition",
                    }
                )
    return segments


def _normal_passes(params: dict) -> list[dict]:
    """普通抽：D0/T0/L0 → D1/T1/L1 → D2/T2/L2 → D3/T3/L3，最多 3 道次。"""
    stages = (
        ("D0", "T0", "L0"),
        ("D1", "T1", "L1"),
        ("D2", "T2", "L2"),
        ("D3", "T3", "L3"),
    )
    passes: list[dict] = []
    for index in range(1, len(stages)):
        d_in = _p(params, stages[index - 1][0])
        t_in = _p(params, stages[index - 1][1])
        d_out = _p(params, stages[index][0])
        t_out = _p(params, stages[index][1])
        if d_in is None or d_out is None:
            continue
        passes.append(
            _pass_entry(
                len(passes) + 1,
                d_in=d_in,
                d_out=d_out,
                t_in=t_in,
                t_out=t_out,
                length=_p(params, stages[index][2]),
            )
        )
    return passes


def _tp_passes(params: dict) -> list[dict]:
    """TP抽：首道次素材→Dn（壁厚优先 Tn，缺失取 T1），其余为 Dn 段的 TL/TLM 分段。"""
    dn = _p(params, "Dn")
    t_out = _p(params, "Tn")
    if t_out is None:
        t_out = _p(params, "T1")
    passes: list[dict] = []
    if dn is not None:
        passes.append(
            _pass_entry(
                1,
                d_in=_p(params, "D0"),
                d_out=dn,
                t_in=_p(params, "T0"),
                t_out=t_out,
                length=_p(params, "Ln"),
            )
        )
    for segment in _wall_segments(params):
        passes.append(
            _pass_entry(
                len(passes) + 1,
                d_in=dn,
                d_out=dn,
                t_in=segment.get("t_in"),
                t_out=segment.get("t_out"),
                length=segment.get("length"),
            )
        )
    return passes


def _taper_passes(params: dict) -> list[dict]:
    """Taper：首道次按 D2→D3 / L3 全长抽管，其余为壁厚 TL/TLM 分段（直径留空）。"""
    passes: list[dict] = []
    d2 = _p(params, "D2")
    d3 = _p(params, "D3")
    if d2 is not None and d3 is not None:
        passes.append(
            _pass_entry(
                1, d_in=d2, d_out=d3, t_in=None, t_out=None, length=_p(params, "L3")
            )
        )
    for segment in _wall_segments(params):
        passes.append(
            _pass_entry(
                len(passes) + 1,
                d_in=None,
                d_out=None,
                t_in=segment.get("t_in"),
                t_out=segment.get("t_out"),
                length=segment.get("length"),
            )
        )
    return passes


def _build_drawing(params: Any) -> dict:
    params = params if isinstance(params, dict) else {}
    normal = _normal_passes(params)
    tp = _tp_passes(params)
    taper = _taper_passes(params)
    if not (normal or tp or taper):
        note = _EMPTY_DRAWING_NOTE if not params else (
            _EMPTY_DRAWING_NOTE
            if not any(_p(params, key) is not None for key in ("D0", "Dn", "T1", "TL1", "TLM1"))
            else "设计参数不完整，三类抽管均未推导出可用道次。"
        )
    else:
        note = (
            "由设计参数推导：普通抽 D0/T0/L0→D1..D3/T1..T3/L1..L3；"
            "TP抽 Dn/Tn/Ln + TL/TLM 壁厚分段（直径按 TP 段大径 Dn）；"
            "Taper D2→D3/L3 全长道次 + TL/TLM 壁厚分段。"
            "缺失字段留空（—），未补造数值。"
        )
    return {
        "normal": {"label": _DRAWING_LABELS["normal"], "passes": normal},
        "tp": {"label": _DRAWING_LABELS["tp"], "passes": tp},
        "taper": {"label": _DRAWING_LABELS["taper"], "passes": taper},
        "note": note,
    }


# ---------------------------------------------------------------------------
# 路线推荐
# ---------------------------------------------------------------------------


def _empty_route(reason: str = "") -> dict:
    return {"recommended": "", "reason": reason, "fitness": None, "options": []}


def _normalise_route(plan: Any) -> dict:
    """规整外部传入的 route_plan（``_route_plan_for`` / ``recommend_pp_method`` 都认）。"""
    plan = _as_dict(plan)
    if not plan:
        return _empty_route()

    raw_options = plan.get("options")
    if not isinstance(raw_options, list):
        raw_options = plan.get("all_options")
    if not isinstance(raw_options, list):
        raw_options = []
        groups = plan.get("groups")
        if isinstance(groups, list):
            for group in groups:
                group = _as_dict(group)
                items = group.get("items")
                if isinstance(items, list):
                    raw_options.extend(items)

    label_by_method: dict[str, str] = {}
    options: list[dict] = []
    for item in raw_options or []:
        item = _as_dict(item)
        if not item:
            continue
        method = item.get("method")
        label = item.get("label") or method or ""
        if method and label:
            label_by_method.setdefault(str(method), str(label))
        feasible = item.get("feasible")
        options.append(
            {
                "label": str(label),
                "feasible": None if feasible is None else bool(feasible),
                "fitness": _num(item.get("fitness")),
                "reason": str(item.get("reason") or ""),
            }
        )

    recommended = (
        plan.get("recommended_label")
        or plan.get("recommended")
        or plan.get("chosen")
        or ""
    )
    recommended = str(recommended)
    recommended = label_by_method.get(recommended, recommended)
    return {
        "recommended": recommended,
        "reason": str(plan.get("reason") or ""),
        "fitness": _num(plan.get("fitness")),
        "options": options,
    }


def _recommend_pp_method():
    """延迟导入 ``core.pp.optimization``（那里要 numpy/pandas，别拖累本模块 import）。"""
    from core.pp.optimization import recommend_pp_method  # noqa: WPS433

    return recommend_pp_method


def _build_route(
    data: dict,
    diameter: dict,
    params: Any,
    route_plan: Any,
    centerline: dict,
) -> dict:
    if isinstance(route_plan, dict) and route_plan:
        return _normalise_route(route_plan)

    length = _num(data.get("center_line_length"))
    if length is None:
        length = _num(centerline.get("length_mm"))
    max_d = _num(diameter.get("max_mm"))
    min_d = _num(diameter.get("min_mm"))
    missing_length = length is None
    missing_diameter = (
        max_d is None or min_d is None or max_d <= 0 or min_d <= 0
    )
    if missing_length and missing_diameter:
        return _empty_route("缺少实测数据（中心线长度 / 外径），未做路线推荐")
    if missing_diameter:
        return _empty_route(
            "缺少外径实测数据（截面 perimeter / equivalent_diameter），未做路线推荐"
        )
    if missing_length:
        return _empty_route("缺少中心线长度实测数据，未做路线推荐")

    l4 = _p(params if isinstance(params, dict) else {}, "L4")
    try:
        recommend = _recommend_pp_method()
    except Exception as exc:  # noqa: BLE001 - 推荐模块不可用不影响摘要
        return _empty_route(f"路线推荐不可用：{type(exc).__name__}: {exc}")
    try:
        rec = recommend(float(length or 0.0), float(max_d or 0.0), float(min_d or 0.0), float(l4 or 0.0))
    except Exception as exc:  # noqa: BLE001
        return _empty_route(f"路线推荐失败：{type(exc).__name__}: {exc}")
    return _normalise_route(rec if isinstance(rec, dict) else {})


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def _empty_summary(part: str, measured_path: Any) -> dict:
    return {
        "part": str(part or ""),
        "generated_at": _now_iso(),
        "centerline": {
            "length_mm": None,
            "points": [],
            "point_count": 0,
            "method": CENTERLINE_METHOD,
        },
        "wall": {
            "min_mm": None,
            "max_mm": None,
            "mean_mm": None,
            "median_mm": None,
            "count": 0,
            "method": WALL_METHOD,
            "segments": [],
        },
        "diameter": {"max_mm": None, "min_mm": None, "equivalent_max_mm": None},
        "sections": {"count": 0, "step_mm": 1.0},
        "route": _empty_route(),
        "design": {
            "id": None,
            "display_name": "",
            "pp_method": "",
            "fitness": None,
            "updated_at": "",
            "params": {},
            "steps": [],
        },
        "drawing": {
            "normal": {"label": _DRAWING_LABELS["normal"], "passes": []},
            "tp": {"label": _DRAWING_LABELS["tp"], "passes": []},
            "taper": {"label": _DRAWING_LABELS["taper"], "passes": []},
            "note": _EMPTY_DRAWING_NOTE,
        },
        "sources": {"measured_path": str(measured_path or ""), "design_id": None},
    }


def build_process_summary(
    *,
    part: str,
    measured_path: Any,
    design: Optional[dict] = None,
    route_plan: Optional[dict] = None,
    out_root: Any = None,
    max_points: int = 80,
) -> dict:
    """构造工艺摘要（结构见模块文档 / 方案 §4.6）。**任何异常都不外抛。**"""
    part = str(part or "")
    summary = _empty_summary(part, measured_path)
    try:
        data, path_used, error = _load_measured(part, measured_path, out_root)
        if error:
            summary["error"] = error
        sections = _sections(data)
        summary["centerline"] = _build_centerline(data, sections, max_points)
        summary["wall"] = _build_wall(data, sections)
        summary["diameter"] = _build_diameter(sections)
        summary["sections"] = _build_sections(data, sections)
        summary["design"] = _build_design(design)
        params = summary["design"].get("params") if isinstance(summary["design"], dict) else {}
        summary["drawing"] = _build_drawing(params)
        summary["route"] = _build_route(
            data, summary["diameter"], params, route_plan, summary["centerline"]
        )
        summary["sources"] = {
            "measured_path": str(path_used or measured_path or ""),
            "design_id": summary["design"].get("id") if isinstance(summary["design"], dict) else None,
        }
    except Exception as exc:  # noqa: BLE001 - 冻结接口要求绝不抛异常
        summary["error"] = f"{type(exc).__name__}: {exc}"
    return _safe(summary)


# ---------------------------------------------------------------------------
# Markdown（report.py 与 write_summary_markdown 共用同一份）
# ---------------------------------------------------------------------------


def _fmt(value: Any, digits: int = 2) -> str:
    number = _num(value)
    return "—" if number is None else f"{number:.{digits}f}"


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\r\n", " ").replace("\n", " ").replace("|", "\\|").strip()


def _md_table(headers: list, rows: list[list]) -> list[str]:
    heads = [_cell(header) for header in headers]
    lines = [
        "| " + " | ".join(heads) + " |",
        "| " + " | ".join("---" for _ in heads) + " |",
    ]
    for row in rows:
        cells = [_cell(item) for item in row]
        if len(cells) < len(heads):
            cells.extend([""] * (len(heads) - len(cells)))
        lines.append("| " + " | ".join(cells[: len(heads)]) + " |")
    return lines


def _pass_rows(passes: list[dict]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in passes:
        item = item if isinstance(item, dict) else {}
        rows.append(
            [
                _cell(item.get("no")),
                _fmt(item.get("d_in"), 2),
                _fmt(item.get("d_out"), 2),
                _fmt(item.get("t_in"), 3),
                _fmt(item.get("t_out"), 3),
                _fmt(item.get("length"), 2),
            ]
        )
    return rows


def summary_markdown(
    summary: Any,
    *,
    heading: str = "## 工艺摘要",
    part: Any = None,
    max_wall_segments: int = 12,
) -> str:
    """工艺摘要 Markdown（报告直接追加，独立落盘也用它）。"""
    summary = _as_dict(summary) or _empty_summary(str(part or ""), "")
    part = part or summary.get("part") or ""
    centerline = _as_dict(summary.get("centerline"))
    wall = _as_dict(summary.get("wall"))
    diameter = _as_dict(summary.get("diameter"))
    sections = _as_dict(summary.get("sections"))
    route = _as_dict(summary.get("route"))
    design = _as_dict(summary.get("design"))
    drawing = _as_dict(summary.get("drawing"))

    lines: list[str] = [heading, ""]
    lines.append(f"- 件号：{part or '—'} · 生成时间：{summary.get('generated_at') or '—'}")
    lines.append(
        f"- 中心线长度：{_fmt(centerline.get('length_mm'), 2)} mm"
        f"（{_int(centerline.get('point_count')) or 0} 个原始站点，"
        f"降采样后 {len(centerline.get('points') or [])} 点）"
    )
    lines.append(
        "- 壁厚：min {min} / mean {mean} / median {median} / max {max} mm".format(
            min=_fmt(wall.get("min_mm"), 3),
            mean=_fmt(wall.get("mean_mm"), 3),
            median=_fmt(wall.get("median_mm"), 3),
            max=_fmt(wall.get("max_mm"), 3),
        )
    )
    lines.append(
        f"- 外径：max {_fmt(diameter.get('max_mm'))} / min {_fmt(diameter.get('min_mm'))} mm"
        f"（等效最大 {_fmt(diameter.get('equivalent_max_mm'))}）"
    )
    lines.append(
        f"- 截面：{_int(sections.get('count')) or 0} 个站位 · "
        f"站位间距 {_fmt(sections.get('step_mm'), 2)} mm"
    )
    recommended = str(route.get("recommended") or "").strip()
    reason = str(route.get("reason") or "").strip()
    lines.append(
        f"- 推荐路线：{recommended or '—'}"
        + (f"（fitness {_fmt(route.get('fitness'), 3)}）" if _num(route.get("fitness")) is not None else "")
        + (f" — {reason}" if reason else "")
    )
    design_bits: list[str] = []
    if design.get("id") is not None:
        design_bits.append(f"#{design.get('id')}")
    if design.get("display_name"):
        design_bits.append(str(design.get("display_name")))
    if design.get("pp_method"):
        design_bits.append(str(design.get("pp_method")))
    if _num(design.get("fitness")) is not None:
        design_bits.append(f"fitness {_fmt(design.get('fitness'), 3)}")
    lines.append(f"- 设计记录：{' · '.join(design_bits) if design_bits else '无'}")
    if summary.get("error"):
        lines.append(f"- 数据告警：{_cell(summary.get('error'))}")
    lines.append("")

    segments = wall.get("segments") or []
    if segments:
        lines.append(f"### 壁厚分段（{len(segments)} 段）")
        lines.append("")
        shown = segments[: max(1, _int(max_wall_segments) or 12)]
        rows = [
            [
                _fmt(_as_dict(item).get("start_mm")),
                _fmt(_as_dict(item).get("end_mm")),
                _fmt(_as_dict(item).get("thickness_mm"), 3),
            ]
            for item in shown
        ]
        lines.extend(_md_table(["起点 mm", "终点 mm", "壁厚 mm"], rows))
        if len(segments) > len(shown):
            lines.append("")
            lines.append(f"_其余 {len(segments) - len(shown)} 段略，完整数据见 JSON。_")
        lines.append("")

    lines.append("### 抽管道次")
    lines.append("")
    for key, fallback in (("normal", "普通抽"), ("tp", "TP抽"), ("taper", "Taper")):
        block = _as_dict(drawing.get(key))
        label = str(block.get("label") or fallback)
        lines.append(f"#### {label}")
        lines.append("")
        passes = block.get("passes")
        passes = passes if isinstance(passes, list) else []
        if not passes:
            lines.append("无")
        else:
            lines.extend(
                _md_table(
                    ["道次", "入口外径 mm", "出口外径 mm", "入口壁厚 mm", "出口壁厚 mm", "长度 mm"],
                    _pass_rows(passes),
                )
            )
        lines.append("")
    note = str(drawing.get("note") or "").strip()
    if note:
        lines.append(f"_注：{_cell(note)}_")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_summary_markdown(part: str, summary: Any, path: Any) -> str:
    """把工艺摘要写成 UTF-8（无 BOM，``\\n`` 换行）Markdown，返回落盘路径。"""
    text = summary_markdown(summary, heading="## 工艺摘要", part=part)
    target = Path(str(path))
    if str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return str(target)


def attach_summary(report: Any, summary: Any) -> dict:
    """把摘要挂到报告 dict 上（返回新 dict；``PartReport`` 的 ``to_dict()`` 也认）。"""
    data: dict = {}
    if isinstance(report, dict):
        data = dict(report)
    elif report is not None and callable(getattr(report, "to_dict", None)):
        try:
            converted = report.to_dict()
        except Exception:  # noqa: BLE001 - 摘要挂载不能因为报告对象炸掉
            converted = None
        if isinstance(converted, dict):
            data = dict(converted)
    elif report is not None:
        try:
            data = dict(report)
        except Exception:  # noqa: BLE001
            data = {}
    if isinstance(summary, dict):
        data["process_summary"] = _safe(summary)
    return data
