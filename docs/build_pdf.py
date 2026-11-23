"""把《许可证与账户管理手册.md》渲染成中文 PDF。

使用 reportlab 内置的中文 CID 字体 STSong-Light，无需安装外部字体。
支持的 Markdown 子集：标题、段落、有序/无序列表、代码块、引用、
表格(| |)、分割线、**加粗**、`行内代码`。

用法：
    python docs/build_pdf.py
产物：
    docs/许可证与账户管理手册.pdf
"""
import os
import re
import html

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Preformatted, Table, TableStyle,
    HRFlowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
MD_PATH = os.path.join(THIS_DIR, '许可证与账户管理手册.md')
PDF_PATH = os.path.join(THIS_DIR, '许可证与账户管理手册.pdf')

MONO_FONT = 'Courier'

# 优先嵌入系统 CJK TrueType 字体（文本可选中/复制、字形更佳）；
# 找不到时回退到 reportlab 内置 CID 字体（可显示但不可复制）。
_TTF_CANDIDATES = [
    '/Library/Fonts/Arial Unicode.ttf',          # macOS
    '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
    'C:/Windows/Fonts/msyh.ttc',                 # Windows 微软雅黑
    'C:/Windows/Fonts/simsun.ttc',               # Windows 宋体
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',  # Linux
    '/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf',
]


def _register_cn_font():
    for path in _TTF_CANDIDATES:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont('CN', path))
                # 把加粗也映射到同一字体，避免 <b> 缺字
                pdfmetrics.registerFont(TTFont('CN-Bold', path))
                pdfmetrics.registerFontFamily('CN', normal='CN', bold='CN-Bold',
                                              italic='CN', boldItalic='CN-Bold')
                return 'CN'
            except Exception:
                continue
    # 回退：内置中文 CID 字体
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    return 'STSong-Light'


CN_FONT = _register_cn_font()

styles = {
    'h1': ParagraphStyle('h1', fontName=CN_FONT, fontSize=20, leading=26,
                         spaceBefore=6, spaceAfter=12, textColor=colors.HexColor('#1a3c6e')),
    'h2': ParagraphStyle('h2', fontName=CN_FONT, fontSize=15, leading=20,
                         spaceBefore=14, spaceAfter=8, textColor=colors.HexColor('#1a3c6e')),
    'h3': ParagraphStyle('h3', fontName=CN_FONT, fontSize=12.5, leading=18,
                         spaceBefore=10, spaceAfter=6, textColor=colors.HexColor('#244')),
    'h4': ParagraphStyle('h4', fontName=CN_FONT, fontSize=11, leading=16,
                         spaceBefore=8, spaceAfter=4, textColor=colors.HexColor('#244')),
    'body': ParagraphStyle('body', fontName=CN_FONT, fontSize=9.5, leading=15,
                           spaceAfter=4),
    'li': ParagraphStyle('li', fontName=CN_FONT, fontSize=9.5, leading=15,
                         leftIndent=14, spaceAfter=2),
    'quote': ParagraphStyle('quote', fontName=CN_FONT, fontSize=9, leading=14,
                            leftIndent=10, textColor=colors.HexColor('#555'),
                            borderColor=colors.HexColor('#cccccc'),
                            backColor=colors.HexColor('#f6f6f6'),
                            borderWidth=0, spaceBefore=4, spaceAfter=6),
    'code': ParagraphStyle('code', fontName=MONO_FONT, fontSize=8.5, leading=12,
                           backColor=colors.HexColor('#f2f2f2'),
                           borderPadding=6, leftIndent=4, spaceBefore=4, spaceAfter=6),
    'cell': ParagraphStyle('cell', fontName=CN_FONT, fontSize=8.5, leading=12),
    'cellh': ParagraphStyle('cellh', fontName=CN_FONT, fontSize=8.5, leading=12,
                            textColor=colors.white),
}


def inline(text: str) -> str:
    """把行内 Markdown 转成 reportlab 的 mini-XML。"""
    text = html.escape(text, quote=False)
    # 行内代码 `code`
    text = re.sub(r'`([^`]+)`',
                  r'<font face="%s" color="#b00020">\1</font>' % MONO_FONT, text)
    # 加粗 **text**
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    # 链接 [text](url) -> text (url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
    return text


def heading_style(level):
    return styles.get(f'h{min(level,4)}', styles['h4'])


def build_table(rows):
    # rows: list of list[str]；第一行表头，第二行是分隔(--)，其余数据
    header = rows[0]
    data_rows = rows[2:] if len(rows) >= 2 else []
    table_data = []
    table_data.append([Paragraph(inline(c), styles['cellh']) for c in header])
    for r in data_rows:
        # 补齐列数
        while len(r) < len(header):
            r.append('')
        table_data.append([Paragraph(inline(c), styles['cell']) for c in r])

    ncols = len(header)
    avail = A4[0] - 40 * mm
    col_w = avail / ncols
    t = Table(table_data, colWidths=[col_w] * ncols, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3c6e')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#bbbbbb')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f7fb')]),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


def parse_table_row(line):
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return [c.strip() for c in s.split('|')]


def main():
    with open(MD_PATH, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n')

    flow = []
    i = 0
    n = len(lines)
    para_buf = []

    def flush_para():
        if para_buf:
            text = ' '.join(x.strip() for x in para_buf).strip()
            if text:
                flow.append(Paragraph(inline(text), styles['body']))
            para_buf.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 代码块
        if stripped.startswith('```'):
            flush_para()
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            i += 1  # 跳过结束 ```
            # 去掉公共缩进
            code_text = '\n'.join(code_lines)
            flow.append(Preformatted(code_text, styles['code']))
            continue

        # 表格
        if stripped.startswith('|') and i + 1 < n and re.match(r'^\s*\|?[\s:\-|]+\|?\s*$', lines[i + 1]):
            flush_para()
            tbl = []
            while i < n and lines[i].strip().startswith('|'):
                tbl.append(parse_table_row(lines[i]))
                i += 1
            flow.append(build_table(tbl))
            flow.append(Spacer(1, 6))
            continue

        # 分割线
        if stripped in ('---', '***', '___'):
            flush_para()
            flow.append(Spacer(1, 4))
            flow.append(HRFlowable(width='100%', thickness=0.6,
                                   color=colors.HexColor('#cccccc')))
            flow.append(Spacer(1, 4))
            i += 1
            continue

        # 标题
        m = re.match(r'^(#{1,6})\s+(.*)$', stripped)
        if m:
            flush_para()
            level = len(m.group(1))
            flow.append(Paragraph(inline(m.group(2)), heading_style(level)))
            i += 1
            continue

        # 引用
        if stripped.startswith('>'):
            flush_para()
            quote_lines = []
            while i < n and lines[i].strip().startswith('>'):
                quote_lines.append(lines[i].strip()[1:].strip())
                i += 1
            flow.append(Paragraph(inline(' '.join(quote_lines)), styles['quote']))
            continue

        # 无序列表
        mlist = re.match(r'^(\s*)[-*]\s+(.*)$', line)
        if mlist:
            flush_para()
            flow.append(Paragraph('• ' + inline(mlist.group(2)), styles['li']))
            i += 1
            continue

        # 有序列表
        mol = re.match(r'^(\s*)(\d+)\.\s+(.*)$', line)
        if mol:
            flush_para()
            flow.append(Paragraph(mol.group(2) + '. ' + inline(mol.group(3)), styles['li']))
            i += 1
            continue

        # 空行 -> 段落分隔
        if stripped == '':
            flush_para()
            i += 1
            continue

        # 普通段落
        para_buf.append(line)
        i += 1

    flush_para()

    doc = SimpleDocTemplate(
        PDF_PATH, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title='同济大学-捷安特管件设计软件 · 许可证与账户管理手册',
    )
    doc.build(flow)
    print('已生成 PDF:', PDF_PATH)


if __name__ == '__main__':
    main()
