"""Static one-page snapshot per fitting: 3D envelope + formline + three sections."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_viewer import build_payload, IN_ROOT, OUT_ROOT  # noqa: E402

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


def projected(payload, yaw=-0.55, pitch=0.35):
    p = payload["p"]
    rings = np.asarray(payload["rings"], float).reshape(-1, p, 3)
    centres = np.asarray(payload["pos"], float).reshape(-1, 3)
    spans = [np.ptp(rings[:, :, a]) for a in range(3)]
    a = int(np.argmax(spans))
    order = [a, (a + 1) % 3, (a + 2) % 3]
    r = rings[:, :, order]
    cy, sy = np.cos(yaw), np.sin(yaw)
    x1 = r[:, :, 0] * cy - r[:, :, 2] * sy
    z1 = r[:, :, 0] * sy + r[:, :, 2] * cy
    cp, sp = np.cos(pitch), np.sin(pitch)
    y1 = r[:, :, 1] * cp - z1 * sp
    c = centres[:, order]
    cx = c[:, 0] * cy - c[:, 2] * sy
    cz = c[:, 0] * sy + c[:, 2] * cy
    cyy = c[:, 1] * cp - cz * sp
    return x1, y1, cx, cyy


def snapshot(case, payload, target):
    fig = plt.figure(figsize=(12.4, 7.2), dpi=140)
    grid = fig.add_gridspec(2, 2, height_ratios=[1.05, 1], width_ratios=[1.35, 1],
                            hspace=.28, wspace=.18)

    ax = fig.add_subplot(grid[0, :])
    x1, y1, cx, cyy = projected(payload)
    for i in range(x1.shape[0]):
        ax.plot(np.r_[x1[i], x1[i][0]], np.r_[y1[i], y1[i][0]], lw=.55,
                color="#2b6cb0", alpha=.6)
    ax.plot(cx, cyy, ls="--", lw=1.3, color="#c05621", label="中心线")
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(f"{payload['case']}　3D 外轮廓（{payload['ns']} 个环，每环 {payload['p']} 点）",
                 fontsize=10, loc="left")
    ax.legend(fontsize=8, loc="lower right", frameon=False)

    ax2 = fig.add_subplot(grid[1, 0])
    s = np.asarray(payload["s"])
    d = np.asarray(payload["d"])
    ax2.plot(s, d, lw=1.5, color="#c05621")
    ax2.fill_between(s, d.min(), d, color="#c05621", alpha=.08)
    for frac, colour in ((0.0, "#1a365d"), (0.5, "#1a365d"), (1.0, "#1a365d")):
        i = int(round((len(s) - 1) * frac))
        ax2.plot([s[i]], [d[i]], "o", ms=4, color=colour)
        ax2.annotate(f"D={d[i]:.2f}", (s[i], d[i]), textcoords="offset points",
                     xytext=(4, 6), fontsize=8)
    extrap = [i for i, v in enumerate(payload["st"]) if v == 1]
    # 只着色两端真正连续的外推段，不能用一个 max() 把整段长度都涂上
    runs, start = [], None
    for i, value in enumerate(payload["st"] + [0]):
        if value == 1 and start is None:
            start = i
        elif value != 1 and start is not None:
            runs.append((start, i - 1))
            start = None
    for a, b in runs:
        ax2.axvspan(s[a], s[b], color="#2f855a", alpha=.10)
    ax2.set_xlabel("中心线长度 (mm)", fontsize=9)
    ax2.set_ylabel("外径 D (mm)", fontsize=9)
    ax2.set_title("形线（外径）沿中心线", fontsize=10, loc="left")
    ax2.grid(alpha=.25, lw=.5)
    ax2.tick_params(labelsize=8)

    ax3 = fig.add_subplot(grid[1, 1])
    p = payload["p"]
    for frac, colour in ((0.02, "#a0aec0"), (0.5, "#2b6cb0"), (0.98, "#c05621")):
        i = int(round((len(s) - 1) * frac))
        uv = np.asarray(payload["sect"][i * p * 2:(i + 1) * p * 2], float).reshape(-1, 2)
        uv = np.vstack([uv, uv[:1]])
        ax3.plot(uv[:, 0], uv[:, 1], lw=1.4, color=colour, label=f"s={s[i]:.0f} mm")
    ax3.set_aspect("equal")
    ax3.grid(alpha=.2, lw=.5)
    ax3.tick_params(labelsize=7)
    ax3.set_title("三个站位的截面外轮廓", fontsize=10, loc="left")
    ax3.legend(fontsize=8, frameon=False)

    fig.savefig(target, bbox_inches="tight")
    plt.close(fig)


def main():
    target_dir = OUT_ROOT / "快照"
    target_dir.mkdir(parents=True, exist_ok=True)
    cases = [sys.argv[1]] if len(sys.argv) > 1 else None
    for case_dir in sorted(p for p in IN_ROOT.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if case_dir.name == "可视化":
            continue
        if cases and case_dir.name not in cases:
            continue
        if not (case_dir / "截面明细.xlsx").exists():
            continue
        payload = build_payload(case_dir)
        target = target_dir / f"{payload['case']}.png"
        snapshot(payload["case"], payload, target)
        print(f"{payload['case']} -> {target}", flush=True)


if __name__ == "__main__":
    main()
