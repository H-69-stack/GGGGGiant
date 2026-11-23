"""Manual XLSX template + parser with row/column-level error reporting."""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import (
    MAX_DIAMETER_MM,
    MAX_XLSX_BYTES,
    MIN_DIAMETER_MM,
    XLSX_CENTERLINE_ALIASES,
    XLSX_CENTERLINE_COL,
    XLSX_CIRCUMFERENCE_ALIASES,
    XLSX_CIRCUMFERENCE_COL,
    XLSX_DIAMETER_ALIASES,
    XLSX_DIAMETER_COL,
    XLSX_MIN_POINTS,
)
from .models import FieldError, ProfileArrays
from .profile_validation import diameter_to_circumference
from .state import QA_MANUAL_DATA_INVALID


def build_xlsx_template_bytes() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "manual_profile"
    headers = [XLSX_CENTERLINE_COL, XLSX_CIRCUMFERENCE_COL]
    header_font = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1478FF")
    thin = Border(
        left=Side(style="thin", color="B0B8C4"),
        right=Side(style="thin", color="B0B8C4"),
        top=Side(style="thin", color="B0B8C4"),
        bottom=Side(style="thin", color="B0B8C4"),
    )
    for col, name in enumerate(headers, start=1):
        cell = ws.cell(1, col, name)
        cell.font = header_font
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
        cell.border = thin
    examples = [
        (0.0, 165.56),
        (50.0, 165.56),
        (100.0, 164.93),
        (150.0, 162.73),
        (200.0, 159.91),
    ]
    for r, (s, c) in enumerate(examples, start=2):
        ws.cell(r, 1, s).number_format = "0.00"
        ws.cell(r, 2, c).number_format = "0.00"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 28
    note = wb.create_sheet("readme")
    note["A1"] = "人工形线模板说明"
    note["A1"].font = Font(bold=True, size=14)
    lines = [
        "只填写本工作表 manual_profile 中的两列，不要同时填写外径和周长。",
        f"列 {XLSX_CENTERLINE_COL}：沿中心线累计弧长，单位 mm。控制点不必等距。",
        f"列 {XLSX_CIRCUMFERENCE_COL}：该位置外径周长，单位 mm（C = πD）。",
        "系统按控制点生成连续人工形线：稳定尺寸段为常值，渐变段为 PCHIP，并统一为大径→小径单调不增。",
        "不要改列名。不要留空行。数值必须为数字，不能是文本。",
        "如需用外径，请将第二列改名为 outer_diameter_mm（不要两列同时存在时混用）。",
        "第一行必须是表头。若工作簿有多个工作表，系统读取第一个数据表。",
    ]
    for i, text in enumerate(lines, start=3):
        note[f"A{i}"] = text
    note.column_dimensions["A"].width = 90
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _norm(name: Any) -> str:
    return str(name or "").strip()


def _match_column(headers: List[str], aliases: Tuple[str, ...]) -> Optional[str]:
    lowered = {h.lower(): h for h in headers}
    for alias in aliases:
        if alias in headers:
            return alias
        if alias.lower() in lowered:
            return lowered[alias.lower()]
    return None


def parse_manual_xlsx(
    data: bytes,
    filename: str,
    *,
    centerline_length_mm: Optional[float] = None,
) -> Dict[str, Any]:
    errors: List[FieldError] = []
    warnings: List[str] = []
    name = (filename or "").lower()

    if not data:
        return _fail("文件为空", [FieldError(None, filename or "", "empty_file", "请上传非空的 .xlsx 文件")])
    if len(data) > MAX_XLSX_BYTES:
        return _fail(
            "文件过大",
            [FieldError(None, filename or "", "file_too_large", f"文件必须小于 {MAX_XLSX_BYTES} 字节")],
        )
    if name.endswith(".xls") and not name.endswith(".xlsx"):
        return _fail(
            "请上传 .xlsx 文件",
            [FieldError(None, filename or "", "not_xlsx", "请使用当前模板另存为 .xlsx")],
        )
    if not name.endswith(".xlsx"):
        return _fail(
            "请上传 .xlsx 文件",
            [FieldError(None, filename or "", "not_xlsx", "扩展名必须是 .xlsx")],
        )

    try:
        import pandas as pd
        from openpyxl import load_workbook
    except Exception as exc:
        return _fail("无法加载表格解析库", [FieldError(None, "", str(exc), "请联系管理员检查后端依赖")])

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        return _fail(
            "文件损坏或不是有效的 XLSX",
            [FieldError(None, filename or "", "corrupt_xlsx", "请用 Excel 另存为 .xlsx 后重试")],
        )

    sheet_names = list(wb.sheetnames)
    if not sheet_names:
        return _fail("工作簿没有工作表", [FieldError(None, "", "no_worksheet", "请使用系统提供的模板")])
    if len(sheet_names) > 1:
        warnings.append(f"工作簿含 {len(sheet_names)} 个工作表，已读取第一个数据表")
    sheet = "manual_profile" if "manual_profile" in sheet_names else sheet_names[0]
    if sheet == "readme" and len(sheet_names) > 1:
        sheet = next((n for n in sheet_names if n != "readme"), sheet)

    try:
        df = pd.read_excel(io.BytesIO(data), sheet_name=sheet, engine="openpyxl")
    except Exception as exc:
        return _fail(
            "无法读取工作表",
            [FieldError(None, sheet, f"worksheet_unreadable: {exc}", "请确认第一行是表头且工作表未加密")],
        )

    if df is None or df.empty:
        return _fail("工作表没有数据", [FieldError(2, "", "empty_sheet", "请至少填写表头和若干数据行")])

    headers = [_norm(c) for c in df.columns]
    df.columns = headers
    s_col = _match_column(headers, XLSX_CENTERLINE_ALIASES)
    c_col = _match_column(headers, XLSX_CIRCUMFERENCE_ALIASES)
    d_col = _match_column(headers, XLSX_DIAMETER_ALIASES)

    if s_col is None:
        errors.append(FieldError(
            1, XLSX_CENTERLINE_COL, "missing_column",
            f"请将中心线列命名为 {XLSX_CENTERLINE_COL}",
        ))
    if c_col is None and d_col is None:
        errors.append(FieldError(
            1, XLSX_CIRCUMFERENCE_COL, "missing_column",
            f"请将周长列命名为 {XLSX_CIRCUMFERENCE_COL}（本项目使用外径周长，不要同时填写外径）",
        ))
    if errors:
        return _fail("人工形线数据格式错误", errors, warnings)

    use_diameter = c_col is None and d_col is not None
    value_col = d_col if use_diameter else c_col
    value_name = XLSX_DIAMETER_COL if use_diameter else XLSX_CIRCUMFERENCE_COL

    s_vals: List[float] = []
    c_vals: List[float] = []
    for excel_row, (_, row) in enumerate(df.iterrows(), start=2):
        s_raw = row.get(s_col)
        v_raw = row.get(value_col)
        s_num, s_err = _as_float(s_raw)
        v_num, v_err = _as_float(v_raw)
        if s_err:
            errors.append(FieldError(
                excel_row, s_col, "expected numeric value",
                f"第 {excel_row} 行 {s_col} = {s_raw!r}，该字段必须为数值。",
            ))
            continue
        if v_err:
            errors.append(FieldError(
                excel_row, value_col, "expected numeric value",
                f"第 {excel_row} 行 {value_col} = {v_raw!r}，该字段必须为数值。",
            ))
            continue
        if not np.isfinite(s_num):
            errors.append(FieldError(excel_row, s_col, "nan_or_inf", f"第 {excel_row} 行 {s_col} 不能是 NaN/Inf"))
            continue
        if not np.isfinite(v_num):
            errors.append(FieldError(excel_row, value_col, "nan_or_inf", f"第 {excel_row} 行 {value_col} 不能是 NaN/Inf"))
            continue
        if v_num < 0:
            errors.append(FieldError(
                excel_row, value_col, "negative",
                f"第 {excel_row} 行 {value_col} = {v_num}，尺寸不能为负。",
            ))
            continue
        d_mm = float(v_num) if use_diameter else float(v_num) / np.pi
        if d_mm < MIN_DIAMETER_MM or d_mm > MAX_DIAMETER_MM:
            errors.append(FieldError(
                excel_row, value_col, "nonphysical_size",
                f"第 {excel_row} 行换算外径 {d_mm:.3f} mm 超出允许范围 "
                f"{MIN_DIAMETER_MM}–{MAX_DIAMETER_MM} mm，请检查单位是否为 mm。",
            ))
            continue
        s_vals.append(float(s_num))
        c_vals.append(float(v_num) if not use_diameter else float(diameter_to_circumference(np.array([v_num]))[0]))

    if errors:
        return _fail("人工形线数据格式错误", errors, warnings)

    if len(s_vals) < XLSX_MIN_POINTS:
        errors.append(FieldError(
            None, value_name, "too_few_points",
            f"有效点数 {len(s_vals)} 不足，至少需要 {XLSX_MIN_POINTS} 行。",
        ))
        return _fail("点数不足", errors, warnings)

    if centerline_length_mm is not None and np.isfinite(centerline_length_mm) and centerline_length_mm > 0:
        max_s = max(s_vals)
        if max_s > float(centerline_length_mm) * 1.02 + 1.0:
            errors.append(FieldError(
                None, s_col or XLSX_CENTERLINE_COL, "exceeds_centerline",
                f"人工数据最大中心线位置 {max_s:.2f} mm 超出 STP 中心线总长 "
                f"{float(centerline_length_mm):.2f} mm，请检查单位或测量起点。",
            ))
            return _fail("人工数据超出中心线总长度", errors, warnings)

    s = np.asarray(s_vals, dtype=float)
    c = np.asarray(c_vals, dtype=float)
    d = c / np.pi
    profile = ProfileArrays(s=s, circumference=c, diameter=d, source="manual_xlsx")
    raw_xlxs = [[float(si), float(di)] for si, di in zip(s, d)]
    return {
        "ok": True,
        "profile": profile,
        "raw_xlxs": raw_xlxs,
        "warnings": warnings,
        "log": [],
        "sheet": sheet,
        "used_column": value_name,
        "filename": filename,
        "point_count": int(len(profile.s)),
        "metrics": {
            "point_count": int(len(profile.s)),
            "s_min": float(np.min(s)),
            "s_max": float(np.max(s)),
        },
    }


def _as_float(value: Any) -> Tuple[float, Optional[str]]:
    if value is None:
        return float("nan"), "empty"
    if isinstance(value, str) and not value.strip():
        return float("nan"), "empty"
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", "")
        num = float(value)
    except (TypeError, ValueError):
        return float("nan"), "not_numeric"
    return num, None


def _fail(message: str, errors: List[FieldError], warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "code": "QA_MANUAL_XLSX_INVALID",
        "status": QA_MANUAL_DATA_INVALID,
        "message": message,
        "details": [e.to_dict() for e in errors],
        "warnings": warnings or [],
        "profile": None,
    }
