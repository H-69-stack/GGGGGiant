"""Exercise merged production APIs on one freshly measured, user-supplied STEP."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get('GTAPP_VALIDATION_OUTPUT', ROOT/'reports/merge_validation_20260921'))
RUNTIME = OUT/'runtime'
os.environ['GTAPP_DATA_DIR'] = str(RUNTIME)
sys.path.insert(0, str(ROOT/'backend'))
import config
config.PP_TUBE_DB = str(RUNTIME/'tubes.db')
config.PP_DESIGN_DB = str(RUNTIME/'designs.db')
import numpy as np
from flask import Flask
from api.pp_dev import _compact_analysis_results
from api.pp import pp_bp
from api.rfm import rfm_bp
from api.tp import tp_bp
from core.pp.thickness_db import save_full_analysis
from core.pp.design_db import save_design
from core.pp.tube.brep_sections import read_shape
from core.common.solid_geometry import valid_solid, volume
from core.pp.mesh_converter import shape_to_mesh
import trimesh


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('case')
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--measured', type=Path, help='Explicit measured result JSON; records its provenance')
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    models = OUT/'models'
    models.mkdir(exist_ok=True)
    raw = json.loads((args.measured or OUT/'measured'/(args.case+'.json')).read_text(encoding='utf8'))
    record = {'case': args.case, 'source': raw['validation'],
              'quality': raw['analysis_quality'], 'operations': {}}
    record['measurement_basis'] = raw.get('end_stage_validation_basis', 'full native STEP read')
    if raw.get('end_stage_validation_basis'):
        # End-only fixtures reuse old measurements; keep the old quality text
        # separate so "ends were excluded" cannot describe the completed model.
        previous_quality = record.pop('quality')
        record['prior_measurement_quality'] = previous_quality
        record['quality'] = {
            'engine': previous_quality['engine'],
            'end_treatment': previous_quality['end_treatment'],
            'review_station_count': previous_quality['review_station_count'],
            'rejected_sections': previous_quality.get('rejected_sections', []),
        }
    stp = args.fixtures/raw['validation']['file']
    assert hashlib.sha256(stp.read_bytes()).hexdigest() == raw['validation']['sha256']
    preview = shape_to_mesh(read_shape(stp), tolerance=.12, angular_tolerance=.2)
    trimesh.Trimesh(np.array(preview['vertices']).reshape(-1, 3),
                   np.array(preview['faces']).reshape(-1, 3)).export(models/(args.case+'_source.stl'))
    result, _ = _compact_analysis_results(raw, stp.name, 'A', 25, time.perf_counter())
    cp = result['centerline_perimeter_data']
    large = max(cp, key=lambda r: r['外径周长(mm)'])
    small = min(cp, key=lambda r: r['外径周长(mm)'])
    large_d = large['外径周长(mm)']/np.pi
    small_d = small['外径周长(mm)']/np.pi
    tp_length = abs(large['中心线长度(mm)']-small['中心线长度(mm)'])
    record['measurements'] = {
        'length_mm': raw['center_line_length'], 'stations': len(raw['sections_full']),
        'perimeter_min_mm': small['外径周长(mm)'], 'perimeter_max_mm': large['外径周长(mm)'],
        'wall_median_mm': raw['wall_thickness_stats']['median_mm'],
    }
    record['tp_inputs'] = {'T_D_mm': large_d, 'D_mm': small_d, 'T_L_mm': tp_length,
                           'large_station_mm': large['中心线长度(mm)'],
                           'small_station_mm': small['中心线长度(mm)'],
                           'basis': 'measured_extrema_for_geometry_regression'}
    tid, error = save_full_analysis(file_name=stp.name, display_name=args.case,
                                    source_file_path=str(stp), source_file_type='stp',
                                    file_hash=raw['validation']['sha256'], payload=result,
                                    db_path=config.PP_TUBE_DB)
    if error: raise RuntimeError(error)
    params = {'Dn': large_d, 'D0': large_d+5, 'D2': small_d, 'L2': tp_length,
              'LL': raw['center_line_length'], 'L1': 100,
              'T1': record['measurements']['wall_median_mm'],
              'T2': record['measurements']['wall_median_mm'],
              'T3': record['measurements']['wall_median_mm']}
    did, _ = save_design(display_name=args.case+' · 合并回归', vehicle_spec='几何回归',
                         tube_number=args.case, pp_method='普通抽+TP抽+扩口',
                         source_tube_id=tid, source_file_name=stp.name, source_file_path=str(stp),
                         solution=params, fitness=0, area_change_rate=None, extension_ratio=None,
                         centerline_perimeter_data=cp, extra_params=params, db_path=config.PP_DESIGN_DB)
    if did < 0: raise RuntimeError('design save failed')
    record.update(tube_id=tid, design_id=did, qa_status=result['qa']['status'])
    app = Flask('merged_geometry_regression')
    app.register_blueprint(pp_bp, url_prefix='/api/pp')
    app.register_blueprint(rfm_bp)
    app.register_blueprint(tp_bp, url_prefix='/api/tp')
    client = app.test_client()
    for kind in ('forming', 'mold', 'tp'):
        started = time.monotonic()
        print(args.case, kind, flush=True)
        info = {'status': 'failed'}
        try:
            if kind == 'tp':
                loaded = client.post('/api/tp/load-design', json={'design_id': did})
                if loaded.status_code != 200: raise ValueError(loaded.json['message'])
                payload = loaded.json
                if not payload.get('profile_ready'): raise ValueError(payload.get('profile_error'))
                generated = client.post('/api/tp/generate-mold', json={
                    'design_id': did, 'centerline_perimeter_data': cp,
                    **{k: payload[k] for k in ['T_D_length', 'D_length', 'T_D_diam', 'D_diam']},
                })
                if generated.status_code != 200: raise ValueError(generated.json['message'])
                body = generated.json
                step_url, stl_url = body['step_download_url'], body['stl_download_url']
                info['profile_points'] = len(payload['profile_data'])
                info['profile'] = {k: payload[k] for k in ['T_D_length', 'D_length', 'T_D_diam', 'D_diam', 'T_L']}
            else:
                init = client.post('/api/rfm/init-from-design', json={'design_id': did, 'operation': 'shaping'})
                if init.status_code != 200: raise ValueError(init.json['message'])
                sid = init.json['session_id']
                processed = client.post('/api/rfm/process', json={'session_id': sid,
                    'config': {'mold_enabled': kind == 'mold', 'mold_auto_size': True, 'mold_margin': 15}})
                if processed.status_code != 200: raise ValueError(processed.json['message'])
                generated = client.post('/api/rfm/generate', json={'session_id': sid})
                if generated.status_code != 200: raise ValueError(generated.json['message'])
                body = generated.json
                step_url, stl_url = '/api/rfm/download/'+sid, '/api/rfm/preview/'+sid
                saved = client.post('/api/rfm/save', json={'session_id': sid})
                if saved.status_code != 200: raise ValueError(saved.json['message'])
            step_response, stl_response = client.get(step_url), client.get(stl_url)
            assert step_response.status_code == stl_response.status_code == 200
            assert step_response.data.startswith(b'ISO-10303-21')
            step = models/(args.case+'_'+kind+'.step')
            step.write_bytes(step_response.data)
            (models/(args.case+'_'+kind+'.stl')).write_bytes(stl_response.data)
            reread = valid_solid(read_shape(step))
            info.update(status='passed', volume_mm3=volume(reread), vertices=body['vertices'],
                        faces=body['faces'], model_kind=body.get('model_kind'), mold_info=body.get('mold_info'),
                        step_sha256=hashlib.sha256(step_response.data).hexdigest())
        except Exception as exc:
            info.update(status='blocked' if '校核' in str(exc) else 'failed', error=str(exc))
        info['seconds'] = round(time.monotonic()-started, 3)
        record['operations'][kind] = info
        (OUT/(args.case+'.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
        print(kind, info['status'], info.get('error', ''), flush=True)


if __name__ == '__main__':
    main()
