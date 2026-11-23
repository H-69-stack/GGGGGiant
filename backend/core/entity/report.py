"""统一版捷安特 · 实体化流水线：报告生成（S9）。

只做一件事：把 :class:`~.contracts.PartReport` 落成「人看 + 机器读」两份文件。

    write_part_report(report, out_dir) -> dict
        ``<out_dir>/report.json`` + ``<out_dir>/report.md``（件级）。

    write_summary(reports, out_root) -> dict
        ``<out_root>/_report/summary.json`` + ``<out_root>/_report/summary.md``（全局）。

两个函数都接受 ``PartReport`` 或它的 ``to_dict()``（及其列表 / ``{件: 报告}``），
并且**容忍参数顺序颠倒**（``ENTITY_PIPELINE.md`` §9 写的是 ``(report, out_dir)``，
早期 orchestrator 调用是 ``(part_dir, report)``，两种都能跑）。

Markdown 结构
-------------

件级 ``report.md``：

1. 标题「Giant-Unified 实体化流水线报告」+ 生成时间 / 件号 / 端部口径 / 环境；
2. 每类模型一节，表格列固定为
   ``路线 | 状态 | 体积 mm³ | 参考体积 | 偏差% | 面数 | 边数 | 自由边 | 分型边 | valid | 分型方向 | 耗时 s``；
3. 「合规与问题」：``checks.conformance_issues()`` 的结论 + ``RouteResult.problems`` / ``error``。

全局 ``summary.md``：环境 → 每条路线一张「件 × 5 类」状态矩阵（ok/error/skipped/cached）
+ 合计行 → 逐件概览 → 体积与偏差 → 合规与问题 → 错误清单 → 超阈值偏差。

实现约定
--------

* 顶层只 import 标准库 + :mod:`.contracts` + :mod:`.checks` + :mod:`.process_summary`
  （三者都是纯标准库，OCP 只在函数内部延迟加载），没装 OCP 的环境里也能 import 本模块；
* **不引第三方库**：列宽用自写的 :func:`text_width` / :func:`pad` 按显示宽度补齐
  （中文、全角标点算 2 宽度，组合符算 0），表格因此是「源码里就对齐」的 Markdown 表格；
* 写盘一律 UTF-8 **无 BOM**、``\n`` 换行、JSON ``ensure_ascii=False``；
* 先写 ``.tmp`` 再 :func:`os.replace`，中途崩掉不会留下半个文件；
* 只读 ``PartReport`` / ``RouteResult``，不改任何契约字段。
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .checks import conformance_issues, summarize
from .contracts import (
    DEFAULT_ROUTE_BY_KEY,
    MODEL_BY_KEY,
    MODEL_KEYS,
    ROUTES,
    ROUTE_LABELS,
    PartReport,
    RouteResult,
    json_safe,
    now_iso,
)
from .process_summary import summary_markdown as process_summary_markdown

__all__ = [
    "REPORT_JSON",
    "REPORT_MD",
    "SUMMARY_JSON",
    "SUMMARY_MD",
    "VOLUME_TOLERANCE_PCT",
    "environment",
    "text_width",
    "pad",
    "render_table",
    "part_markdown",
    "summary_markdown",
    "write_part_report",
    "write_summary",
]

# ---------------------------------------------------------------- 常量

REPORT_JSON = "report.json"
REPORT_MD = "report.md"
SUMMARY_DIR = "_report"
SUMMARY_JSON = "summary.json"
SUMMARY_MD = "summary.md"

#: 报告 schema 版本（机器读的消费方按它判结构）。
SCHEMA_PART = "entity-part-report/1"
SCHEMA_SUMMARY = "entity-summary/1"

#: 体积偏差告警阈值（%，沿用 §6.2 pipe 路的验收口径）。
VOLUME_TOLERANCE_PCT = 1.0

#: 状态归一：契约里是 ok/error/skipped/cached/stopped，历史代码里还出现过同义写法。
STATUS_ALIASES = {
    "ok": "ok",
    "success": "ok",
    "done": "ok",
    "passed": "ok",
    "cached": "cached",
    "cache": "cached",
    "skipped": "skipped",
    "skip": "skipped",
    "pending": "skipped",
    "stopped": "stopped",
    "stop": "stopped",
    "aborted": "stopped",
    "error": "error",
    "failed": "error",
    "fail": "error",
    "exception": "error",
    "timeout": "error",
    "blocked": "error",
}

END_TREATMENT_TEXT = {"c1": "c1（形状外推）", "v1": "v1（线性外推）"}

_FLAG_LABELS = {
    "faces_ne_4": "面数 ≠ 4",
    "solids_ne_1": "实体数 ≠ 1",
    "free_edges_ne_0": "自由边 ≠ 0",
    "parting_ne_2": "共享分型边 ≠ 2",
    "invalid": "OCC 有效性未通过",
}

_MISSING = "—"

#: 侧栏表：模型明细列的固定表头与对齐方式（左 / 右）。
_MODEL_HEADERS = (
    "路线",
    "状态",
    "体积 mm³",
    "参考体积",
    "偏差%",
    "面数",
    "边数",
    "自由边",
    "分型边",
    "valid",
    "分型方向",
    "耗时 s",
)
_MODEL_ALIGNS = (
    "left",
    "left",
    "right",
    "right",
    "right",
    "right",
    "right",
    "right",
    "right",
    "left",
    "left",
    "right",
)


# ---------------------------------------------------------------- 显示宽度


def _char_width(char: str) -> int:
    """单个字符的显示宽度：CJK/全角 2，组合符 0，其余 1。"""
    if not char:
        return 0
    if unicodedata.combining(char):
        return 0
    category = unicodedata.category(char)
    if category in ("Mn", "Me", "Cf"):
        return 0
    if category.startswith("C"):  # 控制符等不可见
        return 0
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 2
    return 1


def text_width(text: Any) -> int:
    """字符串的显示宽度（中文算 2 格，组合符算 0 格）。"""
    return sum(_char_width(char) for char in str(text))


def pad(text: Any, width: int, align: str = "left", fill: str = " ") -> str:
    """按**显示宽度**补齐到 ``width`` 格；超宽不截断（原样返回）。

    ``align``：``left`` / ``right`` / ``center``。
    """
    text = str(text)
    gap = int(width) - text_width(text)
    if gap <= 0:
        return text
    if align == "right":
        return fill * gap + text
    if align == "center":
        left = gap // 2
        return fill * left + text + fill * (gap - left)
    return text + fill * gap


def _cell(value: Any) -> str:
    """表格单元格文本：竖线转义、换行压平（Markdown 表格不能断行）。"""
    text = "" if value is None else str(value)
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text.replace("|", "\\|").strip()


def render_table(
    headers: Sequence[Any],
    rows: Iterable[Sequence[Any]],
    aligns: Optional[Sequence[str]] = None,
    indent: str = "",
) -> list[str]:
    """把二维数据渲染成**源码里就对齐**的 Markdown 表格（行列表）。

    列宽按 :func:`text_width` 计算，中文列因此不会歪；数字列默认右对齐。
    空表也会输出表头 + 分隔行。
    """
    heads = [_cell(item) for item in headers]
    columns = len(heads)
    body: list[list[str]] = []
    for row in rows:
        cells = [_cell(item) for item in row]
        if len(cells) < columns:
            cells.extend([""] * (columns - len(cells)))
        body.append(cells[:columns])

    if aligns is None:
        aligns = ["left"] * columns
    aligns = list(aligns) + ["left"] * (columns - len(aligns))

    widths = [max(3, text_width(head)) for head in heads]
    for cells in body:
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], text_width(cell))

    def line(cells: Sequence[str], mode: str) -> str:
        parts: list[str] = []
        for index, cell in enumerate(cells):
            width = widths[index]
            if mode == "head":
                parts.append(pad(cell, width, "center"))
            else:
                parts.append(pad(cell, width, aligns[index] if mode == "body" else "left"))
        return indent + "| " + " | ".join(parts) + " |"

    lines = [line(heads, "head")]

    markers: list[str] = []
    for index in range(columns):
        width = widths[index]
        align = aligns[index]
        if align == "right":
            markers.append(pad("-" * (width - 1) + ":", width, "left"))
        elif align == "center":
            markers.append(pad(":" + "-" * max(1, width - 2) + ":", width, "left"))
        else:
            markers.append(pad(":" + "-" * max(1, width - 1), width, "left"))
    lines.append(indent + "| " + " | ".join(markers) + " |")

    for cells in body:
        lines.append(line(cells, "body"))
    return lines


# ---------------------------------------------------------------- 数值与文本格式化


def _num(value: Any) -> Optional[float]:
    """转有限 float，转不动给 None（nan/inf 当没有）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    return None if number is None else int(number)


def _fmt_volume(value: Any) -> str:
    """体积：千分位 + 3 位小数（与 §8.1 金标准同格式）。"""
    number = _num(value)
    return _MISSING if number is None else f"{number:,.3f}"


def _fmt_pct(value: Any) -> str:
    number = _num(value)
    return _MISSING if number is None else f"{number:.3f}%"


def _fmt_int(value: Any) -> str:
    number = _int(value)
    return _MISSING if number is None else f"{number:,}"


def _fmt_bool(value: Any) -> str:
    if value is None:
        return _MISSING
    return "是" if bool(value) else "否"


def _fmt_seconds(value: Any) -> str:
    number = _num(value)
    return _MISSING if number is None else f"{number:.2f}"


def _fmt_direction(result: dict) -> str:
    """分型方向：优先中文标注 ``direction_label``，否则 ``(x, y, z)``。"""
    label = result.get("direction_label")
    if label:
        return str(label)
    direction = result.get("direction")
    if direction is None:
        return _MISSING
    if isinstance(direction, str):
        return direction
    if isinstance(direction, (list, tuple)):
        numbers = [_num(item) for item in direction]
        if numbers and all(number is not None for number in numbers):
            return "(" + ", ".join(f"{number:g}" for number in numbers) + ")"
        return str(direction)
    return str(direction)


def _status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "other"
    return STATUS_ALIASES.get(text, text)


def _code(text: Any) -> str:
    """反引号包裹（路径 / 文件名在 Markdown 里更安全）。"""
    raw = str(text or "").strip()
    if not raw:
        return _MISSING
    return "`" + raw.replace("`", "'") + "`"


# ---------------------------------------------------------------- 归一化


def _looks_like_report(value: Any) -> bool:
    if isinstance(value, RouteResult):
        # 单条产出也有 to_dict()，但它是「结果」不是「报告」，不能当报告展开。
        return False
    if isinstance(value, PartReport):
        return True
    if isinstance(value, dict):
        return "results" in value or "part" in value
    # 鸭子类型：任何带 to_dict() 的报告替身（测试替身 / 未来重构）都认。
    return callable(getattr(value, "to_dict", None))


def _report_dict(report: Any) -> dict:
    """``PartReport`` / dict → 纯 dict（``json_safe`` 过一遍）。"""
    if isinstance(report, PartReport):
        data = report.to_dict()
    elif isinstance(report, dict):
        data = dict(report)
    elif callable(getattr(report, "to_dict", None)):
        data = report.to_dict()
        if not isinstance(data, dict):
            raise TypeError(
                "报告的 to_dict() 应返回 dict，收到 "
                f"{type(data).__name__}"
            )
    else:
        raise TypeError(
            "报告需要一个 PartReport 或它的 to_dict()，收到 "
            f"{type(report).__name__}"
        )
    return json_safe(data)


def _result_dict(result: Any) -> dict:
    if isinstance(result, RouteResult):
        return json_safe(result.to_dict())
    if isinstance(result, dict):
        return json_safe(dict(result))
    return {}


def _results(report: dict) -> list[dict]:
    return [_result_dict(item) for item in (report.get("results") or [])]


def _iter_report_dicts(reports: Any) -> list[dict]:
    """把入参归一成 ``[报告 dict]``（容忍单报告 / 列表 / ``{件: 报告}``）。"""
    out: list[dict] = []
    if reports is None:
        return out
    if isinstance(reports, RouteResult):
        return [{"part": reports.part, "results": [json_safe(reports.to_dict())]}]
    if _looks_like_report(reports):
        return [_report_dict(reports)]
    if isinstance(reports, (list, tuple, set)):
        for item in reports:
            if _looks_like_report(item):
                out.append(_report_dict(item))
            elif isinstance(item, RouteResult):  # 裸结果：补成无头报告
                out.append({"part": item.part, "results": [json_safe(item.to_dict())]})
            elif isinstance(item, dict):  # 一条裸 RouteResult：补成无头报告
                out.append({"part": str(item.get("part") or ""), "results": [json_safe(item)]})
        return out
    if isinstance(reports, dict):
        if isinstance(reports.get("parts"), list) and not reports.get("results"):
            return _iter_report_dicts(reports["parts"])
        for name, value in reports.items():
            if _looks_like_report(value):
                report = _report_dict(value)
                report.setdefault("part", str(name))
                out.append(report)
            elif isinstance(value, (list, tuple)):
                out.append({"part": str(name), "results": json_safe(list(value))})
        return out
    return out


def _results_with_part(report: dict) -> list[dict]:
    """补上 ``part`` 字段（老 report.json 里可能只有报告级 part）。"""
    part = str(report.get("part") or "")
    results: list[dict] = []
    for result in _results(report):
        if not result.get("part"):
            result["part"] = part
        results.append(result)
    return results


def _status_counts(results: Iterable[dict]) -> dict:
    counts = {"total": 0, "ok": 0, "cached": 0, "error": 0, "skipped": 0, "stopped": 0,
              "other": 0, "seconds": 0.0}
    for result in results:
        counts["total"] += 1
        status = _status(result.get("status"))
        counts[status if status in counts else "other"] += 1
        seconds = _num(result.get("seconds"))
        if seconds:
            counts["seconds"] = round(counts["seconds"] + seconds, 3)
    return counts


def _conformance(result: dict) -> list[str]:
    """``checks.conformance_issues`` 的薄包装（异常不外溢）。"""
    try:
        return list(conformance_issues(result) or [])
    except Exception:  # noqa: BLE001 - 报告不许因为体检模块炸掉
        return []


def _issue_lines(result: dict) -> list[str]:
    """一条产出的问题文本：``problems`` + 合规结论 + ``error``。"""
    lines: list[str] = []
    for problem in result.get("problems") or []:
        text = str(problem).strip()
        if text:
            lines.append(text)
    for issue in _conformance(result):
        if issue not in lines:
            lines.append(issue)
    error = str(result.get("error") or "").strip()
    if error:
        lines.append("error：" + error.replace("\n", " "))
    return lines


# ---------------------------------------------------------------- 运行环境


_ENV_CACHE: Optional[dict] = None


def environment(refresh: bool = False) -> dict:
    """Python / OCP 版本 + FreeCAD 路径（缓存一次，``refresh`` 可重取）。"""
    global _ENV_CACHE
    if _ENV_CACHE is not None and not refresh:
        return dict(_ENV_CACHE)

    info: dict = {
        "python": platform.python_version(),
        "python_exe": sys.executable,
        "platform": f"{platform.system()} {platform.release()}",
        "ocp": None,
        "ocp_error": None,
        "freecad_home": None,
        "freecad_cmd": None,
        "freecad_exe": None,
        "settings_error": None,
    }

    try:
        import OCP

        version = getattr(OCP, "__version__", None)
        if not version:
            try:
                from importlib.metadata import version as _pkg_version

                version = _pkg_version("cadquery-ocp")
            except Exception:  # noqa: BLE001 - 包元数据缺失不影响报告
                version = None
        info["ocp"] = str(version) if version else "未知"
    except Exception as exc:  # noqa: BLE001 - 没装 OCP 也要能出报告
        info["ocp_error"] = f"{type(exc).__name__}: {exc}"

    try:
        from . import settings

        info["freecad_home"] = str(settings.FREECAD_HOME)
        cmd = settings.freecad_cmd()
        exe = settings.freecad_exe()
        info["freecad_cmd"] = str(cmd) if cmd else None
        info["freecad_exe"] = str(exe) if exe else None
    except Exception as exc:  # noqa: BLE001
        info["settings_error"] = f"{type(exc).__name__}: {exc}"

    _ENV_CACHE = info
    return dict(info)


def _env_line(env: dict) -> str:
    ocp = env.get("ocp") or ("不可用" if env.get("ocp_error") else "未知")
    freecad = env.get("freecad_cmd") or env.get("freecad_exe") or "未找到"
    return f"Python {env.get('python') or '?'} · OCP {ocp} · FreeCAD {freecad}"


def _end_treatment_text(value: Any) -> str:
    text = str(value or "").strip()
    return END_TREATMENT_TEXT.get(text, text or _MISSING)


# ---------------------------------------------------------------- 写盘


def _write_text(path: Path, text: str) -> int:
    """UTF-8 无 BOM + ``\n`` 换行，先写 tmp 再原子替换；返回字节数。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return len(payload)


def _write_json(path: Path, data: Any) -> int:
    text = json.dumps(json_safe(data), ensure_ascii=False, indent=2) + "\n"
    return _write_text(path, text)


# ---------------------------------------------------------------- 件级 Markdown


def _model_heading(key: str, index: int) -> str:
    defn = MODEL_BY_KEY.get(key)
    if defn is None:
        return f"## {index}. {key}"
    return f"## {index}. {defn.op_label} · {defn.name}（`{key}`）"


def _model_rows(results: list[dict]) -> list[list[str]]:
    """按路线顺序出表行（未知路线排后面）。"""
    order = list(ROUTES)
    known = [route for route in order if any(str(r.get("route")) == route for r in results)]
    extras = sorted(
        {
            str(r.get("route"))
            for r in results
            if str(r.get("route")) not in order
        }
    )
    rows: list[list[str]] = []
    for route in known + extras:
        for result in results:
            if str(result.get("route")) != route:
                continue
            rows.append(
                [
                    ROUTE_LABELS.get(route, route),
                    _status(result.get("status")),
                    _fmt_volume(result.get("volume_mm3")),
                    _fmt_volume(result.get("ref_volume_mm3")),
                    _fmt_pct(result.get("volume_error_pct")),
                    _fmt_int(result.get("faces")),
                    _fmt_int(result.get("edges")),
                    _fmt_int(result.get("free_edges")),
                    _fmt_int(result.get("parting_edges")),
                    _fmt_bool(result.get("valid")),
                    _fmt_direction(result),
                    _fmt_seconds(result.get("seconds")),
                ]
            )
    return rows


def part_markdown(
    report: dict,
    env: Optional[dict] = None,
    process_summary: Optional[dict] = None,
) -> str:
    """件级 Markdown（标题 / 环境 → 每类模型一节 → 合规与问题 → 工艺摘要）。

    ``process_summary`` 不传时用报告里已有的 ``report["process_summary"]``；
    两者都没有时输出与旧版逐字节一致（不追加章节）。
    """
    report = _report_dict(report)
    env = env or environment()
    part = str(report.get("part") or "") or "（未知件号）"
    results = _results_with_part(report)
    counts = _status_counts(results)
    lines: list[str] = []

    lines.append("# Giant-Unified 实体化流水线报告")
    lines.append("")
    lines.append(f"**件号** {part} · **端部口径** {_end_treatment_text(report.get('end_treatment'))}")
    lines.append("")
    lines.append(f"- 生成时间：{report.get('generated') or now_iso()}")
    lines.append(f"- 件号：{part}")
    lines.append(f"- 端部口径：{_end_treatment_text(report.get('end_treatment'))}")
    lines.append(f"- 源管件：{_code(report.get('source_step'))}")
    lines.append(f"- 测量缓存：{_code(report.get('measured_path'))}")
    models = report.get("models") or {}
    if isinstance(models, dict) and models:
        lines.append(f"- 模型 STEP：{len(models)} 个（{', '.join(_code(k) for k in models)}）")
    lines.append(
        "- 产出：{total} 条（ok {ok} · cached {cached} · skipped {skipped} · error {error}）"
        " · 耗时 {seconds} s".format(
            total=counts["total"],
            ok=counts["ok"],
            cached=counts["cached"],
            skipped=counts["skipped"],
            error=counts["error"],
            seconds=_fmt_seconds(report.get("seconds") or counts["seconds"]),
        )
    )
    lines.append(f"- 开始 / 结束：{report.get('started') or _MISSING} → {report.get('finished') or _MISSING}")
    lines.append(f"- 环境：{_env_line(env)}")
    lines.append("")
    lines.append("_体积单位 mm³；偏差为相对参考体积的绝对百分比。_")
    lines.append("")

    # 1) 每类模型一节
    by_key: dict[str, list[dict]] = {}
    for result in results:
        by_key.setdefault(str(result.get("key") or ""), []).append(result)

    ordered: list[str] = [key for key in MODEL_KEYS if key in by_key]
    ordered += sorted(key for key in by_key if key not in MODEL_KEYS)

    index = 0
    for key in ordered:
        index += 1
        lines.append(_model_heading(key, index))
        lines.append("")
        rows = _model_rows(by_key[key])
        lines.extend(render_table(_MODEL_HEADERS, rows, _MODEL_ALIGNS))
        lines.append("")

    if not ordered:
        index += 1
        lines.append(f"## {index}. 模型产出")
        lines.append("")
        lines.append("本次没有任何模型产出。")
        lines.append("")

    # 2) 合规与问题
    index += 1
    lines.append(f"## {index}. 合规与问题")
    lines.append("")
    expectations = (
        "规范值：面数 4 · 实体 1 · 自由边 0 · 共享分型边 2 · valid 为真"
        "（仅供对照，不判失败）"
    )
    lines.append(expectations)
    lines.append("")
    issue_rows: list[list[str]] = []
    for result in results:
        key = str(result.get("key") or "")
        route = str(result.get("route") or "")
        for text in _issue_lines(result):
            issue_rows.append([key, ROUTE_LABELS.get(route, route), _status(result.get("status")), text])
    if issue_rows:
        lines.extend(
            render_table(
                ("类", "路线", "状态", "问题"),
                issue_rows,
                ("left", "left", "left", "left"),
            )
        )
        lines.append("")
    else:
        lines.append("未发现合规问题，也无 error / problems。")
        lines.append("")

    logs = [str(item) for item in (report.get("logs") or [])]
    if logs:
        lines.append("### 运行日志（末 20 条）")
        lines.append("")
        lines.append("```text")
        lines.extend(logs[-20:])
        lines.append("```")
        lines.append("")

    summary = process_summary if isinstance(process_summary, dict) else report.get("process_summary")
    if isinstance(summary, dict) and summary:
        lines.append(process_summary_markdown(summary, heading="## 工艺摘要", part=part).rstrip("\n"))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------- 全局 Markdown


def _ordered_parts(reports: list[dict]) -> list[str]:
    seen: list[str] = []
    for report in reports:
        part = str(report.get("part") or "")
        if part and part not in seen:
            seen.append(part)
    return seen


def _ordered_models(results: list[dict]) -> list[str]:
    seen: list[str] = [key for key in MODEL_KEYS if any(str(r.get("key")) == key for r in results)]
    for result in results:
        key = str(result.get("key") or "")
        if key and key not in seen:
            seen.append(key)
    return seen


def _ordered_routes(results: list[dict]) -> list[str]:
    seen: list[str] = [route for route in ROUTES if any(str(r.get("route")) == route for r in results)]
    for result in results:
        route = str(result.get("route") or "")
        if route and route not in seen:
            seen.append(route)
    return seen


def summary_markdown(
    reports: Any,
    *,
    out_root: Any = None,
    env: Optional[dict] = None,
    tolerance_pct: float = VOLUME_TOLERANCE_PCT,
    generated: Optional[str] = None,
) -> str:
    """全局 Markdown（环境 → 状态矩阵 → 逐件概览 → 体积 → 合规 → error → 超阈值）。"""
    env = env or environment()
    report_list = _iter_report_dicts(reports)
    results: list[dict] = []
    for report in report_list:
        results.extend(_results_with_part(report))

    parts = _ordered_parts(report_list)
    for result in results:
        part = str(result.get("part") or "")
        if part and part not in parts:
            parts.append(part)
    observed_models = _ordered_models(results)
    observed_routes = _ordered_routes(results)
    # 矩阵画满 5 类 × 2 路（文档口径），本次没产出的格子写 —，便于一眼看出缺哪块。
    models = list(MODEL_KEYS) + [key for key in observed_models if key not in MODEL_KEYS]
    routes = list(ROUTES) + [route for route in observed_routes if route not in ROUTES]
    counts = _status_counts(results)

    # (part, key, route) → 状态
    status_map: dict[tuple[str, str, str], str] = {}
    for result in results:
        status_map[
            (str(result.get("part") or ""), str(result.get("key") or ""), str(result.get("route") or ""))
        ] = _status(result.get("status"))

    # 默认路线语义：每类只跑自己那条路线时，表头别说成「交叉展开 2 路」。
    default_mix = bool(results) and all(
        str(result.get("route") or "") == DEFAULT_ROUTE_BY_KEY.get(str(result.get("key") or ""))
        for result in results
    )
    scope = "按模型默认分派" if default_mix else "交叉展开"
    used_routes = ", ".join(observed_routes) or _MISSING
    lines: list[str] = []
    lines.append("# Giant-Unified 实体化流水线汇总报告")
    lines.append("")
    lines.append(f"- 生成时间：{generated or now_iso()}")
    lines.append(f"- 输出根：{_code(out_root) if out_root else _MISSING}")
    lines.append(
        f"- 矩阵：{len(parts)} 件 × {len(models)} 类 × {len(routes)} 路（{scope}）；"
        f"本次有产出 {len(results)} 条 / {len(observed_models)} 类 / {len(observed_routes)} 路"
    )
    if default_mix:
        lines.append(f"- 路线：{used_routes}（按模型默认：外径形腔 c1，其余 pipe）")
    else:
        lines.append(f"- 路线：{used_routes}")
    lines.append(
        "- 产出：{total} 条（ok {ok} · cached {cached} · skipped {skipped} · error {error}）".format(
            total=counts["total"],
            ok=counts["ok"],
            cached=counts["cached"],
            skipped=counts["skipped"],
            error=counts["error"],
        )
    )
    lines.append(f"- 环境：{_env_line(env)}")
    lines.append(f"- 体积偏差告警阈值：{_fmt_pct(tolerance_pct)}")
    lines.append("")

    index = 0

    # 1) 每条路线一张矩阵表
    for route in routes:
        index += 1
        lines.append(f"## {index}. 状态矩阵 · {ROUTE_LABELS.get(route, route)}")
        lines.append("")
        lines.append("单元格是该「件 × 类」在本次运行里的状态（ok / error / skipped / cached），"
                     f"`{_MISSING}` 表示没有产出；合计行的模型列是 `ok/总数`，末四列是计数合计。")
        lines.append("")
        table_rows: list[list[str]] = []
        totals = {"ok": 0, "cached": 0, "skipped": 0, "error": 0}
        ok_by_model = {model: 0 for model in models}
        total_by_model = {model: 0 for model in models}
        for part in parts:
            row = [part]
            part_totals = {"ok": 0, "cached": 0, "skipped": 0, "error": 0}
            for model in models:
                status = status_map.get((part, model, route))
                row.append(_MISSING if status is None else status)
                if status is None:
                    continue
                total_by_model[model] += 1
                if status == "ok":
                    ok_by_model[model] += 1
                if status in part_totals:
                    part_totals[status] += 1
                    totals[status] += 1
            row.extend(_fmt_int(part_totals[name]) for name in ("ok", "cached", "skipped", "error"))
            table_rows.append(row)
        # 合计行：模型列 ok/总数，其余列计数合计
        total_row = ["合计"] + [
            f"{ok_by_model[model]}/{total_by_model[model]}" if total_by_model[model] else _MISSING
            for model in models
        ] + [_fmt_int(totals["ok"]), _fmt_int(totals["cached"]), _fmt_int(totals["skipped"]), _fmt_int(totals["error"])]
        table_rows.append(total_row)
        lines.extend(
            render_table(
                ["件号"] + list(models) + ["ok", "cached", "skipped", "error"],
                table_rows,
                ["left"] + ["left"] * len(models) + ["right"] * 4,
            )
        )
        lines.append("")

    # 2) 逐件概览
    index += 1
    lines.append(f"## {index}. 逐件概览")
    lines.append("")
    overview_rows: list[list[str]] = []
    for report in report_list:
        part = str(report.get("part") or "")
        part_results = _results_with_part(report)
        part_counts = _status_counts(part_results)
        worst = None
        for result in part_results:
            value = _num(result.get("volume_error_pct"))
            if value is not None and (worst is None or value > worst):
                worst = value
        overview_rows.append(
            [
                part or _MISSING,
                _end_treatment_text(report.get("end_treatment")),
                _fmt_int(part_counts["total"]),
                _fmt_int(part_counts["ok"]),
                _fmt_int(part_counts["cached"]),
                _fmt_int(part_counts["skipped"]),
                _fmt_int(part_counts["error"]),
                _fmt_pct(worst),
                _fmt_seconds(report.get("seconds")),
            ]
        )
    if overview_rows:
        lines.extend(
            render_table(
                ("件号", "端部口径", "产出", "ok", "cached", "skipped", "error", "最大偏差%", "耗时 s"),
                overview_rows,
                ("left", "left", "right", "right", "right", "right", "right", "right", "right"),
            )
        )
    else:
        lines.append("没有报告数据。")
    lines.append("")

    # 3) 体积与偏差
    index += 1
    lines.append(f"## {index}. 体积与偏差")
    lines.append("")
    volume_rows: list[list[str]] = []
    for result in results:
        if _num(result.get("volume_mm3")) is None:
            continue
        volume_rows.append(
            [
                str(result.get("part") or _MISSING),
                str(result.get("key") or _MISSING),
                ROUTE_LABELS.get(str(result.get("route") or ""), str(result.get("route") or _MISSING)),
                _status(result.get("status")),
                _fmt_volume(result.get("volume_mm3")),
                _fmt_volume(result.get("ref_volume_mm3")),
                _fmt_pct(result.get("volume_error_pct")),
                _fmt_int(result.get("faces")),
                _fmt_int(result.get("free_edges")),
                _fmt_bool(result.get("valid")),
            ]
        )
    if volume_rows:
        lines.extend(
            render_table(
                ("件号", "类", "路线", "状态", "体积 mm³", "参考体积", "偏差%", "面数", "自由边", "valid"),
                volume_rows,
                ("left", "left", "left", "left", "right", "right", "right", "right", "right", "left"),
            )
        )
    else:
        lines.append("没有任何带体积的产出。")
    lines.append("")

    # 4) 合规与问题
    index += 1
    lines.append(f"## {index}. 合规与问题")
    lines.append("")
    try:
        summary_data = summarize(report_list)
    except Exception as exc:  # noqa: BLE001 - 汇总失败不许影响落盘
        summary_data = {}
        lines.append(f"_checks.summarize 不可用：{type(exc).__name__}: {exc}_")
        lines.append("")
    conformance = (summary_data.get("conformance") or {}) if summary_data else {}
    if conformance:
        flag_rows = [
            [_FLAG_LABELS.get(flag, flag), _fmt_int(count)]
            for flag, count in conformance.items()
        ]
        lines.extend(render_table(("规范项", "违规产出数"), flag_rows, ("left", "right")))
        lines.append("")
    issue_rows: list[list[str]] = []
    for result in results:
        for text in _issue_lines(result):
            issue_rows.append(
                [
                    str(result.get("part") or _MISSING),
                    str(result.get("key") or _MISSING),
                    str(result.get("route") or _MISSING),
                    _status(result.get("status")),
                    text,
                ]
            )
    if issue_rows:
        lines.append(f"逐条问题（{len(issue_rows)} 条）：")
        lines.append("")
        lines.extend(
            render_table(
                ("件号", "类", "路线", "状态", "问题"),
                issue_rows,
                ("left", "left", "left", "left", "left"),
            )
        )
    else:
        lines.append("未发现合规问题，也无 error / problems。")
    lines.append("")

    # 5) 错误清单
    index += 1
    lines.append(f"## {index}. 错误清单")
    lines.append("")
    error_rows: list[list[str]] = []
    for result in results:
        status = _status(result.get("status"))
        error = str(result.get("error") or "").strip()
        if status == "error" or error:
            error_rows.append(
                [
                    str(result.get("part") or _MISSING),
                    str(result.get("key") or _MISSING),
                    ROUTE_LABELS.get(str(result.get("route") or ""), str(result.get("route") or _MISSING)),
                    status,
                    error.replace("\n", " ") or _MISSING,
                ]
            )
    if error_rows:
        lines.extend(
            render_table(
                ("件号", "类", "路线", "状态", "error"),
                error_rows,
                ("left", "left", "left", "left", "left"),
            )
        )
    else:
        lines.append("没有 error。")
    lines.append("")

    # 6) 超阈值偏差
    index += 1
    lines.append(f"## {index}. 超阈值偏差（> {_fmt_pct(tolerance_pct)}）")
    lines.append("")
    over_rows: list[list[str]] = []
    over = []
    for result in results:
        value = _num(result.get("volume_error_pct"))
        if value is not None and value > float(tolerance_pct):
            over.append((value, result))
    over.sort(key=lambda pair: pair[0], reverse=True)
    for value, result in over:
        over_rows.append(
            [
                str(result.get("part") or _MISSING),
                str(result.get("key") or _MISSING),
                ROUTE_LABELS.get(str(result.get("route") or ""), str(result.get("route") or _MISSING)),
                _fmt_volume(result.get("volume_mm3")),
                _fmt_volume(result.get("ref_volume_mm3")),
                _fmt_pct(value),
            ]
        )
    if over_rows:
        lines.extend(
            render_table(
                ("件号", "类", "路线", "体积 mm³", "参考体积", "偏差%"),
                over_rows,
                ("left", "left", "left", "right", "right", "right"),
            )
        )
    else:
        lines.append("没有超出阈值的体积偏差。")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------- 落盘（件级）


def write_part_report(
    report: Any = None,
    out_dir: Any = None,
    *,
    process_summary: Optional[dict] = None,
    **kwargs: Any,
) -> dict:
    """写件级 ``report.json`` + ``report.md``，返回落盘信息（纯 dict）。

    签名按 ``ENTITY_PIPELINE.md`` §9：``write_part_report(report, out_dir)``。
    早期 orchestrator 的 ``write_part_report(part_dir, report)`` 也照旧能跑
    （两个位置参数的类型反过来时自动交换）。

    ``process_summary``（可选，A3 工艺摘要）：给了就写进 ``report.json`` 的
    ``process_summary`` 字段，并在 ``report.md`` 末尾追加「## 工艺摘要」章节；
    不传时输出与旧版完全一致。
    """
    if kwargs:
        raise TypeError(f"write_part_report 收到未知参数：{', '.join(sorted(kwargs))}")
    if report is None and out_dir is None:
        raise TypeError("write_part_report 需要 (report, out_dir) 两个参数")
    if _looks_like_report(out_dir) and not _looks_like_report(report):
        report, out_dir = out_dir, report
    if report is None:
        raise TypeError("write_part_report 需要一个 PartReport（或它的 to_dict()）作为第一个参数")
    if out_dir is None:
        raise TypeError("write_part_report 需要一个输出目录 out_dir")

    started = time.perf_counter()
    data = _report_dict(report)
    env = environment()
    part = str(data.get("part") or "")
    results = _results_with_part(data)
    counts = _status_counts(results)

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / REPORT_JSON
    md_path = directory / REPORT_MD

    generated = now_iso()
    payload = dict(data)
    payload.update(
        {
            "schema": SCHEMA_PART,
            "generated": generated,
            "out_dir": str(directory),
            "environment": env,
            "counts": counts,
            "conformance": _conformance_totals(results),
            "problems": _problem_lines(results),
            "logs": [str(item) for item in (data.get("logs") or [])][-200:],
        }
    )
    if process_summary is not None:
        payload["process_summary"] = process_summary

    json_started = time.perf_counter()
    json_bytes = _write_json(json_path, payload)
    json_seconds = time.perf_counter() - json_started

    md_started = time.perf_counter()
    md_bytes = _write_text(md_path, part_markdown(payload, env))
    md_seconds = time.perf_counter() - md_started

    return {
        "schema": SCHEMA_PART,
        "part": part,
        "written": True,
        "generated": generated,
        "out_dir": str(directory),
        "json": str(json_path),
        "md": str(md_path),
        "files": [str(json_path), str(md_path)],
        "json_bytes": json_bytes,
        "md_bytes": md_bytes,
        "json_seconds": round(json_seconds, 3),
        "md_seconds": round(md_seconds, 3),
        "seconds": round(time.perf_counter() - started, 3),
        "results": counts["total"],
        "counts": counts,
    }


#: ``checks.conformance_issues`` 句子的前缀 → 汇总 flag。
#: 该函数返回的文本带实际数字（例如「面数 6 ≠ 4」），只有前缀是稳定的，
#: 因此这里按前缀归类，绝不把数字硬编码进报告。
_ISSUE_PREFIXES = (
    ("面数 ", "faces_ne_4"),
    ("实体数 ", "solids_ne_1"),
    ("自由边 ", "free_edges_ne_0"),
    ("共享分型边 ", "parting_ne_2"),
    ("OCC 有效性", "invalid"),
)


def _issue_flag(issue: str) -> Optional[str]:
    """把一条合规问题归到 ``_FLAG_LABELS`` 的 flag；不认识返回 None。"""
    for prefix, flag in _ISSUE_PREFIXES:
        if issue.startswith(prefix):
            return flag
    return None


def _conformance_totals(results: Iterable[dict]) -> dict:
    totals = {flag: 0 for flag in _FLAG_LABELS}
    for result in results:
        for issue in _conformance(result):
            flag = _issue_flag(issue)
            if flag is not None:
                totals[flag] += 1
    return totals


def _problem_lines(results: Iterable[dict]) -> list[str]:
    lines: list[str] = []
    for result in results:
        key = str(result.get("key") or "")
        route = str(result.get("route") or "")
        for text in _issue_lines(result):
            lines.append(f"{key}/{route}: {text}")
    return lines


# ---------------------------------------------------------------- 落盘（全局）


def _default_out_root() -> Path:
    """``out_root`` 缺省值：优先 ``settings.OUT_ROOT``，拿不到再退回 cwd。"""
    try:
        from . import settings

        return Path(settings.OUT_ROOT)
    except Exception:  # noqa: BLE001 - 兜底，缺省值不该让报告写不出来
        return Path.cwd() / "entity_out"


def write_summary(
    reports: Any = None,
    out_root: Any = None,
    *,
    tolerance_pct: float = VOLUME_TOLERANCE_PCT,
    **kwargs: Any,
) -> dict:
    """写 ``<out_root>/_report/summary.json`` + ``summary.md``，返回落盘信息。

    签名按 ``ENTITY_PIPELINE.md`` §9：``write_summary(reports, out_root)``。
    早期 orchestrator 的 ``write_summary(out_root, reports)`` 也照旧能跑
    （第一个位置参数是路径、第二个是报告集合时自动交换）。

    ``reports`` 可以是 ``PartReport`` / ``report.to_dict()`` / 它们的列表 /
    ``{件: 报告}``。``tolerance_pct`` 覆盖体积偏差告警阈值。
    """
    if kwargs:
        raise TypeError(f"write_summary 收到未知参数：{', '.join(sorted(kwargs))}")
    if reports is None and out_root is None:
        raise TypeError("write_summary 需要 (reports, out_root) 两个参数")
    path_like = (str, os.PathLike)
    # 参数顺序颠倒：write_summary(out_root, reports)
    if (
        isinstance(reports, path_like)
        and out_root is not None
        and not isinstance(out_root, path_like)
    ):
        reports, out_root = out_root, reports
    if reports is None:
        raise TypeError("write_summary 需要报告列表（PartReport 或其 to_dict()）")
    if isinstance(reports, path_like):
        raise TypeError("write_summary 第一个参数应是报告列表，不是路径")
    if out_root is None:
        # 契约要求显式传 out_root；缺省时退回 settings.OUT_ROOT（entity_out），
        # 不要静默落到当前工作目录。
        out_root = os.fspath(_default_out_root())

    started = time.perf_counter()
    report_list = _iter_report_dicts(reports)
    env = environment()
    compiled = _compile_summary(report_list, out_root=out_root, env=env, tolerance_pct=tolerance_pct)

    directory = Path(out_root) / SUMMARY_DIR
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / SUMMARY_JSON
    md_path = directory / SUMMARY_MD

    payload = dict(compiled)
    payload["files"] = {"json": str(json_path), "md": str(md_path)}
    payload["environment"] = env

    json_started = time.perf_counter()
    json_bytes = _write_json(json_path, payload)
    json_seconds = time.perf_counter() - json_started

    md_started = time.perf_counter()
    md_text = summary_markdown(
        report_list,
        out_root=out_root,
        env=env,
        tolerance_pct=tolerance_pct,
        generated=payload.get("generated"),
    )
    md_bytes = _write_text(md_path, md_text)
    md_seconds = time.perf_counter() - md_started

    return {
        "schema": SCHEMA_SUMMARY,
        "written": True,
        "generated": payload.get("generated"),
        "out_root": str(out_root),
        "json": str(json_path),
        "md": str(md_path),
        "files": [str(json_path), str(md_path)],
        "json_bytes": json_bytes,
        "md_bytes": md_bytes,
        "json_seconds": round(json_seconds, 3),
        "md_seconds": round(md_seconds, 3),
        "seconds": round(time.perf_counter() - started, 3),
        "parts": len(payload.get("parts") or []),
        "results": payload.get("counts", {}).get("total", 0),
        "errors": len(payload.get("errors") or []),
        "over_threshold": len(payload.get("over_threshold") or []),
        "counts": payload.get("counts"),
        "totals": payload.get("counts"),
    }


def _compile_summary(
    report_list: list[dict],
    *,
    out_root: Any,
    env: dict,
    tolerance_pct: float,
) -> dict:
    """汇总的全部结构化数据（summary.json 的正文，md 与它同源）。"""
    results: list[dict] = []
    part_reports: list[dict] = []
    for report in report_list:
        part_results = _results_with_part(report)
        results.extend(part_results)
        part_reports.append(
            {
                "part": str(report.get("part") or ""),
                "end_treatment": report.get("end_treatment"),
                "source_step": report.get("source_step"),
                "measured_path": report.get("measured_path"),
                "started": report.get("started"),
                "finished": report.get("finished"),
                "seconds": _num(report.get("seconds")),
                "counts": _status_counts(part_results),
            }
        )

    parts = _ordered_parts(report_list)
    for result in results:
        part = str(result.get("part") or "")
        if part and part not in parts:
            parts.append(part)
    observed_models = _ordered_models(results)
    observed_routes = _ordered_routes(results)
    # 与 Markdown 同源的满画布：5 类 × 2 路在前，额外出现的排后面。
    models = list(MODEL_KEYS) + [key for key in observed_models if key not in MODEL_KEYS]
    routes = list(ROUTES) + [route for route in observed_routes if route not in ROUTES]

    matrix: dict[str, dict[str, dict[str, str]]] = {}
    cells: list[dict] = []
    for result in results:
        part = str(result.get("part") or "")
        key = str(result.get("key") or "")
        route = str(result.get("route") or "")
        matrix.setdefault(route, {}).setdefault(part, {})[key] = _status(result.get("status"))
        cells.append(
            {
                "part": part,
                "key": key,
                "route": route,
                "status": _status(result.get("status")),
                "volume_mm3": _num(result.get("volume_mm3")),
                "ref_volume_mm3": _num(result.get("ref_volume_mm3")),
                "volume_error_pct": _num(result.get("volume_error_pct")),
                "faces": _int(result.get("faces")),
                "edges": _int(result.get("edges")),
                "solids": _int(result.get("solids")),
                "free_edges": _int(result.get("free_edges")),
                "parting_edges": _int(result.get("parting_edges")),
                "valid": result.get("valid"),
                "seconds": _num(result.get("seconds")),
                "issues": _issue_lines(result),
            }
        )
    # 与 Markdown 的满画布对齐：没有产出的路线也留一个空分组（matrix_full 读起来一致）。
    for route in routes:
        matrix.setdefault(route, {})

    errors = [
        {
            "part": cell["part"],
            "key": cell["key"],
            "route": cell["route"],
            "status": cell["status"],
            "error": str(next(
                (
                    item.get("error")
                    for item in results
                    if str(item.get("part") or "") == cell["part"]
                    and str(item.get("key") or "") == cell["key"]
                    and str(item.get("route") or "") == cell["route"]
                    and str(item.get("error") or "").strip()
                ),
                "",
            )),
        }
        for cell in cells
        if cell["status"] == "error"
    ]

    over_threshold = sorted(
        [
            {
                "part": cell["part"],
                "key": cell["key"],
                "route": cell["route"],
                "volume_mm3": cell["volume_mm3"],
                "ref_volume_mm3": cell["ref_volume_mm3"],
                "volume_error_pct": cell["volume_error_pct"],
            }
            for cell in cells
            if cell["volume_error_pct"] is not None and cell["volume_error_pct"] > float(tolerance_pct)
        ],
        key=lambda item: item["volume_error_pct"],
        reverse=True,
    )

    try:
        summary_data = summarize(report_list)
    except Exception as exc:  # noqa: BLE001
        summary_data = {}
        summarize_error = f"{type(exc).__name__}: {exc}"
    else:
        summarize_error = None

    # 计数以本模块为准：``checks.summarize`` 的 schema 里没有 cached，
    # 它会把 cached 记进 other（实测：2 条 cached → other 2），不能直接当合计用。
    counts = _status_counts(results)
    checks_totals = (summary_data.get("totals") or None) if summary_data else None

    worst = None
    for cell in cells:
        value = cell["volume_error_pct"]
        if value is not None and (worst is None or value > worst["volume_error_pct"]):
            worst = {
                "part": cell["part"],
                "key": cell["key"],
                "route": cell["route"],
                "volume_error_pct": value,
            }

    return {
        "schema": SCHEMA_SUMMARY,
        "generated": now_iso(),
        "out_root": str(out_root),
        "tolerance_pct": float(tolerance_pct),
        "expectations": {
            "faces": 4,
            "solids": 1,
            "free_edges": 0,
            "parting_edges": 2,
            "valid": True,
        },
        "parts": parts,
        "models": models,
        "observed_models": observed_models,
        "routes": routes,
        "observed_routes": observed_routes,
        "counts": counts,
        "checks_totals": checks_totals,
        "worst": worst,
        "conformance": (summary_data.get("conformance") if summary_data else {}) or {},
        "summarize_error": summarize_error,
        "matrix": matrix,
        "cells": cells,
        "part_reports": part_reports,
        "errors": errors,
        "over_threshold": over_threshold,
        "problems": _problem_lines(results),
        "checks_problems": (summary_data.get("problems") if summary_data else []) or [],
        "checks_problems_truncated": bool(summary_data.get("problems_truncated")) if summary_data else False,
        "environment": env,
    }
