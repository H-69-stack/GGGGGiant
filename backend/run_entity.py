"""Giant-Unified 实体化流水线 · 命令行入口。

必须在 ``backend`` 目录下运行（才能 ``import core.*``）；也支持从仓库根目录用
``python backend\\run_entity.py`` —— 脚本自己会把 ``backend`` 塞进 ``sys.path``。
``--src`` / ``--out`` 的**相对路径按当前工作目录解析**（不是按 backend）。

用法::

    cd backend
    D:\\python\\envs\\occ\\Scripts\\python.exe run_entity.py --dry-run
    ... python run_entity.py --parts 24TK_1324-1 --models 成型_外径形腔 --routes c1
    ... python run_entity.py --src ..\\gnt管件 --out ..\\entity_out --routes c1,pipe

``--dry-run`` 只打印计划表（件 × 类 × 路），**不建目录、不读几何、不 import
OCP / FreeCAD**。
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
import unicodedata
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:  # 保证 backend 在 sys.path（crash-safe 的 import core.*）
    sys.path.insert(0, _HERE)

from core.entity import orchestrator, route_prefs, settings  # noqa: E402
from core.entity.contracts import (  # noqa: E402
    MODEL_KEYS,
    REFERENCE_MODELS,
    ROUTES,
    PipelineContext,
    default_route,
    normalize_routes,
    routes_for,
)

EXIT_OK = 0
EXIT_RUN_ERRORS = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_entity.py",
        description="Giant-Unified 实体化流水线（5 类模型，默认按类分派路线）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  run_entity.py --dry-run\n"
            "  run_entity.py --parts 24TK_1324-1 --models 成型_外径形腔\n"
            "  run_entity.py --routes c1,pipe --force\n"
        ),
    )
    parser.add_argument("--src", default=None, help="原始管件目录（默认 gnt管件）")
    parser.add_argument("--out", default=None, help="输出根目录（默认 entity_out）")
    parser.add_argument("--parts", action="append", default=None, metavar="件号[,件号]",
                        help="只跑这些件（逗号分隔，可重复；默认源目录全部）")
    parser.add_argument("--models", action="append", default=None, metavar="模型键[,模型键]",
                        help="只跑这些模型（逗号分隔，可重复；默认 5 类全跑）")
    parser.add_argument("--routes", action="append", default=None, metavar="c1[,pipe]",
                        help="只跑这些路线（默认按模型分派：外径形腔 c1、扩口/压扁/压弯 pipe；"
                             "显式 c1,pipe 可双跑对照）")
    parser.add_argument("--force", action="store_true",
                        help="忽略 measured 缓存与已有产物，全部重算")
    parser.add_argument("--resume", dest="resume", action="store_true", default=True,
                        help="断点续跑：产物已存在就跳过（默认开启）")
    parser.add_argument("--no-resume", dest="resume", action="store_false",
                        help="关闭断点续跑，已有产物也重算")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="最多跑前 N 件")
    parser.add_argument("--shaping-route", dest="shaping_route", default=None,
                        choices=("c1", "121"),
                        help="成型_外径形腔实现：c1（默认）或 121（参考保形）；"
                             "本次运行覆盖所有件（不改写偏好文件）")
    parser.add_argument("--direction", dest="direction", default=None,
                        choices=("auto", "X", "Y", "Z"),
                        help="成型_外径形腔分型方向（auto/X/Y/Z）；本次运行覆盖所有件")
    parser.add_argument("--no-subprocess", dest="no_subprocess", action="store_true",
                        help="不用 prepare 子进程，改在进程内测量/导出（调试用）")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="只打印计划表，不写盘、不读几何")
    return parser


def _abspath(value: str | None, fallback) -> str:
    """相对路径按当前工作目录解析。"""
    if not value:
        return str(fallback)
    path = Path(os.path.expandvars(os.path.expanduser(str(value))))
    if not path.is_absolute():
        path = Path.cwd() / path
    return str(path.resolve())


def _split(values) -> list[str]:
    """``None`` / ``"a,b"`` / ``["a,b", "c"]`` → 去重有序列表。"""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values:
        for item in str(value).replace("，", ",").split(","):
            item = item.strip()
            if item and item not in out:
                out.append(item)
    return out


def _console(line) -> None:
    print(line if isinstance(line, str) else str(line), flush=True)


def _fail(message: str, code: int = EXIT_USAGE) -> int:
    print(f"错误：{message}", file=sys.stderr, flush=True)
    return code


# ---------------------------------------------------------------- 计划表

def _dwidth(text: str) -> int:
    """终端显示宽度（CJK 算 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _dwidth(text))


def _plan_rows(specs, routes, out_root: str, prefs=None) -> list[list[str]]:
    rows: list[list[str]] = []
    index = 0
    for spec in specs:
        for route in (tuple(routes) if routes else (default_route(spec.key),)):
            index += 1
            out_path = spec.out_path(route)
            try:
                shown = os.path.relpath(out_path, out_root)
            except ValueError:  # 跨盘符
                shown = out_path
            rows.append([
                f"{index}.",
                spec.part,
                spec.key,
                spec.end_treatment,
                route,
                "solidify" if route == "pipe" else orchestrator.c1_impl(spec.part, spec.key, prefs),
                getattr(spec, "direction", "auto"),
                "self" if spec.reference_step else "-",
                shown,
            ])
    return rows


def print_plan(src: str, out: str, parts: list[str], specs, routes, prefs=None) -> int:
    """打印计划表，返回条数。**只读不写**。"""
    rows = _plan_rows(specs, routes, out, prefs)
    header = ["#", "件号", "模型", "口径", "路线", "实现", "方向", "参考件", "输出（相对 out）"]
    widths = [max(_dwidth(header[i]), *(_dwidth(row[i]) for row in rows)) if rows else _dwidth(header[i])
              for i in range(len(header))]

    print("=== Giant-Unified · 实体化流水线计划（dry-run：不写盘、不读几何）===")
    print(f"  src : {src}")
    print(f"  out : {out}")
    print(f"  件  : {len(parts)} → {', '.join(parts)}")
    models_per_part = len(specs) // max(1, len(parts))
    if routes:
        route_text = f"{len(routes)} 路（{', '.join(routes)}）"
    else:
        route_text = "按模型默认（外径形腔 c1 / 扩口·压扁·压弯 pipe）"
    print(f"  类  : {models_per_part} 种 × {route_text} = {len(rows)} 条计划")
    print("-" * 108)
    print("  ".join(_pad(header[i], widths[i]) for i in range(len(header))).rstrip())
    print("-" * 108)
    for row in rows:
        print("  ".join(_pad(row[i], widths[i]) for i in range(len(header))).rstrip())
    print("-" * 108)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row[5]] = counts.get(row[5], 0) + 1
    detail = " / ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    v1_parts = sorted({spec.part for spec in specs if spec.end_treatment == "v1"})
    ref_rows = sum(1 for row in rows if row[7] != "-")
    route_counts: dict[str, int] = {}
    for row in rows:
        route_counts[row[4]] = route_counts.get(row[4], 0) + 1
    print(f"合计 {len(rows)} 条："
          + "、".join(f"{name} {count}" for name, count in sorted(route_counts.items()))
          + f"（{detail}）；参考件=自己 的 {ref_rows} 条")
    if v1_parts:
        print(f"v1 口径（线性外推）：{', '.join(v1_parts)}"
              f" —— 该件 5 类共用一份 _measured_v1.json")
    print("说明：--dry-run 不建目录、不读几何；真跑会先 measure → 导出 5 类 → 再按默认路线逐格重建。")
    return len(rows)


# ---------------------------------------------------------------- 主流程

def _resolve(args):
    """解析路径与筛选条件；返回 (src, out, parts, models, routes)。"""
    src = _abspath(args.src, settings.SRC_PARTS_DIR)
    out = _abspath(args.out, settings.OUT_ROOT)
    if not os.path.isdir(src):
        raise FileNotFoundError(f"原始管件目录不存在：{src}")

    available = orchestrator.scan_parts(src)
    requested = _split(args.parts)
    if requested:
        unknown = [part for part in requested if part not in available]
        if unknown:
            raise ValueError(
                f"源目录里没有这些件：{', '.join(unknown)}（现有：{', '.join(available)}）"
            )
        parts = requested
    else:
        parts = available
    if args.limit is not None:
        if args.limit < 0:
            raise ValueError(f"--limit 不能为负：{args.limit}")
        parts = parts[: args.limit]
    if not parts:
        raise ValueError(f"没有可跑的件：{src}")

    models = _split(args.models)
    if models:
        unknown = [key for key in models if key not in MODEL_KEYS]
        if unknown:
            raise ValueError(f"未知模型键：{', '.join(unknown)}（可选：{', '.join(MODEL_KEYS)}）")
    else:
        models = list(MODEL_KEYS)

    routes = normalize_routes(_split(args.routes))
    return src, out, parts, models, routes


def _default_options() -> dict:
    """传给 routes 的默认参数（routes 可以只读自己认识的键）。"""
    options = dict(settings.PIPE_OPTIONS)
    options.update(
        {
            "half_samples": settings.HALF_SAMPLES,
            "smooth_passes": settings.SMOOTH_PASSES,
            "station_step_mm": settings.STATION_STEP_MM,
        }
    )
    return options


#: 外径形腔的模型键（``--shaping-route`` / ``--direction`` 作用对象）
SHAPING_KEY = REFERENCE_MODELS[0]


def _build_prefs(out: str, parts: list[str], args) -> dict:
    """读磁盘路线偏好，再叠加本次 CLI 覆盖（只在内存，不改写用户的偏好文件）。"""
    prefs = route_prefs.load_prefs(route_prefs.prefs_path(out))
    if args.shaping_route is None and args.direction is None:
        return prefs
    for part in parts:
        prefs = route_prefs.set_model_pref(
            prefs,
            part,
            SHAPING_KEY,
            impl=args.shaping_route,
            direction=args.direction,
        )
    return prefs


def _print_totals(totals: dict) -> None:
    print("=== 完成 ===")
    routes = tuple(totals.get("routes") or ())
    print(f"  件 {totals['parts']} / 类 {totals['models']} / 路 {len(routes)}"
          f"（{', '.join(routes)}）→ 产物 {totals['results']} 条")
    print(f"  ok={totals['ok']}  cached={totals['cached']}  stopped={totals.get('stopped', 0)}"
          f"  error={totals['error']}")
    for route, stat in (totals.get("by_route") or {}).items():
        print(f"  [{route}] ok={stat['ok']} cached={stat['cached']} stopped={stat.get('stopped', 0)}"
              f" error={stat['error']} ({stat['seconds']}s)")
    print(f"  总耗时 {totals['seconds']}s → {totals['out_root']}")


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # --help / 参数非法：argparse 已经打印过用法，这里只把码传出去
        code = exc.code
        return code if isinstance(code, int) else (EXIT_OK if code is None else EXIT_USAGE)
    try:
        src, out, parts, models, routes = _resolve(args)
    except (FileNotFoundError, ValueError) as exc:
        return _fail(str(exc))

    prefs = _build_prefs(out, parts, args)
    if args.shaping_route or args.direction:
        print(f"  CLI 覆盖：{SHAPING_KEY} → impl={args.shaping_route or '（保持偏好）'}"
              f" / direction={args.direction or '（保持偏好）'}（仅本次运行，不改偏好文件）")

    if args.dry_run:
        try:
            specs = orchestrator.build_specs(parts, models, routes, out_root=out, prefs=prefs)
        except ValueError as exc:
            return _fail(str(exc))
        print_plan(src, out, parts, specs, routes, prefs)
        return EXIT_OK

    options = _default_options()
    options["route_prefs"] = prefs
    if args.no_subprocess:
        options["no_subprocess"] = True

    context = PipelineContext(
        src_dir=src,
        out_root=out,
        parts=list(parts),
        routes=routes,
        models=tuple(models),
        force=bool(args.force),
        resume=bool(args.resume),
        options=options,
        log=_console,
    )
    try:
        result = orchestrator.run(context)
    except KeyboardInterrupt:
        print("\n已中断（KeyboardInterrupt）。已完成的产物保留，可用 --resume 续跑。",
              file=sys.stderr, flush=True)
        return EXIT_INTERRUPTED
    except Exception as exc:
        print(f"错误：流水线异常终止：{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        return EXIT_RUN_ERRORS

    totals = result.get("totals") or {}
    _print_totals(totals) if totals else None
    if result.get("stopped"):
        print(
            f"已按停止请求提前结束：{result.get('completed_cells', 0)}/{result.get('total_cells', 0)} 个单元完成；"
            "已完成产物保留，可用 --resume 续跑。",
            flush=True,
        )
    return EXIT_RUN_ERRORS if totals.get("error") else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
