"""Exercise only the new end stage on SHA-matched prior native CAD sections."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from core.pp.tube.brep_sections import read_shape,shape_points,contour_thickness
from core.pp.tube.end_treatment import complete_ends,planar_end_faces


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('fixtures',type=Path)
    args=ap.parse_args()
    base=ROOT/'reports/merge_validation_20260921'
    out=ROOT/'reports/endpoint_validation_20260922'
    out.mkdir(exist_ok=True)
    runtime=out/'runtime';runtime.mkdir(exist_ok=True)
    reports=[]
    for path in sorted((base/'measured').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf8'))
        source=args.fixtures/raw['validation']['file']
        assert hashlib.sha256(source.read_bytes()).hexdigest()==raw['validation']['sha256']
        rows=raw['sections_full']
        axis=np.array(rows[-1]['position'])-rows[0]['position'];axis/=np.linalg.norm(axis)
        shape=read_shape(source)
        planes=planar_end_faces(shape,axis)
        if planes is None:
            points=shape_points(shape);projection=points@axis
            planes=[dict(point=(axis*t).tolist(),normal=axis.tolist(),source='axial_support_plane')
                    for t in (projection.min(),projection.max())]
        print('START',path.stem,flush=True)
        corrected,meta=complete_ends(rows,planes)
        offset=meta['reference_offset_mm']
        raw_map={round(r['cumulative_length']+offset,7):r for r in rows}
        for row in corrected:
            if row['measurement_status']!='extrapolated':
                original=raw_map[round(row['cumulative_length'],7)]
                assert row['position']==original['position']
                assert row['perimeter']==original['perimeter']
                assert row['wall_thickness']==original['wall_thickness']
                assert row['outer_2d']==original['outer_2d']
        errors=[]
        for row,plane in zip((corrected[0],corrected[-1]),planes):
            errors.append(abs(float((np.array(row['position'])-plane['point'])@plane['normal'])))
            assert errors[-1]<1e-6
            stats=contour_thickness(np.array(row['outer_2d']),np.array(row['inner_2d']))
            assert abs(stats['median_mm']-row['wall_thickness'])<1e-5
        record=dict(case=path.stem,basis='previous SHA-verified native sections; new end stage only',
                    source_sha256=raw['validation']['sha256'],
                    old_trimmed_length_mm=raw['center_line_length'],full_station_length_mm=meta['reference_length_mm'],
                    end_treatment=meta,stations=len(corrected),endpoint_plane_error_mm=errors,
                    unchanged_measured_interior=True,remaining_review_stations=sum(r['measurement_status']=='needs_review' for r in corrected),
                    endpoint_values=[{k:r[k] for k in ('position','perimeter','wall_thickness','cumulative_length')} for r in (corrected[0],corrected[-1])])
        reports.append(record)
        (out/'saved_cases.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf8')
        raw['sections_full']=corrected
        raw['center_line_points']=[r['position'] for r in corrected]
        raw['center_line_length']=meta['reference_length_mm']
        raw['centerline_endpoint_length']=float(np.linalg.norm(np.array(corrected[-1]['position'])-corrected[0]['position']))
        raw['actual_z_length']=raw['centerline_endpoint_length']
        raw['analysis_quality']['end_treatment']=meta
        raw['analysis_quality']['extrapolated_station_count']=meta['extrapolated_station_count']
        raw['analysis_quality']['review_station_count']=record['remaining_review_stations']
        raw['end_stage_validation_basis']=record['basis']
        (runtime/(path.stem+'_end_stage.json')).write_text(json.dumps(raw,ensure_ascii=False),encoding='utf8')
        print('DONE',path.stem,'full length',meta['reference_length_mm'],'end stations',meta['extrapolated_station_count'],flush=True)


if __name__=='__main__':main()
