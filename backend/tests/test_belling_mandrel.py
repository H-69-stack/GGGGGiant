"""Belling mandrel and cavity share one profile but remain independent outputs."""
import sys
from pathlib import Path

import numpy as np
import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.rfm.processors import BellingProcessor
from core.common.solid_geometry import bounds, volume, valid_solid
from core.pp.tube.brep_sections import read_shape


def profile():
    # Deliberately sparse: the fractional end must be interpolated, not snapped.
    return [{'中心线长度(mm)': s, '外径周长(mm)': 2*np.pi*(20-.1*s), '壁厚(mm)': 2+.01*s}
            for s in (0., 10., 20., 60.)]


def test_mandrel_uses_selected_interval_circle_inner_radius_and_fractional_end(tmp_path):
    p = BellingProcessor()
    p.set_session_data(None, profile())
    p.belling_length = 15.35
    p.wall_thickness_source = 'data'
    p.generate_mandrel()
    p.generate_cavity_model()
    r0, r1 = 18., 18.-.11*15.35
    assert volume(p.shape) == pytest.approx(np.pi*15.35*(r0*r0+r0*r1+r1*r1)/3, rel=1e-8)
    assert bounds(p.shape)[1][2] == pytest.approx(15.35)
    assert bounds(p.cavity_shape)[1][2] == pytest.approx(60)
    assert p.mandrel_profile['stations_mm'] == [0., 10., 15.35]
    assert p.mandrel_profile['diameters_mm'][-1] == pytest.approx(2*r1)
    for s, di, do, t in zip(p.mandrel_profile['stations_mm'], p.mandrel_profile['diameters_mm'],
                            p.mandrel_profile['outer_diameters_mm'], p.mandrel_profile['wall_thickness_mm']):
        assert do == pytest.approx(2*(20-.1*s))
        assert (do-di)/2 == pytest.approx(t)
    path = tmp_path/'mandrel.step'
    path.write_bytes(p.get_step_bytes())
    reread = valid_solid(read_shape(path))
    assert volume(reread) == pytest.approx(volume(p.shape), rel=1e-8)


def test_mandrel_constant_wall_and_allowance_do_not_stretch_profile():
    p = BellingProcessor(); p.set_session_data(None, profile())
    p.belling_length = 15.35; p.length_allowance = 3.2; p.wall_thickness = 1.5
    p.generate_mandrel()
    info = p.mandrel_profile
    assert info['profile_interval_mm'] == [0., 15.35]
    assert info['model_length_mm'] == pytest.approx(18.55)
    assert info['diameters_mm'][-1] == info['diameters_mm'][-2]
    assert info['diameters_mm'][0] == pytest.approx(37.)


@pytest.mark.parametrize('length', [0, 61])
def test_mandrel_invalid_length_fails(length):
    p = BellingProcessor(); p.set_session_data(None, profile()); p.belling_length = length
    with pytest.raises(ValueError, match='扩口长度'): p.generate_mandrel()


def test_missing_wall_cannot_silently_create_solid_tube_outer_form_as_mandrel():
    rows = profile(); rows[1]['壁厚(mm)'] = None
    p = BellingProcessor(); p.set_session_data(None, rows); p.belling_length = 15
    p.wall_thickness_source = 'data'
    with pytest.raises(ValueError, match='壁厚'): p.generate_mandrel()
    p.wall_thickness_source = 'constant'; p.wall_thickness = 100
    with pytest.raises(ValueError, match='半径'): p.generate_mandrel()


@pytest.mark.parametrize('mold_enabled', [False, True])
def test_all_belling_outputs_survive_generation_download_save_and_reload(tmp_path, monkeypatch, mold_enabled):
    import api.rfm as api
    monkeypatch.setattr(api, '_SESS_DIR', str(tmp_path))
    monkeypatch.setattr(api, 'DATA_DIR', str(tmp_path))
    app = Flask(__name__); app.register_blueprint(api.rfm_bp); client = app.test_client()
    with app.test_request_context():
        init = api._init({'centerline_perimeter_data': profile()}, 'belling').get_json()
    sid = init['session_id']
    assert init['config']['wall_thickness_source'] == 'data'
    assert client.post('/api/rfm/process', json={'session_id': sid, 'config': {
        'belling_length': 15.35, 'mold_enabled': mold_enabled, 'mold_margin': 8}}).status_code == 200
    main = client.post('/api/rfm/generate', json={'session_id': sid})
    assert main.status_code == 200, main.json
    assert main.json['model_kind'] == ('mold' if mold_enabled else 'mandrel')
    if not mold_enabled:
        assert client.get('/api/rfm/download-mandrel/'+sid).status_code == 200
    mandrel = client.post('/api/rfm/generate-mandrel', json={'session_id': sid})
    assert mandrel.status_code == 200, mandrel.json
    assert mandrel.json['model_kind'] == 'mandrel'
    assert mandrel.json['mold_info'] is None
    before = api._load_sess(sid)['mandrel_step_bytes_b64']
    assert client.post('/api/rfm/generate-cavity', json={'session_id': sid}).status_code == 200
    persisted = api._load_sess(sid)
    assert persisted['mandrel_step_bytes_b64'] == before
    for kind, length in [('mandrel', 15.35), ('cavity', 60.)]:
        downloaded = client.get('/api/rfm/download-'+kind+'/'+sid)
        assert downloaded.status_code == 200
        path = tmp_path/(kind+'.step'); path.write_bytes(downloaded.data)
        solid = valid_solid(read_shape(path))
        assert bounds(solid)[1][2] == pytest.approx(length)
        assert client.get('/api/rfm/preview-'+kind+'/'+sid).status_code == 200
    assert client.post('/api/rfm/save', json={'session_id': sid}).status_code == 200
    assert client.get('/api/rfm/download/'+sid).status_code == 200
    assert client.post('/api/rfm/process', json={'session_id': sid, 'config': {'belling_length': 20.5}}).status_code == 200
    for route in ('download/', 'download-mandrel/', 'download-cavity/'):
        assert client.get('/api/rfm/'+route+sid).status_code == 400
    persisted['analysis_quality'] = {'review_station_count': 1}
    api._save_sess(sid, persisted)
    assert client.post('/api/rfm/generate-mandrel', json={'session_id': sid}).status_code == 200


def test_design_wall_default_is_authoritative_and_bad_source_rejected(tmp_path, monkeypatch):
    import api.rfm as api
    monkeypatch.setattr(api, '_SESS_DIR', str(tmp_path))
    app = Flask(__name__); app.register_blueprint(api.rfm_bp)
    with app.test_request_context():
        body = api._init({'centerline_perimeter_data': profile()}, 'belling', {'params': {'T3': 1.6}}).get_json()
    assert body['config']['wall_thickness_source'] == 'constant'
    assert body['config']['wall_thickness'] == 1.6
    with pytest.raises(ValueError, match='壁厚来源'):
        api._config({'wall_thickness_source': 'unknown'})
