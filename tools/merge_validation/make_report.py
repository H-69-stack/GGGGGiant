"""Build the Chinese illustrated report from measured results and real UI captures."""
import base64
import html
import json
from pathlib import Path

import markdown
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'reports/merge_validation_20260921'
NAME = 'GitHub合并与模具实测报告'
CASES = ['24TK_1324-1', '24TK_1324-2', '24TK_1324-31', '24TK_1442-1', '24TK_2052-2', 'H0A2Z3-DT']


def main():
    records = [json.loads((OUT/(case+'.json')).read_text(encoding='utf8')) for case in CASES]
    audits = {r['case']: r for r in json.loads((OUT/'export_audit.json').read_text(encoding='utf8'))}
    checks = json.loads((OUT/'verification.json').read_text(encoding='utf8'))
    cache_probe = json.loads((OUT/'qa_cache_probe.json').read_text(encoding='utf8'))
    measurements = {r['case']: r for r in json.loads((OUT/'measurement_audit.json').read_text(encoding='utf8'))}
    passed = [r for r in records if all(r['operations'][k]['status'] == 'passed' for k in ('forming', 'mold', 'tp'))]
    blocked = [r for r in records if r not in passed]
    assert len(passed)+len(blocked) == 6
    assert len(audits) == len(passed)
    pdfmetrics.registerFont(TTFont('CN', 'C:/Windows/Fonts/msyh.ttc', subfontIndex=0))
    styles = {
        'title': ParagraphStyle('title', fontName='CN', fontSize=24, leading=34, textColor=colors.HexColor('#163956'), spaceAfter=14),
        'h1': ParagraphStyle('h1', fontName='CN', fontSize=17, leading=25, textColor=colors.HexColor('#174f87'), spaceAfter=9),
        'h2': ParagraphStyle('h2', fontName='CN', fontSize=12, leading=18, spaceAfter=8),
        'body': ParagraphStyle('body', fontName='CN', fontSize=9, leading=15, spaceAfter=8),
        'small': ParagraphStyle('small', fontName='CN', fontSize=8, leading=12, textColor=colors.HexColor('#506679'), spaceAfter=7),
        'cell': ParagraphStyle('cell', fontName='CN', fontSize=8, leading=12),
    }
    story, md = [], []
    width = 180*mm

    def para(text, style='body'):
        story.append(Paragraph(html.escape(text), styles[style]))

    def heading(text, level=2):
        md.append('#'*level+' '+text+'\n')
        para(text, 'h1' if level == 2 else 'h2')

    def paragraph(text, small=False):
        md.append(text+'\n')
        para(text, 'small' if small else 'body')

    def table(headers, rows, col_widths=None):
        md.append('| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(map(str, row))+' |' for row in rows)+'\n')
        cells = [[Paragraph(html.escape(str(v)), styles['cell']) for v in row] for row in [headers]+rows]
        t = Table(cells, colWidths=col_widths, repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#dfeaf4')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f3f6f9')]),
            ('VALIGN', (0,0), (-1,-1), 'TOP'), ('LEFTPADDING',(0,0),(-1,-1),6),
            ('RIGHTPADDING',(0,0),(-1,-1),6), ('TOPPADDING',(0,0),(-1,-1),6),
            ('BOTTOMPADDING',(0,0),(-1,-1),6), ('LINEBELOW',(0,0),(-1,0),.7,colors.HexColor('#9db5cd'))]))
        story.extend([t, Spacer(1,9)])

    def screenshot(name, caption, max_height=110*mm):
        path = OUT/'screenshots'/name
        assert path.is_file(), path
        w, h = PILImage.open(path).size
        scale = min(width/w, max_height/h)
        story.append(Image(str(path), width=w*scale, height=h*scale, hAlign='LEFT'))
        para(caption, 'small')
        md.append(f'![{caption}](screenshots/{name})\n\n{caption}\n')

    def newpage():
        story.append(PageBreak())

    md.append('# '+NAME+'\n\n2026-09-21 · 源码合并与几何回归\n')
    para('GIANT 管件工艺软件', 'h1')
    para('GitHub 合并与\n模具实测报告'.replace('\n',' '), 'title')
    paragraph('2026-09-21 · 使用用户提供的六份 STP，重新读取、生成、导出、重读，并截取运行页面。')
    heading('结论')
    paragraph(f'{len(passed)} / 6 个案例在有效测量范围内完成成型腔体、长方体反切模具、TP 抽套模生成；{len(blocked)} 个案例仍存在局部截面校核问题，被接口明确拦截。本报告没有为被拦截案例填造模具截图。')
    table(['项目','验证结果'], [
        ['GitHub 基线', checks['upstream']],
        ['本地已提交修改', '333b9a9（原生 STP 读取）和 87075d5（模具与反切）'],
        ['合并后的源码提交', checks['merge_commit']],
        ['后端测试', checks['backend_tests']],
        ['前端构建', checks['frontend_build']],
        ['实际 CAD 产物', f'{len(passed)*3} 份 STEP + 对应 STL；全部导出后重读为有效单实体'],
        ['独立几何抽检', f'{len(passed)*13} 个成型法向截面；{len(passed)*9} 个 TP 轴向截面；{len(passed)} 份反切体积校验'],
    ], [37*mm,143*mm])
    paragraph('“通过”指本次几何回归通过，不是生产工艺审批。STP 开口/斜切端部未外推；截图中的反切是带封闭型腔的整体模坯，尚未设计分模面、定位件、排气孔或回弹补偿。', True)
    heading('测试案例总览')
    table(['案例','有效长 mm','壁厚中位 mm','截面','三类模型'], [[r['case'], f"{r['measurements']['length_mm']:.3f}", f"{r['measurements']['wall_median_mm']:.3f}", r['measurements']['stations'], '均通过' if r in passed else '校核拦截'] for r in records], [42*mm,32*mm,35*mm,22*mm,49*mm])
    newpage()
    heading('合并保留了什么，额外修复了什么')
    paragraph('从 GitHub main 拉取最新提交，合入本地两次既有修复；保留最新版形线 QA、人工测量形线、壁厚分段、长度与关键截面校验、Dn/D2 工艺约束和本地开发启动功能。没有用旧版本整体覆盖新版本。')
    table(['问题与后果','修复'], [
        ['新旧读取页面和 API 有文本冲突，直接选任意一侧会丢失 QA 或真实截面坐标。','逐处合并八个冲突文件；测量质量先校核，随后走新版形线选择流程。'],
        ['DEV 读取压缩结果与缓存丢失局部坐标、内外轮廓和质量状态，后续成型无可靠形状。','保留 sections_full 的几何字段及 analysis_quality；缓存恢复后仍执行质量拦截。'],
        ['补全几何后，QA-only 重复写入大体积轮廓导致变慢。','不变的原生轮廓单独压缩保存，QA-only 仅更新轻量元数据。'],
        ['裁掉不可靠末端后仍保留该范围的缺测记录，24TK_1442-1 被误拦截。','在最终有效测量区间重新统计法向与内部缺测；范围外失败保留在端部诊断中。'],
        ['缓存没有内嵌网格时，读取页面原模型不可见。','从已有预览接口恢复原模型、适配相机；中心线与图例统一为蓝色。'],
        ['确认壁厚时重建数据行，关键截面的长宽信息丢失。','重新附加真实截面的主方向长宽；人工工艺曲线与原始测量轮廓分别保留。'],
        ['TP 可选原模型预览把 STP 交给 STL 加载器，可能出现缺少转换依赖的错误。','原生 CAD 读取并生成预览网格；套模几何仍使用同一 CAD 实体导出与显示。'],
        ['旧实物 QA 测试把 STL 切片的两处凸起当作预期结果。','改为验证原生截面已消除伪凸起，同时保留真实法向异常对生产流程的拦截测试。'],
    ], [80*mm,100*mm])
    heading('验证方法')
    paragraph('全部案例来自用户给定目录，按 SHA-256 对应原文件。测量结果写入独立测试数据库；调用生产 PP/RFM/TP 接口生成及保存模型，再下载 STEP/STL。浏览器加载相同接口产出的 STL，并使用软件的生产三维组件截图。案例对照页是专用验证入口，不是另外绘制的效果图；另附正式成型与 TP 页面截图。')
    paragraph('本轮六份 STP 均重新读取。发现末端质量统计问题后，对本轮原始测量统一执行修复后的最终质量阶段，不改动轮廓或填补缺测；24TK_1442-1 又做一次完整 STP 重读，确认几何字段与最终阶段处理结果在 1e-9 mm 内一致。', True)
    paragraph('CAD 独立检查重新打开导出的 STEP，验证单实体、有效性、正体积。每根可用管件在原始局部法向平面抽取 13 个截面，与输入外轮廓比较 Hausdorff 距离和质心距离；TP 切 9 个截面验证内径和外径；反切检查模坯体积守恒。')
    paragraph('TP 测试取各管件测量周长的最大、最小站位作为回归端点，以周长/π 得到大小内径。此设置用于验证型线裁取、方向、闭合和旋转，不等于已优化或已批准的 TP 生产方案。套模固定主体外径 95 mm、法兰外径 125 mm、总长 T_L + 27 mm。')
    paragraph(checks.get('test_note',''), True)

    for r in records:
        newpage()
        case, m, q, tp = r['case'], r['measurements'], r['quality'], r['tp_inputs']
        heading(case+' · 硬模成型')
        paragraph(f"有效中心线 {m['length_mm']:.3f} mm；{m['stations']} 个截面；外周长 {m['perimeter_min_mm']:.3f}～{m['perimeter_max_mm']:.3f} mm；截面壁厚中位数 {m['wall_median_mm']:.3f} mm。")
        paragraph('中心线为法向截面外轮廓面积质心轨迹；壁厚为截面内最短距离的周向统计值。它们不是未随 STP 提供的设计路径或三维曲面法向壁厚。', True)
        if r in passed:
            screenshot(case+'_forming.png', '实际运行截图：左为不反切的外径实心腔体；右为反切后模坯的显示剖视，导出文件仍是完整实体。')
            mold = r['operations']['mold']['mold_info']
            a = audits[case]
            table(['校验','实测结果'], [
                ['自动模坯 XYZ 尺寸 mm', ' × '.join(f'{v:.1f}' for v in mold['size_xyz_mm'])],
                ['最小单侧余量', f"{mold['minimum_margin_mm']:.4f} mm（设定 15 mm）"],
                ['13 个截面最大轮廓偏差', f"{a['max_contour_error_mm']:.6f} mm"],
                ['13 个截面最大质心偏差', f"{a['max_centroid_error_mm']:.6f} mm"],
                ['反切体积守恒误差', f"{a['mold_volume_conservation_error_mm3']:.6f} mm³"],
            ], [80*mm,100*mm])
            paragraph('STEP 文件：models/'+case+'_forming.step；models/'+case+'_mold.step。', True)
        else:
            screenshot(case+'_source.png', '实际导入 STP 的预览。该案例未被强行生成模具。')
            paragraph(f"截面待复核 {q['review_station_count']} 个；缺测 {len(q['rejected_sections'])} 个；最大法向偏差 {q['max_normal_deviation_deg']:.3f}°；最大质心迭代残差 {q['max_centroid_residual_mm']:.3f} mm。")
            paragraph('成型及反切接口结果：'+r['operations']['forming']['error'])
            paragraph('待复核站位 s（mm）：'+'、'.join(f"{s['station_mm']:.2f}" for s in measurements[case]['review_samples'])+'。详细法向、残差和缺测坐标见 measurement_audit.json。', True)
        for warning in q.get('warnings', []):
            paragraph(warning, True)
        newpage()
        heading(case+' · TP 抽套模')
        table(['输入项','本次几何回归参数'], [
            ['大端内径 T_D', f"{tp['T_D_mm']:.6f} mm"],
            ['小端内径 D', f"{tp['D_mm']:.6f} mm"],
            ['型线段长 T_L', f"{tp['T_L_mm']:.6f} mm"],
            ['原始站位：大端 / 小端', f"{tp['large_station_mm']:.6f} / {tp['small_station_mm']:.6f} mm"],
        ], [70*mm,110*mm])
        screenshot(case+'_tp.png', '实际运行截图：TP 套模的完整实体与内部型线剖视。' if r in passed else '实际运行截图：TP 接口拒绝使用未通过校核的测量数据。')
        if r in passed:
            a = audits[case]
            paragraph(f"导出的 STEP 重读有效。9 个截面最大内径偏差 {a['tp_max_diameter_error_mm']:.6f} mm；抽检主体外径均为 95 mm；整体 XYZ 尺寸为 "+' × '.join(f'{v:.3f}' for v in a['tp_extents_xyz_mm'])+' mm。')
            paragraph('STEP 文件：models/'+case+'_tp.step。', True)
        else:
            paragraph('拦截信息：'+r['operations']['tp']['error'])
        paragraph('上述参数只验证该测试管件的型线和旋转生成链路；生产使用前应选择对应工艺阶段，确认大小径、段长和成形余量。', True)

    newpage()
    heading('正式页面操作验证')
    paragraph('除六案例对照外，也在正式 RFM 成型组件中实际加载 24TK_1324-1 的工艺记录，生成外径腔体和实心内径，并切换反切输出；在正式 TP 页面点击生成套模，确认下载入口出现。')
    screenshot('production_mold_view.png', '正式 RFM 成型页面：开启反切，显示模坯尺寸与内腔。', 126*mm)
    screenshot('production_tp.png', '正式 TP 页面：成功生成套模，STEP/STL 下载按钮可用。', 98*mm)
    newpage()
    heading('读取页面与中心线显示')
    paragraph('从隔离数据库恢复 24TK_1324-1，验证真实原模型、蓝色中心线、红色截面叠加、周长和壁厚统计。新版壁厚分段、图纸长度、关键截面与形线 QA 控件保持可用。')
    screenshot('production_reading.png', '正式读取组件运行截图：恢复缓存后可见原模型与中心线，左侧显示有效长度、周长和壁厚。', 160*mm)
    newpage()
    heading('交付与复现')
    paragraph('源码提交、前端构建与自动化测试信息见 verification.json；每个案例的 JSON 保存输入 SHA-256、测量质量、生成结果、输出 STEP 哈希和参数。export_audit.json 保存逐截面独立校验数值。screenshots 目录保存浏览器原始 PNG。')
    warm, restored = cache_probe['runs']
    paragraph(f"缓存专项验证：24TK_1324-2 保留 {cache_probe['native_sections_retained']} 个完整原生截面。已有会话的 QA-only 请求耗时 {warm['http_seconds']:.3f} 秒；清空内存会话、从磁盘恢复后耗时 {restored['http_seconds']:.3f} 秒。两次均未重读 STP，几何压缩文件字节保持不变。原始记录见 qa_cache_probe.json。")
    paragraph('输入与产物一致性：六份输入 STP 的 SHA-256 及十二份导出 STEP 的 SHA-256 均与本轮结果记录一致，见 artifact_integrity.json。')
    performance = checks.get('real_performance')
    if performance:
        paragraph(f"独立完整读取性能测试：24TK_1324-2 首次 STP + QA 用时 {performance['full_reread_s']:.3f} 秒（约 {performance['full_reread_s']/60:.1f} 分钟）；紧接着仅重做 QA 用时 {performance['qa_only_s']:.3f} 秒，读取计数仍为 1。复杂管件的首次 CAD 读取仍较慢，本次缓存修复改善的是后续 QA 操作。")
    paragraph('大体积测量 JSON、测试数据库和 STEP/STL 保存在本机报告目录，并已忽略 Git 跟踪；源码、测试、报告及截图进入 Git。旧本地未跟踪文件如与上游文件重名，会先备份到 premerge_untracked 再更新工程。原测试 STP/PDF 不改写。')
    paragraph('这次交付是合并后的源码版本，未重新打包或替换已安装 EXE，也未修改生产数据库。使用说明和逐项原因说明位于 docs/合并版本使用说明_2026-09-21.md 和 docs/合并修改说明_2026-09-21.md。')
    md.append('复现命令（在仓库根目录，使用含 OCP 的 Python 环境）：\n\n```powershell\npython tools/stp_validation/run_cases.py "测试文件目录" reports/merge_validation_20260921/measured\npython tools/merge_validation/run_case.py 24TK_1324-1 --fixtures "测试文件目录"\npython tools/merge_validation/audit_exports.py\npython tools/merge_validation/serve.py\n# 第二个终端：\ncd frontend\nnpx vite --config vite.merge-validation.config.js\n# 浏览器：http://127.0.0.1:3018/merge-validation.html\n```\n')
    paragraph('继续使用前：重新读取旧版保存且缺少原生截面的管件，完成新版 QA、长度与关键截面确认，再保存关联的工艺设计。两个被拦截案例须先复核局部造型；修改周长曲线不能代替三维法向校核。')
    md_text = '\n'.join(md)
    (OUT/(NAME+'.md')).write_text(md_text, encoding='utf8')
    body = markdown.markdown(md_text, extensions=['tables', 'fenced_code'])
    for path in (OUT/'screenshots').glob('*.png'):
        body = body.replace('screenshots/'+path.name, 'data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode())
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+NAME+'</title><style>body{font-family:"Microsoft YaHei",sans-serif;max-width:1100px;margin:35px auto;padding:0 24px;color:#19384e;line-height:1.8}h1,h2{color:#174f87}h2{border-bottom:2px solid #d8e5ef;padding-top:24px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:10px;border:1px solid #cfdae4;text-align:left}th{background:#e6eef6}tr:nth-child(even){background:#f6f8fa}img{width:100%;height:auto;border:1px solid #dde5ec;border-radius:8px}pre{background:#f1f5f8;padding:18px;overflow:auto}p{margin:14px 0}</style>'+body+'</html>'
    (OUT/(NAME+'.html')).write_text(document, encoding='utf8')
    def footer(canvas, doc):
        canvas.setFont('CN', 8)
        canvas.setFillColor(colors.HexColor('#688094'))
        canvas.drawString(15*mm, 10*mm, 'GIANT · GitHub 合并与模具实测 · 2026-09-21')
        canvas.drawRightString(195*mm, 10*mm, str(doc.page))
    doc = SimpleDocTemplate(str(OUT/(NAME+'.pdf')), pagesize=(210*mm,297*mm),
                            leftMargin=15*mm, rightMargin=15*mm, topMargin=16*mm, bottomMargin=17*mm,
                            title=NAME, author='GIANT 验证记录')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(NAME, 'complete', flush=True)


if __name__ == '__main__':
    main()
