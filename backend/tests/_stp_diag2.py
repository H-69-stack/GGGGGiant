"""诊断脚本：trimesh 直接读 STEP，分析 Z 向切面周长（绕过 cadquery/numpy 版本问题）"""
import sys, os, time
# portable Python DLL path
_pydir = os.path.dirname(sys.executable)
try:
    os.add_dll_directory(os.path.join(_pydir, "Library", "bin"))
except Exception:
    pass
try:
    os.add_dll_directory(_pydir)
except Exception:
    pass

sys.path.insert(0, r"C:\Users\gelbc\PycharmProjects\giant_tube_app2.0.1\backend")

import trimesh as tm
import numpy as np
from scipy.spatial import ConvexHull

FILE = r"C:\Users\gelbc\Desktop\H0A2Z3-DT(1)(1).stp"

print("文件: " + FILE)
print("大小: %.1f KB\n" % (os.path.getsize(FILE) / 1024))

t0 = time.time()
mesh = tm.load(FILE)
print("读取耗时: %.1fs" % (time.time() - t0))
v = np.asarray(mesh.vertices)
print("vertices=%d faces=%d" % (len(mesh.vertices), len(mesh.faces)))
print("bounds X: [%.1f, %.1f] mm" % (v[:, 0].min(), v[:, 0].max()))
print("bounds Y: [%.1f, %.1f] mm" % (v[:, 1].min(), v[:, 1].max()))
print("bounds Z: [%.1f, %.1f] mm" % (v[:, 2].min(), v[:, 2].max()))
print("体积: %.1f mm3" % mesh.volume)

STEP_MM = 1.0
z_lo = float(v[:, 2].min()) + 0.2
z_hi = float(v[:, 2].max()) - 0.2
z_vals = np.arange(z_lo, z_hi + 1e-9, STEP_MM)
print("\nZ 切片: [%.2f, %.2f], 共 %d 面" % (z_lo, z_hi, len(z_vals)))

t1 = time.time()
perimeters = []
for z in z_vals:
    try:
        sec = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
        pts3 = np.asarray(sec.vertices)
        if pts3.shape[0] < 3:
            perimeters.append(np.nan)
            continue
        # ConvexHull 周长（投影到 XY 平面）
        hull = ConvexHull(pts3[:, :2])
        hv = hull.points[hull.vertices]
        closed = np.vstack([hv, hv[0:1]])
        edges = np.linalg.norm(np.diff(closed, axis=0), axis=1)
        perimeters.append(float(np.sum(edges)))
    except Exception:
        perimeters.append(np.nan)

perimeters = np.array(perimeters, dtype=float)
valid = ~np.isnan(perimeters)
z_v = z_vals[valid]
p_v = perimeters[valid]
print("切片完成: %.1fs, 有效 %d/%d" % (time.time() - t1, valid.sum(), len(z_vals)))

# ── 打印数据 ──
mean_p = p_v.mean()
body = p_v[50:-50].mean() if len(p_v) > 100 else p_v.mean()
head5 = p_v[:5].mean()
head11_20 = p_v[10:20].mean()

print("\n前15个切面:")
print("  %8s  %10s  %10s" % ("z(mm)", "周长(mm)", "vs均值%"))
print("  %s  %s  %s" % ("-" * 8, "-" * 10, "-" * 10))
for z, p in zip(z_v[:15], p_v[:15]):
    print("  %8.2f  %10.2f  %+10.1f%%" % (z, p, (p / mean_p - 1) * 100))

print("\n最后10个切面:")
for z, p in zip(z_v[-10:], p_v[-10:]):
    print("  %8.2f  %10.2f  %+10.1f%%" % (z, p, (p / mean_p - 1) * 100))

# ── 统计 ──
print("\n统计:")
print("  有效面: %d" % len(p_v))
print("  全局均值: %.2f mm" % mean_p)
print("  稳定段均值: %.2f mm" % body)
print("  首5点均值: %.2f mm  (vs 均值 %+.1f%%)" % (head5, (head5 / mean_p - 1) * 100))
print("  11-20点均值: %.2f mm  (vs 均值 %+.1f%%)" % (head11_20, (head11_20 / mean_p - 1) * 100))
ratio = head5 / body
print("  首5 / 稳定段: %.4f  (>1.000 = 首端偏大)" % ratio)

# ── 画图（matplotlib 可选） ──
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAVE_PLT = True
except Exception as _e:
    print("\n[图表跳过] matplotlib 不可用: %s" % _e)
    _HAVE_PLT = False

if _HAVE_PLT:
    fig, axes = plt.subplots(2, 1, figsize=(12, 7))
    ax = axes[0]
    ax.plot(z_v, p_v, "b.-", markersize=2, linewidth=0.8)
    ax.axhline(mean_p, color="green", linestyle="--", linewidth=1, label="全局均值 %.2f mm" % mean_p)
    ax.axhline(body, color="orange", linestyle=":", linewidth=1, label="稳定段均值 %.2f mm" % body)
    ax.set_xlabel("Z (mm)")
    ax.set_ylabel("ConvexHull 周长 (mm)")
    ax.set_title("H0A2Z3-DT Z向切面周长分布 (trimesh native, ConvexHull)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    ax2 = axes[1]
    ratio_pct = (p_v / body - 1) * 100
    ax2.plot(z_v, ratio_pct, "r.-", markersize=2)
    ax2.axhline(0, color="gray", linewidth=1)
    ax2.set_xlabel("Z (mm)")
    ax2.set_ylabel("vs 稳定段 (%)")
    ax2.set_title("相对偏差率 (正=偏大, 负=偏小)")
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    out_png = os.path.join(os.path.dirname(FILE), "H0A2Z3_perimeter_diag.png")
    try:
        fig.savefig(out_png, dpi=150)
        print("\n图表已保存: " + out_png)
    except Exception as e:
        print("\n图表保存失败: " + str(e))

# ── 结论 ──
print("\n" + "=" * 50)
print("结论:")
if ratio > 1.005:
    print("  [首端周长偏大] 首5均值 %.2f / 稳定段 %.2f = %.4f" % (head5, body, ratio))
    print("  建议检查: 端部是否有法兰/切割余量/工艺凸起")
elif ratio < 0.995:
    print("  [首端周长偏小] 首5均值 %.2f / 稳定段 %.2f = %.4f" % (head5, body, ratio))
else:
    print("  [首端周长正常] 比值 %.4f (在 0.995~1.005 范围内)" % ratio)
