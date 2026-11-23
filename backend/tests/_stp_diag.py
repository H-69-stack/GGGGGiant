"""诊断脚本：分析 STP 管件，逐段输出原始周长数据（含首尾端）"""
import sys, os, time
sys.path.insert(0, r"C:\Users\gelbc\PycharmProjects\giant_tube_app2.0.1\backend")

_pydir = r"C:\Program Files\GiantTubeApp\python"
for sub in (r"Library\bin", ""):
    p = os.path.join(_pydir, sub)
    if os.path.isdir(p):
        try: os.add_dll_directory(p)
        except Exception: pass

from core.pp.tube import analyze_tube
from core.pp.tube.geometry import trim_and_extrapolate_series
import numpy as np

FILE = r"C:\Users\gelbc\Desktop\H0A2Z3-DT(1)(1).stp"

print(f"文件: {FILE}")
print(f"文件大小: {os.path.getsize(FILE)/1024:.1f} KB\n")

for algo in ("A", "B"):
    print(f"{'='*60}")
    print(f"算法 {algo} (PCA旋转={'是' if algo=='A' else '否'})")
    print(f"{'='*60}")
    t0 = time.time()
    result = analyze_tube(FILE, file_type="stp", algorithm=algo, sparse_step_mm=25.0)
    print(f"耗时: {time.time()-t0:.1f}s")
    if "error" in result:
        print(f"错误: {result['error']}")
        continue

    wt = result.get("wall_thickness_data", [])
    if not wt:
        print("无壁厚数据")
        continue

    print(f"数据点数: {len(wt)}")

    # 原始数据（前30点 + 最后10点）
    print(f"\n前30个点（中心线长度→周长）:")
    print(f"  {'index':>6}  {'中心线长度(mm)':>15}  {'外径周长(mm)':>15}")
    print(f"  {'-'*6}  {'-'*15}  {'-'*15}")
    for i, row in enumerate(wt[:30]):
        cl = row.get("中心线长度(mm)", 0)
        perim = row.get("外径周长(mm)", 0)
        print(f"  {i:>6}  {cl:>15.4f}  {perim:>15.4f}")

    print(f"\n最后10个点:")
    print(f"  {'index':>6}  {'中心线长度(mm)':>15}  {'外径周长(mm)':>15}")
    print(f"  {'-'*6}  {'-'*15}  {'-'*15}")
    for i, row in enumerate(wt[-10:]):
        idx = len(wt) - 10 + i
        cl = row.get("中心线长度(mm)", 0)
        perim = row.get("外径周长(mm)", 0)
        print(f"  {idx:>6}  {cl:>15.4f}  {perim:>15.4f}")

    # 数值统计
    perims = np.array([r.get("外径周长(mm)", 0) for r in wt])
    cls = np.array([r.get("中心线长度(mm)", 0) for r in wt])
    print(f"\n统计:")
    print(f"  总长度: {cls[-1]:.1f} mm")
    print(f"  周长 min/max/mean: {perims.min():.2f} / {perims.max():.2f} / {perims.mean():.2f} mm")
    print(f"  首端10点均值: {perims[:10].mean():.2f} mm")
    print(f"  尾端10点均值: {perims[-10:].mean():.2f} mm")
    print(f"  中段(100:end-100)均值: {perims[100:-100].mean() if len(perims)>200 else 'N/A':}")
    # 首端和稳定段的比值
    if perims[10:30].mean() > 0:
        ratio = perims[:5].mean() / perims[10:30].mean()
        print(f"  首5点均值 / 首11-30点均值 = {ratio:.4f}  (>1.0表示首端偏大)")

    # 打印 step_analyzer 的详细结果（如果包含）
    sparse = result.get("sections_sparse", [])
    if sparse:
        print(f"\n稀疏采样点({len(sparse)}个，每25mm):")
        for row in sparse[:10]:
            print(f"  cl={row.get('centerline_length',0):.1f}mm  周长={row.get('perimeter',0):.2f}mm")
