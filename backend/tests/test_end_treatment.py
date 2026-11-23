"""End repair invariants: fixed stations, linear values and matching CAD rings."""
import copy
import sys
from pathlib import Path
import numpy as np
import pytest
from shapely.geometry import Polygon

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from core.pp.tube.end_treatment import complete_ends, end_options
from core.pp.tube.brep_sections import contour_thickness
from core.pp.tube.geometry import trim_and_extrapolate_centerline


def measured_rows():
    rows=[]
    angles=np.arange(96)*2*np.pi/96
    normal=np.array([.12,.02,1.]);normal/=np.linalg.norm(normal)
    u=np.array([1.,0.,0.]);u-=normal*(normal@u);u/=np.linalg.norm(u)
    v=np.cross(normal,u)
    for s in np.arange(2.2,99,1.):
        perimeter=80+.1*s;wall=1.8+.002*s
        radius=perimeter/(2*96*np.sin(np.pi/96))
        outer=np.column_stack((radius*np.cos(angles),radius*np.sin(angles)))
        inner=outer*((radius-wall)/radius)
        position=np.array([3+.12*s,-2+.02*s,s])
        rows.append(dict(cumulative_length=float(s),position=position.tolist(),perimeter=perimeter,
                         wall_thickness=wall,wall_thickness_min=wall,wall_thickness_max=wall,
                         normal=normal.tolist(),basis_u=u.tolist(),basis_v=v.tolist(),
                         outer_2d=outer.tolist(),inner_2d=inner.tolist(),
                         outline_3d=(position+outer@np.array([u,v])).tolist(),
                         measurement_status='measured',centroid_residual_mm=0.,normal_deviation_deg=0.))
    for i in [0,1,2,3,-1,-2,-3]:
        rows[i]['position'][0]+=20
        rows[i]['perimeter']+=30
        rows[i]['wall_thickness']=.01
        rows[i]['measurement_status']='needs_review'
    return rows


PLANES=[dict(point=[0,0,0],normal=[0,0,1],source='test_plane'),
        dict(point=[0,0,100.65],normal=[0,0,1],source='test_plane')]


def test_linear_end_values_keep_all_stations_and_do_not_anchor_bad_end_centers():
    raw=measured_rows();original=copy.deepcopy(raw)
    out,meta=complete_ends(raw,PLANES,dict(shape_extrapolation='v1'))
    assert raw==original
    assert meta['reference_length_mm']==pytest.approx(100.65,abs=1e-9)
    assert out[0]['cumulative_length']==0
    assert out[-1]['cumulative_length']==pytest.approx(100.65)
    assert set(r['cumulative_length'] for r in raw).issubset(r['cumulative_length'] for r in out)
    for row in out:
        s=row['cumulative_length']
        np.testing.assert_allclose(row['position'],[3+.12*s,-2+.02*s,s],atol=1e-8)
        assert row['perimeter']==pytest.approx(80+.1*s)
        assert row['wall_thickness']==pytest.approx(1.8+.002*s)
    for row in [out[0],out[-1]]:
        assert row['measurement_status']=='extrapolated'
        assert row['measurement_source']=='linear_end_extrapolation'
        assert Polygon(row['outer_2d']).length==pytest.approx(row['perimeter'],abs=1e-7)
        stats=contour_thickness(np.array(row['outer_2d']),np.array(row['inner_2d']))
        assert stats['median_mm']==pytest.approx(row['wall_thickness'],abs=1e-6)
        rebuilt=np.array(row['position'])+np.array(row['outer_2d'])@np.array([row['basis_u'],row['basis_v']])
        np.testing.assert_allclose(rebuilt,row['outline_3d'],atol=1e-8)


def test_c1_shape_extrapolation_is_default_and_keeps_measured_shape_gradient():
    raw=measured_rows()
    out,meta=complete_ends(raw,PLANES)
    assert meta['method']=='c1_shape_extrapolation_v1'
    assert meta['shape_fallback_count']==0
    for end in meta['ends']:
        assert end['shape_extrapolation']=='c1'
        assert end['shape_mode_count']>0
        assert end['shape_fallback'] is None
    by_s={round(r['cumulative_length'],6):r for r in out}
    for s in (0.0,100.65):
        row=by_s[round(s,6)]
        assert row['measurement_source']=='c1_shape_extrapolation'
        assert Polygon(row['outer_2d']).length==pytest.approx(row['perimeter'],abs=1e-9)
        stats=contour_thickness(np.array(row['outer_2d']),np.array(row['inner_2d']))
        assert stats['median_mm']==pytest.approx(row['wall_thickness'],abs=1e-4)

    # At an ideal circular tube, the C1 extrapolated side and the measured side
    # must have the same radial tangent at the anchor station.
    from core.pp.tube.end_treatment import _align_cyclic, _resample_closed
    ext,anchor,nxt=by_s[5.2],by_s[6.2],by_s[7.2]
    frame=anchor
    def local(row):
        p=np.asarray(row['outer_2d'],float)
        p3=(np.asarray(row['position'],float)+p[:,:1]*np.asarray(row['basis_u'],float)
            +p[:,1:]*np.asarray(row['basis_v'],float))
        d=p3-np.asarray(frame['position'],float)
        return np.column_stack([d@np.asarray(frame['basis_u'],float),
                                d@np.asarray(frame['basis_v'],float)])
    qa=_resample_closed(local(anchor))
    qe=_align_cyclic(_resample_closed(local(ext)),qa)
    qn=_align_cyclic(_resample_closed(local(nxt)),qa)
    a=(qa-qe)/1.0
    b=(qn-qa)/1.0
    cosine=np.einsum('ij,ij->i',a,b)/np.maximum(
        np.linalg.norm(a,axis=1)*np.linalg.norm(b,axis=1),1e-12)
    angle=np.degrees(np.arccos(np.clip(cosine,-1.,1.)))
    assert angle.max()<1.0


def test_changing_trim_range_or_fit_window_never_changes_reference_station_axis():
    raw=measured_rows()
    automatic,a=complete_ends(raw,PLANES)
    manual,b=complete_ends(raw,PLANES,dict(start_trim_mm=20,end_trim_mm=17,fit_window_mm=15))
    assert a['reference_length_mm']==b['reference_length_mm']
    assert [r['cumulative_length'] for r in automatic]==[r['cumulative_length'] for r in manual]
    assert b['extrapolated_station_count']>a['extrapolated_station_count']
    for before,after in zip(automatic,manual):
        if 22<after['cumulative_length']<80:
            assert before==after


def test_interior_measurement_problem_remains_blocked_after_end_repair():
    from api.rfm import check_measurement_quality
    rows=measured_rows();rows[50]['measurement_status']='needs_review'
    out,_=complete_ends(rows,PLANES)
    with pytest.raises(ValueError,match='校核'):
        check_measurement_quality({'sections_full':out})


@pytest.mark.parametrize('options',[{'start_trim_mm':-1},{'end_trim_mm':float('nan')},{'fit_window_mm':0}])
def test_invalid_end_options_rejected(options):
    with pytest.raises(ValueError):end_options(options)


def test_overlapping_discard_ranges_rejected():
    with pytest.raises(ValueError,match='不足'):
        complete_ends(measured_rows(),PLANES,dict(start_trim_mm=60,end_trim_mm=60))


def test_isolated_good_section_at_tip_is_not_a_reliable_fit_window():
    raw=measured_rows()
    # One apparently valid ring followed by damaged rings must be discarded.
    raw[0]['measurement_status']='measured'
    out,meta=complete_ends(raw,PLANES)
    assert meta['ends'][0]['anchor_station_mm']==pytest.approx(6.2)
    np.testing.assert_allclose(out[0]['position'],[3,-2,0],atol=1e-8)


def test_concave_end_contour_retains_a_nested_wall_with_requested_thickness():
    from core.pp.tube.end_treatment import _scaled_contours
    outer=Polygon([(0,0),(20,0),(20,8),(8,8),(8,20),(0,20)])
    inner=outer.buffer(-1,join_style=2)
    anchor={'outer_2d':list(outer.exterior.coords)[:-1],
            'inner_2d':list(inner.exterior.coords)[:-1]}
    outside,inside,stats=_scaled_contours(anchor,outer.length*1.03,1.3)
    assert Polygon(outside).contains(Polygon(inside))
    assert Polygon(outside).length==pytest.approx(outer.length*1.03)
    assert stats['median_mm']==pytest.approx(1.3,abs=1e-6)


def test_cached_table_keeps_extrapolated_geometry_stations_and_provenance(tmp_path,monkeypatch):
    import time
    from api.pp_dev import _compact_analysis_results
    from core.pp import dev_cache
    sections,meta=complete_ends(measured_rows(),PLANES)
    raw={'sections_full':sections,'center_line_points':[s['position'] for s in sections],
         'center_line_length':meta['reference_length_mm'],
         'analysis_quality':{'end_treatment':meta},'wall_thickness_stats':{}}
    result,_=_compact_analysis_results(raw,'end.step','A',25,time.perf_counter())
    monkeypatch.setattr(dev_cache,'is_dev_tools_enabled',lambda:True)
    monkeypatch.setattr(dev_cache,'CACHE_DIR',str(tmp_path))
    monkeypatch.setattr(dev_cache,'CACHE_PATH',str(tmp_path/'current.json'))
    dev_cache.persist_from_session('end',{'results':result,'original_filename':'end.step'})
    restored=dev_cache.session_dict_from_cache(dev_cache.load_cache())['results']
    assert restored['analysis_quality']==raw['analysis_quality']
    assert restored['sections_full'][-1]['cumulative_length']==pytest.approx(100.65)
    for section,record in zip(restored['sections_full'],restored['centerline_perimeter_data']):
        assert record['中心线长度(mm)']==section['cumulative_length']
        assert record['外径周长(mm)']==section['perimeter']
        assert record['壁厚(mm)']==section['wall_thickness']
        assert record['measurement_status']==section['measurement_status']
        assert [record[k] for k in ('x','y','z')]==section['position']


def test_tp_accepts_full_station_interval_including_fractional_extrapolated_end():
    from core.tp.mold_generator import select_tp_segment,TPMoldProcessor
    from core.common.solid_geometry import valid_solid
    sections,_=complete_ends(measured_rows(),PLANES)
    rows=[{'中心线长度(mm)':r['cumulative_length'],'外径周长(mm)':r['perimeter']} for r in sections]
    large,small=rows[-1]['外径周长(mm)']/np.pi,rows[0]['外径周长(mm)']/np.pi
    segment=select_tp_segment(rows,100.65,0,large,small)
    assert segment[0]['中心线长度(mm)']==0
    assert segment[-1]['中心线长度(mm)']==pytest.approx(100.65)
    p=TPMoldProcessor();p.set_profile_data(segment);p.set_tp_params(100.65,large,small)
    p.generate_model();valid_solid(p.shape)


def test_upload_rejects_invalid_end_options_before_starting_reader():
    import io
    from flask import Flask
    from api.pp import pp_bp
    app=Flask(__name__);app.register_blueprint(pp_bp,url_prefix='/api/pp')
    response=app.test_client().post('/api/pp/upload',data={
        'file':(io.BytesIO(b'not read'),'tube.step'),'end_treatment':'{"start_trim_mm":-2}'})
    assert response.status_code==400
    assert '端部修正参数无效' in response.json['message']


def test_legacy_centerline_extrapolates_instead_of_restoring_bad_endpoints():
    s=np.arange(80)*1.2
    xyz=np.column_stack((2+.2*s,-3+.1*s,s))
    damaged=xyz.copy();damaged[:10,:2]+=50;damaged[-10:,:2]-=40
    corrected=trim_and_extrapolate_centerline(damaged,trim=10,n_fit=8,stations=s)
    assert corrected.shape==xyz.shape
    np.testing.assert_allclose(corrected,xyz,atol=1e-9)


def test_legacy_centerline_preserves_bent_core_instead_of_flattening_twenty_percent():
    from core.pp.tube.stl_engine import _build_centerline_from_centroids
    z=np.arange(160,dtype=float)
    x=.002*(z-80)**2
    rows=[dict(z=float(s),centroid_2d=[float(a),0.],perimeter=100.) for s,a in zip(z,x)]
    centerline,_=_build_centerline_from_centroids(rows)
    np.testing.assert_allclose(centerline[10:-10,0],x[10:-10],atol=1e-9)
    assert centerline[20,0]>centerline[80,0]+7


def test_extrapolated_contours_generate_cavity_inner_and_reverse_cut():
    from core.rfm.processors import ShapingProcessor
    from core.common.solid_geometry import valid_solid, cut_mold
    sections,_=complete_ends(measured_rows(),PLANES)
    proc=ShapingProcessor();proc.set_sections(sections)
    proc.generate_model();proc.generate_inner_mold()
    valid_solid(proc.shape);valid_solid(proc.inner_shape)
    shape,_=cut_mold(proc.shape,{'mold_auto_size':True,'mold_margin':15})
    valid_solid(shape)


def test_bending_uses_station_axis_after_center_positions_are_corrected():
    from core.rfm.processors import BendingProcessor
    sections,_=complete_ends(measured_rows(),PLANES)
    for row in sections:row['position'][2]*=1.1
    proc=BendingProcessor()
    proc.set_session_data(None,[{'中心线长度(mm)':r['cumulative_length'],'外径周长(mm)':r['perimeter']} for r in sections])
    proc.set_sections(sections);proc.generate_model()
    assert proc.generated_stations[-1]==pytest.approx(100.65)


def test_oblique_end_is_completed_to_cad_end_plane(tmp_path):
    from brep_fixtures import tube,write_step
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon,BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.gp import gp_Pnt,gp_Vec
    from core.pp.tube.brep_engine import analyze_brep_tube
    wire=BRepBuilderAPI_MakePolygon()
    for x,z in [(-30,88),(30,112),(30,150),(-30,150)]:wire.Add(gp_Pnt(x,-100,z))
    wire.Close()
    cutter=BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire.Wire()).Face(),gp_Vec(0,200,0)).Shape()
    path=tmp_path/'slanted.step';write_step(BRepAlgoAPI_Cut(tube(12,10,120),cutter).Shape(),path)
    result=analyze_brep_tube(str(path))
    ends=result['analysis_quality']['end_treatment']['ends']
    for row,end in zip((result['sections_full'][0],result['sections_full'][-1]),ends):
        plane=end['boundary']
        assert plane['source']=='cad_planar_end_face'
        assert abs(np.dot(np.array(row['position'])-plane['point'],plane['normal']))<1e-7
        assert row['perimeter']==pytest.approx(24*np.pi,abs=.02)
    assert result['center_line_length']==pytest.approx(100,abs=.15)
