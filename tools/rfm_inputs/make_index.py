"""Build the index CSV and README for the exported input-data folder."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "gnt管件_输入数据"

README = """# 管件衍生输入数据（中心线 + 形线外径）

本目录是从 `gnt管件/*.stp` 提取出的、程序后续工序真正使用的两份衍生输入：
**中心线**与**形线（外径）**。全部由 STP 原生 B-rep 平面求交测得，沿中心线每 1 mm 一个正截面站位。

## 每个管件一个子文件夹

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `形线_外径.xlsx` | 工作表 `manual_profile`：`centerline_mm`（累计弧长）、`outer_circumference_mm`（外径周长） | **与软件「人工形线」模板同格式**，可直接回灌到形线质量检查 / 工艺规划 |
| `中心线.xlsx` | `station`、`arc_mm`、`x_mm`、`y_mm`、`z_mm`、切向 `tangent_x/y/z` | 压弯工序的目标中心线；也可导出给其它系统 |
| `截面明细.xlsx` | 周长、等效外径、外径面积、内周长、壁厚（min/median/mean/max）、`x_length/y_length` 轴长、截面中心、`basis_u/basis_v` 坐标系、法向、测量状态 | 工艺计算与模具设计所需的逐站位数据 |
| `外轮廓_2D.csv` | 每站位 384 个等弧长外轮廓点（`arc_mm, point_index, u_mm, v_mm`） | 重建外径形腔：世界坐标 = (x,y,z) + u·basis_u + v·basis_v |

## 单位与约定

- 长度单位统一 mm；`centerline_mm` / `arc_mm` 为沿中心线的累计弧长。
- 外径用**周长**表示（`C = πD`），不要与直径混用；`equivalent_outer_diameter_mm = 周长 / π`。
- 壁厚是沿外轮廓 192 个等弧长采样点到内轮廓的**最短距离**统计，不是径向差，也不是法向厚度。
- `x_length_mm` / `y_length_mm` 为**完整轴长**（不是半轴），来自截面外轮廓的主方向外接尺寸。
- `measurement_status`：`measured` 实测；`extrapolated` 端部线性外推；`needs_review` 需人工复核。
- 端部按产品逻辑舍弃不可靠段并线性外推，长度坐标保持完整，外推站位已单独标记。

## 可视化（不用看表格）

`可视化/index.html` 是总览页，列出 6 个管件；点进任一管件是可交互页面：

- 3D 外轮廓：拖动旋转、滚轮缩放、双击复位，当前站位高亮
- 形线（外径）曲线：鼠标移动或拖动即选站位，端部外推段有底色标记
- 截面外轮廓：随站位实时切换，附完整轴长外接框
- 读数：弧长、周长、外径、壁厚、轴长、截面中心、测量状态

全部为单文件离线页面（无外部脚本、无需联网），双击即可打开。
`可视化/快照/<管件>.png` 是同样内容的一页纸静态图，便于贴进报告或微信。

## 校验

`_校验.json` 记录了每个管件的回灌结果：形线 XLSX 经产品自带解析器 `parse_manual_xlsx` 读回后，
与测量值的最大偏差约 5×10⁻⁵ mm；中心线与截面表逐行一致（≤5×10⁻⁶ mm）；
`外轮廓_2D.csv` 重建的截面周长与实测周长偏差 ≤0.06 mm（384 点等弧长采样的弦长误差量级）。

## 重新生成

```powershell
python tools\\rfm_inputs\\export_inputs.py     # 导出
python tools\\rfm_inputs\\verify_inputs.py     # 校验
python tools\\rfm_inputs\\make_index.py        # 索引与说明
python tools\\rfm_inputs\\make_viewer.py       # 交互可视化
python tools\\rfm_inputs\\make_snapshots.py    # 静态快照
```
"""


def main():
    index = json.loads((OUT / "_索引.json").read_text(encoding="utf-8"))
    checks = {c["case"]: c for c in json.loads((OUT / "_校验.json").read_text(encoding="utf-8"))}
    lines = ["case,stations,centerline_length_mm,perimeter_min_mm,perimeter_max_mm,"
             "diameter_min_mm,diameter_max_mm,wall_median_mm,formline_roundtrip_delta_mm,"
             "centerline_delta_mm,contour_rebuilt_delta_mm"]
    for case in index["cases"]:
        check = checks.get(case["case"], {})
        lines.append(",".join(str(v) for v in [
            case["case"], case["stations"], case["centerline_length_mm"],
            case["perimeter_min_mm"], case["perimeter_max_mm"],
            round(case["perimeter_min_mm"] / 3.141592653589793, 3),
            round(case["perimeter_max_mm"] / 3.141592653589793, 3),
            case["wall_median_mm"],
            check.get("formline_max_delta_perimeter_mm"),
            check.get("centerline_max_delta_mm"),
            check.get("contour_rebuilt_max_delta_mm"),
        ]))
    (OUT / "索引.csv").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    (OUT / "README.md").write_text(README, encoding="utf-8")
    print(f"已写出 {OUT / '索引.csv'} 和 {OUT / 'README.md'}")


if __name__ == "__main__":
    main()
