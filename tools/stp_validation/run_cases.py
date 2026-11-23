"""Reproducible STEP fixture run; use the application's CAD Python environment."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--case", default="*")
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--legacy", action="store_true", help="Reproduce the old tessellation + STL analysis path")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from core.pp.tube import analyze_tube
    # Limit legacy multiprocessing on large workstations (same numerical code).
    import core.pp.tube.stl_engine as legacy
    legacy.os.cpu_count = lambda: 5
    for path in sorted(args.fixtures.glob(args.case + ".stp")):
        out = args.output / (path.stem + ".json")
        if out.exists():
            continue
        start = time.monotonic()
        print("START", path.name, flush=True)
        last = [-10]
        def progress(pct, message):
            if pct >= last[0] + 10:
                print(path.stem, pct, message, flush=True)
                last[0] = pct
        if args.legacy:
            import os
            from core.pp.tube.stp_engine import _tessellate_step
            mesh_path = _tessellate_step(str(path), tolerance=.3)
            if not mesh_path:
                raise RuntimeError('Legacy tessellation failed')
            try:
                result = legacy.analyze_stl_tube(mesh_path, progress_callback=progress)
                result['source_format'] = 'stp'
                result['tessellation_tolerance'] = .3
            finally:
                os.remove(mesh_path)
        elif args.native:
            from core.pp.tube.brep_engine import analyze_brep_tube
            result = analyze_brep_tube(str(path), progress_callback=progress)
        else:
            result = analyze_tube(str(path), "stp", progress_callback=progress)
        result["validation"] = {
            "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "seconds": round(time.monotonic() - start, 2),
        }
        out.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        print("DONE", path.name, result.get("error"), result.get("center_line_length"),
              result.get("wall_thickness_stats"), result["validation"]["seconds"], flush=True)


if __name__ == "__main__":
    main()
