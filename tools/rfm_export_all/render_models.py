"""Render one figure per fitting showing the four operation models side by side.

Reads the exported STEP files, tessellates at a coarse tolerance and draws the
mesh vertices, which is enough to see each shape without an OpenGL context.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

PANELS = [("成型_外径形腔.stp", "成型 · 外径形腔"),
          ("扩口_扩口芯轴.stp", "扩口 · 扩口芯轴"),
          ("压扁_压扁模型.stp", "压扁 · 压扁模型"),
          ("压弯_压弯模型.stp", "压弯 · 压弯模型")]


def wireframe(path):
    """Sample every CAD edge — reads like a CAD wireframe, cheap to plot."""
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(path)) == IFSelect_RetDone
    reader.TransferRoots()
    shape = reader.OneShape()
    lines = []
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(exp.Current()))
        ts = np.linspace(curve.FirstParameter(), curve.LastParameter(), 24)
        lines.append(np.array([[p.X(), p.Y(), p.Z()] for p in (curve.Value(float(t)) for t in ts)]))
        exp.Next()
    return lines


def main():
    root = ROOT / "gnt管件_全模型"
    target_dir = root / "_report" / "preview"
    target_dir.mkdir(parents=True, exist_ok=True)
    cases = [sys.argv[1]] if len(sys.argv) > 1 else [p.name for p in sorted(root.iterdir())
                                                     if p.is_dir() and not p.name.startswith("_")]
    for case in cases:
        fig = plt.figure(figsize=(13, 3.6), dpi=130)
        for index, (name, label) in enumerate(PANELS, start=1):
            path = root / case / name
            ax = fig.add_subplot(1, 4, index, projection="3d")
            if not path.exists():
                ax.text2D(.5, .5, "缺失", ha="center", fontsize=9)
                ax.set_axis_off()
                continue
            lines = wireframe(path)
            pts = np.vstack(lines)
            for line in lines:
                ax.plot(line[:, 0], line[:, 1], line[:, 2], lw=.35, color="#2b6cb0")
            ax.set_box_aspect(tuple(np.ptp(pts, axis=0)))
            ax.view_init(elev=14, azim=-60)
            ax.set_axis_off()
            ax.set_title(label, fontsize=9, pad=0)
        fig.suptitle(f"{case} · 程序生成的全部模型", fontsize=11, y=.99)
        fig.tight_layout()
        out = target_dir / f"{case}.png"
        fig.savefig(out, bbox_inches="tight")
        plt.close(fig)
        print(f"{case} -> {out}", flush=True)


if __name__ == "__main__":
    main()
