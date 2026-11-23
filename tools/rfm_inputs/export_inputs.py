"""Export the derived inputs the program works from: centerline + outer formline.

Field names and units follow the product's own interchange format, so the files
can be fed straight back into 形线质量检查 / 工艺规划:

    形线（外径）  manual_profile 表：centerline_mm, outer_circumference_mm
    中心线        站位、累计弧长、X/Y/Z、切向
    截面明细      周长、等效外径、壁厚、轴长、截面坐标基、测量状态
    外轮廓 2D     每个站位的正截面外轮廓点（与成型放样同一套 384 个等弧长点）

Usage:
    python tools/rfm_inputs/export_inputs.py [--case 24TK_1324-1]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.rfm.processors import _resample_contour  # noqa: E402

OUT_ROOT = ROOT / "gnt管件_输入数据"
CACHE = ROOT / "gnt管件_外径形腔" / "_cache"
CONTOUR_POINTS = 384  # 与成型放样 max(384, section_points) 一致


def _log(msg):
    print(msg, flush=True)


def load_case(case):
    data = json.loads((CACHE / f"{case}_measured.json").read_text(encoding="utf-8"))
    return data["sections_full"]


def tangents(positions):
    p = np.asarray(positions, float)
    t = np.gradient(p, axis=0)
    norm = np.linalg.norm(t, axis=1)[:, None]
    return t / np.maximum(norm, 1e-12)


def write_formline_xlsx(path, sections):
    """Exactly the manual_profile template: sheet + headers + readme."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "manual_profile"
    headers = ["centerline_mm", "outer_circumference_mm"]
    for col, name in enumerate(headers, start=1):
        cell = ws.cell(1, col, name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1478FF")
        cell.alignment = Alignment(horizontal="center")
    for row, section in enumerate(sections, start=2):
        ws.cell(row, 1, round(float(section["cumulative_length"]), 4)).number_format = "0.000"
        ws.cell(row, 2, round(float(section["perimeter"]), 4)).number_format = "0.000"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 28
    ws.freeze_panes = "A2"

    note = wb.create_sheet("readme")
    note["A1"] = "形线（外径）数据说明"
    note["A1"].font = Font(bold=True, size=14)
    lines = [
        "本文件由程序自动导出，内容是从管件 STP 直接测量得到的形线，不是人工测量。",
        "列 centerline_mm：沿中心线累计弧长，单位 mm，站位间隔 1 mm。",
        "列 outer_circumference_mm：该站位外径周长，单位 mm（C = πD）。",
        "端部按产品逻辑舍弃不可靠段并线性外推，外推站位在“截面明细”工作表内标记为 extrapolated。",
        "可直接在本软件的「人工形线」导入中使用；如作为人工数据，请自行确认后再进入工艺规划。",
    ]
    for i, text in enumerate(lines, start=3):
        note[f"A{i}"] = text
    note.column_dimensions["A"].width = 100
    wb.save(path)


def write_centerline_xlsx(path, sections):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    positions = np.asarray([s["position"] for s in sections], float)
    tangent = tangents(positions)
    wb = Workbook()
    ws = wb.active
    ws.title = "中心线"
    headers = ["station", "arc_mm", "x_mm", "y_mm", "z_mm",
               "tangent_x", "tangent_y", "tangent_z"]
    for col, name in enumerate(headers, start=1):
        cell = ws.cell(1, col, name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1478FF")
        cell.alignment = Alignment(horizontal="center")
    for row, (section, point, vec) in enumerate(zip(sections, positions, tangent), start=2):
        values = [row - 2, float(section["cumulative_length"]), *point.tolist(), *vec.tolist()]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row, col, round(float(value), 5))
            cell.number_format = "0" if col == 1 else "0.00000"
    for col, width in zip("ABCDEFGH", (9, 12, 13, 13, 13, 12, 12, 12)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(path)


def write_sections_xlsx(path, sections):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    headers = ["station", "arc_mm", "outer_perimeter_mm", "equivalent_outer_diameter_mm",
               "outer_area_mm2", "inner_perimeter_mm", "wall_min_mm", "wall_median_mm",
               "wall_mean_mm", "wall_max_mm", "x_length_mm", "y_length_mm",
               "measurement_status", "x_mm", "y_mm", "z_mm",
               "basis_u_x", "basis_u_y", "basis_u_z", "basis_v_x", "basis_v_y", "basis_v_z",
               "normal_x", "normal_y", "normal_z", "centroid_residual_mm", "normal_deviation_deg"]
    wb = Workbook()
    ws = wb.active
    ws.title = "截面明细"
    for col, name in enumerate(headers, start=1):
        cell = ws.cell(1, col, name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1478FF")
        cell.alignment = Alignment(horizontal="center")

    from core.pp.profile_qa.key_section import xy_from_section_row
    for row, section in enumerate(sections, start=2):
        size = xy_from_section_row(section) or (None, None)
        values = [
            row - 2, section["cumulative_length"], section["perimeter"],
            section.get("equivalent_diameter"), section.get("outer_area"),
            section.get("inner_perimeter"), section.get("wall_thickness_min"),
            section.get("wall_thickness"), section.get("wall_thickness_mean"),
            section.get("wall_thickness_max"), size[0], size[1],
            section.get("measurement_status"),
            *[float(v) for v in section["position"]],
            *[float(v) for v in section["basis_u"]],
            *[float(v) for v in section["basis_v"]],
            *[float(v) for v in section["normal"]],
            section.get("centroid_residual_mm"), section.get("normal_deviation_deg"),
        ]
        for col, value in enumerate(values, start=1):
            if isinstance(value, str) or value is None:
                ws.cell(row, col, value)
            else:
                ws.cell(row, col, round(float(value), 6)).number_format = "0.000000"
    ws.freeze_panes = "B2"

    note = wb.create_sheet("readme")
    note["A1"] = "截面明细说明"
    note["A1"].font = Font(bold=True, size=14)
    lines = [
        "每个站位对应一个垂直于中心线的正截面，站位间隔 1 mm。",
        "outer_perimeter_mm：外轮廓周长；equivalent_outer_diameter_mm = 周长 / π。",
        "wall_*：沿外轮廓 192 个等弧长采样点到内轮廓的最短距离统计（不是径向差）。",
        "x_length_mm / y_length_mm：外轮廓在截面主方向上的外接尺寸（完整轴长，非半轴）。",
        "x/y/z 为该截面中心（外轮廓面积重心）；basis_u/basis_v 为截面内二维坐标基；normal 为截面法向。",
        "measurement_status：measured 为实测，extrapolated 为端部线性外推，needs_review 为需人工复核。",
        "截面内的二维轮廓点见同名 CSV：坐标 = (x,y,z) + u·basis_u + v·basis_v。",
    ]
    for i, text in enumerate(lines, start=3):
        note[f"A{i}"] = text
    note.column_dimensions["A"].width = 110
    wb.save(path)


def write_contour_csv(path, sections):
    count = 0
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["arc_mm", "point_index", "u_mm", "v_mm"])
        for section in sections:
            points = _resample_contour(section["outer_2d"], CONTOUR_POINTS)
            arc = float(section["cumulative_length"])
            for index, (u, v) in enumerate(points):
                writer.writerow([round(arc, 4), index, round(float(u), 5), round(float(v), 5)])
                count += 1
    return count


def export_case(case):
    sections = load_case(case)
    out_dir = OUT_ROOT / case
    out_dir.mkdir(parents=True, exist_ok=True)
    write_formline_xlsx(out_dir / "形线_外径.xlsx", sections)
    write_centerline_xlsx(out_dir / "中心线.xlsx", sections)
    write_sections_xlsx(out_dir / "截面明细.xlsx", sections)
    points = write_contour_csv(out_dir / "外轮廓_2D.csv", sections)
    length = float(sections[-1]["cumulative_length"])
    _log(f"[{case}] {len(sections)} 站位，中心线长 {length:.2f} mm，轮廓点 {points} 行")
    return {"case": case, "stations": len(sections), "centerline_length_mm": round(length, 3),
            "contour_points": points,
            "perimeter_min_mm": round(min(s["perimeter"] for s in sections), 3),
            "perimeter_max_mm": round(max(s["perimeter"] for s in sections), 3),
            "wall_median_mm": round(float(np.median([s["wall_thickness"] for s in sections])), 4)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default=None)
    args = parser.parse_args()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    cases = sorted(p.name[: -len("_measured.json")] for p in CACHE.glob("*_measured.json"))
    if args.case:
        cases = [c for c in cases if c == args.case]

    summary = []
    for case in cases:
        _log(f"[{case}] 导出中心线与形线数据 ...")
        summary.append(export_case(case))

    (OUT_ROOT / "_索引.json").write_text(
        json.dumps({"cases": summary,
                    "columns": {"形线_外径.xlsx": "manual_profile: centerline_mm, outer_circumference_mm",
                                "中心线.xlsx": "station, arc_mm, x/y/z, tangent",
                                "截面明细.xlsx": "周长/等效外径/壁厚/轴长/截面坐标基/状态",
                                "外轮廓_2D.csv": "arc_mm, point_index, u_mm, v_mm（每站位 384 点）"}},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"完成：{len(summary)} 个管件 -> {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
