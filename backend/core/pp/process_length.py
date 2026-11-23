"""Process-planning length: corrected centerline / drawing length.

``actual_z_length`` is the STP endpoint-to-endpoint measurement and must not
be used after 「按分配修正形线」. Downstream optimization, design tables,
and mold mapping read ``centerline_length`` (updated to drawing length).
"""
from __future__ import annotations


def _positive(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def _formline_span(results: dict) -> float:
    rows = results.get("wall_thickness_data") or results.get("centerline_perimeter_data") or []
    if hasattr(rows, "to_dict"):
        try:
            rows = rows.to_dict(orient="records")
        except Exception:
            rows = []
    xs = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("中心线长度(mm)", "中心线位置(mm)"):
            if key not in row:
                continue
            try:
                xs.append(float(row[key]))
            except (TypeError, ValueError):
                pass
            break
    if not xs:
        return 0.0
    span = max(xs) - min(xs)
    return span if span > 0 else 0.0


def process_length(results) -> float:
    """Length for process design / molds. Never reads actual_z_length."""
    if not isinstance(results, dict):
        return 0.0
    for key in ("centerline_length", "centerline_length_mm"):
        v = _positive(results.get(key))
        if v:
            return v
    qa = results.get("qa") if isinstance(results.get("qa"), dict) else {}
    v = _positive(qa.get("centerline_length_mm"))
    if v:
        return v
    lv = qa.get("length_validation") if isinstance(qa.get("length_validation"), dict) else {}
    v = _positive(lv.get("drawing_length") or lv.get("final_length"))
    if v:
        return v
    return _formline_span(results)


def require_process_length(results) -> float:
    length = process_length(results)
    if length <= 0:
        raise ValueError(
            "缺少中心线长度（centerline_length）数据，无法进行工艺计算。"
            "请先完成形线修正。"
        )
    return length


def rfm_process_length(results, rows=None, design=None) -> float:
    """Hard-mold length: corrected formline / drawing. Never sections STP arc.

    After 「按分配修正形线」 the overlay / remapped 3D stations / QA drawing
    are the process length. Raw STP stations stay in ``stp_sections_full``.
    """
    payload = dict(results) if isinstance(results, dict) else {}
    if rows:
        payload["wall_thickness_data"] = rows
        payload["centerline_perimeter_data"] = rows
        span = _formline_span(payload)
        if span:
            return span
    length = process_length(payload)
    if length:
        return length
    params = (design or {}).get("params") if isinstance(design, dict) else {}
    if isinstance(params, dict):
        for key in ("LL", "L"):
            v = _positive(params.get(key))
            if v:
                return v
    return 0.0
