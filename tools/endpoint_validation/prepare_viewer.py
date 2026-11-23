"""Build the diagnostic view from exact saved reader data, without refitting."""
import argparse
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('result',type=Path);args=ap.parse_args()
    raw=json.loads(args.result.read_text(encoding='utf8'))
    out=ROOT/'reports/endpoint_validation_20260922/runtime/viewer';out.mkdir(parents=True,exist_ok=True)
    rows=raw['sections_full'];meta=raw['analysis_quality']['end_treatment']
    data={'length':raw['center_line_length'],'points':[r['position'] for r in rows],
          'trim':[e['trim_length_mm'] for e in meta['ends']],
          'review':[i for i,r in enumerate(rows) if r['measurement_status']=='needs_review'],
          'sections':[dict(s=r['cumulative_length'],p=r['perimeter'],t=r['wall_thickness'],
                           status=r['measurement_status'],side=r.get('extrapolation_side')) for r in rows],
          'basis':('旧版原生截面 + 本次端部处理专项检查' if raw.get('end_stage_validation_basis')
                   else '本次从原始 STP 完整读取；坐标、周长、壁厚直接来自读取结果，未为截图重新拟合。')}
    (out/'data.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
    shutil.copyfile(ROOT/'reports/merge_validation_20260921/models/24TK_1324-2_source.stl',out/'source.stl')
    shutil.copyfile(Path(__file__).with_name('viewer.html'),out/'index.html')
    print(out)

if __name__=='__main__':main()
