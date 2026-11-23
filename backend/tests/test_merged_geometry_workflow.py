"""Compatibility between upstream profile QA and native CAD/mold workflows."""
import copy
import time

import pytest
from flask import Flask

from test_mold_pipeline import measured, rows


def test_dev_reload_keeps_native_geometry_and_quality(tmp_path, monkeypatch):
    from api.pp_dev import _compact_analysis_results
    from core.pp import dev_cache
    from core.rfm.processors import ShapingProcessor
    from core.common.solid_geometry import valid_solid
    from api.rfm import check_measurement_quality

    sections = measured()
    raw = {'algorithm': 'A', 'sections_full': sections,
           'center_line_points': [s['position'] for s in sections],
           'center_line_length': 60.65, 'centerline_endpoint_length': 60.1,
           'analysis_quality': {'engine': 'native_brep_v1', 'review_station_count': 0},
           'wall_thickness_stats': {'median_mm': 2}, 'volume': 123}
    result, _ = _compact_analysis_results(raw, 'fixture.stp', 'A', 25, time.perf_counter())
    monkeypatch.setattr(dev_cache, 'is_dev_tools_enabled', lambda: True)
    monkeypatch.setattr(dev_cache, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(dev_cache, 'CACHE_PATH', str(tmp_path/'current.json'))
    sess = {'results': result, 'original_filename': 'fixture.stp'}
    dev_cache.persist_from_session('fixture', sess)
    restored = dev_cache.session_dict_from_cache(dev_cache.load_cache())['results']
    assert restored['sections_full'][0]['inner_2d'] == sections[0]['inner_2d']
    assert restored['sections_full'][0]['basis_u'] == sections[0]['basis_u']
    assert restored['analysis_quality'] == raw['analysis_quality']
    assert restored['centerline_endpoint_length'] == 60.1
    assert restored['thickness_stats']['median_mm'] == 2
    assert restored['volume'] == 123
    p = ShapingProcessor()
    p.set_sections(restored['sections_full'])
    p.generate_model()
    valid_solid(p.shape)
    restored['analysis_quality']['review_station_count'] = 1
    with pytest.raises(ValueError, match='校核'):
        check_measurement_quality(restored)


def test_save_confirmed_qa_and_wall_curve_preserves_measured_sections(tmp_path, monkeypatch):
    import api.pp as api
    from core.pp.profile_qa.pipeline import initialize_qa_from_records
    from core.pp.thickness_db import get_full_analysis
    monkeypatch.setattr(api, 'PP_TUBE_DB', str(tmp_path/'tubes.db'))
    sections = measured()
    cp = rows()
    result = {'sections_full': copy.deepcopy(sections), 'wall_thickness_data': cp,
              'centerline_perimeter_data': cp, 'centerline_length': 60.65,
              'qa': initialize_qa_from_records(cp, centerline_length=60.65)}
    sid = 'merged_save_fixture'
    monkeypatch.setitem(api._pp_sessions, sid, {'results': result, 'original_filename': 'fixture.stp'})
    app = Flask(__name__)
    app.register_blueprint(api.pp_bp, url_prefix='/api/pp')
    response = app.test_client().post('/api/pp/tubes/save', json={
        'session_id': sid, 'profile_choice': 'auto',
        'manual_segments': {
            'final_curve': [{'s': r['中心线长度(mm)'], 'wall_thickness': 1.8} for r in cp],
            'perimeter_curve': [{'s': r['中心线长度(mm)'], 'perimeter': r['外径周长(mm)']*1.02} for r in cp],
        },
    })
    assert response.status_code == 200, response.json
    saved = get_full_analysis(response.json['tube_id'], api.PP_TUBE_DB)['results']
    assert saved['sections_full'] == sections
    assert saved['wall_thickness_data'][0]['壁厚(mm)'] == 1.8
    assert saved['wall_thickness_data'][0]['x_length'] > 29.9
    assert saved['wall_thickness_data'][0]['y_length'] > 19.9
    assert saved['wall_thickness_data'][0]['外径周长(mm)'] > sections[0]['perimeter']


def test_upstream_tp_dn_mapping_remains_in_effect():
    from api.tp import _tp_params_from_design
    params = {row['参数']: row['值'] for row in _tp_params_from_design({
        'params': {'D0': 70, 'Dn': 50, 'D2': 30, 'L2': 100}
    })}
    assert params['T_D'] == 50
    assert params['D'] == 30
    assert params['T_L'] == 100


def test_qa_only_cache_update_reuses_immutable_native_geometry(tmp_path, monkeypatch):
    from core.pp import dev_cache
    monkeypatch.setattr(dev_cache, 'is_dev_tools_enabled', lambda: True)
    monkeypatch.setattr(dev_cache, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(dev_cache, 'CACHE_PATH', str(tmp_path/'current.json'))
    source = tmp_path/'fixture.stp'
    source.write_text('fixture')
    sess = {'file_path': str(source), 'original_filename': source.name,
            'results': {'sections_full': measured(), 'qa': {'status': 'before'}}}
    first = dev_cache.persist_from_session('fixture', sess, increment_stp=True)
    geometry = tmp_path/first['geometry_file']
    before = geometry.read_bytes()
    monkeypatch.setattr(dev_cache, '_compact_sections', lambda _: pytest.fail('QA must not reserialize native contours'))
    sess['results']['qa'] = {'status': 'after'}
    updated = dev_cache.persist_from_session('fixture', sess, increment_qa=True)
    assert updated['geometry_file'] == first['geometry_file']
    assert geometry.read_bytes() == before
    restored = dev_cache.load_cache()
    assert restored['qa']['status'] == 'after'
    assert restored['sections_full'][0]['outer_2d'] == measured()[0]['outer_2d']
    assert updated['stp_read_count'] == 1 and updated['qa_count'] == 1


def test_tp_design_can_preview_a_native_step_record(tmp_path, monkeypatch):
    from brep_fixtures import tube, write_step
    import api.tp as api
    import core.pp.thickness_db as db
    path = tmp_path/'native.step'
    write_step(tube(12, 10, 60.65), str(path))
    monkeypatch.setattr(api, 'get_design', lambda _: {
        'id': 1, 'source_tube_id': 1, 'params': {'Dn': 24, 'D2': 20}})
    monkeypatch.setattr(db, 'get_full_analysis', lambda _: {
        'source_file_path': str(path), 'results': {'analysis_quality': {}}})
    app = Flask(__name__)
    app.register_blueprint(api.tp_bp, url_prefix='/api/tp')
    response = app.test_client().post('/api/tp/load-design', json={'design_id': 1})
    assert response.status_code == 200
    sid = response.json['session_id']
    assert len(api._tp_stl_analyzers[sid].mesh.vertices) > 20
    api._tp_stl_analyzers.pop(sid)
