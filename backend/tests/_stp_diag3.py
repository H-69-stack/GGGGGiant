"""独立检查 STP 模型在 CL=60~80 区间是否有真实结构突起（走 cadquery）"""
import sys, os, time
sys.path.insert(0, r"C:\Users\gelbc\PycharmProjects\giant_tube_app2.0.1\backend")

import numpy as np
from scipy.spatial import ConvexHull
import cadquery as cq
import trimesh as tm

FILE = r"C:\Users\gelbc\Desktop\H0A2Z3-DT(1)(1).stp"

print("加载 STP (via cadquery)...")
t0 = time.time()
shape = cq.importers.importStep(FILE).val()
bb = shape.BoundingBox()
print("耗时: %.1fs" % (time.time() - t0))
print("bbox Z: [%.2f, %.2f]" % (bb.zmin, bb.zmax))
print("bbox X: [%.2f, %.2f]" % (bb.xmin, bb.xmax))
print("bbox Y: [%.2f, %.2f]" % (bb.ymin, bb.ymax))

# tessellate
print("\nTessellating...")
t1 = time.time()
try:
    verts, faces = shape.tessellate(0.5)
    print("tessellate 耗时: %.1fs, 顶点数: %d" % (time.time() - t1, len(verts)))
except Exception as e:
    print("tessellate 失败: %s" % e)
    sys.exit(1)

# 转 trimesh
import numpy as np
v_arr = np.array([[v.x, v.y, v.z] for v in verts])
f_arr = np.array([[f[0], f[1], f[2]] for f in faces])
mesh = tm.Trimesh(vertices=v_arr, faces=f_arr)
print("bounds Z: [%.2f, %.2f]" % (v_arr[:, 2].min(), v_arr[:, 2].max()))

# 沿 Z 切
z_lo, z_hi = float(v_arr[:, 2].min()), float(v_arr[:, 2].max())
z_vals = np.arange(z_lo + 0.05, z_hi - 0.05, 1.0)

print("\n沿 Z 切面 ConvexHull 周长 (trimesh):")
t2 = time.time()
perimeters = []
for z in z_vals:
    try:
        sec = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
        if sec is None:
            perimeters.append(np.nan); continue
        pts3 = np.asarray(sec.vertices)
        if pts3.shape[0] < 3:
            perimeters.append(np.nan); continue
        hull = ConvexHull(pts3[:, :2])
        hv = hull.points[hull.vertices]
        closed = np.vstack([hv, hv[0:1]])
        edges = np.linalg.norm(np.diff(closed, axis=0), axis=1)
        perimeters.append(float(edges.sum()))
    except Exception:
        perimeters.append(np.nan)

perimeters = np.array(perimeters, dtype=float)
print("耗时: %.1fs" % (time.time() - t2))
ok = ~np.isnan(perimeters)
z_v = z_vals[ok]
p_v = perimeters[ok]
print("有效面: %d 范围 [%.1f, %.1f]" % (len(z_v), z_v.min(), z_v.max()))

# 找异常凸起
median = np.median(p_v)
print("\n全局中位数: %.2f" % median)

# 输出 0~150 段全部
print("\nz = 0~150, 真实周长 (trimesh 直接切):")
for z, p in zip(z_v, p_v):
    if z <= 150:
        bar = '#' * max(0, int((p - median) / 2))
        print(f"  z={z:6.1f}  周长={p:7.2f}  偏差={p-median:+7.2f}  {bar}")
