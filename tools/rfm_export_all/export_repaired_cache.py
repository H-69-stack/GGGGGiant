"""Export a repaired section cache from a raw Giant measurement cache.

The shaping processor re-seams and de-slivers the measured rings before it
lofts.  Those repaired rings are the authoritative section sequence: writing
them back to a cache lets downstream lofting (C1 / pipe_smoother) use exactly
the shape that the Giant repair produces, without re-running shape
extrapolation on the unrepaired raw rings.

Usage:
    python tools/rfm_export_all/export_repaired_cache.py \
        --measured <case>_measured.json --out <case>_repaired.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.rfm.processors import ShapingProcessor  # noqa: E402


def _ring_frame(rings: list[np.ndarray]):
    centers = np.array([r.mean(axis=0) for r in rings], dtype=float)
    n = len(rings)
    frames = []
    for i, (ring, center) in enumerate(zip(rings, centers)):
        if i == 0:
            tangent = centers[1] - centers[0]
        elif i == n - 1:
            tangent = centers[-1] - centers[-2]
        else:
            tangent = centers[i + 1] - centers[i - 1]
        normal = tangent / max(np.linalg.norm(tangent), 1e-12)
        local = ring - center
        local = local - np.outer(local @ normal, normal)
        cov = local.T @ local
        values, vectors = np.linalg.eigh(cov)
        u = vectors[:, int(np.argmax(values))]
        u = u - np.dot(u, normal) * normal
        u /= max(np.linalg.norm(u), 1e-12)
        v = np.cross(normal, u)
        v /= max(np.linalg.norm(v), 1e-12)
        frames.append((center, normal, u, v))
    return frames


def _normalise_frame(normal, basis_u, basis_v):
    n = np.asarray(normal, dtype=float)
    u = np.asarray(basis_u, dtype=float)
    v = np.asarray(basis_v, dtype=float)
    n /= max(np.linalg.norm(n), 1e-12)
    u = u - np.dot(u, n) * n
    u /= max(np.linalg.norm(u), 1e-12)
    v = v - np.dot(v, n) * n - np.dot(v, u) * u
    v /= max(np.linalg.norm(v), 1e-12)
    return n, u, v


def make_cache(measured_path: Path, out_path: Path):
    analysis = json.loads(measured_path.read_text(encoding="utf-8"))
    raw = analysis.get("sections_full") or analysis.get("sections") or []
    if len(raw) < 5:
        raise ValueError(f"not enough sections in {measured_path}")

    proc = ShapingProcessor()
    proc.set_sections(raw)
    proc.generate_model()

    rings = [np.asarray(r, dtype=float) for r in getattr(proc, "generated_rings", [])]
    selected = list(getattr(proc, "selected_station_indices", []) or [])
    if len(rings) < 5 or len(selected) != len(rings):
        raise ValueError("processor did not expose repaired rings")

    # Keep the measurement frame for each selected station.  Deriving a
    # fresh PCA frame per ring can flip the seam and twist the top/bottom
    # split; the raw frame is the one the measurement used.
    frames = []
    for ring, station_index in zip(rings, selected):
        src = raw[int(station_index)]
        center = np.asarray(ring, dtype=float).mean(axis=0)
        n, u, v = _normalise_frame(src["normal"], src["basis_u"], src["basis_v"])
        frames.append((center, n, u, v))

    # A planar cap is required at both ends.  The repair rings come from a
    # 3D measurement and can be a few microns non-planar; project the first
    # and last rings onto their own best-fit section plane.
    for idx in (0, len(rings) - 1):
        center, normal, _, _ = frames[idx]
        offset = (rings[idx] - center) @ normal
        rings[idx] = rings[idx] - np.outer(offset, normal)
    centers = np.array([f[0] for f in frames])
    if len(centers) > 1:
        cumulative = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))]
    else:
        cumulative = np.zeros(len(centers))

    sections = []
    for i, (ring, (center, normal, u, v), station_index, station) in enumerate(
            zip(rings, frames, selected, cumulative)):
        local = ring - center
        outer_2d = np.column_stack([local @ u, local @ v])
        perimeter = float(np.linalg.norm(np.roll(ring, -1, axis=0) - ring, axis=1).sum())
        src = raw[int(station_index)]
        sections.append({
            "position": [round(float(x), 6) for x in center],
            "normal": [round(float(x), 9) for x in normal],
            "basis_u": [round(float(x), 9) for x in u],
            "basis_v": [round(float(x), 9) for x in v],
            "outer_2d": [[round(float(x), 6), round(float(y), 6)] for x, y in outer_2d],
            "outline_3d": [[round(float(x), 6), round(float(y), 6), round(float(z), 6)] for x, y, z in ring],
            "cumulative_length": round(float(station), 6),
            "perimeter": round(perimeter, 6),
            "wall_thickness": src.get("wall_thickness"),
            "measurement_status": "repaired",
            "measurement_source": "rfm_repaired_ring",
            "source_station_index": int(station_index),
        })

    out = {
        "case": measured_path.stem.replace("_measured", ""),
        "source_cache": measured_path.name,
        "center_line_length": round(float(cumulative[-1]), 6),
        "sections_full": sections,
        "analysis_quality": analysis.get("analysis_quality") or {},
        "repair": {
            "method": "rfm_repaired_rings",
            "raw_sections": len(raw),
            "selected_rings": len(sections),
            "selected_station_indices": [int(i) for i in selected],
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--measured", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = make_cache(Path(args.measured), Path(args.out))
    print(f"saved {args.out}: {len(out['sections_full'])} rings, "
          f"{out['center_line_length']:.3f} mm")


if __name__ == "__main__":
    main()
