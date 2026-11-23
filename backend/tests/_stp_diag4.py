"""调用 A 算法并打印中心线轨迹与 z 截面信息"""
import sys, os
sys.path.insert(0, r"C:\Users\gelbc\PycharmProjects\giant_tube_app2.0.1\backend")

if __name__ != "__main__":
    sys.exit(0)

import numpy as np
import trimesh as tm
from scipy.spatial import ConvexHull
import cadquery as cq
from core.pp.tube import analyze_tube

FILE = r"C:\Users\gelbc\Desktop\H0A2Z3-DT(1)(1).stp"

# 用 portable python + 同样的 cadquery 路径
print("==== 测试 1: A 算法得到的数据 ====")
result = analyze_tube(FILE, file_type="stp", algorithm="A", sparse_step_mm=25.0)
print("keys:", list(result.keys()))
if "error" in result:
    print(result["error"]); sys.exit(1)

import json
with open(r"C:\Users\gelbc\Desktop\diag_centerline.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print("已保存 diag_centerline.json")

# 找原始 mesh
print("\n==== 测试 2: 独立验证 PCA 旋转 + Z 切 ====")
shape = cq.importers.importStep(FILE).val()
bb = shape.BoundingBox()
print("原始 bbox Z: [%.2f, %.2f]" % (bb.zmin, bb.zmax))

# tessellate
verts, tris = shape.tessellate(0.5)
v_arr = np.array([[v.x, v.y, v.z] for v in verts])
f_arr = np.array(tris, dtype=np.int32)

# PCA
center = v_arr.mean(axis=0)
print("几何中心: (%.2f, %.2f, %.2f)" % (center[0], center[1], center[2]))

cov = np.cov((v_arr - center).T)
eigvals, eigvecs = np.linalg.eigh(cov)
# eigh 按升序 -> 主轴是 eigvecs[:, 2]
long_axis = eigvecs[:, 2]
print("PCA 主轴 (特征向量3): (%.4f, %.4f, %.4f)" % (long_axis[0], long_axis[1], long_axis[2]))
print("特征值: %s" % eigvals)

# 计算旋转矩阵
def rotation_between_vectors(a, b):
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    s = np.linalg.norm(v)
    c = np.dot(a, b)
    if s < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1, 1, -1])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 - c) / (s * s)

R = rotation_between_vectors(long_axis, np.array([0., 0., 1.]))
print("旋转矩阵 R:")
print(R)

# 应用旋转
v_rot = (R @ (v_arr - center).T).T
print("旋转后 bounds Z: [%.2f, %.2f]" % (v_rot[:, 2].min(), v_rot[:, 2].max()))
print("旋转后 bounds X: [%.2f, %.2f]" % (v_rot[:, 0].min(), v_rot[:, 0].max()))
print("旋转后 bounds Y: [%.2f, %.2f]" % (v_rot[:, 1].min(), v_rot[:, 1].max()))

# 旋转后 Z 切
mesh_rot = tm.Trimesh(vertices=v_rot, faces=f_arr, process=False)
z_min_r = v_rot[:, 2].min() + 0.2
z_max_r = v_rot[:, 2].max() - 0.2
z_vals = np.arange(z_min_r, z_max_r, 1.0)

print("\n旋转后每 1mm Z 切, ConvexHull 周长 + 切片 2D 中心:")
perimeters = []
centroids_x = []
centroids_y = []
for z in z_vals:
    try:
        sec = mesh_rot.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
        pts3 = np.asarray(sec.vertices)
        if pts3.shape[0] < 3:
            perimeters.append(np.nan); centroids_x.append(np.nan); centroids_y.append(np.nan); continue
        hull = ConvexHull(pts3[:, :2])
        hv = hull.points[hull.vertices]
        closed = np.vstack([hv, hv[0:1]])
        edges = np.linalg.norm(np.diff(closed, axis=0), axis=1)
        perimeters.append(float(edges.sum()))
        # 切片 2D 中心用凸包顶点算术平均
        centroids_x.append(float(hv[:, 0].mean()))
        centroids_y.append(float(hv[:, 1].mean()))
    except Exception:
        perimeters.append(np.nan); centroids_x.append(np.nan); centroids_y.append(np.nan)

perimeters = np.array(perimeters)
centroids_x = np.array(centroids_x)
centroids_y = np.array(centroids_y)

# 找异常凸起段
median = np.nanmedian(perimeters)
print("中位数: %.2f" % median)

# 输出每 5mm 的中心
print("\n每 5mm: 周长, 中心(cx, cy)")
for i in range(0, len(z_vals), 5):
    z = z_vals[i]
    p = perimeters[i]
    cx = centroids_x[i]
    cy = centroids_y[i]
    if np.isnan(p): continue
    bar = '*' if p > median * 1.05 else ' '
    print(f"  z={z:6.1f}  p={p:7.2f}  cx={cx:+7.2f}  cy={cy:+7.2f}  {bar}")

# 输出 centerline 总体形态
print("\n中心线轨迹 (cx, cy) 沿 z:")
print("  z_min=%.1f, z_max=%.1f" % (z_vals[0], z_vals[-1]))
print("  cx 范围: [%.2f, %.2f]  跨度: %.2f" % (np.nanmin(centroids_x), np.nanmax(centroids_x), np.nanmax(centroids_x) - np.nanmin(centroids_x)))
print("  cy 范围: [%.2f, %.2f]  跨度: %.2f" % (np.nanmin(centroids_y), np.nanmax(centroids_y), np.nanmax(centroids_y) - np.nanmin(centroids_y)))
