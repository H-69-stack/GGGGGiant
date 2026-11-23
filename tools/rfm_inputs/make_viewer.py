"""Build offline interactive viewers for the exported centerline / formline data.

Reads only the delivered files (截面明细.xlsx + 外轮廓_2D.csv), so the page shows
exactly the data that was exported. Output: one self-contained HTML per fitting
plus an index page. No external scripts, no server — open by double click.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
IN_ROOT = ROOT / "gnt管件_输入数据"
OUT_ROOT = IN_ROOT / "可视化"
TEMPLATE = Path(__file__).resolve().parent / "viewer_template.html"

CONTOUR_POINTS = 96      # 显示用：384 点每 4 点取 1
RINGS = 120              # 3D 视图抽多少圈

STATUS = {"measured": 0, "extrapolated": 1, "needs_review": 2}


def read_table(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["截面明细"]
    rows = list(ws.iter_rows(values_only=True))
    header = list(rows[0])
    index = {name: i for i, name in enumerate(header)}
    out = {key: [] for key in ("s", "c", "d", "wmin", "wmed", "wmax", "xl", "yl", "st",
                               "px", "py", "pz", "bas")}
    for row in rows[1:]:
        out["s"].append(float(row[index["arc_mm"]]))
        out["c"].append(float(row[index["outer_perimeter_mm"]]))
        out["d"].append(float(row[index["equivalent_outer_diameter_mm"]]))
        out["wmin"].append(row[index["wall_min_mm"]])
        out["wmed"].append(row[index["wall_median_mm"]])
        out["wmax"].append(row[index["wall_max_mm"]])
        out["xl"].append(float(row[index["x_length_mm"]]))
        out["yl"].append(float(row[index["y_length_mm"]]))
        out["st"].append(STATUS.get(str(row[index["measurement_status"]]), 0))
        out["px"].append(float(row[index["x_mm"]]))
        out["py"].append(float(row[index["y_mm"]]))
        out["pz"].append(float(row[index["z_mm"]]))
        out["bas"].extend(float(row[index[key]]) for key in
                          ("basis_u_x", "basis_u_y", "basis_u_z",
                           "basis_v_x", "basis_v_y", "basis_v_z",
                           "normal_x", "normal_y", "normal_z"))
    return out


def read_contours(path):
    rings = defaultdict(list)
    with path.open(encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            rings[float(row["arc_mm"])].append((int(row["point_index"]),
                                                float(row["u_mm"]), float(row["v_mm"])))
    arcs = sorted(rings)
    return arcs, [sorted(rings[a]) for a in arcs]


def r4(values):
    return [round(float(v), 4) for v in values]


def build_payload(case_dir):
    table = read_table(case_dir / "截面明细.xlsx")
    arcs, contours = read_contours(case_dir / "外轮廓_2D.csv")
    n = len(table["s"])
    if len(contours) != n:
        raise RuntimeError(f"{case_dir.name}: 轮廓站位数 {len(contours)} 与表格 {n} 不一致")

    # 局部轮廓：每 4 点取 1，保持等弧长顺序
    sect = []
    for contour in contours:
        pts = [contour[k] for k in range(0, len(contour), len(contour) // CONTOUR_POINTS)][:CONTOUR_POINTS]
        for _, u, v in pts:
            sect.append(round(u, 4))
            sect.append(round(v, 4))

    # 3D 环：抽 RINGS 个站位，转世界坐标
    pick = np.unique(np.linspace(0, n - 1, min(RINGS, n)).astype(int))
    rings, ring_index = [], []
    for i in pick:
        px, py, pz = table["px"][i], table["py"][i], table["pz"][i]
        b = i * 9
        ux, uy, uz, vx, vy, vz = table["bas"][b:b + 6]
        for k in range(CONTOUR_POINTS):
            u = sect[i * CONTOUR_POINTS * 2 + k * 2]
            v = sect[i * CONTOUR_POINTS * 2 + k * 2 + 1]
            rings.extend((round(px + u * ux + v * vx, 3),
                          round(py + u * uy + v * vy, 3),
                          round(pz + u * uz + v * vz, 3)))
        ring_index.append(int(i))

    payload = {
        "case": case_dir.name,
        "n": n,
        "p": CONTOUR_POINTS,
        "ns": len(ring_index),
        "length": round(float(table["s"][-1]), 3),
        "s": r4(table["s"]),
        "c": r4(table["c"]),
        "d": r4(table["d"]),
        "wmin": r4(table["wmin"]),
        "wmed": r4(table["wmed"]),
        "wmax": r4(table["wmax"]),
        "xl": r4(table["xl"]),
        "yl": r4(table["yl"]),
        "st": table["st"],
        "pos": r4(np.column_stack([table["px"], table["py"], table["pz"]]).ravel()),
        "bas": r4(table["bas"]),
        "sect": sect,
        "rings": rings,
        "ringIdx": ring_index,
        "reviewCount": sum(1 for v in table["st"] if v == 2),
        "extrapolated": sum(1 for v in table["st"] if v == 1),
    }
    return payload


def summary_text(payload):
    d_min, d_max = min(payload["d"]), max(payload["d"])
    return (f"L {payload['length']:.1f} mm · D {d_min:.2f}–{d_max:.2f} mm · "
            f"{payload['n']} 站位")


def sparkline(payload, width=260, height=64):
    s = np.asarray(payload["s"], float)
    d = np.asarray(payload["d"], float)
    lo, hi = d.min(), d.max()
    pad = max((hi - lo) * 0.12, 0.4)
    lo, hi = lo - pad, hi + pad
    x = np.linspace(6, width - 6, len(s))
    y = height - 6 - (d - lo) / (hi - lo) * (height - 12)
    step = max(1, len(s) // 400)
    path = " ".join(f"{x[i]:.1f},{y[i]:.1f}" for i in range(0, len(s), step))
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'preserveAspectRatio="none">'
            f'<polyline points="{path}" fill="none" stroke="#c05621" stroke-width="1.6"/>'
            f'</svg>')


def build_index(entries):
    cards = []
    for item in entries:
        p = item["payload"]
        cards.append(f"""
      <a class="card" href="{p['case']}.html">
        <div class="head"><span class="case">{p['case']}</span>
          <span class="go">打开交互视图 →</span></div>
        <div class="spark">{sparkline(p)}</div>
        <div class="meta">{summary_text(p)}<br>
          <span class="dim">形线曲线：外径 D 沿中心线长度变化</span></div>
      </a>""")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>管件中心线与形线（外径）可视化</title>
<style>
 body{{margin:0;background:#f6f8fb;color:#1a202c;
      font-family:"Microsoft YaHei","Segoe UI",system-ui,sans-serif}}
 header{{padding:22px 26px 8px}}
 h1{{font-size:19px;margin:0 0 6px}}
 p.sub{{margin:0;color:#64748b;font-size:12.5px;line-height:1.6;max-width:900px}}
 .grid{{display:grid;gap:14px;padding:16px 26px 30px;
        grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}}
 .card{{display:block;text-decoration:none;color:inherit;background:#fff;border:1px solid #dfe5ee;
        border-radius:10px;padding:12px 14px;transition:box-shadow .15s,border-color .15s}}
 .card:hover{{border-color:#93b4dd;box-shadow:0 2px 10px rgba(43,108,176,.12)}}
 .head{{display:flex;justify-content:space-between;align-items:baseline;gap:8px}}
 .case{{font-weight:600;font-size:14px}}
 .go{{font-size:12px;color:#2b6cb0}}
 .spark{{margin:8px 0 6px}}
 .meta{{font-size:12px;color:#475569;line-height:1.6}}
 .dim{{color:#94a3b8}}
 .tips{{margin:6px 26px 0;padding:10px 14px;background:#eef4fc;border:1px solid #d6e4f7;
        border-radius:8px;font-size:12px;color:#334155;line-height:1.7;max-width:900px}}
 code{{background:#e8eef7;padding:1px 5px;border-radius:4px;font-size:11.5px}}
</style></head>
<body>
<header>
  <h1>管件中心线与形线（外径）　·　6 个管件</h1>
  <p class="sub">数据来自 <code>gnt管件_输入数据</code>：STP 原生截面，沿中心线每 1 mm 一个站位。
  点击任一管件进入交互视图：可旋转 3D 外轮廓、沿形线曲线选站位、查看该站位截面形状与读数。</p>
</header>
<div class="tips">
  交互视图操作：3D 区拖动旋转 / 滚轮缩放 / 双击复位；曲线区移动鼠标或拖动即选站位；
  选中后可点下方的站位滑杆或按 <code>←</code> <code>→</code> 微调。
</div>
<div class="grid">{''.join(cards)}
</div>
</body></html>
"""


def main():
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    template = TEMPLATE.read_text(encoding="utf-8")
    entries = []
    for case_dir in sorted(p for p in IN_ROOT.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if not (case_dir / "截面明细.xlsx").exists():
            continue
        payload = build_payload(case_dir)
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        html = (template
                .replace("__TITLE__", f"{payload['case']} · 中心线与形线可视化")
                .replace("__CASE__", payload["case"])
                .replace("__SUMMARY__", summary_text(payload))
                .replace("__N__", str(payload["n"] - 1))
                .replace("__DATA__", data))
        target = OUT_ROOT / f"{payload['case']}.html"
        target.write_text(html, encoding="utf-8")
        entries.append({"payload": payload, "file": target})
        print(f"{payload['case']}: {target.name}  {target.stat().st_size / 1024:.0f} KB", flush=True)

    index = OUT_ROOT / "index.html"
    index.write_text(build_index(entries), encoding="utf-8")
    print(f"总览页：{index}")


if __name__ == "__main__":
    main()
