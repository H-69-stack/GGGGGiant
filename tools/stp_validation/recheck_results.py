"""Re-measure missing stations with finer contours; recompute quality on real arc spacing."""
import argparse,json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from core.pp.tube.brep_sections import read_shape,section_shape
from core.pp.tube.brep_engine import verify_section_normals,_arc,recover_missing_sections
from core.pp.tube.geometry import sparse_indices_by_arc_length


def main():
    pa=argparse.ArgumentParser();pa.add_argument('fixtures',type=Path);pa.add_argument('report',type=Path);args=pa.parse_args()
    for f in sorted((args.report/'measured').glob('*.json')):
        r=json.loads(f.read_text(encoding='utf-8'));q=r['analysis_quality']
        if q.get('final_recheck'):continue
        shape=read_shape(args.fixtures/(f.stem+'.stp'))
        rows,failed,recovered=recover_missing_sections(shape,r['sections_full'])
        for row in rows: row['measurement_status']='measured'
        q['centroid_method']='CAD_intersection_contour_area_at_0.003mm_deflection'
        q.update(verify_section_normals(rows))
        pts=np.array([s['position'] for s in rows]);arc=_arc(pts)
        for i,(s,t) in enumerate(zip(rows,arc)):
            s['index']=i;s['cumulative_length']=float(t)
            if s['normal_deviation_deg']>2 or s['wall_thickness_min']<.1*s['wall_thickness'] or s['centroid_residual_mm']>.2:s['measurement_status']='needs_review'
        q['rejected_sections']=failed;q['review_station_count']=sum(s['measurement_status']=='needs_review' for s in rows)
        q['recovered_stations']=sum(bool(row.get('recovered_station')) for row in rows);q['final_recheck']=True
        q['rejection_counts']={k:sum(x['reason']==k for x in failed) for k in set(x['reason'] for x in failed)}
        q['warnings']=[f"端部轴向约 {sum(q['excluded_axial_ends_mm']):.2f} mm 未给出可靠法向测量；长度为有效测量段弧长。"]
        if failed:q['warnings'].append(f"内部 {len(failed)} 个位置仍缺测，未插值补造周长或壁厚。")
        if q['review_station_count']:q['warnings'].append(f"{q['review_station_count']} 个截面法向/最薄壁需复核，自动工艺应用被阻止。")
        r['sections_full']=rows;r['center_line_points']=pts.tolist();r['center_line_length']=float(arc[-1]);r['centerline_endpoint_length']=r['actual_z_length']=float(np.linalg.norm(pts[-1]-pts[0]))
        r['sections']=[{k:rows[i][k] for k in ['position','perimeter','wall_thickness','cumulative_length','index']}|{'outline':rows[i]['outline_3d']+[rows[i]['outline_3d'][0]]} for i in sparse_indices_by_arc_length(arc,25)]
        wt=np.array([s['wall_thickness'] for s in rows]);r['wall_thickness_stats']={'median_mm':float(np.median(wt)),'mean_mm':float(wt.mean()),'min_mm':float(wt.min()),'max_mm':float(wt.max()),'count':len(wt)}
        f.write_text(json.dumps(r,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        print(f.stem,'recovered',recovered,'missing',len(failed),'review',q['review_station_count'],'angle',q['max_normal_deviation_deg'],flush=True)


if __name__=='__main__':main()
