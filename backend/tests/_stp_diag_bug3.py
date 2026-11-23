"""查看 z=440-510 段原始 primary 切片的 centroid 形态"""
import sys, os
sys.path.insert(0, r"C:\Users\gelbc\PycharmProjects\giant_tube_app2.0.1\backend")

if __name__ != "__main__":
    sys.exit(0)

import numpy as np
import trimesh as tm
import cadquery as cq
from core.pp.tube.geometry import pca_long_axis, rotation_between_vectors
from core.pp.tube.workers import stl_slice_z_chunk

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

print(f"primary 切片总数: {len(primary)}")
print(f"z 范围: [{primary[0]['z']:.2f}, {primary[-1]['z']:.2f}]")
print()

# 算 centroid 用几何法 (vertex mean) 和 shoelace
for i, r in enumerate(primary):
    outer = r.get("outer_2d")
    if outer is None or len(outer) < 3:
        continue
    arr = np.array(outer)
    gcx = float(arr[:, 0].mean())
    gcy = float(arr[:, 1].mean())
    scx, scy = r["centroid_2d"]
    if 440 <= r["z"] <= 510:
        if i > 0:
            pgcx = primary[i-1]["outer_2d"]
            if pgcx is not None and len(pgcx) >= 3:
                pgarr = np.array(pgcx)
                pgcx_v = float(pgarr[:, 0].mean())
                pgcy_v = float(pgarr[:, 1].mean())
            else:
                pgcx_v = pgcy_v = 0
            dxy = np.hypot(gcx - pgcx_v, gcy - pgcy_v)
        else:
            dxy = 0
        marker = " <-- ABNORMAL" if dxy >= 0.3 else ""
        print(f"  z={r['z']:6.2f}  geom: cx={gcx:+7.2f} cy={gcy:+7.2f}  dxy={dxy:.3f}  npts={len(outer)}{marker}")
