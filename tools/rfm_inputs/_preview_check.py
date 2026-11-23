"""Throwaway: mirror the viewer's projection in Python to eyeball the 3D panel."""
import json
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

html = Path(sys.argv[1]).read_text(encoding="utf-8")
payload = json.loads(re.search(r"const D = (\{.*?\});\n", html, re.S).group(1))
P = payload["p"]
rings = np.array(payload["rings"], float).reshape(-1, P, 3)
centers = np.array(payload["pos"], float).reshape(-1, 3)

spans = [np.ptp(rings[:, :, a]) for a in range(3)]
a = int(np.argmax(spans))
order = [a, (a + 1) % 3, (a + 2) % 3]
r = rings[:, :, order]
yaw, pitch = -0.55, 0.35
cy, sy = np.cos(yaw), np.sin(yaw)
x1 = r[:, :, 0] * cy - r[:, :, 2] * sy
z1 = r[:, :, 0] * sy + r[:, :, 2] * cy
cp, sp = np.cos(pitch), np.sin(pitch)
y1 = r[:, :, 1] * cp - z1 * sp

fig, ax = plt.subplots(figsize=(9, 4), dpi=130)
for i in range(rings.shape[0]):
    xs = np.r_[x1[i], x1[i][0]]
    ys = np.r_[y1[i], y1[i][0]]
    ax.plot(xs, ys, lw=.6, color="#2b6cb0", alpha=.55)
c = centers[:, order]
cx = c[:, 0] * cy - c[:, 2] * sy
cz = c[:, 0] * sy + c[:, 2] * cy
cyy = c[:, 1] * cp - cz * sp
ax.plot(cx, cyy, ls="--", lw=1.4, color="#c05621")
ax.set_aspect("equal")
ax.set_title(f"{payload['case']}  主轴 index={a}  spans={np.round(spans,1)}  环数={rings.shape[0]}")
ax.axis("off")
fig.tight_layout()
out = Path(sys.argv[2])
fig.savefig(out, bbox_inches="tight")
print(out)
