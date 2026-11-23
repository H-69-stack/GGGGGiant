"""Analytic CAD dimensions, actual booleans, data restoration and API regressions."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.rfm.processors import BellingProcessor, BendingProcessor, FlatteningProcessor, ShapingProcessor, ellipse_perimeter
from core.common.solid_geometry import valid_solid, volume, bounds, cut_mold, step_bytes
from core.tp.mold_generator import TPMoldProcessor, select_tp_segment
from core.pp.tube.brep_sections import read_shape


def rows(length=60.65, a=15., b=10.):
    return [{'中心线长度(mm)': float(z), '外径周长(mm)': ellipse_perimeter(a,b),
             'x_length': 2*a, 'y_length': 2*b, '壁厚(mm)': 2.}
            for z in np.linspace(0,length,7)]


def measured(length=60.65):
    theta = np.arange(192)*2*np.pi/192
    outer = np.column_stack((15*np.cos(theta),10*np.sin(theta)))
    inner = np.column_stack((13*np.cos(theta)+.4,8*np.sin(theta)))
    return [{'position':[0,0,float(z)], 'normal':[0,0,1], 'basis_u':[1,0,0], 'basis_v':[0,1,0],
             'outer_2d':outer.tolist(),'inner_2d':inner.tolist(), 'cumulative_length':float(z),
             'perimeter':ellipse_perimeter(15,10), 'wall_thickness':2., 'measurement_status':'measured'}
            for z in np.linspace(0,length,7)]


def test_belling_fractional_end_true_circle_and_allowance():
    p = BellingProcessor(); p.set_session_data(None,rows(a=10,b=10))
    p.belling_length=60.65; p.length_allowance=3.25
    p.generate_model(); p.generate_cavity_model()
    assert volume(p.shape) == pytest.approx(np.pi*8**2*63.9,rel=1e-8)
    assert volume(p.cavity_shape) == pytest.approx(np.pi*10**2*60.65,rel=1e-8)
    assert bounds(p.shape)[1][2] == pytest.approx(63.9)


def test_flatten_preserves_axes_and_perimeter_through_transition():
    p=FlatteningProcessor();p.set_session_data(None,rows());p.flattening_length=20;p.transition_length=20
    p.generate_model()
    assert p.generated_axes[0] == pytest.approx([15,10])
    for a,b in p.generated_axes:
        assert ellipse_perimeter(a,b) == pytest.approx(ellipse_perimeter(15,10),rel=1e-10)
    assert p.generated_axes[-1][0] == pytest.approx(p.generated_axes[-1][1])
    valid_solid(p.shape)


def test_bend_keeps_flattening_sections():
    p=FlatteningProcessor();p.set_session_data(None,rows());p.flattening_length=20;p.transition_length=20
    flat_s, flat_dims=p.section_dimensions()
    q=BendingProcessor();q.set_session_data(np.array([[0,0,0],[0,0,60.65]]),rows())
    q.flattening_length=20;q.transition_length=20;q.flattening_executed=True;q.num_sections=p.num_sections
    q.generate_model()
    assert q.generated_stations == pytest.approx(flat_s)
    assert q.generated_axes == pytest.approx(flat_dims)
    # Two-point centerlines no longer crash cubic interpolation.
    valid_solid(q.shape)


def test_shaping_uses_outer_and_eccentric_inner_measured_contours():
    p=ShapingProcessor();p.set_sections(measured());p.generate_model();p.generate_inner_mold()
    assert volume(p.shape) == pytest.approx(np.pi*15*10*60.65,rel=.0005)
    assert volume(p.inner_shape) == pytest.approx(np.pi*13*8*60.65,rel=.0005)
    lo,hi=bounds(p.inner_shape)
    assert (lo[0]+hi[0])/2 == pytest.approx(.4,abs=.001)


@pytest.mark.parametrize('kind',['belling','flattening','bending','shaping'])
def test_all_four_reverse_cut_and_step_roundtrip(kind,tmp_path):
    from core.rfm.processors import get_processor
    p=get_processor(kind);p.set_session_data(np.array([[0,0,0],[0,0,60.65]]),rows())
    p.set_sections(measured())
    if kind=='belling':
        p.generate_cavity_model();cavity=p.cavity_shape
    else:
        if kind=='flattening':p.flattening_length=20;p.transition_length=20
        p.generate_model();cavity=p.shape
    mold,info=cut_mold(cavity,{'mold_margin':15})
    assert info['minimum_margin_mm'] >= 15-1e-6
    assert volume(mold)+volume(cavity) == pytest.approx(np.prod(info['size_xyz_mm']),rel=1e-7)
    path=tmp_path/(kind+'.step');path.write_bytes(step_bytes(mold))
    reread=read_shape(path);valid_solid(reread)
    assert volume(reread) == pytest.approx(volume(mold),rel=1e-7)
    with pytest.raises(ValueError,match='至少大'):
        cut_mold(cavity,{'mold_auto_size':False,'mold_size_x':1,'mold_size_y':1,'mold_size_z':1})


def test_missing_axes_and_inconsistent_dimensions_fail_explicitly():
    p=FlatteningProcessor();p.flattening_length=20;p.transition_length=20
    data=rows()
    for r in data:r.pop('x_length');r.pop('y_length')
    p.set_session_data(None,data)
    with pytest.raises(ValueError,match='缺少'):
        p.generate_model()
    p.ellipse_x=30
    with pytest.raises(ValueError,match='同时填写'):
        p.generate_model()
    p.ellipse_x=20;p.ellipse_y=10
    with pytest.raises(ValueError,match='不一致'):
        p.generate_model()


def test_fractional_sampling_does_not_create_zero_width_end_face():
    p=FlatteningProcessor();p.set_session_data(None,rows(400));p.num_sections=200
    p.flattening_length=100;p.transition_length=150
    p.generate_model()
    assert np.min(np.diff(p.generated_stations)) > 1e-7
    assert p.mesh.is_watertight


def test_tp_clips_axial_interval_and_revolves_closed_material(tmp_path):
    data=[{'中心线长度(mm)':z,'外径周长(mm)':d*np.pi} for z,d in [(0,60),(10,50),(20,45),(30,47),(40,30),(80,20)]]
    segment=select_tp_segment(data,10,40,50,30)
    assert [r['中心线长度(mm)'] for r in segment] == [0,10,20,30]
    assert [r['外径周长(mm)']/np.pi for r in segment] == pytest.approx([50,45,47,30])
    p=TPMoldProcessor();p.set_profile_data(segment);p.set_tp_params(30,50,30);p.generate_model()
    valid_solid(p.shape)
    lo,hi=bounds(p.shape);assert hi[2] == pytest.approx(57)
    # Bore stays open along the axis: cut from the shape removes no material.
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    common=BRepAlgoAPI_Common(p.shape,BRepPrimAPI_MakeCylinder(10,57).Shape()).Shape()
    assert abs(volume(common)) < 1e-6
    path=tmp_path/'tp.step';path.write_bytes(p.get_step_bytes());valid_solid(read_shape(path))


def test_tp_profile_preserves_non_monotone_axial_shape_and_rejects_unmatched_design():
    from core.common.profile_compute import compute_tp_profile
    data=[{'中心线长度(mm)':z,'外径周长(mm)':d*np.pi} for z,d in [(0,60),(10,50),(20,45),(30,47),(40,30),(80,20)]]
    result=compute_tp_profile(data,{'T_D':50,'D':30,'T_L':30})
    assert result['success'],result
    assert [v['X'] for v in result['profile_data']] == pytest.approx([9,19,29,39])
    assert [v['Y'] for v in result['profile_data']] == pytest.approx([30,47,45,50])
    assert not compute_tp_profile(data,{'T_D':70,'D':30})['success']


def test_tp_profile_excludes_l4_straight_before_locating():
    from core.common.profile_compute import compute_tp_profile
    # 整管 80，大小径在两端；工艺 T_L = L−L4 = 50。不扣 L4 会拿整管 80 而对不上。
    data = [{'中心线长度(mm)': z, '外径周长(mm)': d * np.pi}
            for z, d in [(0, 50), (40, 40), (80, 30)]]
    without = compute_tp_profile(data, {'T_D': 50, 'D': 30, 'T_L': 50})
    assert not without['success']
    with_l4 = compute_tp_profile(data, {'T_D': 50, 'D': 30, 'T_L': 50, 'L4': 30})
    assert with_l4['success'], with_l4
    assert abs(with_l4['T_L'] - 50) <= 2
    large_end = compute_tp_profile(
        [{'中心线长度(mm)': z, '外径周长(mm)': d * np.pi}
         for z, d in [(0, 50), (30, 50), (80, 30)]],
        {'T_D': 50, 'D': 30, 'T_L': 50, 'L4': 30},
    )
    assert large_end['success'], large_end
    assert abs(large_end['T_L'] - 50) <= 2


def test_curved_bending_keeps_ellipse_dimensions():
    p=BendingProcessor();p.flattening_executed=True;p.flattening_length=60.65;p.transition_length=0
    angle=np.linspace(0,60.65/120,61)
    centerline=np.column_stack((120*(1-np.cos(angle)),np.zeros(61),120*np.sin(angle)))
    p.set_session_data(centerline,rows());p.generate_model()
    assert p.generated_axes == pytest.approx(np.tile([15,10],(len(p.generated_axes),1)))
    assert bounds(p.shape)[1][0] > 20
    valid_solid(p.shape)


def test_tp_second_clip_mold_keeps_its_own_dxf_template(monkeypatch):
    from core.tp.molds import tp_molds
    if tp_molds.ezdxf is None:
        pytest.skip('ezdxf unavailable')
    seen=[]
    def read(path):
        seen.append(path)
        raise IOError('fixture does not open a real template')
    monkeypatch.setattr(tp_molds.ezdxf,'readfile',read)
    mold=tp_molds.DC0128_AD01_();mold.change=True;mold.parameters['图号']='DC0128-AD010001'
    mold.modify_dxf()
    assert Path(seen[0]).name=='AD01_.dxf'


def test_tp_api_selects_interval_exports_solid_and_rejects_bad_profile(tmp_path,monkeypatch):
    import api.tp as api
    monkeypatch.setattr(api,'DATA_DIR',str(tmp_path))
    app=Flask(__name__);app.register_blueprint(api.tp_bp,url_prefix='/api/tp');client=app.test_client()
    data=[{'中心线长度(mm)':z,'外径周长(mm)':d*np.pi} for z,d in [(0,60),(10,50),(20,45),(30,47),(40,30),(80,20)]]
    payload={'centerline_perimeter_data':data,'T_D_length':10,'D_length':40,'T_D_diam':50,'D_diam':30}
    response=client.post('/api/tp/generate-mold',json=payload)
    assert response.status_code==200,response.json
    downloaded=client.get(response.json['step_download_url'])
    assert downloaded.data.startswith(b'ISO-10303-21')
    path=tmp_path/'roundtrip.step';path.write_bytes(downloaded.data)
    solid=read_shape(path);valid_solid(solid)
    assert bounds(solid)[1][2]==pytest.approx(57)
    # Full data without explicit interval must not silently become a whole-tube mold.
    del payload['T_D_length']
    assert client.post('/api/tp/generate-mold',json=payload).status_code==400


def test_rfm_init_uses_corrected_formline_not_stp_section_length(tmp_path, monkeypatch):
    import api.rfm as api
    monkeypatch.setattr(api, '_SESS_DIR', str(tmp_path))
    monkeypatch.setattr(api, 'DATA_DIR', str(tmp_path))
    app = Flask(__name__)
    app.register_blueprint(api.rfm_bp)
    overlay = [{'中心线长度(mm)': z, '外径周长(mm)': 50 * np.pi, 'x_length': 50, 'y_length': 50, '壁厚(mm)': 2}
               for z in (0.0, 500.0, 1000.0)]
    result = {
        'sections_full': [{'cumulative_length': 0.0, 'position': [0, 0, 0], 'perimeter': 50 * np.pi},
                          {'cumulative_length': 1050.0, 'position': [0, 0, 1050], 'perimeter': 50 * np.pi}],
        'wall_thickness_data': overlay,
        'centerline_length': 1000.0,
        'actual_z_length': 1040.0,
        'analysis_quality': {},
        'length_validation': {
            'status': 'trimmed',
            'correction_side': 'end',
            'small_end_correction_mm': -50,
            'large_end_correction_mm': 0,
        },
    }
    design = {'id': 1, 'params': {'LL': 1000, 'L1': 120, 'T3': 2},
              'centerline_perimeter_data': overlay}
    with app.test_request_context():
        initialized = api._init(result, 'belling', design).get_json()
    assert initialized['success']
    assert initialized['centerline_length'] == pytest.approx(1000)
    sess = api._load_sess(initialized['session_id'])
    assert sess['sections_full'][-1]['cumulative_length'] == pytest.approx(1000)
    assert sess['centerline'][-1][2] == pytest.approx(1000)


def test_api_restores_geometry_config_invalidates_outputs_and_blocks_bad_measurements(tmp_path,monkeypatch):
    import api.rfm as api
    monkeypatch.setattr(api,'_SESS_DIR',str(tmp_path));monkeypatch.setattr(api,'DATA_DIR',str(tmp_path))
    app=Flask(__name__);app.register_blueprint(api.rfm_bp);client=app.test_client()
    result={'sections_full':measured(),'wall_thickness_data':rows(),'analysis_quality':{}}
    with app.test_request_context():
        initialized=api._init(result,'shaping').get_json()
    sid=initialized['session_id']
    assert client.post('/api/rfm/process',json={'session_id':sid,'config':{'mold_enabled':True,'mold_margin':12}}).status_code == 200
    response=client.post('/api/rfm/generate',json={'session_id':sid})
    assert response.status_code == 200,response.json
    assert response.json['model_kind']=='mold'
    assert client.get('/api/rfm/download/'+sid).data.startswith(b'ISO-10303-21')
    assert client.post('/api/rfm/save',json={'session_id':sid}).status_code==200
    catalog=json.loads((tmp_path/'rfm_shaping/shaping_database.json').read_text(encoding='utf8'))
    assert catalog[sid]['mold_info']['minimum_margin_mm']>=12-1e-6
    assert client.post('/api/rfm/process',json={'session_id':sid,'config':{'mold_enabled':False}}).status_code == 200
    assert client.get('/api/rfm/download/'+sid).status_code == 400
    assert client.post('/api/rfm/process',json={'session_id':sid,'config':{'mold_margin':-1}}).status_code == 400
    sess=api._load_sess(sid);sess['analysis_quality']['review_station_count']=1;api._save_sess(sid,sess)
    assert client.post('/api/rfm/generate',json={'session_id':sid}).status_code == 200
    assert client.get('/api/rfm/preview/..').status_code == 400


def test_legacy_tube_listing_preserves_field_names_and_null_lengths(monkeypatch):
    import pandas as pd
    import api.rfm as api
    monkeypatch.setattr(api,'list_database_tubes',lambda _:pd.DataFrame([
        {'ID':7,'显示名称':'fixture','文件名':'fixture.stp','中心线长度(mm)':np.nan}]))
    app=Flask(__name__);app.register_blueprint(api.rfm_bp)
    response=app.test_client().get('/api/rfm/db-tubes')
    assert response.json['tubes']==[{'id':7,'display_name':'fixture','file_name':'fixture.stp','centerline_length':0}]
