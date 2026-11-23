"""诊断：trim 前 centerline 真实形态"""
import sys, os
sys.path.insert(0, r"C:\Users\gelbc\PycharmProjects\giant_tube_app2.0.1\backend")

if __name__ != "__main__":
    sys.exit(0)

import math
import numpy as np
import trimesh as tm
import cadquery as cq
from core.pp.tube.geometry import pca_long_axis, rotation_between_vectors
from core.pp.tube.workers import stl_slice_z_chunk
from core.pp.tube.stl_engine import _filter_outlier_sections

FILE = r"C:\Users\gelbc\Desktop\H0A2Z3-DT(1)(1).stp"

shape = cq.importers.importStep(FILE).val()
verts, faces = shape.tessellate(0.5)
v_arr = np.array([[v.x, v.y, v.z] for v in verts])
f_arr = np.array(faces, dtype=np.int32)
center = v_arr.mean(axis=0)
long_axis = pca_long_axis(v_arr)
R = rotation_between_vectors(long_axis, np.array([0., 0., 1.]))
v_rot = (R @ (v_arr - center).T).T
mesh_rot = tm.Trimesh(vertices=v_rot, faces=f_arr, process=False)
z_min_r = v_rot[:, 2].min() + 0.2
z_max_r = v_rot[:, 2].max() - 0.2
z_vals = np.arange(z_min_r, z_max_r, 1.0)
mesh_bytes = mesh_rot.export(file_type="stl")
primary = stl_slice_z_chunk((mesh_bytes, z_vals.tolist()))
primary.sort(key=lambda r: r["z"])

filtered = _filter_outlier_sections(primary)
print(f"过滤前: {len(primary)} 过滤后: {len(filtered)}", flush=True)

# 改成 vertex-mean centroid
for r in filtered:
    outer = r.get("outer_2d")
    if outer is not None and len(outer) >= 3:
        arr = np.asarray(outer, dtype=float)
        r["centroid_2d"] = [float(arr[:, 0].mean()), float(arr[:, 1].mean())]

centroids = np.array([[r["centroid_2d"][0], r["centroid_2d"][1], r["z"]] for r in filtered], dtype=float)
n = len(centroids)

# 找 cum_len
cum_len = np.zeros(n)
for i in range(1, n):
    cum_len[i] = cum_len[i-1] + math.hypot(
        centroids[i, 0] - centroids[i-1, 0],
        centroids[i, 1] - centroids[i-1, 1],
        centroids[i, 2] - centroids[i-1, 2]
    )

# 查 115~175 段
out = open(r"C:\Users\gelbc\Desktop\diag_bug3.txt", "w", encoding="utf-8")
out.write(f"centroids n={n}\n")
out.write(f"cum_len 范围: [{cum_len[0]:.2f}, {cum_len[-1]:.2f}]\n")
out.write(f"\nCL=115-175 段:\n")
for i in range(n):
    if 115 <= cum_len[i] <= 175:
        z = centroids[i, 2]
        cx, cy = centroids[i, 0], centroids[i, 1]
        cl = cum_len[i]
        if i > 0:
            dxy_l = math.hypot(centroids[i, 0] - centroids[i-1, 0], centroids[i, 1] - centroids[i-1, 1])
        else:
            dxy_l = 0
        marker = " <-- ABNORMAL" if dxy_l >= 0.3 else ""
        out.write(f"  i={i:4d}  CL={cl:6.2f}  z={z:7.2f}  cx={cx:+7.2f}  cy={cy:+7.2f}  dxy={dxy_l:.3f}{marker}\n")
out.close()
print("saved")
