"""Render the all-model export as a Markdown report."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "gnt管件_全模型"
OP_ORDER = ["shaping", "belling", "flattening", "bending"]
OP_LABEL = {"shaping": "成型", "belling": "扩口", "flattening": "压扁", "bending": "压弯"}
MODEL_LABEL = {
    ("shaping", "外径形腔"): "外径形腔（原始外轮廓包络实心体）",
    ("shaping", "实心内径"): "实心内径（原始内轮廓，保留偏心/变壁厚）",
    ("shaping", "反切模具"): "反切模具（以外径形腔布尔减）",
    ("belling", "扩口芯轴"): "扩口芯轴",
    ("belling", "扩口腔体"): "扩口腔体（全长外形）",
    ("belling", "反切模具"): "反切模具（以扩口腔体布尔减）",
    ("flattening", "压扁模型"): "压扁模型",
    ("flattening", "反切模具"): "反切模具（以压扁模型布尔减）",
    ("bending", "压弯模型"): "压弯模型",
    ("bending", "反切模具"): "反切模具（以压弯模型布尔减）",
}


def mb(value):
    return f"{value / 1e6:.1f}"


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_ROOT
    report_dir = root / "_report"
    cases = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(report_dir.glob("*.json")) if p.name != "summary.json"]
    if not cases:
        print("没有报告可汇总")
        return 1

    lines = ["# 管件全模型生成报告", ""]
    lines += [f"- 输出根目录：`{root}`",
              f"- 每个管件一个子文件夹，文件名 `<工序>_<模型>.stp`",
              f"- 共 {len(cases)} 个管件，"
              f"{sum(c.get('ok', 0) for c in cases)} 个模型，失败 {sum(c.get('failed', 0) for c in cases)} 个",
              ""]

    lines += ["## 总览", ""]
    header = "| 管件 | " + " | ".join(OP_LABEL[o] for o in OP_ORDER) + " | 中心线长 (mm) |"
    lines += [header, "|" + " --- |" * (len(OP_ORDER) + 2)]
    for case in cases:
        cells = []
        for operation in OP_ORDER:
            got = [m for m in case["models"]
                   if m.get("operation") == operation and "file" in m]
            bad = [m for m in case["models"]
                   if m.get("operation") == operation and m.get("status") == "failed"]
            cells.append(f"{len(got)} 个模型" + ("（失败）" if bad else ""))
        lines.append(f"| {case['case']} | " + " | ".join(cells)
                     + f" | {case.get('centerline_length_mm', '—')} |")

    for case in cases:
        lines += ["", f"## {case['case']}", "",
                  f"中心线长 {case.get('centerline_length_mm', '—')} mm，"
                  f"用时 {case.get('seconds', '—')} s", ""]
        lines += ["| 工序 | 模型 | 文件 | 大小 (MB) | 体积 (mm³) | 备注 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for operation in OP_ORDER:
            for model in [m for m in case["models"] if m.get("operation") == operation]:
                if model.get("status") == "failed":
                    lines.append(f"| {OP_LABEL[operation]} | — | — | — | — | 失败：{model.get('error')} |")
                    continue
                note = ""
                if model.get("axis_source") == "perimeter_matched_ellipse":
                    note = "轴长按等周长椭圆重建"
                if model.get("mold_info"):
                    info = model["mold_info"]
                    note = (f"模体 {' × '.join(f'{v:.1f}' for v in info['size_xyz_mm'])} mm，"
                            f"单侧余量 {info['minimum_margin_mm']:.1f} mm")
                lines.append(f"| {OP_LABEL[operation]} | {model.get('model')} | {model.get('file')} | "
                             f"{mb(model.get('bytes', 0))} | {model.get('volume_mm3', '—')} | {note} |")

    lines += ["", "## 口径与说明", "",
              "- 截面来自 STP 原生 B-rep 平面求交，沿中心线每 1 mm 一个站位；端部按产品逻辑"
              "舍弃不可靠段并线性外推，外推站位在原数据中单独标记。",
              "- 成型：外径形腔为原始外轮廓放样成的实心包络体；实心内径由原始内轮廓生成，"
              "保留原始偏心与变壁厚；反切模具以外径形腔在 15 mm 单侧余量的长方体内布尔减。",
              "- 扩口：芯轴按统一壁厚（无工艺设计时取 2 mm）从外径周长内缩；扩口腔体为全长外形回转体；"
              "反切模具以扩口腔体布尔减。",
              "- 压扁/压弯：压扁段 200 mm、过渡段 150 mm、总长取中心线长；反切模具以对应模型布尔减。",
              "- 压扁需要「已确认的椭圆轴长」。原始 STP 截面并非椭圆（长短轴外接椭圆的周长比实测周长小 6% 左右），"
              "产品会按设计报错；此处改用**等周长椭圆轴长**（保持实测周长与长短轴比例）作为前序轴长，"
              "报表中已标注。若需严格按工艺设计的轴长，需要在 PP 里先确认该设计。",
              "- 未生成：TP 抽管模具、普通抽/锥形硬模——它们需要工艺设计参数（Dn、D0、L、T 等），"
              "不能只由管件 STP 推出。",
              ""]

    target = report_dir / "全模型生成报告.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写出：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
