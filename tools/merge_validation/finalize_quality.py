"""Reapply the fixed quality stage to this run's raw measurements, without filling geometry."""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'backend'))
from core.pp.tube.brep_sections import read_shape, shape_points
from core.pp.tube.geometry import pca_long_axis
from core.pp.tube.brep_engine import finalize_section_quality


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--case', default='*')
    args = parser.parse_args()
    out = ROOT/'reports/merge_validation_20260921'
    for path in sorted((out/'measured').glob(args.case+'.json')):
        result = json.loads(path.read_text(encoding='utf8'))
        quality = result['analysis_quality']
        if quality.get('post_trim_quality_version') == 2:
            continue
        source = args.fixtures/result['validation']['file']
        assert hashlib.sha256(source.read_bytes()).hexdigest() == result['validation']['sha256']
        axis = pca_long_axis(shape_points(read_shape(source)))
        normals, rejected, excluded = finalize_section_quality(result['sections_full'], quality['rejected_sections'], axis)
        quality.update(normals)
        quality.update(rejected_sections=rejected, excluded_end_rejections=excluded,
                       rejection_counts=dict(Counter(r['reason'] for r in rejected)),
                       review_station_count=sum(r['measurement_status'] == 'needs_review' for r in result['sections_full']),
                       post_trim_quality_version=2)
        quality['warnings'] = [w for w in quality['warnings'] if '有效范围内' not in w and '个截面的法向' not in w]
        if rejected:
            quality['warnings'].append(f'有效范围内 {len(rejected)} 个截面测量失败；保留缺测记录，不填造数值。')
        if quality['review_station_count']:
            quality['warnings'].append(f"{quality['review_station_count']} 个截面的法向或最薄壁位置需人工复核，不能直接用于工艺计算。")
        quality['requires_review'] = bool(quality['warnings'])
        result['validation']['quality_finalized_after_read'] = True
        path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf8')
        print(path.stem, 'internal missing', len(rejected), 'excluded end failures', len(excluded), 'review', quality['review_station_count'], flush=True)
        # Refresh the report metadata only; geometry and operation outcomes are
        # re-generated separately through the production endpoints if needed.
        record_path = out/(path.stem+'.json')
        if record_path.exists():
            record = json.loads(record_path.read_text(encoding='utf8'))
            record['quality'], record['source'] = quality, result['validation']
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
