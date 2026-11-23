"""Verify each exported 外径形腔 against the measured source geometry.

The cavity is a solid envelope, so a section is a single closed wire. For five
stations the written STEP is re-cut with a plane and the wire length compared
with the outer perimeter measured on the source fitting.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.pp.tube.brep_sections import read_shape  # noqa: E402
from core.common.solid_geometry import bounds, valid_solid, volume  # noqa: E402


def wire_perimeter(shape, point, normal):
    """Total length of the plane/solid intersection curves."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pln, gp_Pnt, gp_Dir
    section = BRepAlgoAPI_Section(shape, gp_Pln(gp_Pnt(*map(float, point)), gp_Dir(*map(float, normal))))
    section.Build()
    if not section.IsDone():
        raise ValueError("平面求交失败")
    part = section.Shape()
    if part.IsNull():
        raise ValueError("该站位没有截面")
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(part, props)
    return float(props.Mass())


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "gnt管件_外径形腔"
    src = ROOT / "gnt管件"
    results = []
    for step in sorted(out.glob("*_外径形腔.stp")):
        case = step.name[: -len("_外径形腔.stp")]
        record = {"case": case}
        try:
            analysis = json.loads((out / "_cache" / f"{case}_measured.json").read_text(encoding="utf-8"))
            sections = analysis["sections_full"]
            measured = [s for s in sections if s.get("measurement_status") != "extrapolated"]
            picks = np.linspace(5, len(measured) - 6, 5).astype(int)

            source = read_shape(src / f"{case}.stp")
            cavity = valid_solid(read_shape(step))
            low_s, high_s = bounds(source)
            low_c, high_c = bounds(cavity)
            record["source_extent_mm"] = [round(float(v), 2) for v in high_s - low_s]
            record["cavity_extent_mm"] = [round(float(v), 2) for v in high_c - low_c]
            record["extrapolated_end_lengths_mm"] = (analysis.get("analysis_quality") or {}).get(
                "extrapolated_end_lengths_mm")
            record["volume_mm3"] = round(volume(cavity), 2)

            checks = []
            for i in picks:
                sec = measured[int(i)]
                perimeter = wire_perimeter(cavity, sec["position"], sec["normal"])
                checks.append({
                    "station_mm": round(float(sec["cumulative_length"]), 2),
                    "perimeter_source_mm": round(float(sec["perimeter"]), 3),
                    "perimeter_cavity_mm": round(perimeter, 3),
                    "perimeter_delta_mm": round(perimeter - float(sec["perimeter"]), 3),
                })
            record["stations"] = checks
            record["max_perimeter_delta_mm"] = round(
                max(abs(c["perimeter_delta_mm"]) for c in checks), 3)
            record["status"] = "passed" if record["max_perimeter_delta_mm"] <= 0.5 else "check"
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        results.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    (out / "_report" / "geometry_check.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    good = sum(1 for r in results if r["status"] == "passed")
    print(f"\n几何复核：{good}/{len(results)} 通过（5 个站位切片周长与源件测量值偏差 ≤ 0.5 mm）")


if __name__ == "__main__":
    main()
