"""Build Chinese PDF/HTML reports and interactive views from measured results."""
import argparse
import csv
import html
import json
import shutil
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from matplotlib.font_manager import FontProperties

ROOT = Path(__file__).resolve().parents[2]
FONT = FontProperties(fname='C:/Windows/Fonts/simhei.ttf')
plt.rcParams.update({'font.family': 'SimHei', 'axes.unicode_minus': False, 'font.size': 10,
                     'axes.spines.top': False, 'axes.spines.right': False, 'figure.facecolor': 'white'})

DRAWINGS = {
    '24TK_1324-1': ('24TK-1324-1-5306569_0', '图纸：壁厚 2.0±0.1 mm；755 mm 为图示轴向尺寸，不能直接等同于法向截面测量段弧长。'),
    '24TK_1324-2': ('24TK-1324-2-5305186_0', '图纸：壁厚 2.5±0.1 mm，轴向尺寸 765 mm。管端造型和弯曲必须保留。'),
    '24TK_1324-31': ('24TK-1324-3-5306568_0', '按名称和形状对应 1324-3 图纸（STP 名称为 1324-31）；标注壁厚 1.7±0.1 mm、611 mm 和 6.1°。此文件对应关系尚需工程确认。'),
    '24TK_1442-1': ('24TK-1442-1-5252258 r0', '图纸为变壁厚管：0.9 / 1.2 mm，且明确存在壁厚不受控区。不能把全管平均壁厚与某个局部标注直接相减当作误差。'),
    '24TK_2052-2': ('24TK-2052-2-5251592_1', '图纸标注局部壁厚 1.5±0.2 / 1.7±0.1 mm；模型为弯曲、变截面座叉。需按图纸基准定位每个检测区。'),
    'H0A2Z3-DT': ('H0A2Z3-DT', 'PDF 是多工序、多尺码工艺图，列有原管、抽管、成型及裁剪尺寸。尚未指定 STP 对应的尺码/工序，不能宣称已逐项通过图纸公差验收。'),
}


def aligned(vertices, new, old):
    origin = vertices.mean(axis=0)
    _, _, vt = np.linalg.svd(vertices - origin, full_matrices=False)
    if (new[-1] - new[0]) @ vt[0] < 0:
        vt[0] *= -1
    vt[2] = np.cross(vt[0], vt[1])
    return [(p - origin) @ vt.T for p in (vertices, new, old)], origin, vt


def station_shift(new, old):
    _, _, vt = np.linalg.svd(new - new.mean(axis=0), full_matrices=False)
    axis = vt[0]
    nt, ot = new @ axis, old @ axis
    order = np.argsort(ot)
    valid = (nt >= ot.min()) & (nt <= ot.max())
    baseline = np.column_stack([np.interp(nt[valid], ot[order], old[order, j]) for j in range(3)])
    diff = np.linalg.norm(new[valid] - baseline, axis=1)
    return float(np.median(diff)), float(np.max(diff))


def figures(name, before, after, report):
    mesh = np.load(report / 'assets' / (name + '.npz'))
    verts, faces = mesh['vertices'], mesh['faces']
    new, old = np.array(after['center_line_points']), np.array(before['center_line_points'])
    (v, p, b), origin, basis = aligned(verts, new, old)
    sf, sb = after['sections_full'], before['sections_full']
    s = np.array([x['cumulative_length'] for x in sf])
    s0 = np.array([x['cumulative_length'] for x in sb])
    fig = plt.figure(figsize=(12, 9), layout='constrained')
    gs = fig.add_gridspec(3, 2, height_ratios=[1.4, .85, 1])
    from matplotlib.collections import PolyCollection
    ax = fig.add_subplot(gs[0, :])
    def project(points):
        return np.column_stack((points[:, 0], .85 * points[:, 1] + .53 * points[:, 2]))
    depth = -.53 * v[:, 1] + .85 * v[:, 2]
    order = np.argsort(depth[faces].mean(axis=1))
    ax.add_collection(PolyCollection(project(v)[faces[order]], alpha=.10, linewidths=0, facecolor='#679dc3', rasterized=True))
    ax.plot(*project(p).T, color='#e11d48', lw=2.5, label='修复后：原生截面质心')
    ax.plot(*project(b).T, color='#ef9c3a', lw=1.5, ls='--', label='旧版中心线')
    for row in sf[::max(1, len(sf) // 12)]:
        outline = (np.array(row['outline_3d']) - origin) @ basis.T
        ax.plot(*project(np.vstack((outline, outline[0]))).T, color='#168b88', lw=.6, alpha=.7)
    flagged = [i for i, row in enumerate(sf) if row.get('measurement_status') == 'needs_review']
    if flagged: ax.scatter(*project(p[flagged]).T, marker='x', s=32, c='#7c2143', label='待复核截面', zorder=8)
    missing = after['analysis_quality'].get('rejected_sections', [])
    missing = [r['position'] for r in missing if 'position' in r]
    if missing:
        mp = (np.array(missing) - origin) @ basis.T
        ax.scatter(*project(mp).T, marker='x', s=55, c='black', label='缺测位置', zorder=9)
    ax.autoscale(); ax.margins(.025)
    ax.set_aspect('equal', adjustable='datalim')
    ax.set_axis_off()
    ax.legend(loc='upper left', fontsize=9)
    ax.set_title(f'{name}  |  STP 三维模型正交投影 + 高亮中心线 + 实际截面', loc='left', fontweight='bold')
    for j in range(2):
        ax = fig.add_subplot(gs[1, j])
        ax.plot(b[:, 0], b[:, j+1], color='#ef9c3a', ls='--', label='旧版')
        ax.plot(p[:, 0], p[:, j+1], color='#e11d48', label='修复后')
        ax.set(xlabel='主轴投影坐标 (mm)', ylabel=f'横向 {j+1} 位移 (mm)', title='中心线侧视（纵轴放大，观察端部走势）')
        ax.grid(alpha=.2)
    ax = fig.add_subplot(gs[2, 0])
    ax.plot(s0, [x['perimeter'] for x in sb], color='#ef9c3a', ls='--', label='旧版')
    ax.plot(s, [x['perimeter'] for x in sf], color='#1675a9', label='原生截面')
    ax.set(xlabel='各自有效起点的累计长度 (mm)', ylabel='外轮廓周长 (mm)', title='周长沿程分布')
    ax.legend(); ax.grid(alpha=.2)
    ax = fig.add_subplot(gs[2, 1])
    ax.fill_between(s, [x['wall_thickness_min'] for x in sf], [x['wall_thickness_max'] for x in sf], alpha=.15, color='#168b88', label='截面周向最小—最大')
    ax.plot(s0, [x.get('wall_thickness', np.nan) for x in sb], color='#ef9c3a', ls='--', label='旧版投影差')
    ax.plot(s, [x['wall_thickness'] for x in sf], color='#168b88', label='修复后中位数')
    ax.set(xlabel='各自有效起点的累计长度 (mm)', ylabel='壁厚 (mm)', title='截面内最短壁厚沿程分布')
    ax.legend(fontsize=8); ax.grid(alpha=.2)
    target = report / 'figures' / (name + '_overview.png')
    fig.savefig(target, dpi=160); plt.close(fig)
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.6), layout='constrained')
    selected = [sf[int(k * (len(sf)-1))] for k in (.03, .33, .66, .97)]
    for ax, row in zip(axes, selected):
        for key, color, label in [('outer_2d', '#1675a9', '外壁'), ('inner_2d', '#e68a2e', '内壁')]:
            xy = np.array(row[key]); xy = np.vstack([xy, xy[0]])
            ax.plot(*xy.T, color=color, lw=1.3, label=label)
        ax.scatter([0], [0], c='#e11d48', s=22, zorder=4)
        ax.set_aspect('equal'); ax.grid(alpha=.2)
        ax.set_title(f"s={row['cumulative_length']:.1f} mm\nP={row['perimeter']:.3f}  D=P/π={row['equivalent_diameter']:.3f}\nt={row['wall_thickness']:.3f} [{row['wall_thickness_min']:.3f}, {row['wall_thickness_max']:.3f}]", fontsize=9)
        ax.set_xlabel('截面局部坐标 u (mm)')
    axes[0].set_ylabel('v (mm)'); axes[0].legend(fontsize=8)
    fig.savefig(report / 'figures' / (name + '_sections.png'), dpi=160); plt.close(fig)
    return verts, faces, selected


def interactive(name, before, after, vertices, faces, report):
    import plotly.graph_objects as go
    from plotly.utils import PlotlyJSONEncoder
    new, old = np.array(after['center_line_points']), np.array(before['center_line_points'])
    # Work in a PCA view frame for a readable initial camera; preserve world XYZ
    # for actual measurements in the CSV and original JSON files.
    (v, p, b), origin, basis = aligned(vertices, new, old)
    traces = [go.Mesh3d(x=v[:,0], y=v[:,1], z=v[:,2], i=faces[:,0], j=faces[:,1], k=faces[:,2],
                       opacity=.2, color='#76a9c5', name='导入 STP', hoverinfo='skip'),
              go.Scatter3d(x=p[:,0], y=p[:,1], z=p[:,2], mode='lines', name='修复后中心线', line={'color':'#e11d48','width':7}),
              go.Scatter3d(x=b[:,0], y=b[:,1], z=b[:,2], mode='lines', name='旧版中心线', line={'color':'#e9a23b','width':3,'dash':'dash'})]
    from shapely.geometry import LineString
    def display_contour(points):
        closed = points + [points[0]]
        return np.round(np.asarray(LineString(closed).simplify(.01, preserve_topology=True).coords)[:-1], 5)
    sections = []
    for r in after['sections_full']:
        outer, inner = display_contour(r['outer_2d']), display_contour(r['inner_2d'])
        outline = np.array(r['position']) + outer @ np.array([r['basis_u'], r['basis_v']])
        sections.append({k:r[k] for k in ('cumulative_length','perimeter','equivalent_diameter','wall_thickness','wall_thickness_min','wall_thickness_max','measurement_status','normal_deviation_deg')} |
                        {'outer_2d':outer.tolist(),'inner_2d':inner.tolist(),
                         'outline':np.round((outline-origin)@basis.T,5).tolist(),
                         'point':np.round((np.array(r['position'])-origin)@basis.T,5).tolist()})
    payload = {'traces': traces, 'sections': sections}
    data = json.dumps(payload, cls=PlotlyJSONEncoder)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>CASE | STP验证</title>
<script src="assets/plotly.min.js"></script><script src="assets/report_viewer.js"></script><style>body{margin:0;background:#f3f6fa;color:#17243a;font:15px 'Microsoft YaHei',sans-serif}main{max-width:1500px;margin:auto;padding:24px}h1{font-size:26px}a{color:#1675a9}.grid{display:grid;grid-template-columns:2fr 1fr;gap:18px}.card{background:white;border-radius:12px;padding:18px;margin-bottom:18px}#model{height:470px}#section{height:350px}#curves{height:310px}input{width:100%}.muted{color:#617087;line-height:1.8}.metrics{font-size:18px;line-height:1.9}@media(max-width:900px){.grid{grid-template-columns:1fr}}</style>
<main><a href="index.html">← 返回修复报告</a><h1>CASE · 原生截面验证</h1>
<p class="muted">真实 STP 可视化网格；红色为修复后中心线，橙色为旧版。拖动旋转，滚轮缩放；滑块联动三维截面与数据。显示坐标已旋转为主轴坐标，CSV 保留原始 XYZ。交互显示轮廓简化公差为 0.01 mm，计量数值来自未简化的实际切割。</p>
<div class="grid"><div class="card"><div id="model"></div><input id="station" type="range" min="0" step="1"><div id="summary" class="metrics"></div></div><div class="card"><div id="section"></div><p class="muted">蓝色外壁 / 橙色内壁 / 红点外轮廓面积质心。壁厚为截面内最短距离，非陡峭锥段的三维法向壁厚；D=P/π 为周长等效直径。</p></div></div><div class="card"><div id="curves"></div></div>
<p class="muted">WARNINGS</p></main><script>const data=PAYLOAD;const ss=data.sections; const first=ss[0];
data.traces.push({type:'scatter3d',mode:'lines',name:'选中截面',x:[],y:[],z:[],line:{color:'#00a58b',width:5}});
const viewer=createTubeViewer(document.getElementById('model'),data.traces);
Plotly.newPlot('curves',[{x:ss.map(s=>s.cumulative_length),y:ss.map(s=>s.perimeter),name:'外轮廓周长',line:{color:'#1675a9'}},{x:ss.map(s=>s.cumulative_length),y:ss.map(s=>s.wall_thickness),name:'壁厚中位数',yaxis:'y2',line:{color:'#168b88'}}],{margin:{t:20,l:60,r:60,b:45},xaxis:{title:'累计长度 / mm'},yaxis:{title:'周长 / mm'},yaxis2:{title:'壁厚 / mm',overlaying:'y',side:'right'},legend:{orientation:'h',y:1.15}},{responsive:true});
function update(i){const s=ss[i],o=s.outline.concat([s.outline[0]]);viewer.section(o);Plotly.react('section',['outer_2d','inner_2d'].map((k,j)=>{const a=s[k].concat([s[k][0]]);return{x:a.map(p=>p[0]),y:a.map(p=>p[1]),mode:'lines',name:j?'内壁':'外壁',line:{color:j?'#e68a2e':'#1675a9'}}}).concat([{x:[0],y:[0],mode:'markers',name:'质心',marker:{color:'#e11d48',size:8}}]),{margin:{l:40,r:10,t:20,b:40},xaxis:{title:'u / mm'},yaxis:{scaleanchor:'x',title:'v / mm'},showlegend:false},{responsive:true});document.getElementById('summary').innerHTML=`截面 ${Number(i)+1} / ${ss.length}　s = <b>${s.cumulative_length.toFixed(2)} mm</b><br>P = ${s.perimeter.toFixed(3)} mm　D = ${s.equivalent_diameter.toFixed(3)} mm<br>壁厚中位数 ${s.wall_thickness.toFixed(3)} mm　周向范围 ${s.wall_thickness_min.toFixed(3)}–${s.wall_thickness_max.toFixed(3)} mm<br>法向偏差 ${s.normal_deviation_deg.toFixed(3)}° · ${s.measurement_status==='needs_review'?'⚠ 此截面待复核':'通过局部校核'}`;}
const slider=document.getElementById('station');slider.max=ss.length-1;slider.value=Math.floor(ss.length/2);slider.oninput=()=>update(slider.value);update(slider.value);
</script></html>'''
    page = page.replace('CASE', html.escape(name)).replace('WARNINGS', html.escape(' '.join(after['analysis_quality'].get('warnings', [])))).replace('PAYLOAD', data)
    (report / (name + '.html')).write_text(page, encoding='utf-8')


ISSUES = [
    ('当前主界面把分段默认值显示为读取壁厚', 'ModelUpload 的截面表和详情优先使用自动初始化的工艺分段值，变壁厚会看起来变成恒定中位数。已改为显示截面实际读取值，工艺设定仍保留在独立编辑区和虚线中。'),
    ('STP 几何被粗网格替代', '旧程序以 0.3 mm 网格公差将 STP 转为 STL，再做所有测量。现在直接求原生曲面与平面的交线，周长由 CAD 曲线长度积分得到；显示网格独立。'),
    ('两端各 20% 的真实中心线被拉平', '旧代码用中间段平均 XY 替换两端轨迹，抹掉端部弯曲并产生人为转折。现在由外轮廓面积质心建立轨迹，按局部法向重复切割校正。'),
    ('内壁凸包与投影差并非真实壁厚', '旧算法合并内壁点形成凸包，且用最大投影差的中位数当壁厚。现在保留凹形内外轮廓，沿外轮廓均匀弧长取样到内轮廓线段的最短距离，同时输出周向范围。'),
    ('数据与所显示截面不在同一位置', '旧结果把周长/壁厚平滑、插值，却沿用最近截面的旧三维轮廓；端部还用邻居补值。现在每个记录的平面、质心、轮廓与数值来自同一次实际切割，并保留非整数末端。'),
    ('三维预览与中心线显示链路断裂', '原 STP 预览接口直接发送 STP 给 STLLoader；保留的 Tab1ModelLoader 组件还调用不存在的 sectionsVisual 方法，并读取错误的 outer_edge 字段。当前路由的 ModelUpload 已同步新增质量提示、三位小数显示和中心线高亮。已修复二进制 STL 预览、现有数据直连、截面局部坐标和三维高亮。WebGL 不可用时新增可旋转 SVG 备用渲染。'),
    ('切换模型后壁厚图保留上一个模型的中位线', '图表只监听截面数组，异步更新壁厚统计后没有重画。已同时监听统计值，修复中位线滞后并显示三位小数。'),
    ('结果卡片丢失数值', '壁厚只显示一位小数会掩盖偏差，已改为三位小数；上传结果体积被写成 0；数据库载入时使用了不完整响应中的体积/弯曲字段；文件名卡片使用了无效的 formatter 插槽。已改为真实体积、完整会话数据和文本文件名。'),
]


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('report', type=Path); args=parser.parse_args()
    report=args.report.resolve();(report/'figures').mkdir(exist_ok=True);(report/'csv').mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'frontend/node_modules/plotly.js-dist-min/plotly.min.js',report/'assets/plotly.min.js')
    shutil.copyfile(Path(__file__).with_name('report_viewer.js'),report/'assets/report_viewer.js')
    summaries=[]; cases=[]
    for f in sorted((report/'measured').glob('*.json')):
        after=json.loads(f.read_text(encoding='utf-8'));before=json.loads((report/'before'/f.name).read_text(encoding='utf-8'))
        name=f.stem; sf=after['sections_full']; verts,faces,selected=figures(name,before,after,report)
        interactive(name,before,after,verts,faces,report)
        median_shift,max_shift=station_shift(np.array(after['center_line_points']),np.array(before['center_line_points']))
        fields=['cumulative_length','perimeter','equivalent_diameter','wall_thickness','wall_thickness_min','wall_thickness_max','wall_thickness_mean','wall_area','centroid_residual_mm','normal_deviation_deg','measurement_status','plane_offset_mm','intersection_fuzzy_mm']
        with (report/'csv'/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as out:
            writer=csv.writer(out);writer.writerow(fields+['x','y','z','nx','ny','nz'])
            for s in sf:writer.writerow([s.get(k) for k in fields]+s['position']+s['normal'])
        summary={'case':name,'sections':len(sf),'old_length_mm':before['center_line_length'],'measured_length_mm':after['center_line_length'],
                 'perimeter_min_mm':min(s['perimeter'] for s in sf),'perimeter_max_mm':max(s['perimeter'] for s in sf),
                 'wall_median_mm':after['wall_thickness_stats']['median_mm'],'wall_station_min_mm':min(s['wall_thickness'] for s in sf),
                 'wall_station_max_mm':max(s['wall_thickness'] for s in sf),'max_centerline_change_mm':max_shift,
                 'coverage_percent':after['analysis_quality']['coverage_ratio']*100,
                 'review':after['analysis_quality'].get('review_station_count',0),'normal_max_deg':after['analysis_quality'].get('max_normal_deviation_deg'),
                 'seconds':after['validation']['seconds'],'rejected':len(after['analysis_quality']['rejected_sections'])}
        summaries.append(summary);cases.append((name,before,after,selected,summary))
    (report/'summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=''.join(f"<tr><td><a href='{r['case']}.html'>{r['case']}</a></td><td>{r['sections']}</td><td>{r['old_length_mm']:.2f} → {r['measured_length_mm']:.2f}</td><td>{r['perimeter_min_mm']:.2f}–{r['perimeter_max_mm']:.2f}</td><td>{r['wall_median_mm']:.3f}</td><td>{r['coverage_percent']:.2f}%</td><td>{r['rejected']} / {r['review']}</td><td>{r['seconds']:.0f}s</td></tr>" for r in summaries)
    audit=json.loads((report/'audit.json').read_text(encoding='utf8')) if (report/'audit.json').exists() else []
    audit_max=max((s['centroid_vs_cad_area_mm'] for a in audit for s in a['samples']),default=0.)
    audit_note=f'独立复核：每个案例抽查 3 个截面（共 {sum(len(a["samples"]) for a in audit)} 个），面积质心相对 CAD 面积积分最大差 {audit_max:.6f} mm；所有有效截面的世界坐标与局部坐标、平面法向和累计弧长一致性检查通过。这只验证抽查截面的数值一致性，不代表整条中心线的绝对精度。'
    quality_note='最终校核：1324-1、1324-31、1442-1、H0A2Z3 的有效段无内区缺测或待复核标记；1324-2 有 11 个截面需复核（约 s=52–64 mm）；2052-2 有 2 个缺测位置和 8 个待复核截面。这两个案例尚不能直接用于自动工艺计算。'
    limitations='非圆、变截面管没有唯一的原设计中心线。本报告采用法向截面外轮廓面积质心轨迹；若工艺需要原 CAD 扫掠路径，需另行提供该设计曲线。壁厚为截面平面内最短距离，陡峭锥段与三维表面法向壁厚有差异。开口、斜切、孔槽及未收敛截面必须复核。图纸未给出完整数字中心线/周长真值，因此不能声称所有实际样件已达到某个绝对精度或通过生产验收。'
    parts=[f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>STP 读取修复报告</title><style>body{{font:16px/1.8 'Microsoft YaHei',sans-serif;margin:0;background:#f3f6fa;color:#18283b}}main{{max-width:1180px;margin:auto;padding:36px}}section{{background:white;padding:28px;border-radius:12px;margin:24px 0}}h1{{font-size:36px}}h2{{color:#125f84}}img{{max-width:100%}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:12px;border-bottom:1px solid #dce4ee;text-align:left}}a{{color:#087a9c}}.note{{padding:18px;background:#fff7e5;border-left:4px solid #eaa23d}}.muted{{color:#617087}}</style><main><h1>STP 管件读取 · 修复与验证报告</h1><p class="muted">2026-09-21 · 用户提供的 6 个 STP / 7 个 PDF · 原始数据、图表、软件截图与复现脚本</p><p><a href="STP修复报告.pdf">下载 PDF 报告</a> · <a href="summary.json">数值汇总 JSON</a></p><section><h2>修复结果与边界</h2><p>已改为 STP 原生曲面截面测量，保留端部造型，修复内壁/壁厚算法及三维显示链路。采用解析几何测试和真实文件回归验证；下表为实际运行输出。</p><div class="note">{quality_note}<br>{limitations}</div><table><tr><th>案例 / 交互三维</th><th>有效截面</th><th>旧总长 → 新有效测量长 mm</th><th>周长范围 mm</th><th>壁厚中位数 mm</th><th>轴向覆盖</th><th>缺测 / 待复核</th><th>初次运行耗时</th></tr>{rows}</table><p>长度起止边界可能不同，箭头表示两套输出，不等于长度误差。完整法向双闭环之外的端部没有编造周长和壁厚。耗时不含末次补测；原生每毫米密集切割比旧网格流程慢，性能优化尚未完成。</p></section><section><h2>定位并修复的问题</h2>''']
    parts.extend(f'<h3>{i+1}. {title}</h3><p>{detail}</p>' for i,(title,detail) in enumerate(ISSUES))
    parts.append('</section><section><h2>验证方法</h2><p>解析标准件覆盖：圆管周长、圆弧弯管中心线、凹形截面、偏心壁厚、任意旋转及非整数末端、实心棒拒绝误识别。真实上传 API 测试检查了原生测量标识、体积、壁厚和可解析的二进制 STL。实际文件逐个保留 SHA-256、前后结果及每站 CSV。</p><p>CAD 周长沿原始交线积分。显示/壁厚轮廓离散弦差为 0.003 mm；微小接缝自交只允许在 0.01 mm 界限内修补，并逐截面记录。此数值不是原始 STP 的精度保证。</p></section>')
    if (report/'screenshots/current_ModelUpload_2052.png').exists():
        parts.append('<section><h2>当前主界面运行截图：管件模型分析</h2><p>当前路由使用 ModelUpload.vue。下图为实际运行界面，载入 2052-2；红色为测得中心线，下方分别是周长和壁厚。该案例仍有质量标记，请结合上方限制解读。</p><img src="screenshots/current_ModelUpload_2052.png"><img src="screenshots/current_ModelUpload_quality.png"></section>')
    parts.append('<section><h2>独立 CAD 复核</h2><p>'+audit_note+'</p><p><a href="audit.json">完整复核数值</a> · <a href="tests.txt">自动化测试记录</a> · <a href="REPRODUCE.md">复现与部署说明</a></p></section>')
    for name,before,after,selected,summary in cases:
        drawing, note=DRAWINGS[name]
        warnings=' '.join(after['analysis_quality'].get('warnings',[])) or '未记录缺测或残差警告。'
        parts.append(f'<section><h2>{name}</h2><p>{note}</p><p><a href="{name}.html">打开可旋转三维模型与截面滑块</a> · <a href="csv/{name}.csv">下载每毫米数据 CSV</a></p><p>在共同主轴位置比较，两版中心线最大变化 {summary["max_centerline_change_mm"]:.3f} mm；这是修改幅度，不是独立标定误差。内区缺测 {summary["rejected"]} 个，法向/壁厚待复核 {summary["review"]} 个。</p><div class="note">{html.escape(warnings)}</div><img src="figures/{name}_overview.png"><img src="figures/{name}_sections.png">')
        shot=report/'screenshots'/(name+'.png')
        if shot.exists():parts.append(f'<h3>软件运行截图</h3><p class="muted">本地验证入口使用项目保留的 Tab1ModelLoader 组件与原 PP API，从隔离测试数据库加载本次实际分析结果。浏览器未提供 WebGL，截图采用程序新增的 SVG 矢量三维备用渲染；模型和中心线均为真实几何数据，可拖动旋转。</p><img src="screenshots/{name}.png">')
        detail=report/'screenshots'/(name+'_section.png')
        if detail.exists(): parts.append(f'<h3>软件截面详情实测截图</h3><img src="screenshots/{name}_section.png">')
        parts.append(f'<details><summary>查看原 PDF 图纸</summary><img src="drawings/{drawing}.png"></details></section>')
    parts.append('<section><h2>复现与交付</h2><p>源码已在当前工作区修复。重新启动后端/前端并重新导入模型后生效；旧数据库中的历史分析不会被自动重写。本报告不表示已经重新打包或替换已安装的 EXE。</p><p>运行说明见同目录 REPRODUCE.md。24TK-2052-1 仅提供 PDF，没有对应 STP，因此没有几何测试结果。</p><p>算法依据：<a href="https://dev.opencascade.org/doc/refman/html/class_b_rep_algo_a_p_i___section.html">Open CASCADE Section</a>、<a href="https://dev.opencascade.org/doc/refman/html/class_b_rep_g_prop.html">BRepGProp</a>。</p></section></main></html>')
    (report/'index.html').write_text(''.join(parts),encoding='utf-8')
    pdf(report,cases,limitations,quality_note,audit_note)
    print('Report generated:',report/'index.html',flush=True)


def pdf(report,cases,limitations,quality_note,audit_note):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Image,Table,TableStyle,PageBreak,KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    pdfmetrics.registerFont(TTFont('Chinese','C:/Windows/Fonts/simhei.ttf'))
    styles=getSampleStyleSheet()
    for style in styles.byName.values():style.fontName='Chinese'
    styles.add(ParagraphStyle(name='BodyCN',fontName='Chinese',fontSize=10,leading=16,spaceAfter=9))
    body=styles['BodyCN'];story=[]
    def para(t,style=body):return Paragraph(html.escape(t),style)
    def picture(path,width=490):
        from PIL import Image as PILImage
        with PILImage.open(path) as im:w,h=im.size
        return Image(str(path),width=width,height=width*h/w)
    story += [para('STP 管件读取修复与验证报告',styles['Title']),para('2026-09-21 | GIANT 铝合金异形管 | 六个真实 STP 案例'),Spacer(1,12),para('结论',styles['Heading1']),para('已修复 STP 原生测量及前端三维显示链路。解析几何测试与实际上传接口测试通过；六个真实样件按相同流程验证，所有输出保留原始文件哈希及每站数据。'),para(quality_note),para(limitations),para('问题与修复',styles['Heading1'])]
    for title,detail in ISSUES:story.extend([para(title,styles['Heading2']),para(detail)])
    story.append(PageBreak());story.extend([para('真实案例结果汇总',styles['Heading1']),para('周长、等效直径和壁厚均以毫米计。旧总长与新有效测量长的边界不同，不能将差值直接视作误差。')])
    table=[['案例','有效截面','测量长','周长范围','壁厚中位']]
    for name,_,a,_,s in cases:table.append([name,str(s['sections']),f"{s['measured_length_mm']:.2f}",f"{s['perimeter_min_mm']:.2f}–{s['perimeter_max_mm']:.2f}",f"{s['wall_median_mm']:.3f}"])
    t=Table(table,colWidths=[118,64,82,130,91]);t.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Chinese'),('FONTSIZE',(0,0),(-1,-1),9),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8f0f7')),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#cedae5')),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9)]));story+=[t,Spacer(1,16),para('解析测试与接口测试',styles['Heading2']),para('圆管精确周长、圆弧弯管轨迹、凹形内外轮廓、偏心壁厚范围、旋转不变性、非整数末端、实心棒拒绝误识别，以及真实 STEP 上传→测量→二进制 STL 预览。详见 tests.txt。'),para(audit_note),para('图纸核对限制',styles['Heading2']),para('前三个案例可用标注的 2.0、2.5、1.7 mm 检查主体壁厚。变壁厚模型需按指定基准与受控区分别验收。H0A2Z3 工艺图含多尺码、多工序，尚未指定对应关系。24TK-2052-1 没有对应 STP。'),para('文件说明',styles['Heading2']),para('index.html：完整报告；各案例 HTML：可旋转模型和截面滑块；csv：逐站测量；measured/before：前后完整数据；screenshots：软件运行截图；drawings：用户 PDF 渲染。')]
    if (report/'screenshots/current_ModelUpload_2052.png').exists():
        story += [PageBreak(),para('当前主界面：管件模型分析',styles['Heading1']),para('当前路由的 ModelUpload.vue，真实运行截图。示例为 2052-2，红色为实测中心线；周长及壁厚曲线直接使用实测数据。这个案例仍有待复核位置。'),picture(report/'screenshots/current_ModelUpload_2052.png'),Spacer(1,15),picture(report/'screenshots/current_ModelUpload_quality.png')]
    for name,before,after,selected,summary in cases:
        story += [PageBreak(),para(name,styles['Heading1']),para(DRAWINGS[name][1]),picture(report/'figures'/(name+'_overview.png')),Spacer(1,5),para('图示为真实文件计算输出。三维模型显示网格与计量曲面分离；侧视图纵轴放大。两套曲线的 s=0 各自对应有效起点。')]
        story += [PageBreak(),para(name+' · 截面与质量记录',styles['Heading1']),picture(report/'figures'/(name+'_sections.png')),para('每张截面图标题依次为累计长度、周长、周长等效直径、壁厚中位数及周向最小/最大值。红点为外轮廓面积质心。'),para(' '.join(after['analysis_quality'].get('warnings',[])) or '没有缺测警告。'),para(f"有效截面 {summary['sections']} 个；轴向覆盖 {summary['coverage_percent']:.2f}%；共同位置中心线最大变化 {summary['max_centerline_change_mm']:.3f} mm（修改幅度，不是独立精度）。"),para(f"内区缺测 {summary['rejected']} 个，待复核 {summary['review']} 个；最大法向偏差 {summary['normal_max_deg']:.3f}°。"),para('SHA-256: '+after['validation']['sha256'])]
        shot=report/'screenshots'/(name+'_model.png')
        if shot.exists():
            from PIL import Image as PILImage
            with PILImage.open(shot) as im:w,h=im.size
            story += [PageBreak(),para(name+' · 软件真实运行截图',styles['Heading1']),para('原软件组件与 PP API，隔离数据库加载本次实测数据。当前浏览器无 WebGL，使用新增 SVG 矢量渲染，可拖动旋转。完整截图及壁厚界面见 HTML 报告。'),picture(shot,width=min(490,610*w/h))]
        detail=report/'screenshots'/(name+'_section.png')
        if detail.exists(): story += [PageBreak(),para(name+' · 软件截面详情',styles['Heading1']),picture(detail,width=490)]
    story += [PageBreak(),para('使用与未覆盖事项',styles['Heading1']),para('修复已写入当前项目源码；需要重启程序并重新导入 STP。历史数据库分析不会自动更新，本次没有替换已安装 EXE 或执行生产发布。'),para(limitations),para('开口端部没有完整的法向内外闭环，周长与壁厚不进行盲目外推。报告里的有效中心线长度用于说明测量范围，不能直接替代图纸下料总长。'),para('壁厚离散弦差 0.003 mm；原模型本身的曲面/顶点容差可能更大，参见 assets 中的 metadata。复杂强弯、分叉、多个实体、孔槽打断截面以及仅有外表面的模型不承诺自动复原。现有弯曲角卡片沿用旧弯曲检测定义，不等于图纸两端夹角；本次没有将该卡片当作精度验收指标。'),para('参考：Open CASCADE BRepAlgoAPI_Section / BRepGProp / GCPnts_QuasiUniformDeflection；复现命令及测试环境见 REPRODUCE.md。')]
    def footer(canvas,doc):
        canvas.setFont('Chinese',8);canvas.setFillColor(colors.HexColor('#64748b'));canvas.drawString(44,26,'GIANT · STP 原生截面修复验证 · 2026-09-21');canvas.drawRightString(A4[0]-44,26,str(doc.page))
    SimpleDocTemplate(str(report/'STP修复报告.pdf'),pagesize=A4,rightMargin=44,leftMargin=44,topMargin=38,bottomMargin=44).build(story,onFirstPage=footer,onLaterPages=footer)


if __name__=='__main__':main()
