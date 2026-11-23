"""验证修复后的 A 算法输出"""
import sys, os
sys.path.insert(0, r"C:\Users\gelbc\PycharmProjects\giant_tube_app2.0.1\backend")

if __name__ != "__main__":
    sys.exit(0)

import json
from core.pp.tube import analyze_tube

FILE = r"C:\Users\gelbc\Desktop\H0A2Z3-DT(1)(1).stp"

print("运行 A 算法 (修复后)...")
result = analyze_tube(FILE, file_type="stp", algorithm="A", sparse_step_mm=25.0)
if "error" in result:
    print(result["error"]); sys.exit(1)

with open(r"C:\Users\gelbc\Desktop\diag_fixed.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)

sf = result["sections_full"]
print(f"数据点数: {len(sf)}")

# 检查 60-80 段
print("\n修复后 CL=60~80 段:")
for i in [60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80]:
    if i >= len(sf): continue
    x = sf[i]
    ol = x.get("outline_3d", [])
    if ol:
        zs = [p[2] for p in ol]
        zspan = max(zs) - min(zs) if zs else 0
    else:
        zspan = 0
    print(f"  i=%3d  CL={x['cumulative_length']:6.2f}  peri={x['perimeter']:7.2f}  pos_z={x['position'][2]:.2f}  outline_z_span={zspan:.2f}")

print("\n修复后 CL=566~576 段:")
for i in range(565, 580):
    if i >= len(sf): continue
    x = sf[i]
    ol = x.get("outline_3d", [])
    if ol:
        zs = [p[2] for p in ol]
        zspan = max(zs) - min(zs) if zs else 0
    else:
        zspan = 0
    print(f"  i=%3d  CL={x['cumulative_length']:6.2f}  peri={x['perimeter']:7.2f}  pos_z={x['position'][2]:.2f}  outline_z_span={zspan:.2f}")
