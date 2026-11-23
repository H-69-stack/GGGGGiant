# -*- coding: utf-8 -*-
"""补算缺失的上游工艺设计（「一键全流程」用）。

只做“补缺”，不覆盖已有登记：当某管件在 ``pp_design`` 里没有记录时，
用实测缓存（``entity_out/_cache/<件>_measured.json``）反推一次默认设计：

1. 壁厚曲线：``中心线长度 / 壁厚 / 外径周长``；
2. 分段与壁厚：``core.pp.wall_thickness_segments.analyze_uploaded_wall``；
3. 直线段 L4：``core.pp.formline_conclude.analyze_formline_from_data``；
4. 推荐工艺路线：``core.pp.optimization.recommend_pp_method``；
5. 按推荐路线跑一次默认参数的暴力优化（不跑 GA，秒级）；
6. 适应度 ≥ 0.74 才落库（``core.pp.design_db.save_design``），否则只返回不可行原因。

返回结构::

    {'created': bool, 'state': 'registered'|'created'|'infeasible'|'no_measured'|'error',
     'design_id': int|None, 'route': str, 'fitness': float|None,
     'line_length_mm': float, 'l4_mm': float, 'message': str, 'seconds': float}
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

PI = 3.141592653589793
FEASIBLE_FITNESS = 0.74
ROUTE_ORDER = (
    "仅TP抽",
    "普通抽+TP抽",
    "仅扩口",
    "普通抽+扩口",
    "TP抽+扩口",
    "普通抽+Taper+扩口",
    "普通抽+Taper",
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------- 路径与库


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def design_db_path() -> Path:
    return _backend_dir() / "database" / "pp" / "pp_design.db"


def _measured_candidates(part: str, out_root: Optional[str] = None) -> list[Path]:
    root = Path(out_root) if out_root else _repo_root() / "entity_out"
    cache = root / "_cache"
    return [cache / f"{part}_measured.json", cache / f"{part}_measured_v1.json"]


def _find_measured(part: str, out_root: Optional[str] = None) -> Optional[Path]:
    for path in _measured_candidates(part, out_root):
        if path.is_file():
            return path
    return None


def existing_design(part: str) -> Optional[dict]:
    """按源文件名为件号查最近一条已登记设计；没有则返回 None。"""
    db = design_db_path()
    if not db.is_file():
        return None
    names = (f"{part}.stp", f"{part}.step")
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "select id, display_name, pp_method, fitness, source_file_name"
                " from pp_design where source_file_name in (?, ?) order by id desc limit 1",
                names,
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - 只读查询，失败按未登记处理
        logger.warning("读取 pp_design 失败：%s", exc)
        return None
    return dict(row) if row else None


# ----------------------------------------------------------------- 输入构造


def _sections(part: str, out_root: Optional[str] = None) -> tuple[list[dict], dict]:
    path = _find_measured(part, out_root)
    if path is None:
        raise FileNotFoundError(f"未找到 {part} 的测量缓存（先跑一次测量）")
    data = json.loads(path.read_text(encoding="utf-8"))
    sections = data.get("sections_full") or []
    if not sections:
        raise ValueError(f"{part} 的测量缓存没有 sections_full")
    return sections, data


def _wall_rows(sections: list[dict]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for sec in sections:
        cum = sec.get("cumulative_length")
        wall = sec.get("wall_thickness")
        per = sec.get("perimeter")
        if cum is None or wall is None or not per:
            continue
        rows.append(
            {
                "中心线长度(mm)": float(cum),
                "壁厚(mm)": float(wall),
                "外径周长(mm)": float(per),
            }
        )
    if len(rows) < 3:
        raise ValueError("有效壁厚测点不足 3 个")
    return rows


def _tube_volume(rows: list[dict[str, float]]) -> float:
    """按壁厚环面积沿中心线积分近似成品管壁体积（mm³）。"""
    total = 0.0
    for prev, cur in zip(rows, rows[1:]):
        d0 = prev["外径周长(mm)"] / PI
        d1 = cur["外径周长(mm)"] / PI
        w0 = prev["壁厚(mm)"]
        w1 = cur["壁厚(mm)"]
        a0 = PI * (d0 * w0 - w0 * w0)
        a1 = PI * (d1 * w1 - w1 * w1)
        total += (a0 + a1) / 2.0 * (cur["中心线长度(mm)"] - prev["中心线长度(mm)"])
    return max(total, 0.0)


def _straight_length(rows: list[dict[str, float]]) -> float:
    from core.pp.formline_conclude import analyze_formline_from_data

    cps = [
        {"中心线长度(mm)": row["中心线长度(mm)"], "外径周长(mm)": row["外径周长(mm)"]}
        for row in rows
    ]
    fl = analyze_formline_from_data(
        cps,
        tolerance=1.0,
        min_segment_length=50.0,
        trend_tolerance=0.01,
        min_point_count=30,
    )
    if not fl.get("has_segments"):
        return 0.0
    return float(((fl.get("statistics") or {}).get("total_length")) or 0.0)


_STRAIGHT_CACHE: dict[tuple[str, str, float], float] = {}


def straight_length_for(part: str, out_root: Optional[str] = None) -> float:
    """直线段 L4（按测量文件 mtime 缓存），供路线推荐统一口径使用。"""
    path = _find_measured(part, out_root)
    if path is None:
        raise FileNotFoundError(f"未找到 {part} 的测量缓存")
    key = (part, str(path), path.stat().st_mtime)
    cached = _STRAIGHT_CACHE.get(key)
    if cached is not None:
        return cached
    sections, _data = _sections(part, out_root)
    value = _straight_length(_wall_rows(sections))
    _STRAIGHT_CACHE[key] = value
    return value


_RECOMMEND_CACHE: dict[tuple[str, str, float], dict] = {}


def recommend_for_part(part: str, out_root: Optional[str] = None) -> dict:
    """按实测型线给出工艺路线推荐（L4 已扣除），与设计补算同一口径。"""
    path = _find_measured(part, out_root)
    if path is None:
        raise FileNotFoundError(f"未找到 {part} 的测量缓存")
    key = (part, str(path), path.stat().st_mtime)
    cached = _RECOMMEND_CACHE.get(key)
    if cached is not None:
        return cached
    sections, data = _sections(part, out_root)
    rows = _wall_rows(sections)
    l4 = _straight_length(rows)
    perims = [row["外径周长(mm)"] for row in rows]
    max_d = max(perims) / PI
    min_d = min(perims) / PI
    length = float(data.get("center_line_length") or rows[-1]["中心线长度(mm)"])
    from core.pp.optimization import recommend_pp_method

    rec = dict(recommend_pp_method(length, max_d, min_d, l4) or {})
    rec["line_length_mm"] = round(max(0.0, length - l4), 3)
    rec["l4_mm"] = round(l4, 3)
    rec["centerline_length_mm"] = round(length, 3)
    rec["max_diameter_mm"] = round(max_d, 3)
    rec["min_diameter_mm"] = round(min_d, 3)
    _RECOMMEND_CACHE[key] = rec
    return rec


def _segments(sections: list[dict], rows: list[dict[str, float]]) -> dict[str, float]:
    from core.pp.wall_thickness_segments import analyze_uploaded_wall

    fields = (analyze_uploaded_wall(sections).get("fields") or {})
    segments = {
        key: (0.0 if value is None else float(value))
        for key, value in fields.items()
        if not isinstance(value, (list, dict)) and not isinstance(value, bool)
    }
    perims = [row["外径周长(mm)"] for row in rows]
    segments.setdefault("D1", round(max(perims) / PI, 2))
    segments.setdefault("D3", round(min(perims) / PI, 2))
    return segments


def build_analysis_input(part: str, out_root: Optional[str] = None) -> dict[str, Any]:
    """把实测缓存整理成上游优化器要的 analysis_input。"""
    sections, data = _sections(part, out_root)
    rows = _wall_rows(sections)
    segments = _segments(sections, rows)
    perims = [row["外径周长(mm)"] for row in rows]
    max_d = max(perims) / PI
    min_d = min(perims) / PI
    length = float(data.get("center_line_length") or rows[-1]["中心线长度(mm)"])
    l4 = _straight_length(rows)
    return {
        "part": part,
        "rows": rows,
        "segments": segments,
        "centerline_length": length,
        "volume": _tube_volume(rows),
        "L4": l4,
        "max_diameter": max_d,
        "min_diameter": min_d,
        "line_length": max(0.0, length - l4),
        "measured": _find_measured(part, out_root),
    }


# ----------------------------------------------------------------- 设计生成


def _log(log: Optional[Callable[[str], None]], message: str) -> None:
    if callable(log):
        try:
            log(message)
        except Exception:
            pass


def _recommend(inputs: dict[str, Any]) -> tuple[str, str]:
    from core.pp.optimization import recommend_pp_method

    rec = recommend_pp_method(
        float(inputs["centerline_length"]),
        float(inputs["max_diameter"]),
        float(inputs["min_diameter"]),
        float(inputs["L4"]),
    )
    label = ""
    for option in rec.get("all_options") or []:
        if option.get("method") == rec.get("recommended"):
            label = str(option.get("label") or "")
            break
    return label or "仅TP抽", str(rec.get("reason") or "")


def _optimize(inputs: dict[str, Any], route: str) -> dict[str, Any]:
    from core.pp.optimization import optimize_process_parameters_by_method

    analysis_input = {
        "wall_thickness_data": inputs["rows"],
        "segments": inputs["segments"],
        "centerline_length": inputs["centerline_length"],
        "volume": inputs["volume"],
        "L4": inputs["L4"],
    }
    result = optimize_process_parameters_by_method(
        analysis_input,
        pp_method=route,
        d_precision=1,
        t_precision=1,
        l_precision=0,
        add3=0.0,
    )
    solutions = list(result.get("solutions") or [])
    solutions.sort(key=lambda item: float(item.get("fitness") or 0.0), reverse=True)
    best = solutions[0] if solutions else {}
    return {"best": best, "count": len(solutions), "status": result.get("status")}


def _modified_wall(inputs: dict[str, Any], solution: dict[str, Any], route: str) -> list[dict[str, Any]]:
    from core.pp.modify_thick import apply_modify_thick

    try:
        frame, _segments2 = apply_modify_thick(
            wall_thickness_data=inputs["rows"],
            solution=solution,
            pp_method=route,
            Taper_thick=False,
            segments=inputs["segments"],
        )
    except Exception as exc:
        logger.warning("计算修改后壁厚失败：%s", exc)
        return []
    if frame is None:
        return []
    records = frame.to_dict("records") if hasattr(frame, "to_dict") else []
    out: list[dict[str, Any]] = []
    length_col = "中心线长度(mm)"
    wall_col = "壁厚(mm)"
    mod_col = next((c for c in ("修改后壁厚(mm)", "修改后壁厚") if c in getattr(frame, "columns", [])), None)
    for row in records:
        out.append(
            {
                "中心线长度(mm)": round(float(row.get(length_col) or 0), 3),
                "壁厚(mm)": round(float(row.get(wall_col) or 0), 4),
                "修改后壁厚(mm)": round(float(row.get(mod_col) or row.get(wall_col) or 0), 4),
            }
        )
    return out


def create_default_design(
    part: str,
    out_root: Optional[str] = None,
    *,
    method: Optional[str] = None,
    force: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """为单件补算一条默认工艺设计（幂等：已登记则直接返回）。"""
    started = time.time()
    part = str(part or "").strip()
    if not part:
        return {"created": False, "state": "error", "message": "缺少件号", "seconds": 0.0}

    registered = existing_design(part)
    if registered and not force:
        return {
            "created": False,
            "state": "registered",
            "design_id": registered.get("id"),
            "route": registered.get("pp_method"),
            "fitness": registered.get("fitness"),
            "message": f"已有工艺设计 #{registered.get('id')}（{registered.get('pp_method')}）",
            "seconds": round(time.time() - started, 2),
        }

    try:
        inputs = build_analysis_input(part, out_root)
    except FileNotFoundError as exc:
        return {"created": False, "state": "no_measured", "message": str(exc), "seconds": round(time.time() - started, 2)}
    except Exception as exc:
        return {"created": False, "state": "error", "message": f"{type(exc).__name__}: {exc}", "seconds": round(time.time() - started, 2)}

    route, reason = _recommend(inputs)
    if method:
        route = str(method).strip() or route

    _log(log, f"[design] {part}：推荐路线 {route}（{reason}）")
    try:
        optimized = _optimize(inputs, route)
    except Exception as exc:
        return {
            "created": False,
            "state": "error",
            "route": route,
            "message": f"优化失败：{type(exc).__name__}: {exc}",
            "seconds": round(time.time() - started, 2),
        }

    best = optimized["best"] or {}
    fitness = float(best.get("fitness") or 0.0)
    base = {
        "route": route,
        "reason": reason,
        "fitness": fitness,
        "line_length_mm": round(float(inputs["line_length"]), 2),
        "l4_mm": round(float(inputs["L4"]), 2),
        "best": best,
    }
    if fitness < FEASIBLE_FITNESS:
        _log(log, f"[design] {part}：推荐路线适应度 {fitness:.3f} < {FEASIBLE_FITNESS}，判为不可行，不写库")
        return {
            **base,
            "created": False,
            "state": "infeasible",
            "message": (
                f"推荐路线「{route}」按默认参数求解不可行（适应度 {fitness:.3f} < {FEASIBLE_FITNESS}），"
                "请到「工艺优化 → 工艺参数优化」页调整后保存"
            ),
            "seconds": round(time.time() - started, 2),
        }

    modified = _modified_wall(inputs, best, route)
    extra = {
        "Taper段壁厚过渡": False,
        "原始segments": inputs["segments"],
        "自动补算": True,
    }
    if modified:
        extra["壁厚数据"] = modified

    solution = dict(best)
    for key in ("T1", "T2", "T3", "T4", "TL1", "TL2", "TL3", "TL4", "TLM1", "TLM2", "TLM3", "D1", "D3"):
        if key not in solution and inputs["segments"].get(key):
            solution[key] = inputs["segments"][key]
    solution.setdefault("fitness", fitness)

    cps = [
        {"中心线长度(mm)": row["中心线长度(mm)"], "外径周长(mm)": row["外径周长(mm)"]}
        for row in inputs["rows"]
    ]
    source_step = (_repo_root() / "gnt管件" / f"{part}.stp")
    try:
        from core.pp.design_db import save_design

        saved = save_design(
            display_name=f"{part} 默认设计",
            vehicle_spec="自动补算",
            tube_number="1",
            pp_method=route,
            source_tube_id=None,
            source_file_name=f"{part}.stp",
            source_file_path=str(source_step) if source_step.is_file() else "",
            solution=solution,
            fitness=fitness,
            area_change_rate=best.get("area_change_rate"),
            extension_ratio=best.get("extension_ratio"),
            centerline_perimeter_data=cps,
            extra_params=extra,
            db_path=str(design_db_path()),
        )
    except Exception as exc:
        return {
            **base,
            "created": False,
            "state": "error",
            "message": f"写库失败：{type(exc).__name__}: {exc}",
            "seconds": round(time.time() - started, 2),
        }
    if isinstance(saved, tuple):
        design_id = int(saved[0] or -1)
        save_error = saved[1] if len(saved) > 1 else None
    else:
        design_id = int(saved or -1)
        save_error = None
    if design_id < 0:
        return {
            **base,
            "created": False,
            "state": "error",
            "message": f"写库失败：{save_error or 'save_design 返回 -1'}",
            "seconds": round(time.time() - started, 2),
        }

    _log(log, f"[design] {part}：已写入设计 #{design_id}（{route}，适应度 {fitness:.3f}）")
    return {
        **base,
        "created": True,
        "state": "created",
        "design_id": design_id,
        "message": f"已按推荐路线「{route}」自动补算设计 #{design_id}（适应度 {fitness:.3f}）",
        "seconds": round(time.time() - started, 2),
    }
