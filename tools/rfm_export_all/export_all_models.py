"""Export every model the RFM 硬模 modules generate, for each fitting.

Calls the same processors and helpers the product uses for 生成模型, so the
geometry is identical to what the GUI writes out:

    成型 shaping      → 外径形腔 · 实心内径 · 反切模具（以外径形腔反切）
    扩口 belling      → 扩口芯轴 · 扩口腔体 · 反切模具（以扩口腔体反切）
    压扁 flattening   → 压扁模型 · 反切模具
    压弯 bending      → 压弯模型 · 反切模具

Process dimensions follow the product defaults: belling_length = min(L1, 总长)
(无工艺设计时 L1 取 200), 压扁/压弯 flattening_length = min(L1, 总长)、
transition_length = min(150, 总长 − 压扁段)、total_length = 总长、反切余量 15 mm。

Measured sections come from ``gnt管件_外径形腔/_cache/<case>_measured.json``.

Usage:
    python tools/rfm_export_all/export_all_models.py [--case 24TK_1324-1]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.rfm.processors import get_processor  # noqa: E402
from core.common.solid_geometry import cut_mold, step_bytes, valid_solid, volume  # noqa: E402
from core.pp.tube.brep_sections import read_shape  # noqa: E402

OUT_ROOT = ROOT / "gnt管件_全模型"
MEASURED_CACHE = ROOT / "gnt管件_外径形腔" / "_cache"
OP_LABEL = {"shaping": "成型", "belling": "扩口", "flattening": "压扁", "bending": "压弯"}
MOLD_MARGIN = 15.0


def _log(msg):
    print(msg, flush=True)


def build_rows(sections):
    """Same record shape api/pp.py hands to RFM (周长 + 轴长 + 壁厚 + 坐标)."""
    from core.pp.profile_qa.key_section import xy_from_section_row
    rows = []
    for section in sections:
        entry = {
            "中心线长度(mm)": section.get("cumulative_length", 0),
            "外径周长(mm)": section.get("perimeter", 0),
            "壁厚(mm)": section.get("wall_thickness"),
            "measurement_status": section.get("measurement_status", "measured"),
        }
        size = xy_from_section_row(section)
        if size:
            entry["x_length"] = round(float(size[0]), 4)
            entry["y_length"] = round(float(size[1]), 4)
        if section.get("position") is not None:
            entry.update(zip(("x", "y", "z"), section["position"]))
        rows.append(entry)
    return rows


def perimeter_matched_axes(rows):
    """Rebuild 前序轴长 as ellipses with the *measured* perimeter.

    The flattening module only accepts axes whose ellipse perimeter reproduces
    the process perimeter. A raw STP section is usually not an ellipse (the
    width/height extents give a smaller perimeter), so the module refuses the
    measured extents. This keeps the process invariant — same perimeter, same
    measured aspect ratio — and marks the derivation in the report.
    """
    from scipy.optimize import brentq
    from core.rfm.processors import ellipse_perimeter

    out = []
    for row in rows:
        new = dict(row)
        x, y = row.get("x_length"), row.get("y_length")
        perimeter = float(row["外径周长(mm)"])
        if not x or not y or x <= 0 or y <= 0:
            out.append(new)
            continue
        ratio = max(x, y) / min(x, y)
        # ellipse_perimeter takes SEMI axes; the process fields are FULL axes.
        b = brentq(lambda t: ellipse_perimeter(ratio * t, t) - perimeter,
                   perimeter / (2 * np.pi * ratio), perimeter / 2)
        a = ratio * b
        new["x_length"], new["y_length"] = ((2 * a, 2 * b) if x >= y else (2 * b, 2 * a))
        out.append(new)
    return out


def make_processor(operation, sections, rows, length):
    """Processor configured exactly like _build_processor_from_sess + GUI defaults."""
    config = {}
    if operation == "belling":
        config.update(belling_length=min(200.0, length), wall_thickness=2.0,
                      wall_thickness_source="data", length_allowance=0.0)
    elif operation in ("flattening", "bending"):
        flattening_length = min(200.0, length)
        config.update(flattening_length=flattening_length,
                      transition_length=min(150.0, max(0.0, length - flattening_length)),
                      total_length=length, flattening_allowance=0.0, num_sections=200)
        if operation == "bending":
            config.update(num_sections=152, flattening_executed=False, transition_length=150.0)
    proc = get_processor(operation)
    proc.set_session_data([s["position"] for s in sections], rows)
    proc.set_sections(sections)
    for key, value in config.items():
        if hasattr(proc, key):
            setattr(proc, key, value)
    return proc


def save_shape(shape, path):
    data = step_bytes(shape)
    path.write_bytes(data)
    reread = valid_solid(read_shape(path))
    if abs(volume(reread) - volume(shape)) > max(0.01, volume(shape) * 1e-6):
        raise RuntimeError(f"{path.name} 回读体积不一致")
    return {"file": path.name, "bytes": len(data), "volume_mm3": round(volume(reread), 2)}


def export_case(case, out_dir):
    analysis = json.loads((MEASURED_CACHE / f"{case}_measured.json").read_text(encoding="utf-8"))
    sections = analysis["sections_full"]
    rows = build_rows(sections)
    length = float(analysis.get("center_line_length") or 0)
    made = []

    for operation in ("shaping", "belling", "flattening", "bending"):
        label = OP_LABEL[operation]
        started = time.monotonic()
        try:
            proc = make_processor(operation, sections, rows, length)
            if operation == "shaping":
                proc.generate_model()
                made.append({"operation": operation, "model": "外径形腔", **save_shape(
                    proc.shape, out_dir / f"{label}_外径形腔.stp"), "seconds": None})
                _log(f"  [{case}] {label} · 外径形腔  {made[-1]['file']}  {made[-1]['bytes']/1e6:.1f} MB")
                proc.generate_inner_mold()
                made.append({"operation": operation, "model": "实心内径", **save_shape(
                    proc.inner_shape, out_dir / f"{label}_实心内径.stp")})
                _log(f"  [{case}] {label} · 实心内径  {made[-1]['file']}  {made[-1]['bytes']/1e6:.1f} MB")
                cavity = proc.shape
            elif operation == "belling":
                proc.generate_mandrel()
                made.append({"operation": operation, "model": "扩口芯轴", **save_shape(
                    proc.shape, out_dir / f"{label}_扩口芯轴.stp")})
                _log(f"  [{case}] {label} · 扩口芯轴  {made[-1]['file']}  {made[-1]['bytes']/1e6:.1f} MB")
                proc.generate_cavity_model()
                made.append({"operation": operation, "model": "扩口腔体", **save_shape(
                    proc.cavity_shape, out_dir / f"{label}_扩口腔体.stp")})
                _log(f"  [{case}] {label} · 扩口腔体  {made[-1]['file']}  {made[-1]['bytes']/1e6:.1f} MB")
                cavity = proc.cavity_shape
            else:
                model = "压扁模型" if operation == "flattening" else "压弯模型"
                axis_source = "measured_extents"
                try:
                    proc.generate_model()
                except ValueError as exc:
                    if operation != "flattening" or "轴长" not in str(exc):
                        raise
                    axis_source = "perimeter_matched_ellipse"
                    _log(f"  [{case}] {label}：截面非椭圆，改用等周长椭圆轴长（周长不变）")
                    proc = make_processor(operation, sections, perimeter_matched_axes(rows), length)
                    proc.generate_model()
                made.append({"operation": operation, "model": model, "axis_source": axis_source,
                             **save_shape(proc.shape, out_dir / f"{label}_{model}.stp")})
                _log(f"  [{case}] {label} · {model}  {made[-1]['file']}  {made[-1]['bytes']/1e6:.1f} MB"
                     + ("" if axis_source == "measured_extents" else "（等周长椭圆轴长）"))
                cavity = proc.shape

            mold_started = time.monotonic()
            mold, info = cut_mold(cavity, {"mold_margin": MOLD_MARGIN, "mold_auto_size": True})
            made.append({"operation": operation, "model": "反切模具",
                         **save_shape(mold, out_dir / f"{label}_反切模具.stp"),
                         "mold_info": info, "seconds": round(time.monotonic() - mold_started, 1)})
            _log(f"  [{case}] {label} · 反切模具  {made[-1]['file']}  "
                 f"{made[-1]['bytes']/1e6:.1f} MB  余量 {info['minimum_margin_mm']:.1f} mm")
        except Exception as exc:
            made.append({"operation": operation, "status": "failed",
                         "error": f"{type(exc).__name__}: {exc}",
                         "traceback": traceback.format_exc(limit=5),
                         "seconds": round(time.monotonic() - started, 1)})
            _log(f"  [{case}] {label} 失败：{exc}")
    return {"case": case, "centerline_length_mm": round(length, 3), "models": made}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default="gnt管件")
    parser.add_argument("--case", default=None)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--out", default=None,
                        help="模型输出根目录（默认 gnt管件_全模型）；从头跑时指向独立的运行目录")
    parser.add_argument("--cache", default=None,
                        help="measured.json 所在目录（默认 gnt管件_外径形腔/_cache）")
    args = parser.parse_args()

    global OUT_ROOT, MEASURED_CACHE
    if args.out:
        OUT_ROOT = Path(args.out)
    if args.cache:
        MEASURED_CACHE = Path(args.cache)

    src = ROOT / args.src
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in (".stp", ".step"))
    if args.case:
        files = [p for p in files if p.stem == args.case]

    report_dir = OUT_ROOT / "_report"
    report_dir.mkdir(parents=True, exist_ok=True)

    for path in files:
        case = path.stem
        out_dir = OUT_ROOT / case
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{case}.json"
        if args.skip_existing and report_path.exists():
            _log(f"[{case}] 已有报告，跳过")
            continue
        if not (MEASURED_CACHE / f"{case}_measured.json").exists():
            _log(f"[{case}] 缺少测量缓存，跳过")
            continue
        _log(f"[{case}] 生成全部模型 ...")
        started = time.monotonic()
        try:
            report = export_case(case, out_dir)
        except Exception as exc:
            report = {"case": case, "models": [], "error": f"{type(exc).__name__}: {exc}",
                      "traceback": traceback.format_exc(limit=5)}
            _log(f"[{case}] 失败：{exc}")
        report["ok"] = sum(1 for m in report["models"] if "file" in m)
        report["failed"] = sum(1 for m in report["models"] if m.get("status") == "failed")
        report["seconds"] = round(time.monotonic() - started, 1)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(f"[{case}] 完成 {report['ok']} 个模型（失败 {report['failed']}），{report['seconds']} s")

    reports = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted(report_dir.glob("*.json")) if p.name != "summary.json"]
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "output_folder": str(OUT_ROOT),
        "cases": len(reports),
        "models": sum(r.get("ok", 0) for r in reports),
        "failed": sum(r.get("failed", 0) for r in reports),
        "detail": [{"case": r["case"], "models": r.get("ok", 0), "failed": r.get("failed", 0),
                    "seconds": r.get("seconds")} for r in reports],
    }
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"总计：{summary['models']} 个模型，失败 {summary['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
