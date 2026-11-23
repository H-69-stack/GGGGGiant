"""完整诊断：跑 _build_centerline_from_centroids 看看真实输出"""
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
from core.pp.tube.stl_engine import _build_centerline_from_centroids, _filter_outlier_sections

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

# 跑完整 _build_centerline_from_centroids
centroids_out, raw = _build_centerline_from_centroids(primary)
print(f"out centerline shape: {centroids_out.shape}", flush=True)

# 直接列出中心线点的 z, cx, cy
out = open(r"C:\Users\gelbc\Desktop\diag_bug3.txt", "w", encoding="utf-8")
out.write(f"out centerline shape: {centroids_out.shape}\n")

# 找 cumulative length
cl = np.zeros(len(centroids_out))
for i in range(1, len(centroids_out)):
    cl[i] = cl[i-1] + math.hypot(
        centroids_out[i, 0] - centroids_out[i-1, 0],
        centroids_out[i, 1] - centroids_out[i-1, 1],
        centroids_out[i, 2] - centroids_out[i-1, 2]
    )

out.write(f"cl 范围: [{cl[0]:.2f}, {cl[-1]:.2f}]\n")
# 找 CL=120-170 范围
out.write(f"\nCL=120-170 段 centerline:\n")
for i in range(len(centroids_out)):
    if 120 <= cl[i] <= 170:
        z = centroids_out[i, 2]
        cx, cy = centroids_out[i, 0], centroids_out[i, 1]
        cl_v = cl[i]
        if i > 0:
            dxy_l = math.hypot(centroids_out[i, 0] - centroids_out[i-1, 0], centroids_out[i, 1] - centroids_out[i-1, 1])
        else:
            dxy_l = 0
        marker = " <-- ABNORMAL" if dxy_l >= 0.3 else ""
        out.write(f"  i={i:4d}  CL={cl_v:6.2f}  z={z:7.2f}  cx={cx:+7.2f}  cy={cy:+7.2f}  dxy={dxy_l:.3f}{marker}\n")

# 找 trim_head 周围
out.write(f"\ntrim_head=125 附近 (i=120-130):\n")
for i in range(120, 135):
    if i < len(centroids_out):
        z = centroids_out[i, 2]
        cx, cy = centroids_out[i, 0], centroids_out[i, 1]
        out.write(f"  i={i:4d}  z={z:7.2f}  cx={cx:+7.2f}  cy={cy:+7.2f}\n")

# 找 trim_tail 周围
out.write(f"\ntrim_tail~500 附近 (i=495-510):\n")
for i in range(495, 510):
    if i < len(centroids_out):
        z = centroids_out[i, 2]
        cx, cy = centroids_out[i, 0], centroids_out[i, 1]
        out.write(f"  i={i:4d}  z={z:7.2f}  cx={cx:+7.2f}  cy={cy:+7.2f}\n")

out.close()
print("saved")
