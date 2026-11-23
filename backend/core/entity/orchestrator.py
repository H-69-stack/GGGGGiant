"""实体化流水线主编排器：扫描 → 计划 → 逐件 / 逐类 / 逐路执行 → 汇总。

契约见 ``Giant-Unified/ENTITY_PIPELINE.md`` §9。两条铁律：

* **导入要轻**：顶层只 import 标准库与 ``contracts`` / ``settings``。
  OCP、FreeCAD、trimesh、``giant_adapter``、``routes.*``、``checks``、``report``
  一律在函数内部延迟导入 —— 否则 ``--dry-run`` 会把 FreeCAD 一起拉起来。
* **几何必须串行**：OCP 与 FreeCAD 都不是线程安全的。这里只做单线程串行，
  并发只能落在 ``freecadcmd`` 子进程层面（由 ``routes.pipe`` 自己处理）。

权威签名（ENTITY_PIPELINE.md §9）::

    scan_parts(src_dir) -> list[str]
    build_plan(ctx) -> list[dict]
    run_part(ctx, part) -> PartReport
    main(argv=None) -> int

编排器内部与 ``run_entity.py`` 用的更细的入口::

    scan_step_files(src_dir=None) -> dict[str, str]       # {件号: STEP 绝对路径}
    build_specs(parts, models=None, routes=None, *, out_root=None,
                prefs=None) -> list[ModelSpec]
    build_plan(...)                                        # 见 docstring
    run_part(spec, ctx) -> PartReport                      # 单件单类
    run(ctx, on_progress=None) -> dict                     # 全量

关于 ``build_plan`` 的两种形态：契约写的是 ``build_plan(ctx) -> list[dict]``，
而调度方要求 ``build_plan(parts, models=None, routes=None) -> list[ModelSpec]``。
两者入参类型不同，故按**第一个参数的类型**分派：传 ``PipelineContext`` 返回
json 友好的 dict 列表（契约形态），传件号序列返回 ``ModelSpec`` 列表（调度形态）。
"""
from __future__ import annotations

import inspect
import json
import os
import re
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from . import contracts, route_prefs, settings
from .contracts import (
    MODEL_KEYS,
    ROUTES,
    ModelSpec,
    PartReport,
    PipelineContext,
    RouteResult,
    default_route,
    end_treatment_for,
    model_def,
    normalize_routes,
    now_iso,
    routes_for,
    uses_reference_route,
)

__all__ = [
    "scan_parts",
    "scan_step_files",
    "build_specs",
    "build_plan",
    "run_part",
    "run",
    "main",
    "c1_impl",
    "route_module_for",
]

# ---------------------------------------------------------------- 常量

#: 原始管件扩展名（源目录里两种都可能有）
STEP_SUFFIXES = (".stp", ".step")

#: 产物目录名（settings.part_paths 里的子目录），定计划时写死，运行期不建盘
MODELS_SUBDIR = "00_models"

#: 断了续跑时读回的历史结果，status 统一标这个
STATUS_CACHED = "cached"
#: 路线抛异常时的 status（契约里的 "failed" 也用同一含义，见回报④）
STATUS_ERROR = "error"
#: 协作式停止时「还没开始」的单元；不是失败，--resume 会接着跑
STATUS_STOPPED = "stopped"
#: 视为「成功」的 status
SUCCESS_STATUSES = ("ok", STATUS_CACHED)
#: ``ctx.options`` 里放 ``threading.Event`` 的键（S8 Web「停止」按钮用）
STOP_OPTION = "stop_event"

#: "(2)" / "（2）" / " - 副本" / "_副本" 之类的副本尾巴
_COPY_SUFFIX_RE = re.compile(r"\s*(?:\(\d+\)|（\d+）|-\s*副本|_\s*副本|副本)\s*$")
_NATURAL_RE = re.compile(r"(\d+)")


# ---------------------------------------------------------------- 扫描

def _natural_key(text: str) -> list:
    """件号自然排序：``24TK_1324-2`` 排在 ``24TK_1324-31`` 前面。"""
    return [int(tok) if tok.isdigit() else tok.lower() for tok in _NATURAL_RE.split(text)]


def _normalize_stem(stem: str) -> str:
    """去掉副本尾巴，得到件号。"""
    out = (stem or "").strip()
    prev = None
    while prev != out:
        prev = out
        out = _COPY_SUFFIX_RE.sub("", out).strip()
    return out


def _prefer(new_path: str, old_path: str) -> bool:
    """同名多份时优先「没有副本尾巴」的那个，其次路径短者。"""
    new_stem, old_stem = Path(new_path).stem, Path(old_path).stem
    new_clean = _normalize_stem(new_stem) == new_stem
    old_clean = _normalize_stem(old_stem) == old_stem
    if new_clean != old_clean:
        return new_clean
    return (len(new_path), new_path) < (len(old_path), old_path)


def scan_step_files(src_dir: Optional[str] = None) -> dict[str, str]:
    """扫源目录里的 ``.stp`` / ``.step``，返回 ``{件号: 绝对路径}``。

    忽略以下两类：

    * ``_cache`` / ``_report`` / ``_tmp`` 等任意以 ``_`` 或 ``.`` 开头的子目录；
    * ``某件 (2).stp``、``某件 - 副本.stp`` 这类副本（同件号只留一份）。
    """
    src = Path(src_dir or settings.SRC_PARTS_DIR).expanduser()
    src = src if src.is_absolute() else (Path.cwd() / src)
    src = src.resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"原始管件目录不存在：{src}")

    found: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(("_", ".")))
        for name in sorted(filenames):
            stem, ext = os.path.splitext(name)
            if ext.lower() not in STEP_SUFFIXES:
                continue
            part = _normalize_stem(stem)
            if not part:
                continue
            path = os.path.join(dirpath, name)
            previous = found.get(part)
            if previous is None or _prefer(path, previous):
                found[part] = path
    return found


def scan_parts(src_dir: Optional[str] = None) -> list[str]:
    """件号列表，按自然序排好（6 件 → ``24TK_1324-1, 24TK_1324-2, …``）。"""
    return sorted(scan_step_files(src_dir), key=_natural_key)


# ---------------------------------------------------------------- 计划

def c1_impl(part: str, key: str, prefs=None) -> str:
    """该「件 + 模型」在 c1 侧跑哪个实现（决策在编排器，不在 routes）。"""
    if uses_reference_route(part, key, prefs):
        return "121保形"
    if model_def(key).c1_kind == "selfref":
        return "自参考保形"
    return "C1外推"


def _ctx_prefs(ctx: PipelineContext) -> dict:
    """``ctx.options["route_prefs"]``（还没装载 / 不合规时给空偏好 = 全部走默认）。"""
    options = getattr(ctx, "options", None)
    if not isinstance(options, dict):
        return {}
    prefs = options.get("route_prefs")
    return prefs if isinstance(prefs, dict) else {}


def _part_end_treatment(part: str, keys=None, prefs=None) -> str:
    """件级端部口径：同件 5 类共用一份 measured.json，不能按模型各说各话。

    * 任一待跑模型解析成 ``121`` → ``v1``（121 要 v1 口径的测量缓存）；
    * 任一待跑模型被**显式**设成 ``c1`` → ``c1``（用户明确要求 c1，即使该件是 2052）；
    * 否则保持旧判据（:data:`contracts.REFERENCE_PARTS` → ``v1``）。
    """
    key_list = list(keys or MODEL_KEYS)
    explicit = {key: route_prefs.get_model_pref(prefs or {}, part, key)["impl"] for key in key_list}
    if any(route_prefs.resolve_impl(prefs or {}, part, key) == "121" for key in key_list):
        return "v1"
    if any(value == "c1" for value in explicit.values()):
        return "c1"
    return end_treatment_for(part, prefs=prefs)


def _normalize_names(values, allowed: tuple[str, ...], what: str) -> list[str]:
    """把 ``None`` / ``"a,b"`` / ``["a", "b"]`` 统一成去重后的有序列表。"""
    if values is None:
        return list(allowed)
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values:
        for item in str(value).replace("，", ",").split(","):
            item = item.strip()
            if not item or item in out:
                continue
            out.append(item)
    unknown = [item for item in out if item not in allowed]
    if unknown:
        raise ValueError(f"未知{what}：{', '.join(unknown)}（可选：{', '.join(allowed)}）")
    return out or list(allowed)


def build_specs(
    parts,
    models=None,
    routes=None,
    *,
    out_root: Optional[str] = None,
    prefs=None,
) -> list[ModelSpec]:
    """每件每类一条 :class:`ModelSpec`（**不建盘、不读几何**）。

    ``routes`` 只用于校验与 c1 侧实现判定；真正的路线执行在 :func:`run_part`。
    ``prefs``（:mod:`route_prefs` 结构）为每个「件 + 模型」解析 ``c1`` / ``121``
    与分型方向：``121`` → 参考件 = 本件导出的形腔 STEP；``c1`` → 无参考件。
    ``end_treatment`` 仍按**件级**取（同件 5 类共用一份 measured.json）。
    """
    if isinstance(parts, str):
        parts = [parts]
    part_list: list[str] = []
    for part in parts or []:
        part = str(part).strip()
        if part and part not in part_list:
            part_list.append(part)
    if not part_list:
        raise ValueError("没有待跑的件号（parts 为空）")

    keys = _normalize_names(models, MODEL_KEYS, "模型键")
    if routes is not None:
        _normalize_names(routes, ROUTES, "路线")

    out = Path(out_root or settings.OUT_ROOT).expanduser()
    specs: list[ModelSpec] = []
    for part in part_list:
        end_treatment = _part_end_treatment(part, keys, prefs)
        part_dir = str(out / part)
        models_dir = os.path.join(part_dir, MODELS_SUBDIR)
        measured = str(settings.measured_path(part, end_treatment, out))
        for key in keys:
            step = os.path.join(models_dir, f"{key}.stp")
            impl = route_prefs.resolve_impl(prefs, part, key)
            specs.append(
                ModelSpec(
                    part=part,
                    key=key,
                    step=step,
                    end_treatment=end_treatment,
                    out_dir=part_dir,
                    measured_path=measured,
                    # 121 保形 / 自参考保形的参考件 = 本件刚导出的形腔 STEP（由 run_part 落地）
                    reference_step=step if impl == "121" else None,
                    direction=route_prefs.resolve_direction(prefs, part, key),
                )
            )
    return specs


def _spec_dict(spec: ModelSpec, route: Optional[str] = None, prefs=None) -> dict:
    defn = spec.defn
    data = {
        "part": spec.part,
        "key": spec.key,
        "op": defn.op,
        "op_label": defn.op_label,
        "name": defn.name,
        "slug": defn.slug,
        "contour": defn.contour,
        "c1_kind": defn.c1_kind,
        "step": spec.step,
        "end_treatment": spec.end_treatment,
        "direction": getattr(spec, "direction", "auto"),
        "out_dir": spec.out_dir,
        "measured_path": str(spec.measured_path) if spec.measured_path else None,
        "reference_step": spec.reference_step,
        "c1_impl": c1_impl(spec.part, spec.key, prefs),
    }
    if route is not None:
        data["route"] = route
        data["impl"] = "solidify" if route == "pipe" else data["c1_impl"]
        data["out_path"] = spec.out_path(route)
        data["reference"] = "self" if spec.reference_step else None
    return data


def _spec_routes(spec: ModelSpec, routes=None) -> tuple:
    """该模型这一轮要跑的路线：显式路线列表，否则它自己的默认路线。"""
    return tuple(routes) if routes else (default_route(spec.key),)


def build_plan(parts, models=None, routes=None, *, out_root: Optional[str] = None):
    """计划表。两种形态按第一个参数的类型分派（见模块 docstring）。

    * ``build_plan(ctx: PipelineContext)`` → ``list[dict]``，**已经按路线展开**
      （件 × 类 × 路，一行 = 一次路线执行，带 ``route`` / ``impl`` / ``out_path``），
      和 dry-run 打印的表格一一对应；
    * ``build_plan(parts, models, routes)`` → ``list[ModelSpec]``（一行 = 一个模型的
      STEP，路线在执行期才分叉，故条数是前者的 ``1/len(routes)``）。
    """
    if isinstance(parts, PipelineContext):
        ctx = parts
        prefs = _ctx_prefs(ctx)
        specs = build_specs(ctx.parts, ctx.models, ctx.routes, out_root=ctx.out_root, prefs=prefs)
        route_list = normalize_routes(ctx.routes)
        if route_list is None:  # 按模型默认：外径形腔走 c1，其余走 pipe
            return [_spec_dict(spec, default_route(spec.key), prefs) for spec in specs]
        return [_spec_dict(spec, route, prefs) for spec in specs for route in route_list]
    return build_specs(parts, models, routes, out_root=out_root)


# ---------------------------------------------------------------- 上下文小工具

def _source_step(part: str, ctx: PipelineContext) -> str:
    """该件的原始管件 STEP（扫一次后缓存进 ctx）。"""
    cache = getattr(ctx, "_entity_src", None)
    if not cache:
        cache = scan_step_files(ctx.src_dir)
        ctx._entity_src = cache
    try:
        return cache[part]
    except KeyError:
        raise FileNotFoundError(
            f"源目录里没有件 {part}：{ctx.src_dir}（现有 {len(cache)} 件：{', '.join(sorted(cache))}）"
        ) from None


def _make_logger(ctx: PipelineContext, on_progress=None):
    """把进度同时送进 ``ctx.log``、``on_progress`` 与件报告的 logs。"""
    base = ctx.log if callable(ctx.log) else print

    def log(line) -> None:
        text = line if isinstance(line, str) else str(line)
        try:
            base(text)
        except Exception:  # 日志后端炸了不能影响几何
            print(text)
        if on_progress is not None:
            try:
                on_progress(text)
            except TypeError:
                try:
                    on_progress({"message": text})
                except Exception:
                    pass
            except Exception:
                pass

    ctx.log = log
    return log


def _new_report(part: str, ctx: PipelineContext) -> PartReport:
    end_treatment = _part_end_treatment(
        part, tuple(ctx.models or ()) or MODEL_KEYS, _ctx_prefs(ctx)
    )
    try:
        source = _source_step(part, ctx)
    except FileNotFoundError:
        source = ""
    return PartReport(
        part=part,
        source_step=source,
        end_treatment=end_treatment,
        measured_path=str(settings.measured_path(part, end_treatment, ctx.out_root)),
        started=now_iso(),
    )


def _note(report: PartReport, line: str) -> None:
    report.logs.append(line)
    if len(report.logs) > 400:  # 别让 report.json 无限膨胀
        del report.logs[:-400]


# ---------------------------------------------------------------- S0 / S1

def _measure(spec: ModelSpec, ctx: PipelineContext, log) -> Any:
    """S0 测量（同件只跑一次，命中 ``ctx.measured`` 缓存）。"""
    cached = None
    if isinstance(ctx.measured, dict):
        cached = ctx.measured.get(spec.part)
    if cached is not None and not ctx.force:
        log(f"  [{spec.part}] 复用测量缓存 {getattr(cached, 'path', spec.measured_path)}")
        return cached

    from . import giant_adapter  # 延迟导入：S1 模块，顶层别碰

    source = _source_step(spec.part, ctx)
    cache_path = spec.measured_path or str(
        settings.measured_path(spec.part, spec.end_treatment, ctx.out_root)
    )
    log(f"  [{spec.part}] 测量 {os.path.basename(source)}（口径 {spec.end_treatment}）→ {cache_path}")
    measured = giant_adapter.measure(
        spec.part,
        source,
        spec.end_treatment,
        cache_path,
        force=ctx.force,
        log=log,
    )
    if not isinstance(ctx.measured, dict):
        ctx.measured = {}
    ctx.measured[spec.part] = measured
    return measured


_MODEL_PATH_KEYS = ("step", "path", "step_path", "file")


def _exported_step(entry) -> str:
    """``export_models`` 的单项归一成路径：可能是 str、``{"step": ...}`` 或带 ``.step`` 的对象。"""
    if isinstance(entry, (str, os.PathLike)):
        return os.fspath(entry)
    if isinstance(entry, dict):
        for name in _MODEL_PATH_KEYS:
            value = entry.get(name)
            if isinstance(value, (str, os.PathLike)) and value:
                return os.fspath(value)
        return ""
    for name in _MODEL_PATH_KEYS:
        value = getattr(entry, name, None)
        if isinstance(value, (str, os.PathLike)) and value:
            return os.fspath(value)
    return ""


def _model_path(part_dir: str, key: str, exported: dict) -> str:
    """export_models 的返回值归一成实际存在的 STEP 路径（slug / 中文键 / ``00_models`` 都认）。"""
    candidates: list[str] = []
    direct = _exported_step(exported.get(key))
    if direct:
        candidates.append(direct)
    slug = model_def(key).slug
    for subdir in (MODELS_SUBDIR, ""):
        for name in (key, slug):
            for suffix in STEP_SUFFIXES:
                candidates.append(os.path.join(part_dir, subdir, f"{name}{suffix}"))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    fallback = direct or os.path.join(part_dir, MODELS_SUBDIR, f"{key}.stp")
    return os.path.abspath(fallback)


def _prepare_cancelled(exc: BaseException) -> bool:
    """是否是 ``prepare_proc.PrepareCancelled``（按类名认，模块缺席也不会炸）。"""
    return any(cls.__name__ == "PrepareCancelled" for cls in type(exc).__mro__)


def _prepare_models(spec: ModelSpec, ctx: PipelineContext, log, wanted) -> Optional[dict]:
    """优先用 ``prepare_proc.run_prepare`` 子进程（测量 + 导出一步到位）。

    返回 ``{key: STEP}``；模块缺席 / ``PrepareFailed`` / 返回不合格 → ``None``，
    由调用方回落到进程内的 ``measure + export_models``。
    ``PrepareCancelled``（用户停止）直接上抛给上层，不当失败回落。
    """
    options = getattr(ctx, "options", None) or {}
    if isinstance(options, dict) and options.get("no_subprocess"):
        log(f"  [{spec.part}] --no-subprocess：跳 prepare 子进程，改用进程内测量/导出")
        return None
    try:
        from . import prepare_proc  # A1 并行开发中：可能还没落地
    except Exception as exc:  # noqa: BLE001 - 缺模块必须回落，不能拖垮整条流水线
        log(f"  [{spec.part}] prepare_proc 不可用（{type(exc).__name__}: {exc}），回落进程内测量/导出")
        return None
    runner = getattr(prepare_proc, "run_prepare", None)
    if not callable(runner):
        log(f"  [{spec.part}] prepare_proc 没有 run_prepare，回落进程内测量/导出")
        return None

    source = _source_step(spec.part, ctx)
    log(f"  [{spec.part}] prepare 子进程：测量（口径 {spec.end_treatment}）+ 导出 {len(wanted)} 类模型")
    try:
        result = runner(
            part=spec.part,
            step=source,
            end_treatment=spec.end_treatment,
            out_root=ctx.out_root,
            models=wanted,
            force=ctx.force,
            log=log,
            stop_event=_stop_event(ctx),
        )
    except Exception as exc:  # noqa: BLE001 - PrepareFailed 等一律回落
        if _prepare_cancelled(exc):
            raise
        log(f"  [{spec.part}] prepare 失败（{type(exc).__name__}: {exc}），回落进程内测量/导出")
        return None

    raw = result.get("models") if isinstance(result, dict) else None
    if not isinstance(raw, dict) or not raw:
        log(f"  [{spec.part}] prepare 返回没有 models，回落进程内测量/导出")
        return None
    models: dict[str, str] = {}
    for key, value in raw.items():
        path = _exported_step(value)
        if path:
            models[str(key)] = os.path.abspath(path)
    if not models:
        log(f"  [{spec.part}] prepare 返回的模型路径都解析不了，回落进程内测量/导出")
        return None
    for key in wanted:  # prepare 只给了一部分时补上契约路径
        models.setdefault(key, _model_path(spec.out_dir, key, {}))

    measured_path = result.get("measured_path") if isinstance(result, dict) else None
    if isinstance(measured_path, str) and measured_path:
        paths = getattr(ctx, "_entity_measured_paths", None)
        if not isinstance(paths, dict):
            paths = {}
            ctx._entity_measured_paths = paths
        paths[spec.part] = os.path.abspath(measured_path)
    log(f"  [{spec.part}] prepare 完成：{len(models)} 类模型")
    return models


def _measure_export_models(spec: ModelSpec, ctx: PipelineContext, log) -> dict:
    """进程内回落：S1 测量 + 导出 5 类模型 STEP。"""
    part = spec.part
    measured = _measure(spec, ctx, log)
    from . import giant_adapter  # 延迟导入

    models_dir = os.path.join(spec.out_dir, MODELS_SUBDIR)
    wanted = [key for key in (tuple(ctx.models) or contracts.MODEL_KEYS)]
    log(f"  [{part}] 导出 {len(wanted)} 类模型 → {models_dir}")
    exported = giant_adapter.export_models(part, measured, models_dir, models=wanted, log=log) or {}
    if not isinstance(exported, dict):
        raise TypeError(f"export_models 返回了 {type(exported).__name__}，契约要求 dict{{key: step}}")

    models: dict[str, str] = {}
    for key in tuple(ctx.models) or MODEL_KEYS:
        models[key] = _model_path(spec.out_dir, key, exported)
    for key, value in exported.items():  # 契约之外多给的键也留着
        extra = _exported_step(value)
        if extra:
            models.setdefault(str(key), os.path.abspath(extra))
    return models


def _part_models(spec: ModelSpec, ctx: PipelineContext, log) -> dict:
    """S1：测量 + 导出 5 类模型 STEP（同件只跑一次，缓存在 ctx 上）。

    优先 ``prepare_proc.run_prepare``（子进程里已做 measure + export）；模块缺席 /
    失败 / ``--no-subprocess`` 时回落到 :func:`_measure_export_models`。
    """
    store = getattr(ctx, "_entity_models", None)
    if not isinstance(store, dict):
        store = {}
        ctx._entity_models = store
    part = spec.part
    if part in store and not ctx.force:
        return store[part]

    wanted = [key for key in (tuple(ctx.models) or contracts.MODEL_KEYS)]
    models = _prepare_models(spec, ctx, log, wanted)
    if models is None:
        models = _measure_export_models(spec, ctx, log)
    store[part] = models
    return models


# ---------------------------------------------------------------- 路线调度

def route_module_for(part: str, key: str, route: str, prefs=None):
    """按契约选出该「件 + 模型 + 路线」的实现模块。

    c1 侧由编排器决定（不交给 routes 互相猜；``prefs`` 显式 121 时也走 selfref）：

    * ``c1_kind == "selfref"``（扩口腔体 / 扩口芯轴 / 压扁 / 压弯）→ ``routes.selfref``
    * ``uses_reference_route``（2052-2 的外径形腔，或偏好里显式 121）→ ``routes.selfref``
    * 其余（五件的外径形腔）→ ``routes.c1``（C1 外推）
    """
    if route == "pipe":
        return _import_route("pipe", part, key)
    if route != "c1":
        raise ValueError(f"未知路线：{route}（可选：{', '.join(ROUTES)}）")
    want_selfref = model_def(key).c1_kind == "selfref" or uses_reference_route(part, key, prefs)
    return _import_route("selfref" if want_selfref else "c1", part, key)


def _import_route(name: str, part: str, key: str):
    """导入 ``routes.<name>``；缺模块直接报错，**不做 c1↔selfref 回落**。

    回落会让「C1 外推」悄悄换成「自参考」几何：产物看着正常、口径却是错的，
    比这条路线直接 ``status="error"`` 更难发现。
    """
    import importlib

    package = f"{__package__ or __name__.rsplit('.', 1)[0]}.routes"  # core.entity.routes
    try:
        return importlib.import_module(f"{package}.{name}")
    except ImportError as exc:
        raise ImportError(f"{part} / {key}：路线模块 routes.{name} 不可用（{exc}）") from exc


#: ``merge_check`` 里由位置参数传入、不用 kwargs 补的那两个。
_CHECK_FIXED_PARAMS = frozenset(
    {"result", "route_result", "rep", "rec", "record", "check", "check_record"}
)


def _check_kwargs(fn, result: RouteResult) -> dict:
    """按目标函数签名挑出我们有的参考量，别硬塞不认识的参数。

    ``checks.merge_check(result, rec, ref_volume=None, ref_bbox=None)`` 的前两个参数
    由调用方按位置传入，这里只补 ``ref_volume`` / ``ref_bbox``。
    """
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return {}
    kwargs: dict = {}
    for param in params:
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        name = param.name
        if name in _CHECK_FIXED_PARAMS:
            continue
        if name in ("ref_volume", "ref_volume_mm3"):
            if result.ref_volume_mm3 is not None:
                kwargs[name] = result.ref_volume_mm3
        elif name in ("ref_bbox", "ref_bbox_mm"):
            if result.ref_bbox is not None:
                kwargs[name] = result.ref_bbox
        elif param.default is inspect._empty:
            raise TypeError(f"checks 函数 {getattr(fn, '__name__', fn)} 有无法满足的必填参数 {name}")
    return kwargs


def _apply_checks(result: RouteResult, log) -> RouteResult:
    """S6：补齐拓扑 / 体积 / 包围盒体检。

    ``checks.merge_check(result, rec, ...)`` 要一份 ``CheckRecord``：优先用
    ``safe_inspect_step``（绝不抛，读不动就留空字段），没有再用 ``inspect_step``。
    缺模块 / 读不动 STEP 都只记一笔，不打断流水线。
    """
    if result is None or not result.step or not os.path.isfile(str(result.step)):
        return result
    try:
        from . import checks as checks_mod
    except Exception as exc:
        log(f"  ! checks 模块不可用，跳过体检：{exc}")
        return result

    merge = getattr(checks_mod, "merge_check", None) or getattr(checks_mod, "compare", None)
    if not callable(merge):
        log("  ! checks 模块没有 merge_check / compare，跳过体检")
        return result

    try:
        record = None
        safe = getattr(checks_mod, "safe_inspect_step", None)
        plain = getattr(checks_mod, "inspect_step", None)
        if callable(safe):
            record = safe(result.step)
        elif callable(plain):
            record = plain(result.step)
        if record is None:
            return result
        return merge(result, record, **_check_kwargs(merge, result)) or result
    except Exception as exc:  # 体检永远不许让流水线失败
        message = f"checks 失败：{type(exc).__name__}: {exc}"
        log(f"  ! {message}")
        try:
            result.problems.append(message)
        except Exception:
            pass
    return result


def _route_error(spec: ModelSpec, route: str, message: str, seconds: float = 0.0, **extra) -> RouteResult:
    return RouteResult(
        part=spec.part,
        key=spec.key,
        route=route,
        status=STATUS_ERROR,
        error=message,
        seconds=round(seconds, 3),
        **extra,
    )


def _execute_route(spec: ModelSpec, route: str, ctx: PipelineContext, log) -> RouteResult:
    """跑一条路。任何非 KeyboardInterrupt 的异常都收成 ``status="error"``。"""
    started = time.perf_counter()
    try:
        module = route_module_for(spec.part, spec.key, route, _ctx_prefs(ctx))
        raw = module.run(spec, ctx)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        elapsed = time.perf_counter() - started
        message = f"{type(exc).__name__}: {exc}"
        tb = traceback.format_exc(limit=8)
        log(f"  ✗ {spec.part} / {spec.key} / {route} 失败：{message}")
        for line in reversed(tb.strip().splitlines()):
            if line.strip():
                log(f"      {line.strip()}")
                break
        return _route_error(spec, route, message, elapsed, artifacts={"traceback": tb})

    elapsed = time.perf_counter() - started
    if raw is None:
        return _route_error(spec, route, "路线返回 None（契约要求 RouteResult）", elapsed)
    if isinstance(raw, dict):
        raw = contracts.result_from_dict(raw)
    if not isinstance(raw, RouteResult):
        return _route_error(
            spec, route, f"路线返回 {type(raw).__name__}，契约要求 RouteResult", elapsed
        )

    result = raw
    result.part = spec.part
    result.key = spec.key
    result.route = result.route or route
    if result.status in (None, "", "pending"):
        # routes 没写 status 但也没报错，按成功算；报错就保持 error/failed
        result.status = "ok" if result.error is None else "failed"
    if not result.seconds:
        result.seconds = round(elapsed, 3)
    _apply_checks(result, log)

    if result.status in SUCCESS_STATUSES and result.step and not os.path.isfile(str(result.step)):
        result.problems.append(f"产物不存在：{result.step}")
    log(
        f"  ✓ {spec.part} / {spec.key} / {route}  {result.status}  {result.seconds}s"
        f"  面={result.faces} 实体={result.solids} 自由边={result.free_edges}"
    )
    return result


# ---------------------------------------------------------------- 断点续跑

def _read_back_result(spec: ModelSpec, route: str, ctx: PipelineContext) -> Optional[RouteResult]:
    """从该件历史的 ``report.json`` 里读回同一条产物。"""
    path = os.path.join(spec.out_dir, "report.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    for item in contracts.iter_results(data):
        if not isinstance(item, dict):
            continue
        if item.get("key") == spec.key and item.get("route") == route:
            try:
                return contracts.result_from_dict(item)
            except Exception:
                return None
    return None


def _cached_result(spec: ModelSpec, route: str, ctx: PipelineContext, log) -> Optional[RouteResult]:
    """``out_path(route)`` 已存在且没 ``--force`` → 直接复用，不重算。"""
    if ctx.force or not getattr(ctx, "resume", True):
        return None
    out_path = spec.out_path(route)
    if not os.path.isfile(out_path):
        return None

    result = _read_back_result(spec, route, ctx)
    if result is None:
        result = RouteResult(part=spec.part, key=spec.key, route=route, status=STATUS_CACHED)
    result.part = spec.part
    result.key = spec.key
    result.route = route
    result.step = out_path
    result.error = None
    # 产物可能被别人替换过：按磁盘上的当前文件重新复核体积 / 面数（merge_check 不改 status）
    _apply_checks(result, log)
    result.status = STATUS_CACHED
    # 复核后若出现致命违规（面数 / 实体数 / 自由边 / 无效实体）→ 不许记成 cached 成功，
    # 否则 --resume 会把坏件永久掩盖。分型边条数属「只记录不判失败」的软问题，
    # 记进 problems 后仍保持 cached（与 ENTITY_PIPELINE.md §8 的口径一致）。
    try:
        from . import checks as checks_mod

        hard = checks_mod.hard_issues(result)
        soft = checks_mod.soft_issues(result)
    except Exception:  # noqa: BLE001 - 体检不可用时不阻断缓存复用
        hard, soft = [], []
    if soft:
        result.problems = list(result.problems) + soft
        log(f"  ! {spec.part} / {spec.key} / {route} 缓存产物有软问题（只记录）："
            + "；".join(str(item) for item in soft[:4]))
    if hard:
        text = "；".join(str(item) for item in hard[:4])
        result.status = STATUS_ERROR
        result.error = f"缓存产物复核不合规：{text}"
        result.problems = list(result.problems) + hard
        log(f"  ✗ {spec.part} / {spec.key} / {route} 缓存产物不合规：{text}")
    return result


def _cached_path(spec: ModelSpec, route: str, ctx: PipelineContext) -> str:
    """``--resume`` 且产物在盘上 → 返回路径（否则空串），供「全 cached 就不测量」判断。"""
    if ctx.force or not getattr(ctx, "resume", True):
        return ""
    path = spec.out_path(route)
    return path if os.path.isfile(path) else ""


def _read_back_models(part_dir: str) -> dict[str, str]:
    """上次 report.json 里的 ``{类: STEP}``（复用缓存时不重新导出，但摘要别丢）。"""
    path = os.path.join(part_dir, "report.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in models.items()
        if isinstance(value, str) and os.path.isfile(value)
    }


# ---------------------------------------------------------------- 协作式停止

_STOP_MESSAGE = "停止请求已收到，正在第 {index}/{total} 个单元处退出"
_STOPPED_NOTE = "停止请求已收到，该单元未开始执行"


def _stop_event(ctx: PipelineContext):
    """``ctx.options["stop_event"]``（threading.Event）；没有/不合规都当 None。"""
    options = getattr(ctx, "options", None) or {}
    try:
        event = options.get(STOP_OPTION) if isinstance(options, dict) else getattr(options, STOP_OPTION, None)
    except Exception:
        return None
    return event if callable(getattr(event, "is_set", None)) else None


def _stop_requested(ctx: PipelineContext) -> bool:
    event = _stop_event(ctx)
    if event is None:
        return False
    try:
        return bool(event.is_set())
    except Exception:
        return False


def _cells_total(ctx: PipelineContext) -> int:
    return int(getattr(ctx, "_entity_cells_total", 0) or 0)


def _cells_done(ctx: PipelineContext) -> int:
    return int(getattr(ctx, "_entity_cells_done", 0) or 0)


def _count_cell(ctx: PipelineContext) -> None:
    """记一个「已处理」单元（ok / cached / error 都算，stopped 不算）。"""
    ctx._entity_cells_done = _cells_done(ctx) + 1


def _stop_at(ctx: PipelineContext, log) -> bool:
    """安全点：有停止请求就立刻返回 True，且同一轮 run 只打一行停止日志。"""
    if not _stop_requested(ctx):
        return False
    if not getattr(ctx, "_entity_stop_logged", False):
        done, total = _cells_done(ctx), _cells_total(ctx)
        index = min(done + 1, total) if total else done + 1
        log(_STOP_MESSAGE.format(index=index, total=total or "?"))
        ctx._entity_stop_logged = True
    ctx._entity_stopped = True
    return True


def _stopped_result(spec: ModelSpec, route: str) -> RouteResult:
    result = RouteResult(part=spec.part, key=spec.key, route=route, status=STATUS_STOPPED)
    result.problems.append(_STOPPED_NOTE)
    return result


def _report_payload(report: PartReport) -> dict:
    """``PartReport.to_dict()`` + ``stopped`` 标记（contracts 没有这个字段）。"""
    payload = report.to_dict()
    payload["stopped"] = bool(getattr(report, "stopped", False)) or any(
        str(item.get("status")) == STATUS_STOPPED
        for item in (payload.get("results") or [])
        if isinstance(item, dict)
    )
    return payload


# ---------------------------------------------------------------- 单件执行

def run_part(spec, ctx: Optional[PipelineContext] = None) -> PartReport:
    """跑一个待办单元，返回该件的 :class:`PartReport`。

    两种调用形态：

    * ``run_part(spec, ctx)`` —— 调度形态，一次一个模型；
    * ``run_part(ctx, part)`` —— 契约形态，一次把该件 5 类全跑完。
    """
    if isinstance(spec, PipelineContext):  # 契约形态 (ctx, part)
        ctx, part = spec, ctx
        if not isinstance(part, str):
            raise TypeError("run_part(ctx, part) 需要件号字符串")
        specs = build_specs(
            [part], ctx.models, ctx.routes, out_root=ctx.out_root, prefs=_ctx_prefs(ctx)
        )
        return _run_specs(specs, ctx, part)
    if ctx is None:  # 单测便利：只给 spec 也能跑
        ctx = PipelineContext(
            src_dir=str(settings.SRC_PARTS_DIR),
            out_root=os.path.dirname(os.path.abspath(spec.out_dir)) or str(settings.OUT_ROOT),
            parts=[spec.part],
            routes=None,
            models=MODEL_KEYS,
        )
    return _run_specs([spec], ctx, spec.part)


def _run_specs(specs: list[ModelSpec], ctx: PipelineContext, part: str) -> PartReport:
    """同件的一组 :class:`ModelSpec`：先测量/导出一次，再逐类逐路跑。"""
    log = ctx.log if callable(ctx.log) else print
    report = _new_report(part, ctx)
    if not specs:
        report.finished = now_iso()
        return report

    routes = normalize_routes(ctx.routes)  # None = 每个模型各走自己的默认路线
    cells = len(specs) * (len(routes) if routes else 1)
    if not _cells_total(ctx):
        ctx._entity_cells_total = cells

    # 安全点①：单元级——已请求停止就不测量（measure 要几十秒），直接记 stopped
    stopped = _stop_at(ctx, log)

    models: dict[str, str] = {}
    prepare_error: Optional[str] = None
    # 全部单元都有产物时（典型：--resume 复跑），连测量/导出都不做，直接读回复核
    all_cached = all(
        _cached_path(spec, route, ctx)
        for spec in specs
        for route in (routes or (default_route(spec.key),))
    )
    if all_cached:
        models = _read_back_models(specs[0].out_dir)
        report.models = dict(models)
        log(f"  [{part}] 全部 {cells} 条产物已存在，跳过测量/导出")
    elif not stopped:
        try:
            models = _part_models(specs[0], ctx, log)
            report.models = dict(models)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if _prepare_cancelled(exc):
                raise  # 停止请求：上抛给 API/CLI 按 stopped 收尾，不记 error
            prepare_error = f"{type(exc).__name__}: {exc}"
            log(f"[{part}] 测量/导出失败，该件所有路线记为 error：{prepare_error}")

    # prepare 子进程可能把 measured 落在它自己算的路径上（一般是同一份缓存）
    measured_map = getattr(ctx, "_entity_measured_paths", None)
    if isinstance(measured_map, dict) and measured_map.get(part):
        for spec in specs:
            spec.measured_path = measured_map[part]

    prefs = _ctx_prefs(ctx)
    for spec in specs:
        if route_prefs.resolve_impl(prefs, spec.part, spec.key) == "121":
            # 121 保形：参考件 = 本件刚导出的外径形腔 STEP
            spec.reference_step = models.get(spec.key) or spec.step
        elif spec.reference_step:
            spec.reference_step = models.get(spec.key) or spec.step
        spec.step = models.get(spec.key) or spec.step

        for route in (routes or (default_route(spec.key),)):
            # 安全点③：每个路线单元前后都看一次停止请求
            if stopped or _stop_at(ctx, log):
                stopped = True
                report.add(_stopped_result(spec, route))
                continue
            cached = _cached_result(spec, route, ctx, log)
            if cached is not None:
                log(f"  ↻ {part} / {spec.key} / {route} 产物已存在，跳过重算")
                result = cached
            elif prepare_error:
                result = _route_error(spec, route, f"测量/导出失败：{prepare_error}")
            else:
                result = _execute_route(spec, route, ctx, log)
            report.add(result)
            _count_cell(ctx)
            if result.status not in SUCCESS_STATUSES:
                _note(report, f"{spec.key}/{route}: {result.status} {result.error or ''}".strip())
            if _stop_at(ctx, log):  # 单元跑完才发现请求：下一条会记 stopped，先打日志
                stopped = True

    report.finished = now_iso()
    try:
        started = _parse_iso(report.started)
        if started is not None:
            report.seconds = round((_dt_now() - started).total_seconds(), 3)
    except Exception:
        report.seconds = round(sum(float(r.seconds or 0) for r in report.results), 3)
    if not report.seconds:
        report.seconds = round(sum(float(r.seconds or 0) for r in report.results), 3)
    return report


def _parse_iso(text: str):
    import datetime as _dt

    try:
        return _dt.datetime.fromisoformat(text)
    except Exception:
        return None


def _dt_now():
    import datetime as _dt

    return _dt.datetime.now().astimezone()


# ---------------------------------------------------------------- 汇总落盘

#: ``report.write_*`` 里「目录」那一侧可能叫的名字。
_DIR_PARAM_NAMES = ("part_dir", "out_dir", "out_root", "root", "directory", "dir", "path", "where")


def _first_param_name(fn) -> str:
    try:
        params = [
            param
            for param in inspect.signature(fn).parameters.values()
            if param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD)
        ]
    except (TypeError, ValueError):
        return ""
    return params[0].name if params else ""


def _call_writer(fn, payload, target, *, payload_names: tuple[str, ...], **kwargs):
    """按目标函数首参数名决定 ``(payload, target)`` 还是 ``(target, payload)``。

    ``ENTITY_PIPELINE.md`` §9 写的是 ``write_part_report(part_dir, report)``，S9 落地的
    是 ``write_part_report(report, out_dir)``。两种都认：先看首参数名，猜不出来就按
    S9 顺序试一次，``TypeError`` / ``AttributeError`` 再换过来。

    ``kwargs`` 原样透传（例如件级报告的 ``process_summary``）。
    """
    first = _first_param_name(fn)
    if first in payload_names:
        return fn(payload, target, **kwargs)
    if first in _DIR_PARAM_NAMES:
        return fn(target, payload, **kwargs)
    try:
        return fn(payload, target, **kwargs)
    except (TypeError, AttributeError):
        return fn(target, payload, **kwargs)


def _part_process_summary(report: PartReport, ctx: PipelineContext, log):
    """件级工艺摘要（中心线/壁厚/推荐路线/三类抽管道次）；失败只告警。"""
    try:
        from .process_summary import build_process_summary
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] process_summary 不可用，报告不含工艺摘要：{exc}")
        return None
    measured = (getattr(ctx, "_entity_measured_paths", None) or {}).get(report.part)
    design = None
    try:
        from . import design_gen
        from ..pp import design_db

        brief = design_gen.existing_design(report.part)
        if brief and brief.get("id") is not None:
            design = design_db.get_design(int(brief["id"]), db_path=str(design_gen.design_db_path())) or brief
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] 读取设计记录失败（{report.part}）：{type(exc).__name__}: {exc}")
    route_plan = None
    try:
        from . import design_gen

        route_plan = design_gen.recommend_for_part(
            report.part, getattr(ctx, "out_root", None)
        )
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] 路线推荐失败（{report.part}）：{type(exc).__name__}: {exc}")
    try:
        return build_process_summary(
            part=report.part,
            measured_path=measured,
            design=design,
            route_plan=route_plan,
            out_root=getattr(ctx, "out_root", None),
        )
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] 工艺摘要生成失败（{report.part}）：{type(exc).__name__}: {exc}")
        return None


def _write_part_report(report: PartReport, ctx: PipelineContext, log) -> None:
    """S9：件级 report.json / report.md（缺模块只告警）。"""
    try:
        from . import report as report_mod
    except Exception as exc:
        log(f"[warn] report 模块不可用，跳过件级报告：{exc}")
        return
    writer = getattr(report_mod, "write_part_report", None)
    if not callable(writer):
        log("[warn] report.write_part_report 缺失，跳过件级报告")
        return
    try:
        summary = _part_process_summary(report, ctx, log)
        _call_writer(
            writer,
            _report_payload(report),  # 带上 stopped，report.json 里能看出是停下来的
            ctx.part_dir(report.part),
            payload_names=("report", "part_report", "rep", "part"),
            process_summary=summary,
        )
    except Exception as exc:
        log(f"[warn] 写件级报告失败（{report.part}）：{type(exc).__name__}: {exc}")


def _summary_payloads(reports: list[PartReport], ctx: PipelineContext) -> list[dict]:
    """全局汇总的数据源：本次运行的件 + 磁盘上其它已生成 ``report.json`` 的件。

    单件运行（页面上「运行」只跑当前件）不应该把 ``_report/summary.json`` 缩成一件，
    所以这里把 ``<out_root>/<件>/report.json`` 全部并进来；本次运行的结果优先。
    """
    payloads: dict[str, dict] = {}
    for report in reports:
        payloads[report.part] = _report_payload(report)
    root = Path(getattr(ctx, "out_root", "") or "")
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            path = child / "report.json"
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(data, dict):
                continue
            part = str(data.get("part") or child.name)
            payloads.setdefault(part, data)
    return [payloads[key] for key in sorted(payloads)]


def _write_summary(reports: list[PartReport], ctx: PipelineContext, log) -> dict:
    """S9：全局 ``_report/summary.json|md``（缺模块只告警，返回值兜底）。"""
    fallback = {"parts": len(reports), "written": False}
    try:
        from . import report as report_mod
    except Exception as exc:
        log(f"[warn] report 模块不可用，跳过全局汇总：{exc}")
        return fallback
    writer = getattr(report_mod, "write_summary", None)
    if not callable(writer):
        log("[warn] report.write_summary 缺失，跳过全局汇总")
        return fallback
    try:
        written = _call_writer(
            writer,
            _summary_payloads(reports, ctx),
            ctx.out_root,
            payload_names=("reports", "part_reports", "items", "docs"),
        )
        return contracts.json_safe(written) if written is not None else {"written": True}
    except Exception as exc:
        log(f"[warn] 写全局汇总失败：{type(exc).__name__}: {exc}")
        return fallback


def _totals(reports: list[PartReport], ctx: PipelineContext, seconds: float) -> dict:
    results = [result for report in reports for result in report.results]
    statuses = Counter(result.status or "unknown" for result in results)
    routes = normalize_routes(ctx.routes) or routes_for(ctx.models)
    by_route: dict[str, dict] = {}
    for route in routes:
        subset = [r for r in results if r.route == route]
        by_route[route] = {
            "results": len(subset),
            "ok": sum(1 for r in subset if r.status == "ok"),
            "cached": sum(1 for r in subset if r.status == STATUS_CACHED),
            "stopped": sum(1 for r in subset if r.status == STATUS_STOPPED),
            "error": sum(
                1 for r in subset if r.status not in SUCCESS_STATUSES and r.status != STATUS_STOPPED
            ),
            "seconds": round(sum(float(r.seconds or 0) for r in subset), 3),
        }
    return {
        "parts": len(reports),
        "models": len(tuple(ctx.models) or MODEL_KEYS),
        "routes": list(routes),
        "results": len(results),
        "ok": statuses.get("ok", 0),
        "cached": statuses.get(STATUS_CACHED, 0),
        "stopped": statuses.get(STATUS_STOPPED, 0),
        "error": sum(
            count
            for status, count in statuses.items()
            if status not in SUCCESS_STATUSES and status != STATUS_STOPPED
        ),
        "status": dict(statuses),
        "by_route": by_route,
        "seconds": round(seconds, 3),
        "started": reports[0].started if reports else now_iso(),
        "finished": now_iso(),
        "out_root": str(ctx.out_root),
        "src_dir": str(ctx.src_dir),
    }


# ---------------------------------------------------------------- 全量入口

def run(ctx: PipelineContext, on_progress=None) -> dict:
    """单线程串行跑完整个计划，返回 ``{"parts": [...], "totals": {...}}``。

    * 逐件 → 逐类 → 逐路线；S0/S1 每件只做一次（缓存进 ctx）。
    * ``on_progress`` 收每条日志文本（异常吞掉，绝不影响几何）。
    * 退出前写件级报告与全局汇总（S9，缺模块只告警）。
    """
    if not isinstance(ctx, PipelineContext):
        raise TypeError(f"run() 需要 PipelineContext，拿到 {type(ctx).__name__}")
    started = time.perf_counter()

    settings.ensure_out(ctx.out_root)  # 建 _cache/_tmp/_report
    log = _make_logger(ctx, on_progress)

    # 路线偏好：调用方（API / CLI）可先塞 ctx.options["route_prefs"]；否则读磁盘
    options = getattr(ctx, "options", None)
    if not isinstance(options, dict):
        options = {}
        ctx.options = options
    prefs = options.get("route_prefs")
    if not isinstance(prefs, dict):
        prefs = route_prefs.load_prefs(route_prefs.prefs_path(ctx.out_root))
    options["route_prefs"] = prefs

    parts = [str(p).strip() for p in (ctx.parts or []) if str(p).strip()]
    if not parts:
        parts = scan_parts(ctx.src_dir)
    if not parts:
        raise RuntimeError(f"源目录里没有 .stp/.step：{ctx.src_dir}")
    ctx.parts = parts
    ctx.routes = normalize_routes(ctx.routes)  # None = 按模型默认（外径形腔 c1、其余 pipe）
    ctx.models = tuple(_normalize_names(ctx.models, MODEL_KEYS, "模型键"))

    route_count = len(ctx.routes) if ctx.routes else 1
    total = len(parts) * len(ctx.models) * route_count
    ctx._entity_cells_total = total
    ctx._entity_cells_done = 0
    ctx._entity_stopped = False
    ctx._entity_stop_logged = False
    route_text = "路 = " + ",".join(ctx.routes) if ctx.routes else "路 = 按模型默认（外径形腔 c1 / 其余 pipe）"
    log(
        f"实体化流水线：{len(parts)} 件 × {len(ctx.models)} 类 × 1 {route_text} × {route_count} = {total} 条"
        f"（force={ctx.force} resume={ctx.resume}）"
    )
    log(f"  源目录 {ctx.src_dir}")
    log(f"  输出根 {ctx.out_root}")
    if _stop_event(ctx) is not None:
        log("  已启用协作式停止（ctx.options['stop_event']）")

    reports: list[PartReport] = []
    for index, part in enumerate(parts, 1):
        log(f"[{index}/{len(parts)}] {part} 开始")
        specs = build_specs([part], ctx.models, ctx.routes, out_root=ctx.out_root, prefs=prefs)
        merged = _new_report(part, ctx)
        started_part = time.perf_counter()

        if _stop_at(ctx, log):  # 安全点②：件级，一件不碰直接记 stopped
            for spec in specs:
                for route in _spec_routes(spec, ctx.routes):
                    merged.add(_stopped_result(spec, route))
        else:
            for spec_index, spec in enumerate(specs):
                try:
                    partial = run_part(spec, ctx)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:  # 单件炸了不影响后面的件
                    if _prepare_cancelled(exc):
                        raise  # 停止请求：交给上层按 stopped 收尾
                    message = f"{type(exc).__name__}: {exc}"
                    log(f"  ✗ {part} / {spec.key} 整体失败：{message}")
                    for route in _spec_routes(spec, ctx.routes):
                        merged.add(_route_error(spec, route, message))
                    continue
                merged.results.extend(partial.results)
                merged.models.update(partial.models or {})
                for line in partial.logs:
                    _note(merged, line)
                if _stop_at(ctx, log):  # 该类里已经停下：剩下的类全部记 stopped
                    for rest in specs[spec_index + 1 :]:
                        for route in _spec_routes(rest, ctx.routes):
                            merged.add(_stopped_result(rest, route))
                    break

        merged.stopped = any(result.status == STATUS_STOPPED for result in merged.results)
        merged.finished = now_iso()
        merged.seconds = round(time.perf_counter() - started_part, 3)
        reports.append(merged)

        ok = sum(1 for r in merged.results if r.status in SUCCESS_STATUSES)
        suffix = f"，{sum(1 for r in merged.results if r.status == STATUS_STOPPED)} 条因停止未开始" if getattr(merged, "stopped", False) else ""
        log(
            f"[{index}/{len(parts)}] {part} 完成：{ok}/{len(merged.results)} 条成功"
            f"（{merged.seconds}s）{suffix}"
        )
        _write_part_report(merged, ctx, log)

    seconds = time.perf_counter() - started
    totals = _totals(reports, ctx, seconds)
    summary = _write_summary(reports, ctx, log)
    completed_cells = max(totals["results"] - totals["stopped"], 0)
    stopped = bool(totals["stopped"]) or bool(getattr(ctx, "_entity_stopped", False))
    if stopped:
        log(
            f"已按停止请求提前结束：{completed_cells}/{total} 个单元完成，"
            f"ok={totals['ok']} cached={totals['cached']} stopped={totals['stopped']}"
            f" error={totals['error']}，总耗时 {totals['seconds']}s"
        )
    else:
        log(
            f"全部完成：{totals['results']} 条，ok={totals['ok']} cached={totals['cached']}"
            f" error={totals['error']}，总耗时 {totals['seconds']}s"
        )
    # parts 给 json 友好的 dict；reports 留对象，方便 report.write_summary / 上层复用
    return {
        "parts": [_report_payload(report) for report in reports],
        "totals": totals,
        "summary": summary,
        "reports": reports,
        "stopped": stopped,
        "completed_cells": completed_cells,
        "total_cells": total,
    }


# ---------------------------------------------------------------- CLI 兼容入口

def main(argv=None) -> int:
    """``orchestrator.main``（契约 §9）：转给 ``backend/run_entity.py`` 的 argparse。"""
    backend = str(settings.BACKEND_DIR)
    import sys

    if backend not in sys.path:
        sys.path.insert(0, backend)
    import run_entity  # backend/run_entity.py，本身就是 CLI

    return run_entity.main(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
