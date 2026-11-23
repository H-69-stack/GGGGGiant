"""测试新的 STP 文件 H0A2Z3-DT(1)(1).stp (用户发来的新版本)"""
import sys, os
sys.path.insert(0, r"C:\Users\gelbc\PycharmProjects\giant_tube_app2.0.1\backend")

if __name__ != "__main__":
    sys.exit(0)

import json
from core.pp.tube import analyze_tube

FILE = r"C:\Users\gelbc\Documents\xwechat_files\wxid_vfh7bfixnd9r22_9c9c\msg\file\2026-08\H0A2Z3-DT(1)(1).stp"

print(f"测试文件: {FILE}")
print("运行 A 算法...")
result = analyze_tube(FILE, file_type="stp", algorithm="A", sparse_step_mm=25.0)
if "error" in result:
    print("ERROR:", result["error"]); sys.exit(1)

out_path = r"C:\Users\gelbc\Desktop\diag_new_stp.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"已保存: {out_path}")

sf = result["sections_full"]
print(f"数据点数: {len(sf)}")

# 全局 peri 范围
peris = [x["perimeter"] for x in sf]
print(f"peri 范围: [{min(peris):.2f}, {max(peris):.2f}]")

# 全部 zspan
max_zspan = 0
max_i = -1
for i, x in enumerate(sf):
    zs = [p[2] for p in x.get("outline_3d", [])]
    if zs:
        zspan = max(zs) - min(zs)
        if zspan > max_zspan:
            max_zspan = zspan
            max_i = i
print(f"max zspan = {max_zspan:.2f} at i={max_i}")

# 全段 peri 突变点
import math
print("\n所有 peri 突变点 (|delta|>1):")
prev = None
for i, x in enumerate(sf):
    p = x["perimeter"]
    if prev is not None and abs(p - prev) > 1:
        print(f"  i={i:3d}  CL={x['cumulative_length']:6.2f}  peri={prev:.2f}->{p:.2f}  delta={p-prev:+.2f}")
    prev = p

# 全段 zspan>5 的点
print("\nzspan > 5 的所有点:")
for i, x in enumerate(sf):
    zs = [p[2] for p in x.get("outline_3d", [])]
    if zs:
        zspan = max(zs) - min(zs)
        if zspan > 5:
            print(f"  i={i:3d}  CL={x['cumulative_length']:6.2f}  peri={x['perimeter']:7.2f}  pos_z={x['position'][2]:7.2f}  zspan={zspan:.2f}")

# centerline 全程平滑度
pts = result["center_line_points"]
print(f"\ncenterline 点数: {len(pts)}")
print(f"全程 dxy 大值 (>0.5):")
dxy_list = []
for i in range(len(pts) - 1):
    cx1, cy1, _ = pts[i]
    cx2, cy2, _ = pts[i+1]
    dxy = math.hypot(cx2 - cx1, cy2 - cy1)
    dxy_list.append((i, dxy, cx1, cy1))
big = [t for t in dxy_list if t[1] > 0.5]
for i, dxy, cx, cy in big[:30]:
    print(f"  i={i:4d}  dxy={dxy:.3f}  cx={cx:+.2f}  cy={cy:+.2f}")
print(f"总大值数: {len(big)}")
