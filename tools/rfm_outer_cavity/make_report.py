"""Render the outer-cavity batch summary as a Markdown report."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "gnt管件_外径形腔"
    report_dir = out / "_report"
    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    cases = summary["cases"]

    lines = [
        "# 管件外径形腔 STEP 生成报告",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 源文件夹：`{summary['source_folder']}`",
        f"- 输出文件夹：`{summary['output_folder']}`",
        f"- 工序：{summary['operation']}",
        f"- 算法：{summary['algorithm']}（PCA 长轴 → 法向截面 → 1 mm 站位）",
        f"- 结果：{summary['passed']}/{summary['total']} 成功",
        "",
        "## 明细",
        "",
        "| 管件 | 状态 | 中心线长 (mm) | 测量站位 | 截面环用量 | 形腔体积 (mm³) | 包围盒 X×Y×Z (mm) | 耗时 (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        m = case.get("measurement") or {}
        loft = case.get("loft") or {}
        bounds = case.get("bounds_xyz_mm")
        size = ("{:.1f} × {:.1f} × {:.1f}".format(*(b - a for a, b in zip(*bounds)))
                if bounds else "—")
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            case["case"],
            "成功" if case["status"] == "passed" else "失败：" + (case.get("error") or "")[:40],
            m.get("centerline_length_mm", "—"),
            m.get("stations", "—"),
            loft.get("used_rings", "—"),
            case.get("cavity_volume_mm3", "—"),
            size,
            case.get("seconds", "—"),
        ))

    warnings = [(c["case"], w) for c in cases for w in (c.get("measurement") or {}).get("warnings") or []]
    lines += ["", "## 测量质量", ""]
    if warnings:
        lines.append("分析器对各管件给出的提示（保留原始记录，未做任何修补）：")
        lines.append("")
        for case, warn in warnings:
            lines.append(f"- **{case}**：{warn}")
    else:
        lines.append("无异常提示。")

    lines += [
        "",
        "## 几何口径",
        "",
        "- 外径形腔 = 原始外轮廓（`sections_full[*].outer_2d`）逐站点放样成的实心包络实体，"
        "与成型页“关闭反切”时的输出完全一致，可直接用于 CAD 布尔减。",
        "- 截面来自 STP 原生 B-rep 平面求交（弧长 1 mm 站位），不经 STL 二次切片，"
        "不以外轮廓推内轮廓。",
        "- 每个 STEP 写出后均回读校验：单一有效实体、体积与生成实体一致。",
        "",
        "## 复现",
        "",
        "```",
        "python tools/rfm_outer_cavity/run_outer_cavity.py --src gnt管件 --out gnt管件_外径形腔",
        "```",
    ]

    target = report_dir / "外径形腔生成报告.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告已写出：{target}")


if __name__ == "__main__":
    main()
