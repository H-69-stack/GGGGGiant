"""Render each 外径形腔 from its measurement cache.

Left: outer contour rings in 3D (exactly the rings the loft uses).
Right: outer perimeter along the centerline — the section-growth curve.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[2]


def world_rings(analysis, every):
    out = []
    for sec in analysis["sections_full"][::every]:
        pts = np.asarray(sec["outer_2d"], float)
        out.append(np.asarray(sec["position"], float)
                   + pts[:, :1] * np.asarray(sec["basis_u"], float)
                   + pts[:, 1:] * np.asarray(sec["basis_v"], float))
    return out


def render(case, analysis, target, every=20):
    rings = world_rings(analysis, every)
    sections = analysis["sections_full"]
    stations = np.array([s["cumulative_length"] for s in sections], float)
    perimeters = np.array([s["perimeter"] for s in sections], float)

    fig = plt.figure(figsize=(12.5, 4.4), dpi=140)
    grid = fig.add_gridspec(1, 2, width_ratios=[1.35, 1])

    ax = fig.add_subplot(grid[0, 0], projection="3d")
    for ring in rings:
        closed = np.vstack([ring, ring[:1]])
        ax.plot(closed[:, 0], closed[:, 1], closed[:, 2], lw=.5, color="#2b6cb0")
    allpts = np.vstack(rings)
    ax.set_box_aspect(tuple(np.ptp(allpts, axis=0)))
    ax.view_init(elev=16, azim=-62)
    ax.set_axis_off()
    ax.set_title(f"{case} · 外径形腔轮廓（每 {every} 站位一环）", fontsize=10, pad=0)

    bx = fig.add_subplot(grid[0, 1])
    bx.plot(stations, perimeters, lw=1.1, color="#c05621")
    bx.set_xlabel("中心线长度 (mm)", fontsize=8)
    bx.set_ylabel("外径周长 (mm)", fontsize=8)
    bx.set_title("外径周长沿轴线变化", fontsize=10)
    bx.grid(alpha=.25, lw=.5)
    bx.tick_params(labelsize=7)

    fig.tight_layout()
    fig.savefig(target, bbox_inches="tight")
    plt.close(fig)
    return len(rings)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "gnt管件_外径形腔"
    target_dir = out / "_report" / "preview"
    target_dir.mkdir(parents=True, exist_ok=True)
    for cache in sorted((out / "_cache").glob("*_measured.json")):
        case = cache.name[: -len("_measured.json")]
        analysis = json.loads(cache.read_text(encoding="utf-8"))
        n = render(case, analysis, target_dir / f"{case}.png")
        print(f"{case}: {n} 环 -> {target_dir / (case + '.png')}", flush=True)


if __name__ == "__main__":
    main()
