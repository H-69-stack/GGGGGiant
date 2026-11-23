"""查看 CL=120-170 段的 centerline 形态 + outline_z_span"""
import sys, os
sys.path.insert(0, r"C:\Users\gelbc\PycharmProjects\giant_tube_app2.0.1\backend")

if __name__ != "__main__":
    sys.exit(0)

import json
import numpy as np
from core.pp.tube import analyze_tube

FILE = r"C:\Users\gelbc\Desktop\H0A2Z3-DT(1)(1).stp"

print("运行 A 算法...")
result = analyze_tube(FILE, file_type="stp", algorithm="A", sparse_step_mm=25.0)
if "error" in result:
    print(result["error"]); sys.exit(1)

with open(r"C:\Users\gelbc\Desktop\diag_fix2.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)

# 重新加载原始 primary sections（在 trim 前）— 用 _build_centerline_from_centroids 反推
import sys
from core.pp.tube.stl_engine import _build_centerline_from_centroids, _filter_outlier_sections
from core.pp.tube.geometry import trim_and_extrapolate_centerline, smooth_centerline_savgol
import numpy as np

# 跑一次 A 算法，拿到原始 primary sections
from core.pp.tube import analyze_stp_tube
analyze_res = analyze_stp_tube(FILE, algorithm="A", sparse_step_mm=25.0)
# analyze_stp_tube 内部跑的是 analyze_stl_tube，没返回 primary sections
# 改用 analyze_stl_tube 之类的 — 直接调 stp_engine wrapper
# 简单点：直接做完整的 _build_centerline_from_centroids 验证
# 复制 analyze_stl_tube 中 1) PCA 2) 一次切割 3) _build 三步
from core.pp.tube.geometry import pca_long_axis, rotation_between_vectors
import trimesh as tm
import cadquery as cq
shape = cq.importers.importStep(FILE).val()
bb = shape.BoundingBox()
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

from core.pp.tube.workers import stl_slice_z_chunk
mesh_bytes = mesh_rot.export(file_type="stl")
primary = stl_slice_z_chunk((mesh_bytes, z_vals.tolist()))
primary.sort(key=lambda r: r["z"])
print(f"原始 primary 切片数: {len(primary)}")

# 算它的 centroid_2d 范围
cx_arr = np.array([r["centroid_2d"][0] for r in primary])
cy_arr = np.array([r["centroid_2d"][1] for r in primary])
print(f"原始 centroid cx: min={cx_arr.min():.2f} max={cx_arr.max():.2f} span={cx_arr.max()-cx_arr.min():.2f}")
print(f"原始 centroid cy: min={cy_arr.min():.2f} max={cy_arr.max():.2f} span={cy_arr.max()-cy_arr.min():.2f}")

# 找原 centroid 跳变最大的（用 1mm z 间距索引）
# z=485~507 段
print("\n原始 primary 切片 z=480~510 段 centroid:")
for i, r in enumerate(primary):
    z = r["z"]
    if 480 <= z <= 510:
        cx, cy = r["centroid_2d"]
        # 找前后 dxy
        if i > 0:
            pcx, pcy = primary[i-1]["centroid_2d"]
            dxy = np.hypot(cx - pcx, cy - pcy)
        else:
            dxy = 0
        # 找前后 dz
        if i > 0:
            dz = z - primary[i-1]["z"]
        else:
            dz = 0
        print(f"  z={z:6.2f}  cx={cx:+7.2f}  cy={cy:+7.2f}  dxy={dxy:.3f}  dz={dz:+.3f}")

sf = result["sections_full"]
pts = np.array(result["center_line_points"])
print(f"数据点数: {len(sf)}")
print(f"centerline 点数: {len(pts)}")

# 详细查 CL=110-180 段
print("\nCL=110-180 段 (cl, peri, pos_z, outline_z_span, center_pt_z, centerline tangent vs Z):")
print(f"  {'CL':>6}  {'peri':>7}  {'pos_z':>7}  {'z_span':>7}  {'cx':>7}  {'cy':>7}  {'dxy_next':>9}  {'dz_next':>7}")
# 算切线
tangents = np.gradient(pts, axis=0)
for i in range(0, len(sf)):
    x = sf[i]
    cl = x["cumulative_length"]
    if 110 <= cl <= 180:
        ol = x.get("outline_3d", [])
        if ol:
            zs = [p[2] for p in ol]
            zspan = max(zs) - min(zs) if zs else 0
        else:
            zspan = 0
        # 找 center_point z 最近的 centerline index
        cpz = x["position"][2]
        idx = int(np.argmin(np.abs(pts[:, 2] - cpz)))
        # 切线
        t = tangents[idx]
        cos_z = t[2] / max(np.linalg.norm(t), 1e-9)
        # 下一个 centerline
        if idx + 1 < len(pts):
            dxy = np.linalg.norm(pts[idx+1, :2] - pts[idx, :2])
            dz = pts[idx+1, 2] - pts[idx, 2]
        else:
            dxy = 0; dz = 0
        cx = x["position"][0]
        cy = x["position"][1]
        flag = " <-- ABNORMAL" if zspan > 3 else ""
        print(f"  {cl:6.1f}  {x['perimeter']:7.2f}  {cpz:7.2f}  {zspan:7.2f}  {cx:+7.2f}  {cy:+7.2f}  {dxy:9.3f}  {dz:+7.3f}  cos_z={cos_z:+.3f}{flag}")

# 找 centerline 跳变最大的 i 索引
print("\ncenterline 中横向跳变最大的 10 段 (dxy):")
dxy_full = np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1)
top = np.argsort(dxy_full)[-10:][::-1]
for i in top:
    print(f"  i={i:4d}  z={pts[i, 2]:.2f}  dxy={dxy_full[i]:.3f}  ({pts[i, 0]:+.2f},{pts[i, 1]:+.2f}) -> ({pts[i+1, 0]:+.2f},{pts[i+1, 1]:+.2f})")

# centerline 总形态
print(f"\ncenterline 总长: {len(pts)}")
print(f"  dxy 总和: {dxy_full.sum():.3f}")
print(f"  跳变 >= 0.5mm 的段数: {(dxy_full >= 0.5).sum()}")
print(f"  跳变 >= 1.0mm 的段数: {(dxy_full >= 1.0).sum()}")
