"""实体化流水线 · 上游导出适配层（S1）。

把 Giant 既有的两段上游能力收进流水线，并**去掉反切模具**：

* :func:`measure`        STP → 截面测量（``core.pp.tube.analyze_tube``，算法 A，
  ``sparse_step_mm=25.0``，端部口径 ``{"shape_extrapolation": "c1"|"v1"}``）。结果带缓存。
* :func:`export_models`  截面 → :data:`core.entity.contracts.MODELS` 的 5 类模型 STEP。

导出逻辑是 ``tools/rfm_export_all/export_all_models.py`` 的**复制**（``build_rows`` /
``perimeter_matched_axes`` / ``make_processor`` 三条逻辑逐行搬进来），刻意**不 import 那个脚本**：
它是批处理工具，不是库，且含已下线的 mold 分支。与本模块的差异只有一条——
逐模型独立 try/except，并删掉 ``cut_mold`` / ``*_反切模具.stp``。

规则（见 ``ENTITY_PIPELINE.md`` §12）：

* 模块顶层只 import 标准库与 ``contracts`` / ``settings`` —— 契约要求 import 时不得启动线程、
  不得读大文件、不得调 FreeCAD；numpy / scipy / OCP / processors 全部在函数内部延迟 import。
* 绝不产出反切模具；本模块的模型集合恒等于 ``contracts.MODEL_KEYS``（6 个）。
* 单类失败只记 ``{"status": "skipped", "reason": ...}`` 并继续，绝不整体抛异常。

``measure`` 的返回值是 :class:`MeasureResult`：既是 ``{"analysis": ..., "meta": ...}`` 的 dict，
又提供 ``contracts.Measured`` 的属性（``.sections_full`` / ``.analysis`` / ``.path`` / ...），
两种调用约定都能用；见 :func:`to_measured`。
"""
from __future__ import annotations

import gc
import json
import math
import os
import time
from typing import Any, Callable, Iterable, Optional

from . import contracts, settings

__all__ = [
    "MeasureResult",
    "build_rows",
    "export_models",
    "make_processor",
    "measure",
    "perimeter_matched_axes",
    "step_paths",
    "to_measured",
]

#: ``measure()`` 的默认算法与站位步长（对齐 ``tools/rfm_outer_cavity/run_outer_cavity.py``）
DEFAULT_ALGORITHM = "A"
SPARSE_STEP_MM = 25.0

#: ``export_models`` 的文件命名：``slug``（默认，契约变量）或 ``key``（中文键，见 ENTITY_PIPELINE §2）
NAMINGS = ("slug", "key")

#: 允许出现在 ``_measured.json`` 里、但**不**是截面列表的顶层键（用于宽松识别）
_ANALYSIS_KEYS = ("sections_full", "sections", "center_line_length", "analysis_quality")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _emit(log: Optional[Callable[[str], None]], message: str) -> None:
    if log is not None:
        try:
            log(message)
        except Exception:  # 日志永不打断几何
            pass


def _f(value: Any, default: float = 0.0) -> float:
    """尽量转 float，失败给 default（`_measured.json` 里可能存在 null）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _looks_like_path(text: Any) -> bool:
    """区分「件号」和「文件路径」（兼容契约的 measure(part, src_step, ...) 调用顺序）。"""
    if not isinstance(text, str):
        return False
    low = text.lower()
    if os.sep in text or "/" in text or "\\" in text:
        return True
    return low.endswith((".stp", ".step"))


class MeasureResult(dict):
    """``{"analysis": dict, "meta": dict, ...}``，同时可当 ``contracts.Measured`` 用。

    dict 侧是本模块对外的定稿结构；属性侧（``sections_full`` / ``path`` / ``cached`` ...）
    让按 ``ENTITY_PIPELINE.md`` §9 契约写的调用方（``orchestrator`` / ``routes/*``）无需改动。
    """

    @property
    def part(self) -> str:
        return self["part"]

    @property
    def path(self) -> str:
        return self["path"]

    @property
    def end_treatment(self) -> str:
        return self["end_treatment"]

    @property
    def seconds(self) -> float:
        return self["seconds"]

    @property
    def cached(self) -> bool:
        return bool(self["cached"])

    @property
    def analysis(self) -> dict:
        return self["analysis"]

    @property
    def meta(self) -> dict:
        return self["meta"]

    @property
    def sections_full(self) -> list:
        """1 mm 站位截面（含 position / outer_2d / inner_2d / basis_u / basis_v / perimeter）。"""
        return self["analysis"].get("sections_full") or []

    def to_measured(self) -> contracts.Measured:
        """转成契约 dataclass（要让 ``dataclasses.asdict`` / ``json.dumps`` 报告用时）。"""
        return contracts.Measured(
            part=self["part"],
            path=self["path"],
            sections_full=self.sections_full,
            analysis=self["analysis"],
            end_treatment=self["end_treatment"],
            seconds=float(self["seconds"]),
            cached=bool(self["cached"]),
        )

    # 契约里叫 Measured；同一个对象两种叫法都可用
    to_contract = to_measured


def to_measured(result: Any) -> contracts.Measured:
    """任何 ``measure`` 结果（dict 或 MeasureResult）→ ``contracts.Measured``。"""
    if isinstance(result, contracts.Measured):
        return result
    if isinstance(result, MeasureResult):
        return result.to_measured()
    analysis = _analysis_dict(result)
    return contracts.Measured(
        part=str((result or {}).get("part") or "") if isinstance(result, dict) else "",
        path=str((result or {}).get("path") or "") if isinstance(result, dict) else "",
        sections_full=analysis.get("sections_full") or [],
        analysis=analysis,
        end_treatment=str((result or {}).get("end_treatment") or contracts.DEFAULT_END_TREATMENT)
        if isinstance(result, dict)
        else contracts.DEFAULT_END_TREATMENT,
        seconds=_f((result or {}).get("seconds")) if isinstance(result, dict) else 0.0,
        cached=bool((result or {}).get("cached")) if isinstance(result, dict) else False,
    )


def _analysis_dict(analysis: Any) -> dict:
    """把 ``measure`` 结果 / 缓存路径 / Measured / 裸 analysis 统一成裸 analysis dict。"""
    if isinstance(analysis, (str, os.PathLike)):
        analysis = _read_cache(os.fspath(analysis))
    if isinstance(analysis, contracts.Measured):
        return analysis.analysis or {}
    if isinstance(analysis, dict):
        nested = analysis.get("analysis")
        if isinstance(nested, dict) and (
            "sections_full" in nested or not any(k in analysis for k in _ANALYSIS_KEYS)
        ):
            return nested
        return analysis
    # 鸭子类型：带 sections_full / analysis 属性的对象
    nested = getattr(analysis, "analysis", None)
    if isinstance(nested, dict):
        return nested
    sections = getattr(analysis, "sections_full", None)
    if sections is not None:
        return {"sections_full": sections}
    raise TypeError(f"无法识别的测量结果类型：{type(analysis).__name__}")


# ---------------------------------------------------------------------------
# S0 · 测量
# ---------------------------------------------------------------------------


def _meta_from_analysis(analysis: dict, end_treatment: str) -> dict:
    """对齐 ``tools/rfm_outer_cavity/run_outer_cavity.py`` 的 ``record["measurement"]``。"""
    sections = analysis.get("sections_full") or []
    quality = analysis.get("analysis_quality") or {}
    wall = analysis.get("wall_thickness_stats") or {}
    meta = {
        "engine": quality.get("engine"),
        "center_line_length_mm": round(_f(analysis.get("center_line_length")), 3),
        "stations": len(sections),
        # 与 run_outer_cavity 一致：外推站位不算「实测」
        "measured_stations": sum(
            1 for s in sections if (s or {}).get("measurement_status") != "extrapolated"
        ),
        "station_step_mm": quality.get("station_step_mm"),
        "max_normal_deviation_deg": quality.get("max_normal_deviation_deg"),
        "max_centroid_residual_mm": quality.get("max_centroid_residual_mm"),
        "rejected_sections": len(quality.get("rejected_sections") or []),
        "review_station_count": quality.get("review_station_count"),
        "extrapolated_end_lengths_mm": quality.get("extrapolated_end_lengths_mm"),
        "wall_thickness_median_mm": wall.get("median_mm"),
        "warnings": list(quality.get("warnings") or []),
        "end_treatment": end_treatment,
    }
    return contracts.json_safe(meta)


def _read_cache(cache_path: str) -> dict:
    """读 ``*_measured.json``（67 MB 量级，读法直接影响 --resume 的启动开销）。

    二进制一次性读入 + 解析期间临时关掉分代 GC：该 JSON 含约 500 万个 float/list 对象，
    解析中途触发的回收白费时间，实测 1.9 s → 1.5 s。
    """
    with open(cache_path, "rb") as stream:
        data = stream.read()
    enabled = gc.isenabled()
    gc.disable()
    try:
        return json.loads(data)
    finally:
        if enabled:
            gc.enable()


def _write_cache(cache_path: str, analysis: dict) -> None:
    parent = os.path.dirname(os.path.abspath(cache_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = cache_path + ".part"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(analysis, stream, ensure_ascii=False)
    os.replace(tmp, cache_path)  # 半截 JSON 永不会被当成缓存命中


def derive_analysis(analysis: dict, end_treatment: str, log: Any = None) -> dict:
    """用已有测量结果重算端部，得到另一种口径（不重新解析 STEP）。

    ``c1`` 与 ``v1`` 共用同一批实测截面与同一条站点网格，唯一差别在
    :func:`core.pp.tube.end_treatment.complete_ends` 的 ``shape_extrapolation``
    分支：c1 用实测形状梯度外推端部轮廓，v1 用最近可靠截面按周长/壁厚线性缩放。
    因此拿另一口径的 ``sections_full`` 直接重跑 ``complete_ends`` 即可，代价约
    10 s（实测 633 站位 12 s），而不是重新测一遍（同件 15–58 min）。

    ``sections_full`` 里两端的外推站位会被重新计算；中间实测站位原样保留。
    端面平面直接取首/末站位的 position + normal —— 它们本来就落在端面上。
    """
    treatment = str(end_treatment or "").lower()
    if treatment not in contracts.END_TREATMENTS:
        raise ValueError(
            f"未知端部口径：{end_treatment}（可选：{', '.join(contracts.END_TREATMENTS)}）"
        )
    sections = analysis.get("sections_full") or []
    if len(sections) < 6:
        raise ValueError(f"截面不足（{len(sections)} 站），无法重算端部")

    quality = dict(analysis.get("analysis_quality") or {})
    source_meta = dict(quality.get("end_treatment") or {})
    options = dict(source_meta.get("options") or {})
    source_treatment = str(options.get("shape_extrapolation") or contracts.DEFAULT_END_TREATMENT)
    options["shape_extrapolation"] = treatment  # 只换端部外推方式，其余（舍弃/窗口）沿用

    # 延迟 import：契约层不碰 OCP/几何依赖
    from core.pp.tube.end_treatment import complete_ends, end_options

    planes = [
        {"point": sections[0]["position"], "normal": sections[0]["normal"]},
        {"point": sections[-1]["position"], "normal": sections[-1]["normal"]},
    ]
    rows, meta = complete_ends(sections, planes, end_options(options))
    if len(rows) != len(sections):
        raise ValueError(f"端部重算改变了站点数（{len(sections)} → {len(rows)}），拒绝写入")
    stations = [float(row["cumulative_length"]) for row in rows]
    if any(b <= a for a, b in zip(stations, stations[1:])):
        raise ValueError("端部重算后站点坐标不单调，拒绝写入")

    meta["derived_from"] = source_treatment  # 记录来源口径（c1 / v1）
    derived = dict(analysis)
    derived["sections_full"] = rows
    quality["end_treatment"] = contracts.json_safe(meta)
    quality["extrapolated_station_count"] = int(meta.get("extrapolated_station_count") or 0)
    derived["analysis_quality"] = quality
    derived["center_line_length"] = float(stations[-1])
    _emit(
        log,
        f"端部重算：{len(rows)} 站位，外推 {meta.get('extrapolated_station_count')} 站"
        f"（{meta.get('contour_policy')}）",
    )
    return derived


def measure(
    step_path: Optional[str] = None,
    *legacy: Any,
    part: Optional[str] = None,
    end_treatment: Optional[str] = None,
    cache_path: Optional[str] = None,
    force: bool = False,
    algorithm: str = DEFAULT_ALGORITHM,
    log: Optional[Callable[[str], None]] = None,
    src_step: Optional[str] = None,
) -> MeasureResult:
    """测量一个原始管件的截面（带 JSON 缓存）。

    参数
    ----
    step_path
        原始管件 ``.stp`` 路径（本模块定稿签名）。兼容契约写法
        ``measure(part, src_step, end_treatment, cache_path)``：第一个位置参数是件号时，
        后面 0~3 个位置参数依次是 ``src_step`` / ``end_treatment`` / ``cache_path``。
    part
        件号（缺省取文件名 stem）。
    end_treatment
        ``"c1"``（默认，形状外推）或 ``"v1"``（线性外推）。
    cache_path
        缓存 JSON 路径；缺省 ``settings.measured_path(part, end_treatment)``。
    force
        True 时忽略缓存重测。
    algorithm
        ``"A"`` / ``"B"``，透传 ``analyze_tube``。

    返回
    ----
    :class:`MeasureResult`：``{"analysis": dict, "meta": {...}}``，另带
    ``part`` / ``path`` / ``end_treatment`` / ``cached`` / ``seconds``。
    """
    # --- 兼容契约 measure(part, src_step, end_treatment, cache_path) 的位置参数顺序
    if legacy:
        if len(legacy) > 3:
            raise TypeError("measure() 最多接受 4 个位置参数（part, src_step, end_treatment, cache_path）")
        part, step_path = step_path, legacy[0]
        if len(legacy) > 1:
            end_treatment = legacy[1]
        if len(legacy) > 2:
            cache_path = legacy[2]
    if src_step:
        if part is None and step_path is not None and not _looks_like_path(step_path):
            part, step_path = step_path, src_step
        else:
            step_path = src_step
    if not step_path:
        raise TypeError("measure() 缺少 step_path（原始管件 .stp 路径）")

    step_path = os.path.abspath(os.fspath(step_path))
    part = str(part or os.path.splitext(os.path.basename(step_path))[0])
    end_treatment = (end_treatment or contracts.end_treatment_for(part)).lower()
    if end_treatment not in contracts.END_TREATMENTS:
        raise ValueError(
            f"未知端部口径：{end_treatment}（可选：{', '.join(contracts.END_TREATMENTS)}）"
        )
    algorithm = (algorithm or DEFAULT_ALGORITHM).upper()
    if cache_path is None:
        cache_path = str(settings.measured_path(part, end_treatment))
    cache_path = os.path.abspath(os.fspath(cache_path))

    started = time.monotonic()
    # --- 缓存命中：直接读 JSON，不再碰 OCP
    if os.path.isfile(cache_path) and not force:
        analysis = _read_cache(cache_path)
        seconds = time.monotonic() - started
        _emit(
            log,
            f"[{part}] 复用测量缓存 {os.path.basename(cache_path)}"
            f"（{len(analysis.get('sections_full') or [])} 个站位，{seconds:.2f} s）",
        )
        return MeasureResult(
            analysis=analysis,
            meta=_meta_from_analysis(analysis, end_treatment),
            part=part,
            path=cache_path,
            end_treatment=end_treatment,
            cached=True,
            seconds=round(seconds, 3),
        )

    # --- 另一口径已有缓存：只重算端部，不重新解析 STEP（15–58 min → 约 20 s）
    # ``force`` 只用来作废「目标口径已有的缓存」；目标缓存本来就不存在时，
    # 用户点「重新测量 v1」要的正是这份结果，派生比真测快两个数量级。
    other = "c1" if end_treatment == "v1" else "v1"
    other_path = os.path.abspath(os.fspath(settings.measured_path(part, other)))
    derive_allowed = not force or not os.path.isfile(cache_path)
    if derive_allowed and os.path.isfile(other_path):
        try:
            derived = derive_analysis(_read_cache(other_path), end_treatment, log=log)
            _write_cache(cache_path, derived)
            seconds = time.monotonic() - started
            meta = _meta_from_analysis(derived, end_treatment)
            _emit(
                log,
                f"[{part}] 复用 {other} 测量缓存重算端部 → {end_treatment}"
                f"（{meta['stations']} 个站位，{seconds:.1f} s，未重新测量）",
            )
            return MeasureResult(
                analysis=derived,
                meta=meta,
                part=part,
                path=cache_path,
                end_treatment=end_treatment,
                cached=False,
                seconds=round(seconds, 3),
            )
        except Exception as exc:  # noqa: BLE001 - 重算失败就退回去真测量
            _emit(
                log,
                f"[{part}] 端部重算失败（{type(exc).__name__}: {exc}），改为重新测量 {end_treatment}",
            )

    # --- 真测量：OCP 只在函数内部 import（OCP/FreeCAD 非线程安全，调用方必须串行）
    from core.pp.tube import analyze_tube

    if not os.path.isfile(step_path):
        raise FileNotFoundError(f"找不到原始管件：{step_path}")
    _emit(log, f"[{part}] 测量截面算法{algorithm}（端部口径 {end_treatment}）…")

    def _progress(pct: int, message: str) -> None:
        _emit(log, f"[{part}] {int(pct):3d}% {message}")

    analysis = analyze_tube(
        step_path,
        "stp",
        algorithm,
        sparse_step_mm=SPARSE_STEP_MM,
        end_treatment={"shape_extrapolation": end_treatment},
        progress_callback=_progress if log is not None else None,
    )
    if not isinstance(analysis, dict) or analysis.get("error"):
        raise RuntimeError((analysis or {}).get("error") or f"[{part}] STP 分析无结果")

    _write_cache(cache_path, analysis)
    seconds = time.monotonic() - started
    meta = _meta_from_analysis(analysis, end_treatment)
    _emit(
        log,
        f"[{part}] 测量完成：{meta['stations']} 个站位 / {meta['center_line_length_mm']:.1f} mm，"
        f"{seconds:.1f} s → {os.path.basename(cache_path)}",
    )
    return MeasureResult(
        analysis=analysis,
        meta=meta,
        part=part,
        path=cache_path,
        end_treatment=end_treatment,
        cached=False,
        seconds=round(seconds, 3),
    )


# ---------------------------------------------------------------------------
# S1 · 5 类模型导出（复制自 tools/rfm_export_all/export_all_models.py，去掉 mold）
# ---------------------------------------------------------------------------


def build_rows(sections: Iterable[dict]) -> list[dict]:
    """与 ``api/pp.py`` 交给 RFM 的记录同形（周长 + 轴长 + 壁厚 + 坐标）。"""
    from core.pp.profile_qa.key_section import xy_from_section_row

    rows = []
    for section in sections:
        entry = {
            "中心线长度(mm)": section.get("cumulative_length", 0),
            "外径周长(mm)": section.get("perimeter", 0),
            "壁厚(mm)": section.get("wall_thickness"),
            "measurement_status": section.get("measurement_status", "measured"),
        }
        size = xy_from_section_row(section)
        if size:
            entry["x_length"] = round(float(size[0]), 4)
            entry["y_length"] = round(float(size[1]), 4)
        if section.get("position") is not None:
            entry.update(zip(("x", "y", "z"), section["position"]))
        rows.append(entry)
    return rows


def perimeter_matched_axes(rows: list[dict]) -> list[dict]:
    """把前序轴长重建成「周长等于实测值」的椭圆（压扁模块的硬性要求）。

    压扁模块只接受「椭圆周长能复现工艺周长」的轴长。STP 原始截面通常不是椭圆
    （宽高极值算出的周长偏小），模块会拒绝实测轴长。这里保住工艺不变量——
    同一周长、同一实测长短轴比——并在报告里标注该推导。
    """
    from scipy.optimize import brentq

    from core.rfm.processors import ellipse_perimeter

    out = []
    for row in rows:
        new = dict(row)
        x, y = row.get("x_length"), row.get("y_length")
        perimeter = float(row["外径周长(mm)"])
        if not x or not y or x <= 0 or y <= 0:
            out.append(new)
            continue
        ratio = max(x, y) / min(x, y)
        # ellipse_perimeter 收半轴；工艺字段是全轴。
        b = brentq(
            lambda t: ellipse_perimeter(ratio * t, t) - perimeter,
            perimeter / (2 * math.pi * ratio),
            perimeter / 2,
        )
        a = ratio * b
        new["x_length"], new["y_length"] = (2 * a, 2 * b) if x >= y else (2 * b, 2 * a)
        out.append(new)
    return out


def make_processor(operation: str, sections: list[dict], rows: list[dict], length: float):
    """按 ``_build_processor_from_sess`` + GUI 默认值配置 processor。"""
    from core.rfm.processors import get_processor

    config: dict[str, Any] = {}
    if operation == "belling":
        config.update(
            belling_length=min(200.0, length),
            wall_thickness=2.0,
            wall_thickness_source="data",
            length_allowance=0.0,
        )
    elif operation in ("flattening", "bending"):
        flattening_length = min(200.0, length)
        config.update(
            flattening_length=flattening_length,
            transition_length=min(150.0, max(0.0, length - flattening_length)),
            total_length=length,
            flattening_allowance=0.0,
            num_sections=200,
        )
        if operation == "bending":
            config.update(num_sections=152, flattening_executed=False, transition_length=150.0)
    proc = get_processor(operation)
    proc.set_session_data([s["position"] for s in sections], rows)
    proc.set_sections(sections)
    for key, value in config.items():
        if hasattr(proc, key):
            setattr(proc, key, value)
    return proc


def _normalize_models(models: Optional[Iterable[str]]) -> list[str]:
    """模型键 / slug / 中文名 → 注册表顺序的键列表。"""
    if not models:
        return list(contracts.MODEL_KEYS)
    alias: dict[str, str] = {}
    for model in contracts.MODELS:
        alias[model.key] = model.key
        alias[model.slug] = model.key
        alias[model.name] = model.key
    wanted = set()
    for raw in models:
        text = str(raw)
        if text not in alias:
            raise KeyError(
                f"未知模型：{text}（可选：{', '.join(contracts.MODEL_KEYS)}，"
                f"或 slug：{', '.join(m.slug for m in contracts.MODELS)}）"
            )
        wanted.add(alias[text])
    return [key for key in contracts.MODEL_KEYS if key in wanted]


def _topology(shape) -> dict:
    """面 / 边 / 实体计数（只读遍历，不改几何）。"""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    counts = {}
    for name, kind in (("faces", TopAbs_FACE), ("edges", TopAbs_EDGE), ("solids", TopAbs_SOLID)):
        explorer = TopExp_Explorer(shape, kind)
        total = 0
        while explorer.More():
            total += 1
            explorer.Next()
        counts[name] = total
    return counts


def _write_step(shape, path: str) -> dict:
    """写 STEP 并回读校验（体积一致 → 单实体 → 面/边计数）。"""
    from core.common.solid_geometry import bounds, step_bytes, valid_solid, volume
    from core.pp.tube.brep_sections import read_shape

    data = step_bytes(shape)  # 内部先 valid_solid(shape)
    with open(path, "wb") as stream:
        stream.write(data)
    reread = valid_solid(read_shape(path))  # read_shape 要求恰好 1 个实体
    written = volume(shape)
    measured = volume(reread)
    if abs(measured - written) > max(0.01, abs(written) * 1e-6):
        raise RuntimeError(
            f"{os.path.basename(path)} 回读体积不一致：{written:.3f} → {measured:.3f} mm³"
        )
    low, high = bounds(reread)
    record = {
        "step": path,
        "volume_mm3": round(float(measured), 3),
        "bytes": len(data),
        "bbox": [
            [round(float(v), 3) for v in low],
            [round(float(v), 3) for v in high],
        ],
        "valid": True,
    }
    record.update(_topology(reread))
    return record


def export_models(
    part: str,
    analysis: Any,
    out_dir: Any,
    models: Optional[Iterable[str]] = None,
    *,
    log: Optional[Callable[[str], None]] = None,
    naming: str = "slug",
) -> dict[str, dict]:
    """把测量截面导出成 5 类模型 STEP（不含任何反切模具）。

    参数
    ----
    part
        件号（只用于日志）。
    analysis
        ``measure()`` 的返回值、缓存 JSON 路径、``contracts.Measured`` 或裸 analysis dict。
    out_dir
        STEP 输出目录（不存在则建）。文件名为 ``<ModelDef.slug>.stp``（``naming="key"``
        时改用中文键 ``<key>.stp``，对齐 ``ENTITY_PIPELINE.md`` §2 的 ``00_models``）。
    models
        只导出指定模型（键 / slug / 中文名均可）；缺省 5 类全出。

    返回
    ----
    ``{key: {"status": "ok", "step": ..., "volume_mm3": .., "faces": .., "edges": ..,
    "seconds": .., "bytes": .., "solids": .., "bbox": ..}}``；
    数据缺失或几何失败时该键为 ``{"status": "skipped", "reason": ...}``，其余照常。
    只要 ``{key: path}`` 的扁平表用 :func:`step_paths`。
    """
    if naming not in NAMINGS:
        raise ValueError(f"未知命名方式：{naming}（可选：{', '.join(NAMINGS)}）")
    keys = _normalize_models(models)
    out = os.path.abspath(os.fspath(out_dir))
    os.makedirs(out, exist_ok=True)
    results: dict[str, dict] = {}

    def _skip(key: str, reason: str) -> None:
        results[key] = {"status": "skipped", "reason": reason, "key": key}
        _emit(log, f"[{part}] {key} 跳过：{reason}")

    analysis_dict = _analysis_dict(analysis)
    sections = analysis_dict.get("sections_full") or []
    if not isinstance(sections, list) or len(sections) < 2:
        reason = f"测量结果缺少 sections_full（现有 {len(sections) if sections else 0} 个站位）"
        for key in keys:
            _skip(key, reason)
        return results

    length = _f(analysis_dict.get("center_line_length"))
    if length <= 0:
        length = _f((sections[-1] or {}).get("cumulative_length"))
    try:
        rows = build_rows(sections)
    except Exception as exc:  # 截面结构损坏：全部跳过，不炸整件
        reason = f"截面记录无法整理成工艺行：{type(exc).__name__}: {exc}"
        for key in keys:
            _skip(key, reason)
        return results

    # --- 每个 op 只建一次 processor（形状按模型按需生成）
    state: dict[str, Any] = {}
    axis_source: dict[str, str] = {}

    def _processor(operation: str, use_rows: Optional[list[dict]] = None):
        proc = make_processor(operation, sections, use_rows if use_rows is not None else rows, length)
        return proc

    def _shaping():
        if "shaping" not in state:
            state["shaping"] = _processor("shaping")
        return state["shaping"]

    def _belling():
        if "belling" not in state:
            state["belling"] = _processor("belling")
        return state["belling"]

    def _flat_or_bend(operation: str):
        if operation not in state:
            proc = _processor(operation)
            try:
                proc.generate_model()
            except ValueError as exc:
                # 只有「轴长不一致」可用于等周长椭圆兜底；其余错误原样上抛
                if operation != "flattening" or "轴长" not in str(exc):
                    raise
                axis_source[operation] = "perimeter_matched_ellipse"
                _emit(log, f"[{part}] 压扁：截面非椭圆，改用等周长椭圆轴长（周长不变）")
                proc = _processor(operation, perimeter_matched_axes(rows))
                proc.generate_model()
            else:
                axis_source[operation] = "measured_extents"
            state[operation] = proc
        return state[operation]

    def _shape_of(key: str):
        """按模型键生成几何；复用已生成的 processor 形状。"""
        if key == "成型_外径形腔":
            proc = _shaping()
            if getattr(proc, "shape", None) is None:
                proc.generate_model()
            return proc.shape
        if key == "扩口_扩口腔体":
            proc = _belling()
            if getattr(proc, "cavity_shape", None) is None:
                proc.generate_cavity_model()
            return proc.cavity_shape
        if key == "扩口_扩口芯轴":
            proc = _belling()
            if getattr(proc, "shape", None) is None:
                proc.generate_mandrel()
            return proc.shape
        model = contracts.model_def(key)
        proc = _flat_or_bend(model.op)
        return proc.shape

    for key in keys:
        model = contracts.model_def(key)
        started = time.monotonic()
        step_file = os.path.join(out, f"{model.slug if naming == 'slug' else model.key}.stp")
        try:
            shape = _shape_of(key)
            if shape is None:
                _skip(key, f"{model.op} 未产出几何")
                continue
            record = _write_step(shape, step_file)
        except Exception as exc:
            _skip(key, f"导出失败：{type(exc).__name__}: {exc}")
            continue
        seconds = round(time.monotonic() - started, 2)
        record.update(
            {
                "status": "ok",
                "key": key,
                "slug": model.slug,
                "operation": model.op,
                "seconds": seconds,
            }
        )
        if model.op in axis_source and key in ("压扁_压扁模型", "压弯_压弯模型"):
            record["axis_source"] = axis_source[model.op]
        results[key] = contracts.json_safe(record)
        _emit(
            log,
            f"[{part}] {key} → {os.path.basename(step_file)}  "
            f"{record['volume_mm3'] / 1e3:.1f} cm³  {record['faces']} 面 / "
            f"{record['edges']} 边  {record['bytes'] / 1e6:.2f} MB  {seconds} s",
        )
    return results


def step_paths(results: dict) -> dict[str, str]:
    """``export_models`` 结果 → ``{key: 成功写出的 STEP 路径}``（契约 §9 的返回形状）。"""
    out = {}
    for key, record in (results or {}).items():
        if isinstance(record, dict) and record.get("status") == "ok" and record.get("step"):
            out[key] = record["step"]
    return out
