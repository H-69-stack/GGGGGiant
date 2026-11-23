"""统一版捷安特 · 实体化流水线数据契约（唯一权威定义）。

规则：
* 模块顶层只 import 标准库；OCP / FreeCAD / trimesh 一律在函数内部延迟 import。
* 跨模块传递只用本文件的 dataclass 或纯 dict，必须 ``json.dumps`` 得动。
* 任何实现都不得改这里的字段名 —— 10 个子模块都按它编码。

见 ``Giant-Unified/ENTITY_PIPELINE.md``。
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

# ---------------------------------------------------------------- 路线与口径

ROUTES = ("c1", "pipe")
ROUTE_LABELS = {
    "c1": "c1（C1 外推 / 保形 4 面）",
    "pipe": "pipe（FreeCAD 重蒙皮）",
}

END_TREATMENTS = ("c1", "v1")
DEFAULT_END_TREATMENT = "c1"

#: 这些件走 v1（线性外推）口径，且外径形腔改走「口径形腔 → 保形」。
REFERENCE_PARTS = ("24TK_2052-2",)
REFERENCE_MODELS = ("成型_外径形腔",)


def _pref_impl(prefs, part: str, key: str | None) -> str:
    """从 ``prefs`` 里取显式 ``impl``（``""`` = 没配置）；不 import route_prefs 以免循环依赖。"""
    if not isinstance(prefs, dict) or key is None:
        return ""
    try:
        parts = prefs.get("parts")
        entry = parts.get(str(part)) if isinstance(parts, dict) else None
        models = entry.get("models") if isinstance(entry, dict) else None
        model = models.get(str(key)) if isinstance(models, dict) else None
        value = model.get("impl") if isinstance(model, dict) else None
    except Exception:  # noqa: BLE001 - 偏好文件脏了也不能把流水线带崩
        return ""
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    return text if text in ("c1", "121") else ""


def end_treatment_for(part: str, key: str | None = None, prefs=None) -> str:
    """该件（某模型）测量/导出用哪个端部口径（``c1`` / ``v1``）。

    * ``prefs`` 显式把该模型设成 ``121`` → ``v1``；设成 ``c1`` → ``c1``；
    * 未显式配置时保持原判据：:data:`REFERENCE_PARTS` 里的件统一 ``v1``。

    ``key`` 为空时只能按件判定；同件 5 类共用一个 measured.json，所以调用方
    （``orchestrator.build_specs``）按「件级」取口径，避免一份缓存出现两种口径。
    """
    impl = _pref_impl(prefs, part, key)
    if impl == "121":
        return "v1"
    if impl == "c1":
        return "c1"
    return "v1" if part in REFERENCE_PARTS else DEFAULT_END_TREATMENT


def uses_reference_route(part: str, key: str, prefs=None) -> bool:
    """该件的该模型在 c1 路里是否走 121 保形（而不是 C1 外推）。

    ``prefs`` 显式配置优先（``121`` → True，``c1`` → False）；否则保持原判据。
    """
    impl = _pref_impl(prefs, part, key)
    if impl == "121":
        return True
    if impl == "c1":
        return False
    return part in REFERENCE_PARTS and key in REFERENCE_MODELS


# ---------------------------------------------------------------- 模型注册表


@dataclass(frozen=True)
class ModelDef:
    key: str          # 中文键，也是 STEP 文件名，例如 "成型_外径形腔"
    op: str           # core.rfm.processors 的 operation：shaping/belling/flattening/bending
    op_label: str     # 中文工序名：成型/扩口/压扁/压弯
    name: str         # 中文模型名：外径形腔/扩口腔体/...
    contour: str      # outer / solid（c1 路取轮廓的口径）
    slug: str         # ascii 短名，供日志/临时文件
    c1_kind: str      # measured_outer / selfref


MODELS: tuple[ModelDef, ...] = (
    ModelDef("成型_外径形腔", "shaping", "成型", "外径形腔", "outer", "shaping_cavity", "measured_outer"),
    ModelDef("扩口_扩口腔体", "belling", "扩口", "扩口腔体", "solid", "belling_cavity", "selfref"),
    ModelDef("扩口_扩口芯轴", "belling", "扩口", "扩口芯轴", "solid", "belling_mandrel", "selfref"),
    ModelDef("压扁_压扁模型", "flattening", "压扁", "压扁模型", "solid", "flattening_model", "selfref"),
    ModelDef("压弯_压弯模型", "bending", "压弯", "压弯模型", "solid", "bending_model", "selfref"),
)

MODEL_BY_KEY: dict[str, ModelDef] = {m.key: m for m in MODELS}
MODEL_KEYS: tuple[str, ...] = tuple(m.key for m in MODELS)
#: 已下线，只用于过滤历史目录
MOLD_MARKER = "反切模具"


def model_def(key: str) -> ModelDef:
    try:
        return MODEL_BY_KEY[key]
    except KeyError:
        raise KeyError(f"未知模型键：{key}（可选：{', '.join(MODEL_KEYS)}）") from None


# ---------------------------------------------------------------- 默认路线

#: 默认路线：外径形腔只走 c1（C1 外推 / 2052-2 口径保形），其余四类只走 pipe。
#: 显式传 ``--routes c1,pipe`` 时仍可双跑对照（计划翻倍）。
DEFAULT_ROUTE_BY_KEY: dict[str, str] = {
    key: ("c1" if key == "成型_外径形腔" else "pipe") for key in MODEL_KEYS
}


def default_route(key: str) -> str:
    """该模型默认走哪条路线（未显式指定路线时用）。"""
    try:
        return DEFAULT_ROUTE_BY_KEY[key]
    except KeyError:
        raise KeyError(f"未知模型键：{key}（可选：{', '.join(MODEL_KEYS)}）") from None


def routes_for(keys=None) -> tuple:
    """这些模型实际会用到的路线集合（按 :data:`ROUTES` 顺序，供汇总/展示）。"""
    wanted = {default_route(key) for key in (keys or MODEL_KEYS)}
    return tuple(route for route in ROUTES if route in wanted)


def normalize_routes(requested=None) -> Optional[tuple]:
    """把 ``--routes`` 取值规整为 ``None``（按模型默认）或非空元组。

    * ``None`` / 空列表 / 空字符串 → ``None``：每个模型各走自己的默认路线；
    * 显式列表 → 校验、去重保序，返回元组（此时每个模型都跑这些路线）。
    """
    if requested is None:
        return None
    if isinstance(requested, str):
        requested = [requested]
    out = []
    for item in requested:
        item = str(item).strip()
        if item and item not in out:
            out.append(item)
    if not out:
        return None
    unknown = [item for item in out if item not in ROUTES]
    if unknown:
        raise ValueError(f"未知路线：{', '.join(unknown)}（可选：{', '.join(ROUTES)}）")
    return tuple(out)



# ---------------------------------------------------------------- 基础结构


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class ModelSpec:
    """一个「件 + 模型」的待办单元。"""

    part: str
    key: str
    step: str                       # 5 类模型 STEP 的绝对路径
    end_treatment: str              # c1 / v1
    out_dir: str                    # 该件的输出目录（<out>/<part>）
    #: 可选：该件的 measured.json（成型两类需要）
    measured_path: Optional[str] = None
    #: 可选：参考件 STEP（121 保形用；自参考时等于 step）
    reference_step: Optional[str] = None
    #: 分型方向："auto" / "X" / "Y" / "Z"（加在末尾，保持旧位置参数兼容）
    direction: str = "auto"

    @property
    def defn(self) -> ModelDef:
        return model_def(self.key)

    def out_path(self, route: str) -> str:
        import os

        sub = {"c1": "10_c1", "pipe": "20_pipe"}[route]
        return os.path.join(self.out_dir, sub, f"{self.key}__{route}.step")


@dataclass
class Measured:
    """S0 测量结果。"""

    part: str
    path: str
    sections_full: list
    analysis: dict
    end_treatment: str
    seconds: float = 0.0
    cached: bool = False


@dataclass
class CheckRecord:
    """几何体检（只是记录，不判失败）。"""

    faces: Optional[int] = None
    edges: Optional[int] = None
    solids: Optional[int] = None
    free_edges: Optional[int] = None
    valid: Optional[bool] = None
    volume_mm3: Optional[float] = None
    bbox: Optional[list] = None
    parting_edges: Optional[int] = None
    extra: dict = field(default_factory=dict)


@dataclass
class RouteResult:
    """一条路对一个模型的一次产出。"""

    part: str
    key: str
    route: str
    status: str = "pending"          # ok / error / skipped / cached / failed(≡error) / stopped
    step: Optional[str] = None
    stl: Optional[str] = None
    volume_mm3: Optional[float] = None
    ref_volume_mm3: Optional[float] = None
    volume_error_pct: Optional[float] = None
    faces: Optional[int] = None
    edges: Optional[int] = None
    solids: Optional[int] = None
    free_edges: Optional[int] = None
    parting_edges: Optional[int] = None
    valid: Optional[bool] = None
    bbox: Optional[list] = None
    ref_bbox: Optional[list] = None
    bbox_dev_mm: Optional[float] = None
    direction: Optional[Any] = None  # "X"/"Y"/"Z" 或 [x, y, z]
    direction_label: Optional[str] = None
    rule: Optional[str] = None       # symmetry / major_axis / bend_plane / fixed
    sections_used: Optional[int] = None
    sections_total: Optional[int] = None
    attempts: Optional[list] = None
    seconds: float = 0.0
    logs: list = field(default_factory=list)
    error: Optional[str] = None
    artifacts: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class PartReport:
    """一个件的全部产出。"""

    part: str
    source_step: str
    end_treatment: str
    measured_path: Optional[str] = None
    results: list = field(default_factory=list)
    started: str = field(default_factory=now_iso)
    finished: Optional[str] = None
    seconds: float = 0.0
    logs: list = field(default_factory=list)
    models: dict = field(default_factory=dict)   # {key: 5 类 STEP 路径}

    def add(self, result: RouteResult) -> RouteResult:
        self.results.append(result)
        return result

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["results"] = [r.to_dict() if isinstance(r, RouteResult) else r for r in self.results]
        return d


@dataclass
class PipelineContext:
    """一次流水线运行的全部上下文（orchestrator 创建，routes 只读）。"""

    src_dir: str
    out_root: str
    parts: list = field(default_factory=list)       # 待跑件号
    #: ``None`` = 每个模型走 :func:`default_route`（外径形腔 c1、其余 pipe）；
    #: 显式元组 = 每个模型都跑这些路线（如 ``("c1","pipe")`` 双跑对照）。
    routes: Optional[tuple] = None
    models: tuple = MODEL_KEYS
    force: bool = False
    resume: bool = True
    options: dict = field(default_factory=dict)     # half_points / max_sections / ...
    log: Callable[[str], None] = print
    #: 运行时缓存：{part: Measured}
    measured: dict = field(default_factory=dict)

    def part_dir(self, part: str) -> str:
        import os

        return os.path.join(self.out_root, part)


# ---------------------------------------------------------------- 小工具


def json_safe(obj: Any) -> Any:
    """把 numpy / Path 之类的对象转成可 json 的值（尽量不 import numpy）。"""
    import os

    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, os.PathLike):
        return os.fspath(obj)
    tolist = getattr(obj, "tolist", None)
    if callable(tolist):
        try:
            return tolist()
        except Exception:
            pass
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return str(obj)


def result_from_dict(d: dict) -> RouteResult:
    """从 dict 还原（读历史 report.json 用）。"""
    fields = {f.name for f in dataclasses.fields(RouteResult)}
    return RouteResult(**{k: v for k, v in (d or {}).items() if k in fields})


def iter_results(report: dict) -> Iterable[dict]:
    return iter((report or {}).get("results") or [])
