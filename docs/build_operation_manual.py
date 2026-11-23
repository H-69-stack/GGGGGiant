# -*- coding: utf-8 -*-
"""生成《自行车抽管软件操作说明书》Word 文件。内容以当前源码界面为准。"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn, nsmap
from docx.shared import Cm, Inches, Pt, RGBColor, Emu
from docx.enum.section import WD_ORIENT

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / "自行车抽管软件操作说明书.docx"
SHOT = ROOT / "reports"

APP = "自行车抽管软件"
VER = "2.0.1.5.1123"
DATE = "2026年9月"

NAVY = RGBColor(0x1F, 0x3A, 0x5F)
STEEL = RGBColor(0x4A, 0x55, 0x68)
LINE = "D0D7DE"
HEAD_BG = "1F3A5F"
ROW_BG = "F4F7FA"
PLACE_BG = "EEF2F6"
WARN_BG = "FFF6E5"


def set_run_font(run, name_cn="宋体", name_en="Times New Roman", size=10.5, bold=False, color=None):
    run.bold = bold
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = color
    run.font.name = name_en
    r = run._element.get_or_add_rPr()
    rFonts = r.get_or_add_rFonts()
    rFonts.set(qn("w:eastAsia"), name_cn)
    rFonts.set(qn("w:ascii"), name_en)
    rFonts.set(qn("w:hAnsi"), name_en)


def set_cell_shading(cell, hex_color):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)


def set_cell_border(cell, color="BFBFBF"):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), color)
        tcBorders.append(el)
    tcPr.append(tcBorders)


def set_cell_margins(cell, top=40, bottom=40, left=80, right=80):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcMar = OxmlElement("w:tcMar")
    for m, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        el = OxmlElement(f"w:{m}")
        el.set(qn("w:w"), str(v))
        el.set(qn("w:type"), "dxa")
        tcMar.append(el)
    tcPr.append(tcMar)


def prevent_row_split(row):
    tr = row._tr
    trPr = tr.get_or_add_trPr()
    cant = OxmlElement("w:cantSplit")
    trPr.append(cant)


def add_page_number(paragraph):
    run = paragraph.add_run()
    fld1 = OxmlElement("w:fldChar")
    fld1.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    fld2 = OxmlElement("w:fldChar")
    fld2.set(qn("w:fldCharType"), "end")
    run._r.append(fld1)
    run._r.append(instr)
    run._r.append(fld2)
    set_run_font(run, size=9, color=STEEL)


def add_num_pages(paragraph):
    run = paragraph.add_run()
    fld1 = OxmlElement("w:fldChar")
    fld1.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " NUMPAGES "
    fld2 = OxmlElement("w:fldChar")
    fld2.set(qn("w:fldCharType"), "end")
    run._r.append(fld1)
    run._r.append(instr)
    run._r.append(fld2)
    set_run_font(run, size=9, color=STEEL)


def setup_styles(doc):
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    pf = normal.paragraph_format
    pf.line_spacing = 1.25
    pf.space_after = Pt(6)
    pf.first_line_indent = Cm(0.74)

    for i, (sz, space_before, space_after) in enumerate(
        [(16, 16, 8), (14, 14, 6), (12, 10, 4)], start=1
    ):
        st = styles[f"Heading {i}"]
        st.font.color.rgb = NAVY
        st.font.bold = True
        st.font.size = Pt(sz)
        st.font.name = "Times New Roman"
        st._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
        st.paragraph_format.first_line_indent = Cm(0)
        st.paragraph_format.space_before = Pt(space_before)
        st.paragraph_format.space_after = Pt(space_after)
        st.paragraph_format.line_spacing = 1.2
        st.paragraph_format.keep_with_next = True


def P(doc, text, *, indent=True, bold=False, size=10.5, center=False, color=None, space_after=6):
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Cm(0.74) if indent else Cm(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.25
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    set_run_font(run, size=size, bold=bold, color=color or RGBColor(0x22, 0x22, 0x22))
    return p


def mixed(doc, parts, *, indent=True, space_after=6):
    """parts: list of (text, bold)."""
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Cm(0.74) if indent else Cm(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.25
    for text, bold in parts:
        run = p.add_run(text)
        set_run_font(run, size=10.5, bold=bold, color=RGBColor(0x22, 0x22, 0x22))
    return p


def bullet(doc, text, bold_lead=None):
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Cm(0)
    p.paragraph_format.left_indent = Cm(0.74)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.2
    if bold_lead:
        r1 = p.add_run(bold_lead)
        set_run_font(r1, size=10.5, bold=True)
        r2 = p.add_run(text)
        set_run_font(r2, size=10.5)
    else:
        r = p.add_run("· " + text)
        set_run_font(r, size=10.5)
    return p


def caption(doc, text, kind="表"):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent = Cm(0)
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text)
    set_run_font(run, name_cn="黑体", size=9, bold=True, color=STEEL)
    return p


def add_table(doc, headers, rows, col_widths=None, title=None):
    if title:
        caption(doc, title)
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    usable = Cm(16.0)
    if col_widths:
        widths = [Cm(w) for w in col_widths]
    else:
        w = usable / len(headers)
        widths = [w] * len(headers)
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Cm(0)
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(h)
        set_run_font(run, name_cn="黑体", size=9, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))
        set_cell_shading(cell, HEAD_BG)
        set_cell_border(cell, "1F3A5F")
        set_cell_margins(cell)
        cell.width = widths[i]
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            cell = table.rows[ri + 1].cells[ci]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.first_line_indent = Cm(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.15
            run = p.add_run(str(val))
            set_run_font(run, size=9)
            if ri % 2 == 1:
                set_cell_shading(cell, ROW_BG)
            set_cell_border(cell)
            set_cell_margins(cell)
            cell.width = widths[ci]
        prevent_row_split(table.rows[ri + 1])
    prevent_row_split(table.rows[0])
    # keep table from stretching
    tbl = table._tbl
    tblPr = tbl.tblPr if tbl.tblPr is not None else OxmlElement("w:tblPr")
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:w"), str(int(sum(w.cm for w in widths) * 567)))
    tblW.set(qn("w:type"), "dxa")
    tblPr.append(tblW)
    return table


def add_image(doc, path, title, width_cm=14.5):
    p = Path(path)
    if not p.exists():
        placeholder(doc, title, "图片文件未找到，请后续人工补充。")
        return
    caption(doc, title, kind="图")
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.first_line_indent = Cm(0)
    para.paragraph_format.space_after = Pt(4)
    run = para.add_run()
    run.add_picture(str(p), width=Cm(width_cm))


def placeholder(doc, where, what):
    caption(doc, f"【截图位置：{where}】")
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = table.rows[0].cells[0]
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent = Cm(0)
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after = Pt(16)
    run = p.add_run(f"需要展示：{what}\n（请在软件实际界面截取后插入此处）")
    set_run_font(run, size=10, color=STEEL)
    set_cell_shading(cell, PLACE_BG)
    set_cell_border(cell, "8A96A3")
    cell.width = Cm(16)


def callout(doc, items):
    """items: list of '① 说明' """
    for it in items:
        p = doc.add_paragraph()
        p.paragraph_format.first_line_indent = Cm(0)
        p.paragraph_format.left_indent = Cm(0.5)
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run(it)
        set_run_font(run, size=10)


def field_block(doc, name, content, source, unit, example, role, note=None):
    mixed(doc, [("字段名称：", True), (name, True)], indent=False, space_after=2)
    mixed(doc, [("填写内容：", True), (content, False)], indent=False, space_after=2)
    mixed(doc, [("数据来源：", True), (source, False)], indent=False, space_after=2)
    mixed(doc, [("单位：", True), (unit, False)], indent=False, space_after=2)
    mixed(doc, [("填写示例：", True), (example, False)], indent=False, space_after=2)
    mixed(doc, [("作用：", True), (role, False)], indent=False, space_after=2)
    if note:
        mixed(doc, [("注意事项：", True), (note, False)], indent=False, space_after=8)
    else:
        P(doc, "", indent=False, space_after=6)


def note_box(doc, text, title="说明"):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = table.rows[0].cells[0]
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.first_line_indent = Cm(0)
    p.paragraph_format.space_after = Pt(0)
    r1 = p.add_run(title + "：")
    set_run_font(r1, size=10, bold=True, color=NAVY)
    r2 = p.add_run(text)
    set_run_font(r2, size=10)
    set_cell_shading(cell, WARN_BG)
    set_cell_border(cell, "C9A227")
    set_cell_margins(cell, 80, 80, 120, 120)
    P(doc, "", indent=False, space_after=4)


def add_toc(doc):
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Cm(0)
    run = p.add_run()
    fldBegin = OxmlElement("w:fldChar")
    fldBegin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-3" \\h \\z \\u '
    fldSeparate = OxmlElement("w:fldChar")
    fldSeparate.set(qn("w:fldCharType"), "separate")
    hint = OxmlElement("w:t")
    hint.text = "（打开文档后请右键目录，选择“更新域”以生成完整目录）"
    fldEnd = OxmlElement("w:fldChar")
    fldEnd.set(qn("w:fldCharType"), "end")
    run._r.append(fldBegin)
    run._r.append(instr)
    run._r.append(fldSeparate)
    run._r.append(hint)
    run._r.append(fldEnd)
    set_run_font(run, size=10.5, color=STEEL)


def configure_section(section):
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)
    section.top_margin = Cm(2.4)
    section.bottom_margin = Cm(2.4)
    section.header_distance = Cm(1.2)
    section.footer_distance = Cm(1.2)

    header = section.header
    header.is_linked_to_previous = False
    hp = header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    hp.paragraph_format.first_line_indent = Cm(0)
    run = hp.add_run(f"{APP}操作说明书")
    set_run_font(run, name_cn="黑体", size=9, color=STEEL)
    # header line
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), "1F3A5F")
    pBdr.append(bottom)
    hp._p.get_or_add_pPr().append(pBdr)

    footer = section.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fp.paragraph_format.first_line_indent = Cm(0)
    r1 = fp.add_run("第 ")
    set_run_font(r1, size=9, color=STEEL)
    add_page_number(fp)
    r2 = fp.add_run(" 页 / 共 ")
    set_run_font(r2, size=9, color=STEEL)
    add_num_pages(fp)
    r3 = fp.add_run(" 页")
    set_run_font(r3, size=9, color=STEEL)
    pBdr2 = OxmlElement("w:pBdr")
    top = OxmlElement("w:top")
    top.set(qn("w:val"), "single")
    top.set(qn("w:sz"), "6")
    top.set(qn("w:space"), "4")
    top.set(qn("w:color"), "1F3A5F")
    pBdr2.append(top)
    fp._p.get_or_add_pPr().append(pBdr2)


def cover(doc):
    for _ in range(4):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
    P(doc, "企业软件交付文档", indent=False, center=True, size=12, color=STEEL, space_after=18)
    P(doc, f"《{APP}操作说明书》", indent=False, center=True, size=26, bold=True, color=NAVY, space_after=16)
    P(doc, "Software Operation Manual", indent=False, center=True, size=14, color=STEEL, space_after=28)
    add_table(
        doc,
        ["项目", "内容"],
        [
            ["软件名称", APP],
            ["当前版本", f"{VER}（以登录页显示的版本号为准）"],
            ["文档性质", "操作说明书（面向现场工程师）"],
            ["适用对象", "工艺规划、模具设计、硬模成型工程师及系统管理员"],
            ["编写依据", "当前软件界面、业务逻辑与已保存分析结果"],
            ["编写日期", DATE],
        ],
        col_widths=[4.5, 11.5],
        title=None,
    )
    P(doc, "", indent=False, space_after=20)
    P(
        doc,
        "使用说明：请按第4章推荐顺序完成一次完整操作。各模块介绍页若与计算页不一致，以计算页实际按钮和字段为准。",
        indent=False,
        center=True,
        size=10,
        color=STEEL,
    )
    doc.add_page_break()


def build():
    doc = Document()
    setup_styles(doc)
    configure_section(doc.sections[0])
    cover(doc)

    doc.add_heading("目录", level=1)
    P(doc, "打开本文件后，请在目录上右键选择“更新域 / 更新整个目录”，即可生成可点击目录。", indent=False)
    add_toc(doc)
    doc.add_page_break()

    # ── 1 ──
    doc.add_heading("1  软件简介", level=1)
    P(
        doc,
        f"{APP}用于自行车管件从三维模型读取、工艺参数优化，到抽管模具计算和硬模成型模型生成的连续作业。"
        "软件按工作角色开放功能，工程师可按本说明书完成一次从模型导入到结果导出的完整操作。",
    )

    doc.add_heading("1.1  用途与适用对象", level=2)
    add_table(
        doc,
        ["角色", "可使用的功能", "典型任务"],
        [
            ["工艺规划", "上传分析、工艺优化", "导入管件模型，确认形线，优化并保存工艺设计"],
            ["模具设计", "普通抽、TP抽、Taper", "按工艺设计计算抽管模具参数，保存或导出"],
            ["硬模成型", "硬模成型四道工序", "生成扩口、压扁、压弯、成型模型或反切模坯"],
            ["ALL", "以上全部业务功能", "跨工序连续作业"],
            ["管理员", "全部业务功能 + 管理菜单", "审批账号、部署与授权维护"],
        ],
        col_widths=[3.2, 6.0, 6.8],
        title="表1-1  工作角色与功能范围",
    )
    P(
        doc,
        "管理员账户不受工作角色限制。普通用户首次登录后，账号会自动绑定当前浏览器，不能直接在其他浏览器使用。",
        indent=True,
    )

    doc.add_heading("1.2  主要功能", level=2)
    bullet(doc, "上传管件 STL/STP/STEP 模型，自动提取中心线、截面周长和壁厚。")
    bullet(doc, "由工程师确认壁厚分段、图纸长度、关键截面和形线来源后，保存管件记录。")
    bullet(doc, "按已保存管件选择工艺路线，优化素材尺寸和各工步参数，保存工艺设计。")
    bullet(doc, "普通抽、TP抽、Taper 模块按工艺设计（或 DXF）计算模具参数，并可导出压缩包。")
    bullet(doc, "硬模成型按工艺设计生成扩口、压扁、压弯、成型模型，可选反切实体模坯。")
    bullet(doc, "提供模型旋转工具，以及管理员的用户审批、注册和部署授权功能。")

    doc.add_heading("1.3  基本工作流程", level=2)
    P(doc, "完整作业按以下顺序进行。后一步依赖前一步已经确认并保存的数据。")
    add_table(
        doc,
        ["顺序", "操作", "结果"],
        [
            ["1", "激活软件并登录", "进入工作台"],
            ["2", "上传并分析管件模型", "得到中心线、周长、壁厚"],
            ["3", "确认壁厚、长度、形线并保存管件", "管件数据库中形成可用记录"],
            ["4", "加载管件并优化工艺参数", "得到素材尺寸和各工步参数"],
            ["5", "保存工艺设计", "模具设计与硬模成型可加载该设计"],
            ["6", "计算抽管模具或生成硬模模型", "保存到模具库，或下载 STEP/ZIP"],
        ],
        col_widths=[2.2, 7.2, 6.6],
        title="表1-2  推荐作业顺序",
    )
    note_box(
        doc,
        "不要把软件自动读取的中心线长度直接当作图纸总长。中心线是截面外轮廓面积质心轨迹；非圆截面的等效直径是外周长除以 π，不是截面最大宽度。",
        "重要",
    )

    # ── 2 ──
    doc.add_heading("2  软件运行与进入", level=1)

    doc.add_heading("2.1  启动软件", level=2)
    P(doc, "安装完成后，按以下方式启动：")
    mixed(
        doc,
        [
            ("步骤 1：", True),
            ("在安装目录中双击“启动软件.bat”。若目录中存在 GiantTubeApp.exe，也可直接启动该程序。", False),
        ],
        indent=False,
    )
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("等待程序启动。正式安装包默认打开本机浏览器，访问端口一般为 5890。不要把后端接口端口当作软件页面打开。", False),
        ],
        indent=False,
    )
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("若浏览器未自动打开，可在地址栏输入本机访问地址。管理员可在“管理 → 部署设置”中查看本机和局域网地址。", False),
        ],
        indent=False,
    )
    P(
        doc,
        "停止软件时，使用安装目录中的“停止软件.bat”。同一台计算机不要同时启动两个实例。",
    )
    placeholder(doc, "软件启动后的浏览器首页或激活页", "启动完成后的第一屏，便于确认软件已打开")

    doc.add_heading("2.2  首次激活", level=2)
    P(
        doc,
        "未授权时，软件会进入“软件激活”页，无法直接登录。该页机器码由软件根据本机硬件自动生成，无需人工填写。",
    )
    mixed(doc, [("步骤 1：", True), ("确认页面标题为软件名称，并显示“软件激活”。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("查看“本机机器码”。点击“复制机器码”，将机器码发送给软件供应商。", False),
        ],
        indent=False,
    )
    field_block(
        doc,
        "授权码",
        "供应商签发的授权字符串。",
        "软件供应商根据本机机器码签发，不是工程师自行编写。",
        "无",
        "GT-XXXX-XXXX-…",
        "完成本机授权后，才能进入登录页。",
        "请完整粘贴，不要漏掉前缀或换行。",
    )
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("将授权码粘贴到“授权码”输入框，点击“激活并进入软件”。激活成功后进入登录页。", False),
        ],
        indent=False,
    )
    P(
        doc,
        "管理员也可在“管理 → 部署设置”中再次安装授权码，或上传 license.dat。替换许可证后，一般无需重启，软件会在后续请求中重新读取。",
    )
    placeholder(doc, "软件激活页面", "① 本机机器码  ② 复制机器码  ③ 授权码输入框  ④ 激活并进入软件")
    callout(
        doc,
        [
            "① 本机机器码（软件自动生成）",
            "② 复制机器码",
            "③ 粘贴授权码",
            "④ 点击“激活并进入软件”",
        ],
    )

    doc.add_heading("2.3  注册账号", level=2)
    P(
        doc,
        "登录页可点击“没有账号？注册新账号”。自助注册提交后必须等待管理员审批，审批通过后才能登录。管理员代注册的账号会自动审批通过。",
    )
    mixed(doc, [("步骤 1：", True), ("在登录页点击“没有账号？注册新账号”。", False)], indent=False)
    mixed(doc, [("步骤 2：", True), ("按下列字段填写注册信息。", False)], indent=False)

    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["账号（只能使用数字）", "登录用数字账号", "由本单位账号规则分配，工程师填写", "无", "是"],
            ["密码", "登录密码，至少8位，含大小写字母、数字和特殊字符", "工程师自行设定", "无", "是"],
            ["姓名", "显示名称", "工程师本人姓名", "无", "是"],
            [
                "用户类型",
                "工艺规划 / 模具设计 / 硬模成型 / ALL",
                "按岗位选择；可选范围受许可证限制",
                "无",
                "是",
            ],
        ],
        col_widths=[3.4, 4.4, 4.6, 1.6, 2.0],
        title="表2-1  注册页填写项",
    )
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("点击“提交注册（待审批）”，等待管理员在“用户管理”中点击“通过”。", False),
        ],
        indent=False,
    )
    note_box(
        doc,
        "普通用户账户与浏览器一一绑定。首次登录后自动绑定当前浏览器。换电脑或更换浏览器后，需联系管理员解绑。请勿把账号借给他人使用。",
        "注意",
    )

    doc.add_heading("2.4  登录", level=2)
    mixed(doc, [("步骤 1：", True), ("打开登录页，确认页面显示软件名称和版本号。", False)], indent=False)
    field_block(
        doc,
        "账号",
        "已审批通过的数字账号。",
        "管理员分配或自助注册后获批的账号。",
        "无",
        "1001",
        "识别当前操作者并加载对应工作角色。",
    )
    field_block(
        doc,
        "密码",
        "该账号的登录密码。",
        "注册时设定，或后续在“个人信息”中修改。",
        "无",
        "（按本单位密码规则）",
        "完成身份校验。",
        "长度不能少于8位。页面提示需包含大小写字母、数字和特殊字符。",
    )
    mixed(
        doc,
        [("步骤 2：", True), ("点击“登录”。成功后进入工作台；失败时按页面提示检查账号、密码或审批状态。", False)],
        indent=False,
    )
    placeholder(doc, "登录页面", "① 账号  ② 密码  ③ 登录  ④ 注册新账号")

    doc.add_heading("2.5  首页与菜单", level=2)
    P(
        doc,
        "登录后进入“工作台”。页面顶部显示欢迎姓名和许可有效期。左侧为功能菜单，中间为模块入口卡片。菜单内容随工作角色变化。",
    )
    add_table(
        doc,
        ["菜单位置", "页面名称", "作用"],
        [
            ["首页", "工作台", "进入各业务模块"],
            ["上传分析 → 模块介绍", "上传分析介绍", "说明本模块用途"],
            ["上传分析 → 模型文件上传", "管件模型分析", "导入模型、确认形线、保存管件"],
            ["工艺优化 → 模块介绍", "总体工艺规划介绍", "说明工艺优化用途"],
            ["工艺优化 → 工艺参数优化", "工艺参数优化", "加载管件、优化并保存工艺设计"],
            ["普通抽 / TP抽 / Taper", "计算与查找数据", "抽管模具参数计算与历史查询"],
            ["硬模成型", "加载数据及四道工序", "生成扩口、压扁、压弯、成型模型"],
            ["工具 → 模型旋转", "模型旋转工具", "按坐标轴旋转 STP 后下载"],
            ["管理", "用户管理 / 注册用户 / 部署设置", "仅管理员可见"],
            ["个人信息", "个人信息", "查看权限、改密、退出"],
        ],
        col_widths=[4.6, 4.6, 6.8],
        title="表2-2  左侧菜单与对应页面",
    )
    P(
        doc,
        "工作台卡片与菜单一致：有工艺规划权限时显示“上传分析”“工艺优化”；有模具设计权限时显示普通抽、TP抽、Taper；有硬模成型权限时显示硬模入口。硬模卡片上会显示加载数据→扩口→压扁→压弯→成型的步骤状态。",
    )
    placeholder(doc, "工作台首页", "① 许可状态  ② 上传分析  ③ 工艺优化  ④ 模具设计  ⑤ 硬模成型")

    doc.add_heading("2.6  个人信息", level=2)
    P(doc, "从左侧菜单进入“个人信息”，或从工作台进入“用户与设置”。本页用于核对账号状态，不参与工艺计算。")
    mixed(doc, [("步骤 1：", True), ("查看账号、姓名、权限、工作角色、审批状态、浏览器绑定和账户有效期。", False)], indent=False)
    P(
        doc,
        "以上信息无需填写，由软件根据登录账号和服务器许可证自动显示。管理员账户有效期显示为“永久”。普通用户未绑定时，将在首次登录后自动绑定。",
    )
    mixed(doc, [("步骤 2：", True), ("如需改密，展开“修改密码”。", False)], indent=False)
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["旧密码", "当前正在使用的密码", "工程师本人记忆", "无", "是"],
            ["新密码", "拟改用的密码", "工程师自行设定", "无", "是"],
            ["确认新密码", "再次输入新密码", "与新密码一致", "无", "是"],
        ],
        col_widths=[3.2, 4.2, 4.4, 1.6, 2.6],
        title="表2-3  修改密码填写项",
    )
    mixed(doc, [("步骤 3：", True), ("点击“确认修改密码”。", False)], indent=False)
    mixed(doc, [("步骤 4：", True), ("需要结束工作时，点击“退出登录”。", False)], indent=False)

    # ── 3 ──
    doc.add_heading("3  操作前准备", level=1)
    P(doc, "开始分析前，按实际作业准备下列数据。只列出本软件会用到的输入。")
    add_table(
        doc,
        ["数据", "格式", "数据来源", "用途"],
        [
            ["管件三维模型", "STP / STEP（优先），或 STL", "设计部门提供的三维模型", "中心线、周长、壁厚读取"],
            ["管件工程图纸", "PDF 或纸质图纸", "设计部门", "填写壁厚分段、图纸总长、关键截面"],
            ["人工测量形线（可选）", "XLSX", "现场或质检测量后按模板整理", "STP 形线异常时替代自动形线"],
            ["管件标准 DXF（可选）", "DXF", "设计或工艺部门", "不走工艺设计库时的模具计算备用路径"],
            ["车种号 / 管件号 / 设计名称", "文字或编号", "工艺或生产管理系统", "保存工艺设计时命名"],
        ],
        col_widths=[3.6, 3.8, 4.4, 4.2],
        title="表3-1  操作前需准备的数据",
    )
    P(doc, "单位约定：软件内部一律按毫米（mm）使用，不做单位换算。上传前请确认模型单位为 mm。")
    P(doc, "推荐文件：优先使用原始 STP/STEP。网格预览仅用于观察，截面计算在 CAD 曲面上进行。")
    P(
        doc,
        "旧数据库记录如果缺少原始内外轮廓或局部坐标，打开旧记录不会自动修复。需要重新分析原 STP，重新保存管件，并保存新的工艺设计。",
    )

    # ── 4 ──
    doc.add_heading("4  软件操作流程", level=1)
    P(doc, "日常作业按下列主流程进行：")
    P(doc, "文件准备  →  导入并分析模型  →  工程师确认形线  →  保存管件  →  工艺参数优化  →  保存工艺设计  →  模具计算或硬模生成  →  检查结果并导出", indent=False)
    P(doc, "三条后续路径共用同一份工艺设计，不要混用不同管件或不同阶段的记录。")
    add_table(
        doc,
        ["后续路径", "入口", "依赖的上游数据"],
        [
            ["普通抽 / TP抽 / Taper", "模具设计对应菜单", "已保存且工艺方法匹配的工艺设计；也可改用 DXF"],
            ["扩口 / 压扁 / 压弯 / 成型", "硬模成型 → 加载数据", "已保存工艺设计；四道工序必须先加载会话"],
            ["仅查看历史模具参数", "各模块“查找数据”", "此前已保存到模具数据库的记录"],
        ],
        col_widths=[4.6, 5.2, 6.2],
        title="表4-1  工艺设计保存后的后续路径",
    )
    note_box(
        doc,
        "请先完成管件校核再优化。存在待复核截面、缺测截面或未选择形线来源时，优化和模具生成会被拒绝。手工修改工艺周长或壁厚，不会覆盖成型所用的原始截面轮廓。",
        "顺序要求",
    )

    # ── 5.1 ──
    doc.add_heading("5  各功能模块操作说明", level=1)
    doc.add_heading("5.1  管件模型分析", level=2)
    P(
        doc,
        "功能说明：从左侧菜单进入“上传分析 → 模型文件上传”。页面标题为“管件模型分析”。本页读取管件三维模型，生成中心线和截面数据，再由工程师确认壁厚分段、图纸长度、关键截面和形线来源，最后保存到管件数据库。",
    )
    P(
        doc,
        "输入数据：管件 STP/STEP/STL，以及图纸上的壁厚分段和总长。必要时准备人工测量 XLSX。",
    )

    doc.add_heading("5.1.1  进入功能并选择加载方式", level=3)
    mixed(doc, [("步骤 1：", True), ("进入“上传分析 → 模型文件上传”。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("在左侧选择“上传文件”或“数据库加载”。新管件选“上传文件”；已保存记录选“数据库加载”。", False),
        ],
        indent=False,
    )
    add_image(
        doc,
        SHOT / "merge_validation_20260921" / "screenshots" / "production_reading.png",
        "图5-1  管件模型分析页面（上传/加载、3D预览与壁厚分段确认）",
        15.8,
    )
    callout(
        doc,
        [
            "① 选择“上传文件”或“数据库加载”",
            "② 选择算法并上传 STL/STP 文件",
            "③ 查看 3D 预览中的中心线与截面",
            "④ 在“壁厚分段确认”中填写图纸壁厚和长度",
        ],
    )

    doc.add_heading("5.1.2  上传新模型并开始分析", level=3)
    mixed(doc, [("步骤 1：", True), ("选择分析算法。", False)], indent=False)
    field_block(
        doc,
        "分析算法",
        "在“A算法”和“B算法”中选择其一。",
        "工程师根据模型摆放方向判断。",
        "无",
        "默认 A算法",
        "决定如何确定管件主方向后再切截面。",
        "A算法按模型主方向定位；B算法要求管件主方向已大致沿 Z 轴。软件不会在 A 失败时自动改用 B。",
    )
    mixed(doc, [("步骤 2：", True), ("将文件拖到上传框，或点击上传框选择文件。", False)], indent=False)
    field_block(
        doc,
        "模型文件",
        "当前待分析的管件三维模型。",
        "设计部门提供的 STP/STEP，或已网格化的 STL。",
        "无（模型内部按 mm）",
        "24TK_1324-31.stp",
        "软件据此自动解析中心线、周长和壁厚。",
        "支持 .stl / .stp / .step。优先使用原始 STP。",
    )
    mixed(doc, [("步骤 3：", True), ("按需要填写分析参数。一般可保持默认。", False)], indent=False)
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["采样间隔", "沿中心线取截面的间距", "工程师设定，默认 1.0", "mm", "否，有默认值"],
            ["起点舍弃", "从大周长端起舍弃的端部长度", "留空由软件自动识别；也可按图纸或观察填写", "mm", "否"],
            ["终点舍弃", "从另一端舍弃的端部长度", "同上", "mm", "否"],
        ],
        col_widths=[2.8, 4.2, 5.2, 1.6, 2.2],
        title="表5-1  模型分析参数",
    )
    P(
        doc,
        "起点位于大周长端。留空时，软件自动寻找端部连续可靠截面，舍弃不可靠的中心点、周长和壁厚，再线性外推补回，完整长度坐标不缩短。修改舍弃长度后必须重新点击“开始分析”。",
    )
    mixed(
        doc,
        [
            ("步骤 4：", True),
            ("点击“开始分析”。复杂 STP 可能需要数分钟至数十分钟，请保持页面打开。", False),
        ],
        indent=False,
    )
    P(doc, "分析完成后，软件自动显示：")
    bullet(doc, "左侧中心线长度、截面数、周长平均值/最小/最大值、壁厚中位数/平均值/最小最大值。")
    bullet(doc, "截面数据表：序号、累计长度、周长、壁厚、来源（测量 / 外推 / 待复核）。")
    bullet(doc, "右侧 3D 预览，以及壁厚曲线、截面周长曲线。")
    bullet(doc, "如有质量问题，顶部显示“STP 原生截面测量”警告。")
    P(
        doc,
        "上述数值均由软件根据上传模型自动计算，无需人工填写。中心线是法向截面外轮廓面积质心轨迹，不是 CAD 中未提供的设计路径。显示壁厚是截面内外轮廓最短二维距离的统计，坡度很大处不能直接当作三维法向壁厚。",
    )
    P(doc, "用户应检查：")
    bullet(doc, "3D 中心线是否沿管件走向，端部橙色段是否为线性外推。")
    bullet(doc, "截面表中是否出现“待复核”。有待复核或缺测时，不能直接进入工艺优化或模具生成。")
    bullet(doc, "周长、壁厚数量级是否与图纸一致。")
    bullet(doc, "3D 可用“原始网格 / 中心线 / 截面轮廓”开关观察；滚轮滚动页面，Ctrl+滚轮缩放模型。")

    doc.add_heading("5.1.3  从数据库加载已有管件", level=3)
    mixed(doc, [("步骤 1：", True), ("切换到“数据库加载”。", False)], indent=False)
    field_block(
        doc,
        "搜索文件名 / 显示名称",
        "用于筛选历史记录的关键字。",
        "工程师根据保存时的名称输入。",
        "无",
        "24TK_1324",
        "缩小历史记录范围。",
    )
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("按需要选择排序：最新优先、最早优先、名称 A→Z / Z→A、中心线长度升降序。", False),
        ],
        indent=False,
    )
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("点击一条记录加载。可用行内按钮重命名或删除。删除前会提示“此操作不可撤销”。", False),
        ],
        indent=False,
    )
    P(doc, "加载后的图表和确认流程与新分析相同。旧记录不会自动按新版读取规则重算。")

    doc.add_heading("5.1.4  确认壁厚分段与图纸长度", level=3)
    P(
        doc,
        "“开始分析”只完成原始形线和图表。壁厚、图纸总长、长度修正和形线确认必须由工程师继续操作。",
    )
    mixed(doc, [("步骤 1：", True), ("在“壁厚分段确认”中选择壁厚段数：1 段、2 段、3 段或 4 段。默认 2 段。", False)], indent=False)
    field_block(
        doc,
        "壁厚段数",
        "当前管件按图纸划分的常值壁厚段数量。",
        "管件工程图纸中的壁厚分段。",
        "无",
        "2 段",
        "决定需要填写的 T 和 TL/TLM 项数。",
    )
    mixed(doc, [("步骤 2：", True), ("填写各段壁厚 T1～T4。", False)], indent=False)
    field_block(
        doc,
        "T1～T4",
        "各常值壁厚段的图纸壁厚。",
        "管件工程图纸的壁厚标注。软件会用截面壁厚中位数预填，但必须按图纸核对。",
        "mm",
        "2.0",
        "记录工艺分段，供后续长度校验和工艺优化使用。不会覆盖 STP 测得的截面壁厚。",
        "有厚度的段必须同时有长度。至少 T1 和 TL1 需大于 0。",
    )
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("多段时，拖动“壁厚区间”滑块，使分段边界与曲线变化位置大致对应。", False),
        ],
        indent=False,
    )
    mixed(doc, [("步骤 4：", True), ("填写图纸长度。", False)], indent=False)
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["TL1", "第一段常值壁厚长度。单段时即为图纸总长", "工程图纸", "mm", "是"],
            ["TLM1 / TLM2 / TLM3", "相邻壁厚段之间的过渡段长度", "工程图纸过渡区标注", "mm", "有过渡时填写"],
            ["TL2 / TL3 / TL4", "后续常值壁厚段长度", "工程图纸", "mm", "对应段存在时填写"],
        ],
        col_widths=[3.2, 5.0, 3.4, 1.6, 2.8],
        title="表5-2  图纸长度填写项",
    )
    P(
        doc,
        "图纸总长由软件把大于 0 的 TL 与 TLM 相加得到，无需另填总长框。单段时 TL1 就是图纸总长。不要用 STP 中心线长度去填这些框。",
    )
    field_block(
        doc,
        "图纸总长（显示值）",
        "不需人工填写。",
        "软件根据已填 TL/TLM 自动求和。",
        "mm",
        "8840",
        "作为长度校验的基准。",
    )
    mixed(doc, [("步骤 5：", True), ("点击“确认壁厚并校验长度”。", False)], indent=False)
    P(doc, "软件比较图纸总长与 STP 形线弧长，并显示：")
    bullet(doc, "图纸总长、STP读取管件长度、长度差异（STP − 图纸）。")
    bullet(doc, "校验状态：一致 / STP过长 / STP不足 / 待确认截短方向 / 未校验。")
    bullet(doc, "自动判断的小径端：形线起点端或终点端。两端尺寸差不超过 0.50 mm 时，需人工指定“起点端”或“终点端”。")
    P(doc, "该结果由软件自动计算，但小径端和修正分配需要工程师结合图纸确认。")

    doc.add_heading("5.1.5  按分配修正形线", level=3)
    P(doc, "当校验状态为 STP过长或 STP不足时，可把长度差分配到两端。")
    field_block(
        doc,
        "小径端修正",
        "加到小径端（或从小径端截去）的长度。正数为该端补长，负数为该端截短。",
        "工程师根据图纸与 STP 差异判断；不填则默认全部差异由小径端承担。",
        "mm",
        "-12.0",
        "使形线弧长与图纸总长一致。",
    )
    field_block(
        doc,
        "大径端修正",
        "不需填写，只读显示。",
        "软件按长度守恒自动计算。",
        "mm",
        "由差异减去小径端修正得到",
        "保证两端修正之和等于图纸差。",
    )
    mixed(doc, [("步骤：", True), ("填写小径端修正后，点击“按分配修正形线”。", False)], indent=False)
    P(
        doc,
        "修正只改用于工艺的形线拷贝，不改原始截面几何。截短会删除该端长度；补长时，直管端按常值外延，否则按端头约 40 mm 斜率外推。",
    )

    doc.add_heading("5.1.6  关键截面校验", level=3)
    P(doc, "须在长度校验通过（一致、已截短或已补长）后进行。本步记录校验结果，不修改形线。")
    mixed(doc, [("步骤 1：", True), ("填写截面长度、截面宽度，必要时填写预计位置。", False)], indent=False)
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["截面长度", "图纸关键截面的长度方向尺寸", "工程图纸关键截面标注", "mm", "查找时必填"],
            ["截面宽度", "图纸关键截面的宽度方向尺寸", "工程图纸", "mm", "查找时必填"],
            ["预计位置", "该截面沿中心线的预期弧长位置，从当前形线起点 0 起算", "图纸或工艺人员估算", "mm", "否"],
        ],
        col_widths=[2.8, 5.4, 3.8, 1.6, 2.4],
        title="表5-3  关键截面输入",
    )
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("点击“查找可能位置”。软件在长度修正后的截面长宽上搜索，容差 ±1 mm，长宽可对调。", False),
        ],
        indent=False,
    )
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("查看候选表中的轴向范围、实际长宽和偏差。点选一行只保存该段；不点选则保存全部段。点击“保存校验记录”。", False),
        ],
        indent=False,
    )
    mixed(doc, [("步骤 4：", True), ("可点击“新增关键截面”继续下一处。已保存行可用“✕”删除。", False)], indent=False)
    P(doc, "该结果由软件自动识别，需要工程师结合图纸确认位置是否合理。")

    doc.add_heading("5.1.7  形线质量检查", level=3)
    P(
        doc,
        "完成长度校验后，在页面下部“形线质量检查”中处理形线。外径应沿轴向单调不增。软件可能给出局部异常、须人工测量、直管段待确认或通过等状态。",
    )
    mixed(doc, [("步骤 1：", True), ("若出现局部异常，可点击“查看原始数据 / 预览建议平滑”，比较红带可疑段与橙色建议曲线。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("确认建议合理后点击“应用建议平滑”；否则点击“保留原始数据”。保留原始数据若仍不单调，不能作为最终合格形线。", False),
        ],
        indent=False,
    )
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("若必须人工测量，点击“下载 XLSX 模板”，按模板填写后点击“上传人工测量 XLSX”。", False),
        ],
        indent=False,
    )
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["centerline_mm", "中心线累计长度", "人工沿管件测量或按图纸整理", "mm", "是"],
            ["outer_circumference_mm", "外径周长", "人工测量外周长，不要同时填外径", "mm", "是"],
        ],
        col_widths=[3.8, 3.6, 4.8, 1.6, 2.2],
        title="表5-4  人工测量形线模板列",
    )
    P(doc, "软件将同尺寸段按常值连接，渐变段光滑过渡，并统一为大径到小径单调不增。不要同时填写外径列。")
    mixed(
        doc,
        [
            ("步骤 4：", True),
            ("单调通过后，在候选直管段表中对各段点击“确认直管段”或“取消直管段”，再点击“完成本步确认”。已确认直管段长度之和作为 L4。", False),
        ],
        indent=False,
    )
    mixed(
        doc,
        [
            ("步骤 5：", True),
            ("点击“采用自动读取形线”或“采用人工测量形线”。未选择前不能保存管件。", False),
        ],
        indent=False,
    )
    P(doc, "用户应检查：周长曲线是否还有明显凸起或振荡；直管段位置是否与图纸一致；形线来源选择是否与实际采用数据一致。")

    doc.add_heading("5.1.8  保存管件", level=3)
    mixed(doc, [("步骤 1：", True), ("确认已选择形线来源后，点击底部“保存到数据库”。", False)], indent=False)
    field_block(
        doc,
        "记录名称",
        "本次分析在数据库列表中的显示名称。",
        "工程师命名，建议与管件图号一致。",
        "无",
        "H0A2Z3-DT 演示",
        "供工艺优化页识别和加载。",
        "名称不能为空。",
    )
    mixed(doc, [("步骤 2：", True), ("在对话框中填写名称，点击“保存”。", False)], indent=False)
    P(
        doc,
        "保存内容包括分析截面、壁厚分段、长度校验结果、关键截面记录和已选形线。保存后仍可重新分析并覆盖为新记录。随后进入“工艺优化 → 工艺参数优化”。",
    )
    note_box(
        doc,
        "形线 QA 通过不等于三维截面法向已经校核通过。内部待复核或缺测仍会阻止后续自动生成。",
        "注意",
    )

    # ── 5.2 ──
    doc.add_heading("5.2  工艺参数优化", level=2)
    P(
        doc,
        "功能说明：从左侧菜单进入“工艺优化 → 工艺参数优化”。本页从管件数据库加载已分析记录，选择工艺路线，求解素材尺寸和各工步参数，确认后保存为工艺设计。",
    )
    P(
        doc,
        "输入数据：上一步已保存的管件记录。本页不再上传 STP。侧栏中的“模块介绍”仅作说明；当前实际操作页就是本页，不是三步向导。",
    )

    doc.add_heading("5.2.1  加载管件分析记录", level=3)
    mixed(doc, [("步骤 1：", True), ("进入“工艺优化 → 工艺参数优化”。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("在“第一步：从数据库加载分析结果”中搜索、排序并选中记录，点击“加载”。", False),
        ],
        indent=False,
    )
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["搜索文件名 / 显示名称", "筛选管件记录", "保存管件时的名称", "无", "否"],
            ["排序", "最新优先等 6 种", "工程师选择", "无", "否"],
        ],
        col_widths=[4.0, 4.0, 4.0, 1.6, 2.4],
        title="表5-5  工艺优化加载项",
    )
    P(
        doc,
        "列表中的中心线长度、截面数、算法和文件类型由软件从管件库读取。加载后，优化器使用壁厚/周长曲线、壁厚分段、图纸总长或修正后形线弧长、体积、已确认 L4 和形线段长度。",
    )
    P(
        doc,
        "若提示模型有缺测或未通过校核的截面，必须返回管件模型分析页处理，不能在本页绕过。",
    )
    placeholder(doc, "工艺参数优化——加载分析结果", "① 搜索框  ② 记录列表  ③ 加载  ④ 删除")

    doc.add_heading("5.2.2  选择工艺路线", level=3)
    mixed(doc, [("步骤 1：", True), ("查看自动推荐条中的形线长度、外径变化率和推荐原因。该信息由软件根据已加载形线自动计算。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("在“GA 遗传算法”“暴力枚举”卡片中点选一条路线，或在“自由组合”中按普通抽→TP抽→Taper→扩口勾选后点击“确认此组合”。", False),
        ],
        indent=False,
    )
    add_table(
        doc,
        ["显示名称", "适用提示（界面）", "求解方式"],
        [
            ["Taper+TP抽+扩口", "形线长度 ≥ 300mm（次选）", "GA 遗传算法"],
            ["普通抽+TP抽+Taper+扩口", "通用兜底方案", "GA 遗传算法"],
            ["仅TP抽", "形线 < 150mm，变化率 < 30%", "暴力枚举"],
            ["普通抽+TP抽", "形线 < 150mm，变化率 < 30%", "暴力枚举"],
            ["仅扩口", "形线 < 150mm，变化率 < 30%", "直接赋值"],
            ["普通抽+扩口", "形线 < 150mm，变化率 ≥ 30%", "暴力枚举"],
            ["TP抽+扩口", "150mm ≤ 形线 < 300mm", "暴力枚举"],
            ["普通抽+Taper+扩口", "形线长度 ≥ 300mm（优先）", "暴力枚举"],
            ["普通抽+Taper", "特殊定制", "暴力枚举"],
        ],
        col_widths=[5.2, 7.0, 3.8],
        title="表5-6  工艺路线（以计算页卡片为准）",
    )
    P(
        doc,
        "推荐规则由软件按形线段长度（总长 − L4）和外径变化率自动给出，工程师仍可改选。界面另有“加长Taper+TP抽+扩口”卡片，当前与“Taper+TP抽+扩口”共用同一选项，不宜当作独立路线依赖。",
    )
    P(
        doc,
        "仅扩口不搜索素材尺寸：素材外径 D0 直接等于直线段小径 D3，扩口大径等于管件大径 D1，素材壁厚 T0 等于传入壁厚的最大值。素材长度 L0 仍按成品体积加 30% 加工余量计算，扩口长度 L1 为成品长度减去直线段 L4，没有普通抽段。修改后壁厚保持原曲线。形线小于 150mm 且外径变化率小于 30% 时可以选择该路线；此条件下自动推荐仍是仅TP抽。",
    )
    mixed(doc, [("步骤 3：", True), ("确认底部已显示“已选择：…”。需要重选时点击“重置”。", False)], indent=False)

    doc.add_heading("5.2.3  设置优化参数并运行", level=3)
    P(doc, "选中路线后出现“[路线名]工艺优化模式”。")
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["外径参数(D)精度", "结果中外径小数位数，0～3，默认 1", "工程师设定", "无", "否"],
            ["壁厚参数(T)精度", "壁厚小数位数，默认 1", "工程师设定", "无", "否"],
            ["长度参数(L)精度", "长度小数位数，默认 0", "工程师设定", "无", "否"],
            ["因封口添加长度1", "GA 路线素材长度附加量，默认 10", "工艺经验", "mm", "GA 路线显示"],
            ["因taper添加长度2", "GA 路线 Taper 附加量，默认 10", "工艺经验", "mm", "GA 路线显示"],
            ["扩口段补偿长度3", "部分枚举路线扩口补偿，默认 0", "工艺经验", "mm", "对应路线显示"],
            ["优化目标权重", "扩口率、截面积变化率、Taper 延伸率等权重，总和保持默认合计", "工程师调节，默认按路线给出", "%", "子目标不少于 2 项时显示"],
        ],
        col_widths=[3.6, 5.0, 3.4, 1.6, 2.4],
        title="表5-7  优化前可调参数",
    )
    P(
        doc,
        "权重可锁定单项后调节其余项。扩口率理想小于 80%，抽制截面积变化率理想约 20%～50%，Taper 外延伸率理想小于 30%。这些是软件适应度所用的约束提示，不是现场必须达到的唯一标准。",
    )
    mixed(doc, [("步骤：", True), ("点击“开始优化工艺参数”，等待进度结束。", False)], indent=False)
    P(
        doc,
        "软件将按路线自动求解素材外径 D0、壁厚 T0、长度 L0 及各工步尺寸。长管多变量走遗传搜索，短管少变量走整数枚举。若提示无可行解，可检查形线和路线；部分情况下页面提供“选择补充方案”。",
    )

    doc.add_heading("5.2.4  检查优化结果", level=3)
    P(doc, "结果区显示可行或未达阈值的标签。遗传算法可行阈值显示为 0.74，枚举显示为 1.00。")
    bullet(doc, "在方案下拉框中切换方案，查看工艺优化度、截面积变化率、延伸率。")
    bullet(doc, "阅读“工艺参数详情”各段数值和评估提示。")
    bullet(doc, "查看适应度变化曲线（遗传算法时较有意义）。")
    bullet(doc, "核“壁厚数据表格”：中心线长度和外径周长来自已加载管件；“原壁厚”来自分析曲线；“修改后壁厚”由软件按当前方案重算，可直接改。")
    bullet(doc, "需要时打开“启用验算”，查看修改后壁厚截面积变化率是否大致落在 20%～50%。")
    bullet(doc, "核“总参数表（All_data）”。表中数值由软件计算，均可编辑；保存时以该表最终值为准。")
    mixed(
        doc,
        [
            ("如需改参：", True),
            ("点击“编辑参数”，修改允许项（常见为 D0、T0，部分路线还可改 D2），点击“应用更改”。软件只重算派生量和评估，不再重新搜索。", False),
        ],
        indent=False,
    )
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位"],
        [
            ["D0", "素材外径", "软件优化，可改", "mm"],
            ["T0", "素材壁厚", "软件优化，可改", "mm"],
            ["L0", "素材长度", "软件按体积和余量计算", "mm"],
            ["Dn", "普通抽外径（扩口段小径、TP抽段大径）", "软件计算", "mm"],
            ["D1", "扩口段大径", "软件计算", "mm"],
            ["D2", "TP抽段小径 / Taper 大径", "软件计算，部分路线可改", "mm"],
            ["D3", "Taper 小径 / 直线段直径", "软件计算", "mm"],
            ["T1～T4", "各段壁厚", "由修改后壁厚曲线再识别", "mm"],
            ["TL1～TL5", "各段壁厚对应长度", "软件按曲线分段", "mm"],
            ["TLM1～TLM4", "过渡段长度", "软件按曲线分段", "mm"],
            ["LL", "总长度", "来自已确认管件长度", "mm"],
            ["L1", "扩口后保留长度", "软件计算", "mm"],
            ["L2", "TP抽长度", "软件计算", "mm"],
            ["L3", "Taper长度", "软件计算", "mm"],
            ["L4", "直线段长度", "形线 QA 中已确认直管段", "mm"],
            ["Ln", "普通抽长度", "软件计算", "mm"],
            ["LL1", "TP抽大径端直管长度", "软件计算", "mm"],
            ["BL / B_D", "弯管长度 / 弯管直径", "来自管件分析或方案派生", "mm"],
            ["D20", "补充方案2的 TP 抽段直径", "仅补充方案场景", "mm"],
        ],
        col_widths=[2.6, 6.4, 5.0, 2.0],
        title="表5-8  总参数表（All_data）含义",
    )
    P(doc, "用户应检查：D0/T0 是否在本厂素材规格内；各工步长度之和是否与图纸总长相符；评估提示是否出现明显超限。")

    doc.add_heading("5.2.5  保存工艺设计", level=3)
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["车种号", "所属车种规格编号", "工艺或生产管理系统", "无", "是"],
            ["管件号", "该管件编号", "图纸或生产编号", "无", "是"],
            ["设计名称", "本条工艺设计的显示名称", "工程师命名", "无", "是"],
            ["是否将 Taper 段壁厚处理成过渡段", "开：Taper 壁厚按过渡处理；关：按直径比缩放", "工程师按工艺习惯选择，默认关", "无", "否"],
        ],
        col_widths=[4.4, 4.2, 3.4, 1.6, 2.4],
        title="表5-9  保存工艺设计填写项",
    )
    mixed(
        doc,
        [
            ("步骤：", True),
            ("填写三项名称后，点击“保存设计到工艺设计数据库”。可用“刷新表格”重新取预览。", False),
        ],
        indent=False,
    )
    P(
        doc,
        "保存后，普通抽、TP抽、Taper 和硬模成型都可以加载这条设计。保存后如需修改，应重新优化或改参后再保存为新记录，避免后续模块继续使用旧参数。",
    )

    # ── 5.3 ──
    doc.add_heading("5.3  普通抽模具计算", level=2)
    P(
        doc,
        "功能说明：从“普通抽 → 普通抽计算”进入，页面标题为“Normal 模具参数计算”。根据工艺设计或 DXF 计算选定机台的模具参数，并保存到模具数据库或导出压缩包。",
    )
    P(doc, "输入数据：优先使用含“普通抽”的工艺设计；备用路径为管件标准 DXF。")
    note_box(
        doc,
        "模块介绍页仍按“先导入 DXF”叙述。当前计算页默认数据来源是“来自工艺设计数据库”，介绍页内容滞后，操作请以本页为准。",
        "界面说明",
    )

    doc.add_heading("5.3.1  加载管件", level=3)
    mixed(doc, [("步骤 1：", True), ("进入“普通抽 → 普通抽计算”。", False)], indent=False)
    mixed(doc, [("步骤 2：", True), ("选择数据来源。", False)], indent=False)
    field_block(
        doc,
        "数据来源",
        "“来自工艺设计数据库”或“来自DXF文件”。",
        "工程师选择。推荐使用已保存工艺设计。",
        "无",
        "来自工艺设计数据库",
        "决定后续加载方式。",
    )
    field_block(
        doc,
        "工艺设计记录",
        "下拉选择一条工艺设计。",
        "上一步保存在工艺设计数据库中、工艺方法含“普通抽”的记录。",
        "无",
        "显示名称 | 普通抽+TP抽 | 车种号",
        "自动带入管件参数。",
    )
    field_block(
        doc,
        "管件DXF文件",
        "仅在 DXF 路径下选择标准 DXF。",
        "设计或工艺部门提供的管件标准图纸文件。",
        "无",
        "管件.dxf",
        "由软件解析管件尺寸。",
        "选择文件后仍须点击“加载管件”。",
    )
    mixed(doc, [("步骤 3：", True), ("点击“加载管件”。", False)], indent=False)
    P(doc, "软件显示“管件参数”表。数值来自工艺设计映射或 DXF 解析，可在表中修改后再计算。")

    doc.add_heading("5.3.2  选择机台和模具", level=3)
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["机台类型", "DC0124 / DC0121 / DC0125，默认 DC0124", "按现场实际机台选择", "无", "是"],
            ["额外壁厚", "开关，默认开。开时使用带成型模的模具清单", "按是否需要成型类模具判断", "无", "否"],
            ["模具选择", "多选，切换机台后默认全选当前清单", "工程师按本工序需要勾选", "无", "至少计算所需项"],
        ],
        col_widths=[2.8, 5.6, 4.0, 1.4, 2.2],
        title="表5-10  普通抽机台与模具",
    )
    P(doc, "额外壁厚关闭时，常见模具为裁剪夹模、缩管模、抽管芯轴、抽管退料模（DC0121 含裁剪夹模1/2）。开启后增加成型模、成型芯轴、成型退料模。")
    mixed(doc, [("步骤：", True), ("确认管件参数后点击“计算”。", False)], indent=False)

    doc.add_heading("5.3.3  检查、保存与导出", level=3)
    P(doc, "计算结果按模具分页，每页为参数、值、描述。值可改，修改后软件按公式回写。")
    mixed(doc, [("保存：", True), ("点击“保存到模具数据库”。无二次确认框。", False)], indent=False)
    mixed(doc, [("导出：", True), ("点击“导出ZIP”，下载 mold_export.zip，供后续出图或存档。", False)], indent=False)
    P(doc, "历史记录在“普通抽 → 查找数据”中按机台和模具类型查询，并可下载 CSV。")
    placeholder(doc, "普通抽计算页", "① 数据来源  ② 工艺设计/DXF  ③ 机台与模具  ④ 计算  ⑤ 保存/导出")

    # ── 5.4 ──
    doc.add_heading("5.4  TP抽模具计算", level=2)
    P(
        doc,
        "功能说明：从“TP抽 → TP抽计算”进入，页面标题为“TP 模具参数计算”。用于 DC0128 机台模具参数计算，并可根据型线生成抽套模三维模型。",
    )
    P(doc, "输入数据：工艺方法含“TP抽”的工艺设计；或 DXF + 用于提形线的 STL。")

    doc.add_heading("5.4.1  加载并确认型线", level=3)
    mixed(doc, [("步骤 1：", True), ("数据来源默认“来自工艺设计数据库”，选择记录后点击“加载管件”。", False)], indent=False)
    P(doc, "机台类型固定显示 DC0128，不能更改。")
    P(
        doc,
        "工艺设计路径下，软件自动生成型线。DXF 路径须再上传 STL，点击“上传STL并分析形线”。",
    )
    P(doc, "型线参数由软件自动显示，无需人工填写：")
    bullet(doc, "TP抽起点长度、TP抽终点长度。")
    bullet(doc, "TP段长度（T_L）及其来源：自动检测 / 数据范围 / 工艺参数。")
    bullet(doc, "大端直径、小端直径。")
    bullet(doc, "型线表：X 为轴向坐标，Y 为直径（不是半径）。")
    note_box(
        doc,
        "请核对本条记录确为 TP 工艺阶段。不要用其他工序参数代替。若提示大小径不在测量型线范围内，或段长与 T_L 不一致，应停下来核对，不要继续用错误型线。",
        "必须检查",
    )

    doc.add_heading("5.4.2  生成套模与计算模具", level=3)
    field_block(
        doc,
        "干涉长度",
        "套模相关干涉长度。",
        "工程师按工艺设定，默认 50。",
        "mm",
        "50",
        "参与后续模具参数计算。",
    )
    P(doc, "模具清单为：裁剪夹模1、裁剪夹模2、抽管退料模、抽管芯轴、抽套模。可按需要勾选。")
    mixed(doc, [("步骤 1：", True), ("在“3D套模预览”中点击“生成套模”。", False)], indent=False)
    add_image(
        doc,
        SHOT / "merge_validation_20260921" / "screenshots" / "production_tp.png",
        "图5-2  TP 抽套模三维预览与下载",
        14.5,
    )
    callout(doc, ["① 点击“生成套模”", "② 旋转查看套模外形", "③ 下载 STEP 或 STL"])
    P(
        doc,
        "软件按两个端点的轴向范围裁取真实型线，保留段内局部起伏，再与固定模体外形闭合并旋转 360°。当前沿用外形为：主体外径 95 mm、法兰外径 125 mm、法兰厚度 8 mm、总长 T_L + 27 mm，型线小端起点约在 Z=9 mm。型腔任意位置直径必须小于 95 mm，否则应改用其他外形模板。",
    )
    mixed(doc, [("步骤 2：", True), ("点击“计算”，核对各模具参数后，“保存到模具数据库”或“导出ZIP”（文件名 tp_mold_export.zip）。", False)], indent=False)
    mixed(doc, [("步骤 3：", True), ("可下载 TPDrawMold.step / TPDrawMold.stl。", False)], indent=False)
    P(doc, "查找历史数据：进入“TP抽 → 查找数据”，模具类型包括 ADIE、ADIE_、ADBT、ADBT_、AD01、AD01_、AD04。")

    # ── 5.5 ──
    doc.add_heading("5.5  Taper模具计算", level=2)
    P(
        doc,
        "功能说明：从“Taper → Taper计算”进入，页面标题为“Taper 锥度模具参数计算”。用于 EC0120 / EC0121 机台锥度模具参数计算。",
    )
    P(doc, "输入数据：工艺方法含“Taper”的工艺设计；或 DXF + STL 形线。")
    mixed(doc, [("步骤 1：", True), ("选择数据来源并“加载管件”。", False)], indent=False)
    field_block(
        doc,
        "机台类型",
        "EC0120 或 EC0121，默认 EC0120。",
        "按现场机台选择。",
        "无",
        "EC0120",
        "决定模具清单。",
        "当参数 TL 大于 480 mm 时，页面提示请选择 EC0121 机床。",
    )
    P(doc, "型线参数由软件显示 m_D 长度、B_D 长度和 X/Y 表。DXF 路径同样需要先完成 STL 形线。")
    field_block(
        doc,
        "TP过渡形线长度",
        "Taper 与前序 TP 之间的过渡形线长度。",
        "工程师设定；默认 25。从工艺设计加载时，软件可能带入建议值。",
        "mm",
        "25",
        "参与锥度模具型线过渡计算。",
        "允许范围 0～200 mm。",
    )
    P(doc, "EC0120 模具清单：Taper模两瓣、定位杆、夹模、后定位模、内定位模、拉杆模。EC0121：Taper模四瓣、定位杆、夹模。")
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("确认型线后点击“计算”，检查各模具参数，再“保存到模具数据库”或“导出ZIP”（文件名 taper_mold_export.zip）。", False),
        ],
        indent=False,
    )
    P(doc, "查找数据页可按机台和模具类型 B000、C000、J000、E000、I000、H000 查询并下载 CSV。")
    placeholder(doc, "Taper 计算页", "① 数据来源  ② 机台  ③ TP过渡形线长度  ④ 计算  ⑤ 保存/导出")

    # ── 5.6 ──
    doc.add_heading("5.6  硬模成型", level=2)
    P(
        doc,
        "功能说明：硬模成型包含加载数据、扩口、压扁、压弯、成型。未加载工艺设计时，后四项菜单不可用。四个工序都可选择是否生成反切实体模具。",
    )
    P(doc, "输入数据：已保存的工艺设计，以及该设计关联的原始管件截面。")

    doc.add_heading("5.6.1  加载数据", level=3)
    mixed(doc, [("步骤 1：", True), ("进入“硬模成型 → 加载数据”。未加载时页面提示“请先从工艺设计数据库加载”。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("在“工艺设计数据库”中按工艺名称、车种规格或管件号搜索，选择排序后点击该行“加载”。", False),
        ],
        indent=False,
    )
    P(
        doc,
        "加载成功后显示工艺名称、车种规格、抽管方式和中心线长度。这些信息由软件从工艺设计库读取，无需填写。随后可进入扩口、压扁、压弯或成型。",
    )
    placeholder(doc, "硬模成型——加载数据", "① 搜索  ② 工艺设计列表  ③ 加载  ④ 已加载状态条")

    doc.add_heading("5.6.2  各工序共用界面", level=3)
    P(doc, "进入任一工序后，页面顶部显示已加载工艺设计和下列只读参数，均来自工艺设计，无需填写：")
    add_table(
        doc,
        ["显示项", "含义", "数据来源", "单位"],
        [
            ["L1 (扩口段长度)", "扩口后保留长度", "工艺设计", "mm"],
            ["T3 (壁厚)", "对应壁厚段", "工艺设计", "mm"],
            ["D1 (扩口段大径)", "扩口大端直径", "工艺设计", "mm"],
            ["D2 (TP抽段小径)", "TP 小端直径", "工艺设计", "mm"],
            ["D3 (直线段直径)", "Taper 小径 / 直线段直径", "工艺设计", "mm"],
            ["L4 (直线段长度)", "已确认直管段长度", "工艺设计 / 形线确认", "mm"],
        ],
        col_widths=[4.2, 4.4, 4.6, 2.8],
        title="表5-11  硬模页面只读工艺参数",
    )
    P(doc, "生成过程中请勿关闭页面。复杂管件可能需要数分钟。参数修改后，必须重新生成才能保存或下载。")

    doc.add_heading("5.6.3  扩口", level=3)
    P(doc, "功能说明：按等周长圆截面沿直线轴布置，生成扩口芯轴和扩口腔体。")
    mixed(doc, [("步骤 1：", True), ("进入“硬模成型 → 扩口”。", False)], indent=False)
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["扩口段长度", "从大端 s=0 起的扩口长度", "默认取 min(L1, 中心线长度)；可按图纸改", "mm", "是"],
            ["芯轴延长余量", "芯轴在扩口段之外再加的长度，默认 0", "工艺留量，工程师填写", "mm", "否"],
            ["芯轴壁厚来源", "沿用前序数据表 / 指定统一壁厚", "工程师选择；有 T3 时默认指定统一壁厚", "无", "是"],
            ["壁厚", "指定统一壁厚时的芯轴壁厚，默认 T3 或 2.0", "图纸或工艺设计 T3", "mm", "指定统一壁厚时必填"],
        ],
        col_widths=[3.2, 4.8, 4.2, 1.6, 2.2],
        title="表5-12  扩口操作参数",
    )
    P(
        doc,
        "芯轴为正圆。芯轴直径 = 外径周长 ÷ π − 2×壁厚。芯轴长度 = 扩口段长度 + 延长余量。外径型腔沿用完整管件型线。关闭反切时输出扩口芯轴和扩口腔体；开启反切时另生成外部模具，芯轴和型腔仍可分别查看、下载。",
    )
    mixed(doc, [("步骤 2：", True), ("按需要设置反切选项（见 5.6.7），点击“生成芯轴与型腔”。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("在“扩口芯轴 / 扩口型腔（全长） / 外部反切模具”页签中检查首末端直径和长度，确认后点击“保存”或下载对应 STEP。", False),
        ],
        indent=False,
    )
    P(doc, "用户应检查：扩口段长度是否与工艺 L1 相符；芯轴壁厚是否按前序数据还是统一值；端部余量是否足够。")

    doc.add_heading("5.6.4  压扁", level=3)
    P(doc, "功能说明：按已确认椭圆轴长和周长，沿直中心线生成压扁模型。轴长是完整轴长，例如 30×20 mm 对应半轴 15 和 10 mm。")
    mixed(doc, [("步骤 1：", True), ("进入“硬模成型 → 压扁”。", False)], indent=False)
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["压扁长度", "压扁段长度，默认 L1 或 200", "工艺设计或图纸", "mm", "是"],
            ["整管长度", "压扁后模型总长；不填则按数据范围", "默认中心线长度，不能超出有效测量长度", "mm", "否"],
            ["压扁余量", "作用于短轴全长后再按周长求另一轴，默认 0", "工艺留量", "mm", "否"],
            ["过渡段长度", "由压扁截面逐渐恢复圆形的长度，默认 150", "工艺设计或经验", "mm", "否"],
            ["截面数量（轴向）", "生成用轴向站位数，默认 200", "软件计算密度，可改", "无", "否"],
            ["椭圆 X 全轴长", "压扁截面 X 向完整轴长", "两项留空则用前序逐截面轴长；同时填写则整段统一", "mm", "两项同填或同空"],
            ["椭圆 Y 全轴长", "压扁截面 Y 向完整轴长", "同上", "mm", "两项同填或同空"],
        ],
        col_widths=[3.4, 4.8, 4.2, 1.6, 2.0],
        title="表5-13  压扁操作参数",
    )
    P(
        doc,
        "软件会校验椭圆周长与前序周长是否相容。不相容或缺少已确认轴长时会报错，不会用猜测值代替。压扁段加过渡段不能超过总长。过渡段在恢复圆形的同时保持各站位周长。",
    )
    mixed(doc, [("步骤 2：", True), ("设置反切选项后点击“生成模型”，检查预览再保存或下载 STEP。", False)], indent=False)
    note_box(
        doc,
        "部分旧记录中的轴长可能来自包围盒，并不等于已确认椭圆轴长。新版原始截面也不会自动伪造这两项。缺少轴长时必须由工程师提供已确认值。",
        "数据限制",
    )

    doc.add_heading("5.6.5  压弯", level=3)
    P(
        doc,
        "功能说明：把圆截面或上一道压扁的同一组截面布置到读取的中心线上。本页不是手工输入弯曲角度的工具，目标中心线来自导入管件。",
    )
    mixed(doc, [("步骤 1：", True), ("进入“硬模成型 → 压弯”。", False)], indent=False)
    field_block(
        doc,
        "先执行压扁工序",
        "开关。开启后复用压扁截面计算，只把截面放到中心线上；关闭则使用等周长圆截面。",
        "若同一工艺设计下刚成功生成过压扁，软件会自动打开并带入压扁段、过渡段、余量和轴长。",
        "无",
        "开启（继承压扁时）",
        "保证压弯截面与前一道压扁一致。",
        "这些配置保存在当前浏览器会话中。",
    )
    P(doc, "开启后还需确认压扁段长度、过渡段长度、压扁余量、椭圆轴长和截面数量。参数须与前一道压扁相同。")
    mixed(doc, [("步骤 2：", True), ("设置反切选项后点击“生成模型”，检查中心线走向和截面后再保存或下载。", False)], indent=False)

    doc.add_heading("5.6.6  成型", level=3)
    P(
        doc,
        "功能说明：直接使用原始外轮廓、内轮廓、轮廓位置和局部坐标系生成模型。本页没有额外数值输入。",
    )
    mixed(doc, [("步骤 1：", True), ("进入“硬模成型 → 成型”。确认已加载的工艺名称和来源管件无误。", False)], indent=False)
    mixed(doc, [("步骤 2：", True), ("按需要设置反切，点击“生成模型”。", False)], indent=False)
    add_image(
        doc,
        SHOT / "merge_validation_20260921" / "screenshots" / "production_forming.png",
        "图5-3  成型页：已加载工艺、操作参数与外径形腔预览",
        15.6,
    )
    callout(
        doc,
        [
            "① 确认已加载工艺设计",
            "② 生成反切实体模具开关",
            "③ 点击“生成模型”",
            "④ 在“外径形腔 / 实心内径”中检查结果",
        ],
    )
    P(doc, "关闭反切时输出：")
    bullet(doc, "外径形腔：原始外轮廓围成的实心包络体，可用于 CAD 布尔减。")
    bullet(doc, "实心内径：由原始内轮廓生成，保留原始偏心和变壁厚。")
    P(doc, "成型不再从 STL 二次切片，也不会用单个壁厚从外轮廓反推内轮廓。")
    P(doc, "用户应检查：来源管件是否正确；质量警告是否仍存在；有效长度是否覆盖需要成型的区段。")

    doc.add_heading("5.6.7  生成反切实体模具", level=3)
    P(doc, "四个工序共用下列选项，默认关闭。")
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["生成反切实体模具", "关闭保留原模型；开启后用外径腔体反切长方体", "工程师选择，默认关", "无", "否"],
            ["自动推荐外形尺寸", "按腔体包围盒加双侧余量取整到 0.1 mm", "软件计算，默认开", "无", "反切开时"],
            ["单侧余量", "模体每侧超出腔体的留量，默认 15，范围 1～500", "工艺留量", "mm", "反切开时"],
            ["模体 X/Y/Z 尺寸", "关闭自动推荐后的自定义外形", "工程师按推荐值修改；每侧至少再留 1 mm", "mm", "手动尺寸时必填"],
            ["剖开查看内部", "仅改变预览，不影响下载的 STEP", "工程师查看用，默认开", "无", "否"],
        ],
        col_widths=[3.6, 5.2, 3.6, 1.6, 2.0],
        title="表5-14  反切模具选项",
    )
    mixed(doc, [("步骤 1：", True), ("打开“生成反切实体模具”，保留“自动推荐外形尺寸”，确认单侧余量。", False)], indent=False)
    mixed(doc, [("步骤 2：", True), ("点击“生成模型”或“生成芯轴与型腔”。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("查看“模体 XYZ”和“最小单侧余量”。若要改尺寸，关闭自动推荐，修改 X/Y/Z 后重新生成。", False),
        ],
        indent=False,
    )
    add_image(
        doc,
        SHOT / "mold_validation_20260921" / "screenshots" / "real_shaping_mold.png",
        "图5-4  反切实体模具预览（示例：模体约 640.7×93.6×79.1 mm，单侧余量 15 mm）",
        14.8,
    )
    callout(
        doc,
        [
            "① 查看模体 XYZ 与最小单侧余量",
            "② 打开“剖开查看内部”检查型腔",
            "③ 点击“保存”或“下载反切模具 STEP”",
        ],
    )
    note_box(
        doc,
        "当前反切输出是带封闭型腔的整体模坯，尚未自动分模，也不含定位销、紧固孔、排气、脱模斜度或回弹补偿。推荐尺寸只表示几何包容与留量，未计算模具强度。后续制造设计仍应按具体机台完成。",
        "范围说明",
    )

    # ── 5.7 ──
    doc.add_heading("5.7  模型旋转工具", level=2)
    P(
        doc,
        "功能说明：从“工具 → 模型旋转”进入。用于把 STP/STEP 按坐标轴旋转后重新下载。本工具不依赖硬模会话，也不改写管件数据库。",
    )
    mixed(doc, [("步骤 1：", True), ("拖拽或选择 STP/STEP 文件，点击“上传模型”。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("在 X/Y/Z 三列中点击“顺时针 90°”“逆时针 90°”或“旋转 180°”。角度遵循右手系；顺时针/逆时针按“从 +轴 方向朝向原点看”定义。", False),
        ],
        indent=False,
    )
    mixed(
        doc,
        [
            ("步骤 3：", True),
            ("在预览中确认姿态。可“重置旋转”，或点击“下载旋转后模型（STP/STEP）”。", False),
        ],
        indent=False,
    )
    P(doc, "当 B 算法要求模型主方向沿 Z 轴时，可先在本工具中把管件转到 Z 向，再回到管件模型分析页重新上传。")
    placeholder(doc, "模型旋转工具", "① 上传 STP  ② 三轴旋转按钮  ③ 预览  ④ 下载")

    # ── 5.8 ──
    doc.add_heading("5.8  管理功能（仅管理员）", level=2)
    P(doc, "左侧“管理”菜单仅管理员可见，用于账号维护和部署授权，不参与工艺计算。")

    doc.add_heading("5.8.1  用户管理", level=3)
    mixed(doc, [("步骤 1：", True), ("进入“管理 → 用户管理”。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 2：", True),
            ("对待审批用户点击“通过”或“拒绝”。自助注册用户必须通过后才能登录。", False),
        ],
        indent=False,
    )
    mixed(doc, [("步骤 3：", True), ("需要换机登录时，点击“解绑设备”。解绑后用户可在新浏览器登录并重新绑定。", False)], indent=False)
    mixed(
        doc,
        [
            ("步骤 4：", True),
            ("点击“编辑”可改姓名、工作角色、权限、账户有效期；新密码留空表示不修改。管理员账户永久有效。普通用户有效期不得超过服务器许可证到期日。", False),
        ],
        indent=False,
    )
    P(doc, "删除用户前会要求确认。")

    doc.add_heading("5.8.2  注册用户", level=3)
    P(doc, "进入“管理 → 注册用户”。管理员代注册的用户自动审批通过，首次登录仍会绑定当时所用浏览器。")
    add_table(
        doc,
        ["参数", "含义", "数据来源", "单位", "是否必填"],
        [
            ["账号", "数字账号，最小值 1", "本单位账号规则", "无", "是"],
            ["姓名", "用户姓名", "人事或现场提供", "无", "是"],
            ["密码 / 确认密码", "至少 8 位", "管理员设定后告知本人", "无", "是"],
            ["工作角色", "工艺规划 / 模具设计 / 硬模成型 / ALL", "按岗位；受许可证允许范围限制", "无", "是"],
            ["权限", "user 或 admin", "管理员指定", "无", "是"],
            ["账户有效期", "日期或勾选永久；留空则默认与服务器许可证一致", "许可证与管理制度", "无", "否"],
        ],
        col_widths=[3.2, 5.0, 4.2, 1.4, 2.2],
        title="表5-15  管理员代注册填写项",
    )
    mixed(doc, [("步骤：", True), ("填写后点击“注册”。许可证无效时按钮不可用。", False)], indent=False)

    doc.add_heading("5.8.3  部署设置", level=3)
    P(doc, "进入“管理 → 部署设置”，用于查看本机机器码、安装授权、查看访问地址和配置 IP 白名单。")
    bullet(doc, "本机机器码：软件自动生成，可复制后发给供应商。")
    bullet(doc, "软件授权：粘贴 GT- 开头授权码并“安装授权码”，或上传 license.dat 后“安装许可证”。")
    bullet(doc, "内网访问：显示当前端口、本机 URL 和检测到的局域网地址，可复制发给同网用户。")
    field_block(
        doc,
        "启用 IP 白名单 / 允许访问的网段",
        "开关打开后，仅允许所列 CIDR 网段访问。多个网段用逗号分隔。",
        "本单位网络规划，由管理员填写。",
        "无",
        "192.168.1.0/24",
        "限制非授权网段访问。",
    )
    mixed(doc, [("步骤：", True), ("修改网络配置后点击“保存网络配置”。", False)], indent=False)
    placeholder(doc, "部署设置页", "① 机器码  ② 安装授权码  ③ 访问地址  ④ IP 白名单")

    # ── 6 ──
    doc.add_heading("6  结果保存与导出", level=1)
    add_table(
        doc,
        ["操作位置", "保存或导出内容", "格式 / 位置", "后续用途"],
        [
            ["管件模型分析 → 保存到数据库", "截面、壁厚分段、形线来源、校验记录", "管件数据库，按记录名称列出", "工艺优化加载"],
            ["工艺参数优化 → 保存设计到工艺设计数据库", "工艺路线、All_data、修改后壁厚", "工艺设计数据库", "模具计算与硬模加载"],
            ["普通抽 / TP / Taper → 保存到模具数据库", "各模具参数", "对应模具数据库", "查找数据页查询"],
            ["普通抽 / TP / Taper → 导出ZIP", "模具参数及相关图纸文件", "浏览器下载 zip", "出图、存档、交接"],
            ["TP → 下载 STEP/STL", "抽套模三维模型", "TPDrawMold.step / .stl", "导入 CAD"],
            ["硬模各工序 → 保存", "当前生成的模型会话结果", "软件内部保存", "本模块后续查看"],
            ["硬模各工序 → 下载 STEP", "芯轴、型腔、外径形腔、实心内径或反切模坯", "浏览器下载 .step", "导入 CAD 继续设计"],
            ["查找数据 → 下载CSV", "历史模具参数表", "CSV（UTF-8）", "核对与存档"],
            ["模型旋转 → 下载", "旋转后的三维模型", "STP/STEP", "调整姿态后再分析"],
        ],
        col_widths=[4.6, 4.4, 3.6, 3.4],
        title="表6-1  可保存与可导出的结果",
    )
    P(doc, "下载文件默认进入浏览器下载目录。导出 ZIP 和 STEP 前请确认已重新生成，避免把旧参数下的模型当作新结果。")
    P(doc, "保存到数据库的记录可以再次加载和修改后另存。删除管件或工艺设计不可撤销，删除前应确认没有下游作业仍在使用。")
    P(
        doc,
        "硬模“保存”写入当前会话对应的存储；具体库表命名以现场部署为准。下载的 STEP 为完整 CAD 实体。剖视只改变屏幕显示。",
    )

    # ── 7 ──
    doc.add_heading("7  常见问题", level=1)

    def qa(title, cause, handle):
        doc.add_heading(title, level=3)
        mixed(doc, [("可能原因：", True), (cause, False)], indent=False, space_after=2)
        mixed(doc, [("处理方法：", True), (handle, False)], indent=False, space_after=8)

    qa(
        "7.1  打开软件后一直停在激活页",
        "本机尚未授权，或无法连接本机服务。",
        "确认软件已启动；复制机器码向供应商获取授权码后激活。若提示无法连接服务，先用“停止软件”再重新启动。",
    )
    qa(
        "7.2  登录失败或提示待审批",
        "账号未审批、密码不符、许可证无效，或普通用户未在已绑定浏览器登录。",
        "联系管理员在“用户管理”中通过申请或解绑设备。核对接账号是否为数字账号，密码是否满足复杂度。",
    )
    qa(
        "7.3  左侧看不到某个模块",
        "当前工作角色不包含该功能。",
        "在“个人信息”中查看工作角色。工艺规划才能使用上传分析和工艺优化；模具设计才能使用普通抽/TP/Taper；硬模成型才能使用四道工序。需要跨模块时由管理员改为 ALL。",
    )
    qa(
        "7.4  文件无法上传或选了文件没有反应",
        "扩展名不在允许范围内，或尚未点击对应的加载/分析按钮。",
        "模型分析仅接受 .stl / .stp / .step；模具 DXF 路径仅接受 .dxf；人工形线仅接受 .xlsx。选文件后必须再点“开始分析”或“加载管件”。",
    )
    qa(
        "7.5  模型解析很慢或失败",
        "STP 较复杂，原生截面计算耗时；或模型损坏、单位不是 mm、B 算法方向未沿 Z。",
        "等待进度结束，不要关闭页面。失败后改用 A 算法，或先在“模型旋转”中把主方向转到 Z 再分析。确认模型单位为 mm。",
    )
    qa(
        "7.6  提示待复核截面或缺测",
        "部分法向截面未形成完整内外双闭环，或质量检查未通过。",
        "回到管件模型分析查看截面表“来源”列和警告。调整起点/终点舍弃后重新分析。不要试图用放大模体或跳过 QA 来绕过。",
    )
    qa(
        "7.7  图纸总长与软件长度不一致",
        "把 STP 中心线长度填进了图纸长度，或漏填过渡段；或端部外推后完整坐标与旧版“有效测量长”口径不同。",
        "TL/TLM 必须按图纸填写。单段时 TL1 即为图纸总长。用“确认壁厚并校验长度”查看差异，再分配小径端/大径端修正。",
    )
    qa(
        "7.8  无法保存管件",
        "尚未在形线质量检查中选择“采用自动读取形线”或“采用人工测量形线”。",
        "先完成长度校验和形线确认，再保存。记录名称不能为空。",
    )
    qa(
        "7.9  工艺优化提示请先复核截面",
        "加载的管件记录未通过校核。",
        "返回模型分析处理待复核/缺失截面，重新保存管件后再加载。",
    )
    qa(
        "7.10  优化没有可行解或结果明显不合理",
        "工艺路线与形线长度、外径变化率不匹配，或素材约束过严。",
        "先看自动推荐再改选路线。检查 D0/T0 是否在本厂规格内，必要时编辑参数后“应用更改”。不要把未达阈值的方案直接当生产方案。",
    )
    qa(
        "7.11  模具计算加载不到工艺设计",
        "工艺方法不包含当前模块关键字，例如普通抽页只列出含“普通抽”的设计。",
        "回到工艺优化确认已保存，且路线名称含对应工步。或改用 DXF 备用路径（TP/Taper 还需 STL 形线）。",
    )
    qa(
        "7.12  TP 套模生成失败或提示直径超出",
        "型腔直径超过固定模体 95 mm，或大小径、段长与测量型线不符。",
        "核对 T_D、D、T_L 和两个站位。更大规格需要其他外形模板，当前程序会拒绝自交模体。",
    )
    qa(
        "7.13  硬模菜单是灰色的",
        "尚未加载工艺设计会话。",
        "先进入“硬模成型 → 加载数据”并点击“加载”。",
    )
    qa(
        "7.14  压扁提示轴长与周长不一致",
        "填写的是半轴、单位错误，或前序记录缺少已确认椭圆轴长。",
        "按完整轴长填写；两项同填或同空。不要沿用旧程序固定乘 0.6 的猜测。修正前不要继续压弯。",
    )
    qa(
        "7.15  反切提示模体尺寸不足或实体无效",
        "自定义 XYZ 未包住腔体，或截面/中心线质量不足导致布尔失败。",
        "改回自动推荐并保证每侧至少 1 mm。检查上游管件校核。软件不会降级导出破损三角面 STEP。",
    )
    qa(
        "7.16  参数改了但下载结果没变",
        "修改参数后没有重新生成。",
        "页面会提示“参数已修改，请重新生成后再保存或下载”。重新生成后再导出。",
    )
    qa(
        "7.17  页面没有结果或三维空白",
        "尚未加载数据、生成未完成，或预览资源需重新登录。",
        "确认已加载并等待生成结束。下载失败时先确认仍处于登录状态。可用网格/中心线开关检查模型是否被隐藏。",
    )
    qa(
        "7.18  软件提示需要工程师确认",
        "长度截短方向不明、直管段待确认、形线来源未选，或关键截面需人工判断。",
        "按页面红色/黄色提示逐项确认，不要把未确认结果交给下一模块。",
    )

    # 附录
    doc.add_heading("附录 A  三类数据对照", level=1)
    add_table(
        doc,
        ["类型", "识别方式", "示例"],
        [
            ["用户输入数据", "页面有输入框、下拉或开关，说明书写“由工程师填写/选择”", "TL1、车种号、机台类型、扩口段长度"],
            ["软件自动读取数据", "来自上传文件或数据库记录，页面只读显示", "中心线坐标、截面周长、工艺设计 L1/D2"],
            ["软件计算数据", "点击分析/优化/生成后出现，可再由工程师改", "图纸总长合计、D0/T0、套模 STEP、反切模体尺寸"],
        ],
        col_widths=[3.6, 6.6, 5.8],
        title="表A-1  数据来源对照",
    )
    P(
        doc,
        "需要人工确认的软件结果（如小径端、直管段、关键截面位置、工艺路线）应结合图纸或现场工艺判断，不要把自动识别直接当成已审核结论。",
    )

    doc.add_heading("附录 B  完整操作核对清单", level=1)
    add_table(
        doc,
        ["序号", "核对项", "通过标准"],
        [
            ["1", "软件已激活且许可有效", "工作台显示许可有效期"],
            ["2", "账号已登录且角色正确", "左侧能看到本次作业所需菜单"],
            ["3", "模型分析完成", "有中心线、周长、壁厚，无未处理的待复核"],
            ["4", "壁厚与图纸长度已确认", "长度校验为一致、已截短或已补长"],
            ["5", "形线来源已选择", "已点“采用自动读取形线”或“采用人工测量形线”"],
            ["6", "管件已保存", "工艺优化页能搜索到该记录"],
            ["7", "工艺设计已保存", "模具页或硬模加载页能看到该设计"],
            ["8", "模具或硬模已重新生成", "无“参数已修改请重新生成”提示"],
            ["9", "导出文件已检查", "STEP/ZIP 能在 CAD 中打开，尺寸数量级合理"],
        ],
        col_widths=[1.8, 6.2, 8.0],
        title="表B-1  一次完整作业核对清单",
    )

    doc.add_heading("附录 C  名词说明", level=1)
    add_table(
        doc,
        ["名词", "在本软件中的含义"],
        [
            ["中心线", "法向截面外轮廓所围面积的形心连线，不是未提供的 CAD 设计路径"],
            ["等效直径", "外周长 ÷ π，用于非圆截面比较，不是最大宽度"],
            ["完整长度坐标", "保留到两端的参考弧长；端部数值可能为线性外推"],
            ["形线", "沿中心线的周长（或直径）变化曲线，供工艺计算使用"],
            ["L4", "工程师确认后的直管段长度之和"],
            ["工艺设计", "优化后保存的一套工艺参数，供模具和硬模加载"],
            ["反切", "用外径实心腔体去减长方体，得到带封闭型腔的模坯"],
            ["工作角色", "决定可见菜单：工艺规划、模具设计、硬模成型或 ALL"],
        ],
        col_widths=[3.6, 12.4],
        title="表C-1  名词说明",
    )

    P(doc, "", indent=False)
    P(
        doc,
        "—— 全文结束。请结合本单位图纸、机台清单和素材规格使用。界面按钮名称如有升级，以软件当前页面为准。",
        indent=False,
        center=True,
        color=STEEL,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
