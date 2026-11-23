"""Verify the exported input data against the measured geometry.

1) 形线_外径.xlsx is fed back through the product's own parser (parse_manual_xlsx).
2) 中心线.xlsx reproduces every section position.
3) 外轮廓_2D.csv, rebuilt with position + u·basis_u + v·basis_v, reproduces the
   measured outer perimeter.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.pp.profile_qa.manual_profile_parser import parse_manual_xlsx  # noqa: E402

OUT_ROOT = ROOT / "gnt管件_输入数据"
CACHE = ROOT / "gnt管件_外径形腔" / "_cache"


def read_centerline(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["中心线"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    data = np.array([r[2:5] for r in rows[1:]], float)
    return header, data


def read_sections(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["截面明细"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    index = {name: i for i, name in enumerate(header)}
    picked = [{"arc": r[index["arc_mm"]], "perimeter": r[index["outer_perimeter_mm"]],
               "position": [r[index["x_mm"]], r[index["y_mm"]], r[index["z_mm"]]],
               "u": [r[index["basis_u_x"]], r[index["basis_u_y"]], r[index["basis_u_z"]]],
               "v": [r[index["basis_v_x"]], r[index["basis_v_y"]], r[index["basis_v_z"]]],
               "status": r[index["measurement_status"]]} for r in rows[1:]]
    return picked


def read_contours(path):
    rings = defaultdict(list)
    with path.open(encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            rings[float(row["arc_mm"])].append((int(row["point_index"]),
                                                float(row["u_mm"]), float(row["v_mm"])))
    return rings


def main():
    results = []
    for case_dir in sorted(p for p in OUT_ROOT.iterdir() if p.is_dir()):
        case = case_dir.name
        record = {"case": case}
        try:
            analysis = json.loads((CACHE / f"{case}_measured.json").read_text(encoding="utf-8"))
            sections = analysis["sections_full"]

            # 1) 形线 XLSX -> 产品自己的解析器
            parsed = parse_manual_xlsx((case_dir / "形线_外径.xlsx").read_bytes(),
                                       "形线_外径.xlsx",
                                       centerline_length_mm=float(sections[-1]["cumulative_length"]))
            record["formline_parse_ok"] = bool(parsed.get("ok"))
            if parsed.get("ok"):
                profile = parsed["profile"]
                delta_s = np.max(np.abs(np.asarray(profile.s, float)
                                        - np.asarray([s["cumulative_length"] for s in sections], float)))
                delta_c = np.max(np.abs(np.asarray(profile.circumference, float)
                                        - np.asarray([s["perimeter"] for s in sections], float)))
                record["formline_max_delta_arc_mm"] = round(float(delta_s), 6)
                record["formline_max_delta_perimeter_mm"] = round(float(delta_c), 6)
            else:
                record["formline_error"] = parsed.get("message") or parsed.get("code")

            # 2) 中心线
            _, line = read_centerline(case_dir / "中心线.xlsx")
            reference = np.asarray([s["position"] for s in sections], float)
            record["centerline_max_delta_mm"] = round(
                float(np.max(np.abs(line - reference))), 6)

            # 3) 截面明细 + 外轮廓 CSV
            rows = read_sections(case_dir / "截面明细.xlsx")
            record["section_rows"] = len(rows)
            record["section_max_delta_perimeter_mm"] = round(
                float(max(abs(r["perimeter"] - s["perimeter"]) for r, s in zip(rows, sections))), 6)
            record["section_max_delta_position_mm"] = round(
                float(max(max(abs(a - b) for a, b in zip(r["position"], s["position"]))
                          for r, s in zip(rows, sections))), 6)

            rings = read_contours(case_dir / "外轮廓_2D.csv")
            arcs = np.array(sorted(rings), float)
            picks = [0, len(rows) // 3, 2 * len(rows) // 3, len(rows) - 1]
            worst = 0.0
            for i in picks:
                row = rows[int(i)]
                key = arcs[int(np.argmin(np.abs(arcs - float(row["arc"]))))]
                pts = sorted(rings[key])
                uv = np.array([[u, v] for _, u, v in pts], float)
                world = (np.asarray(row["position"], float)
                         + uv[:, :1] * np.asarray(row["u"], float)
                         + uv[:, 1:] * np.asarray(row["v"], float))
                closed = np.vstack([world, world[:1]])
                perimeter = float(np.linalg.norm(np.diff(closed, axis=0), axis=1).sum())
                worst = max(worst, abs(perimeter - float(row["perimeter"])))
                if len(uv) != 384:
                    record["contour_point_count"] = len(uv)
            record["contour_rebuilt_max_delta_mm"] = round(worst, 4)
            # 384 点等弧长重采样的弦长本身带来约 0.05 mm 量级误差
            record["status"] = "passed" if (record.get("formline_parse_ok")
                                            and record["centerline_max_delta_mm"] <= 1e-4
                                            and worst <= 0.1) else "check"
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        results.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    (OUT_ROOT / "_校验.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    good = sum(1 for r in results if r["status"] == "passed")
    print(f"\n输入数据校验：{good}/{len(results)} 通过")


if __name__ == "__main__":
    main()
