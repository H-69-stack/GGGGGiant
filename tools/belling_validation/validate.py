"""Real-case belling regression through production APIs and exported CAD sections."""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'reports/belling_validation_20260923'
RUNTIME = OUT/'runtime'
RUNTIME.mkdir(parents=True, exist_ok=True)
os.environ['GTAPP_DATA_DIR'] = str(RUNTIME)
sys.path.insert(0, str(ROOT/'backend'))
import config
config.PP_TUBE_DB = str(RUNTIME/'tubes.db')
config.PP_DESIGN_DB = str(RUNTIME/'designs.db')
import numpy as np
from flask import Flask, jsonify
from api.pp import pp_bp
from api.rfm import rfm_bp, _load_sess
from core.pp.thickness_db import save_full_analysis
from core.pp.design_db import save_design
from core.pp.tube.brep_sections import read_shape
from core.common.solid_geometry import bounds, valid_solid, volume

app = Flask('belling_fixture_validation')
app.register_blueprint(pp_bp, url_prefix='/api/pp')
app.register_blueprint(rfm_bp)


def seed():
    raw = json.loads((ROOT/'reports/endpoint_validation_20260922/runtime/24TK_1324-1_end_stage.json').read_text(encoding='utf8'))
    fixture = ROOT.parent.parent/'Desktop/GIANT相关/测试管件stp和pdf/测试管件stp和pdf'/raw['validation']['file']
    # ROOT is inside PycharmProjects, so the fixtures are under the same user.
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == raw['validation']['sha256']
    cp = [{'中心线长度(mm)': r['cumulative_length'], '外径周长(mm)': r['perimeter'], '壁厚(mm)': r['wall_thickness']}
          for r in raw['sections_full']]
    quality = dict(raw['analysis_quality'])
    quality['warnings'] = ['测试数据沿用已验证的原生 CAD 截面，并已应用端部外推。']
    result = {'centerline_length': raw['center_line_length'], 'centerline_perimeter_data': cp,
              'wall_thickness_data': cp, 'analysis_quality': quality}
    tid, error = save_full_analysis(file_name=fixture.name, display_name='24TK_1324-1 · 扩口验证',
        source_file_path=str(fixture), source_file_type='stp', file_hash=raw['validation']['sha256'],
        payload=result, db_path=config.PP_TUBE_DB)
    if error: raise RuntimeError(error)
    did, error = save_design(display_name='24TK_1324-1 · 扩口 80.5 mm', vehicle_spec='实测案例验证',
        tube_number='24TK_1324-1', pp_method='普通抽+TP抽+扩口', source_tube_id=tid,
        source_file_name=fixture.name, source_file_path=str(fixture), solution={'L1':80.5},
        fitness=0, area_change_rate=None, extension_ratio=None, centerline_perimeter_data=cp,
        extra_params={'L1':80.5}, db_path=config.PP_DESIGN_DB)
    if did < 0: raise RuntimeError(error)
    return did, cp, raw['validation']


def section_diameter(shape, z):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pln, gp_Pnt, gp_Dir
    section = BRepAlgoAPI_Section(shape, gp_Pln(gp_Pnt(0,0,z),gp_Dir(0,0,1)), False)
    section.Build()
    assert section.IsDone()
    prop = GProp_GProps(); BRepGProp.LinearProperties_s(section.Shape(), prop)
    return prop.Mass()/np.pi


def validate():
    did, cp, source = seed()
    client = app.test_client()
    init = client.post('/api/rfm/init-from-design', json={'design_id':did, 'operation':'belling'})
    assert init.status_code == 200, init.json
    sid = init.json['session_id']
    cfg = {'belling_length':80.5, 'length_allowance':0, 'wall_thickness_source':'data',
           'mold_enabled':True, 'mold_auto_size':True, 'mold_margin':15}
    assert client.post('/api/rfm/process', json={'session_id':sid, 'config':cfg}).status_code == 200
    models = OUT/'models'; models.mkdir(exist_ok=True)
    report = {'case':'24TK_1324-1', 'design_id':did, 'session_id':sid, 'source':source,
              'basis':'SHA-verified prior native CAD sections with validated end completion; no full STP reread',
              'config':cfg, 'operations':{}}
    shapes = {}
    for kind, route in [('mandrel','-mandrel'), ('cavity','-cavity'), ('mold','')]:
        print('GENERATE', kind, flush=True)
        response = client.post('/api/rfm/generate'+route, json={'session_id':sid})
        assert response.status_code == 200, response.json
        step = client.get('/api/rfm/download'+route+'/'+sid)
        stl = client.get('/api/rfm/preview'+route+'/'+sid)
        assert step.status_code == stl.status_code == 200
        path = models/('24TK_1324-1_'+kind+'.step'); path.write_bytes(step.data)
        (models/('24TK_1324-1_'+kind+'.stl')).write_bytes(stl.data)
        shape = valid_solid(read_shape(path)); shapes[kind] = shape
        report['operations'][kind] = dict(status='passed', response=response.json,
            step_sha256=hashlib.sha256(step.data).hexdigest(), volume_mm3=volume(shape),
            bounds_mm=[v.tolist() for v in bounds(shape)])
        (OUT/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    assert abs(bounds(shapes['mandrel'])[1][2] - 80.5) < 1e-6
    stations = np.array([r['中心线长度(mm)'] for r in cp])
    report['cad_section_checks'] = []
    for z in [0.5, 20.25, 40.25, 60.25, 80.25]:
        per = np.interp(z,stations,[r['外径周长(mm)'] for r in cp])
        wall = np.interp(z,stations,[r['壁厚(mm)'] for r in cp])
        dm, dc = section_diameter(shapes['mandrel'],z), section_diameter(shapes['cavity'],z)
        error = max(abs(dm-(per/np.pi-2*wall)),abs(dc-per/np.pi))
        # CAD plane/near-cylindrical cone intersections use kernel tolerances.
        # 0.0001 mm is a sub-micron bound, well below display/manufacturing units.
        assert error < 1e-4, (z, dm, dc, error)
        report['cad_section_checks'].append(dict(station_mm=z, perimeter_mm=per, wall_mm=wall,
            mandrel_diameter_mm=dm, cavity_diameter_mm=dc, radial_gap_mm=(dc-dm)/2, error_mm=error))
    # Generate the mold last: it must not overwrite either paired result.
    persisted = _load_sess(sid)
    assert persisted['mandrel_generated'] and persisted['cavity_generated'] and persisted['generated']
    assert client.post('/api/rfm/save', json={'session_id':sid}).status_code == 200
    (OUT/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    (RUNTIME/'fixture.json').write_text(json.dumps({'design_id':did}), encoding='utf8')
    print('PASSED',[(r['station_mm'],r['error_mm']) for r in report['cad_section_checks']],flush=True)


@app.get('/api/validation/belling-fixture')
def fixture_info():
    return jsonify(json.loads((RUNTIME/'fixture.json').read_text(encoding='utf8')))


if __name__ == '__main__':
    if '--serve' in sys.argv:
        app.run(host='127.0.0.1', port=5056, use_reloader=False, threaded=True)
    else:
        validate()
