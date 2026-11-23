"""Batch-generate RFM 外径形腔 STEP for every fitting in a folder.

Uses the same production code path as the shaping page:
    analyze_tube(stp, 'stp') -> ShapingProcessor.generate_model() -> step_bytes()

Usage:
    python tools/rfm_outer_cavity/run_outer_cavity.py --src "gnt管件" --out "gnt管件_外径形腔"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

from core.pp.tube import analyze_tube  # noqa: E402
from core.rfm.processors import ShapingProcessor  # noqa: E402
from core.common.solid_geometry import step_bytes, valid_solid, volume  # noqa: E402
from core.pp.tube.brep_sections import read_shape  # noqa: E402


def _log(msg):
    print(msg, flush=True)


def _bounds(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    values = box.Get()
    return [[round(float(v), 3) for v in values[:3]], [round(float(v), 3) for v in values[3:]]]


def _load_or_measure(path: Path, cache_dir: Path, algorithm: str, force: bool,
                     end_treatment=None):
    """Measurement is the expensive step; cache it so a killed run can resume."""
    cache = cache_dir / f"{path.stem}_measured.json"
    if cache.exists() and not force:
        _log(f"[{path.stem}] 复用已验证的截面测量缓存 {cache.name}")
        return json.loads(cache.read_text(encoding="utf-8"))
    analysis = analyze_tube(str(path), "stp", algorithm,
                            sparse_step_mm=25.0,
                            end_treatment=end_treatment,
                            progress_callback=lambda pct, msg: _log(f"[{path.stem}] {pct:3d}% {msg}"))
    if not isinstance(analysis, dict) or analysis.get("error"):
        raise RuntimeError((analysis or {}).get("error") or "STP 分析无结果")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
    return analysis


def process_one(path: Path, out_dir: Path, report_dir: Path, algorithm: str,
                cache_dir: Path, force: bool = False, end_treatment=None):
    started = time.monotonic()
    case = path.stem
    record = {"case": case, "source": path.name, "source_bytes": path.stat().st_size,
              "operation": "shaping", "output": None, "status": "running"}

    _log(f"[{case}] 1/3 读取 STP 并测量截面 ...")
    analysis = _load_or_measure(path, cache_dir, algorithm, force, end_treatment)

    sections = analysis.get("sections_full") or []
    quality = analysis.get("analysis_quality") or {}
    record["measurement"] = {
        "engine": quality.get("engine"),
        "centerline_length_mm": round(float(analysis.get("center_line_length") or 0), 3),
        "stations": len(sections),
        "measured_stations": sum(1 for s in sections if s.get("measurement_status") != "extrapolated"),
        "station_step_mm": quality.get("station_step_mm"),
        "max_normal_deviation_deg": quality.get("max_normal_deviation_deg"),
        "max_centroid_residual_mm": quality.get("max_centroid_residual_mm"),
        "rejected_sections": len(quality.get("rejected_sections") or []),
        "review_station_count": quality.get("review_station_count"),
        "extrapolated_end_lengths_mm": quality.get("extrapolated_end_lengths_mm"),
        "wall_thickness_median_mm": (analysis.get("wall_thickness_stats") or {}).get("median_mm"),
        "warnings": quality.get("warnings") or [],
    }
    if end_treatment:
        record["measurement"]["end_treatment"] = end_treatment

    _log(f"[{case}] 2/3 放样外径形腔（原始外轮廓包络实体）...")
    proc = ShapingProcessor()
    proc.set_sections(sections)
    proc.generate_model()
    shape = proc.shape
    selected = getattr(proc, "selected_station_indices", None)
    if selected:
        stations = [float(sections[i]["cumulative_length"]) for i in selected]
        spans = np.diff(stations)
        record["loft"] = {"input_rings": len(sections), "used_rings": len(selected),
                          "first_station": selected[0], "last_station": selected[-1],
                          "max_station_span_mm": round(float(spans.max()) if spans.size else 0.0, 3)}

    cav_volume = volume(shape)
    _log(f"[{case}] 3/3 写出 STEP 并回读校验 ...")
    step = step_bytes(shape)
    out_path = out_dir / f"{case}_外径形腔.stp"
    out_path.write_bytes(step)

    reread = valid_solid(read_shape(out_path))
    reread_volume = volume(reread)
    if abs(reread_volume - cav_volume) > max(0.01, cav_volume * 1e-6):
        raise RuntimeError(f"STEP 回读体积不一致：{cav_volume:.3f} -> {reread_volume:.3f} mm³")

    record.update(status="passed", output=out_path.name, output_bytes=len(step),
                  cavity_volume_mm3=round(reread_volume, 3),
                  bounds_xyz_mm=_bounds(reread),
                  step_header=step[:22].decode("ascii", "replace"),
                  seconds=round(time.monotonic() - started, 1))
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default="gnt管件", help="STP 管件所在文件夹")
    parser.add_argument("--out", default="gnt管件_外径形腔", help="外径形腔 STEP 输出文件夹")
    parser.add_argument("--algorithm", default="A", choices=["A", "B"])
    parser.add_argument("--case", default=None, help="只处理指定管件（去掉扩展名）")
    parser.add_argument("--force", action="store_true", help="忽略已有缓存并重新测量")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已生成的 STEP")
    parser.add_argument("--end-treatment", default="c1", choices=["c1", "v1"],
                        help="端部外推口径：c1=形状外推（现行默认），v1=线性外推（2026-10-31 前的历史口径）")
    args = parser.parse_args()
    end_treatment = {"shape_extrapolation": args.end_treatment}

    src = (ROOT / args.src).resolve() if not Path(args.src).is_absolute() else Path(args.src)
    out_dir = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_dir = out_dir / "_report"
    report_dir.mkdir(exist_ok=True)
    cache_dir = out_dir / "_cache"

    files = sorted([p for p in src.iterdir() if p.suffix.lower() in (".stp", ".step")])
    if args.case:
        files = [p for p in files if p.stem == args.case]
    if not files:
        _log(f"未在 {src} 找到 STP 文件")
        return 2

    _log(f"源文件夹：{src}")
    _log(f"输出文件夹：{out_dir}")
    _log(f"待处理管件：{len(files)} 个 -> {', '.join(p.name for p in files)}")

    for path in files:
        started = time.monotonic()
        existing = out_dir / f"{path.stem}_外径形腔.stp"
        if args.skip_existing and existing.exists() and not args.force:
            _log(f"[{path.stem}] 已存在 {existing.name}，跳过")
            continue
        try:
            record = process_one(path, out_dir, report_dir, args.algorithm, cache_dir, args.force,
                                 end_treatment)
        except Exception as exc:  # keep going: one bad fitting must not stop the batch
            record = {"case": path.stem, "source": path.name, "status": "failed",
                      "error": f"{type(exc).__name__}: {exc}",
                      "traceback": traceback.format_exc(limit=6),
                      "seconds": round(time.monotonic() - started, 1)}
            _log(f"[{path.stem}] 失败：{record['error']}")
        (report_dir / f"{path.stem}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(f"[{path.stem}] {record['status']}  ({record.get('seconds')} s)")

    # Summary always describes every case recorded in the folder, so a batch
    # resumed in several runs still reports the whole set.
    cases = []
    for report in sorted(report_dir.glob("*.json")):
        if report.name in ("summary.json", "geometry_check.json"):
            continue
        record = json.loads(report.read_text(encoding="utf-8"))
        if isinstance(record, dict) and "case" in record:
            cases.append(record)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_folder": str(src),
        "output_folder": str(out_dir),
        "operation": "shaping · 外径形腔（原始外轮廓包络实体）",
        "algorithm": args.algorithm,
        "end_treatment": end_treatment,
        "total": len(cases),
        "passed": sum(1 for r in cases if r["status"] == "passed"),
        "failed": sum(1 for r in cases if r["status"] != "passed"),
        "cases": cases,
    }
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"完成：{summary['passed']}/{summary['total']} 成功，结果目录 {out_dir}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
