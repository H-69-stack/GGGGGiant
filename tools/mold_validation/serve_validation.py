"""Loopback host exercising production PP/RFM/TP blueprints with isolated DBs."""
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
runtime=ROOT/'reports/mold_validation_20260921/ui_runtime'
runtime.mkdir(parents=True,exist_ok=True)
os.environ['GTAPP_DATA_DIR']=str(runtime)
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'backend/tests'))
import config
config.PP_TUBE_DB=str(runtime/'tubes.sqlite')
config.PP_DESIGN_DB=str(runtime/'designs.sqlite')
from flask import Flask
from core.pp.thickness_db import save_full_analysis
from core.pp.design_db import save_design, list_designs
from api.pp import pp_bp
from api.rfm import rfm_bp
from api.tp import tp_bp
from test_mold_pipeline import measured, rows


def seed():
    existing={r['display_name'] for r in list_designs(db_path=config.PP_DESIGN_DB)}
    names=['解析基准椭圆 30×20（长 400 mm）','24TK_1324-31','24TK_2052-2']
    for name in names:
        if name in existing:continue
        if name.startswith('解析'):
            sections=measured(400);cp=rows(400)
            result={'sections_full':sections,'centerline_length':400,'center_line':{'points':[s['position'] for s in sections]},
                    'analysis_quality':{},'wall_thickness_data':cp,'centerline_perimeter_data':cp}
            params={'L1':100,'T3':2,'D1':30,'D2':20,'D3':20}
        else:
            result=json.loads((ROOT/'reports/stp_validation_20260921/measured'/f'{name}.json').read_text(encoding='utf8'))
            result['centerline_length']=result['center_line_length']
            cp=[{'中心线长度(mm)':s['cumulative_length'],'外径周长(mm)':s['perimeter'],'壁厚(mm)':s['wall_thickness']} for s in result['sections_full']]
            result['wall_thickness_data']=result['centerline_perimeter_data']=cp
            params={'L1':100,'T3':result['wall_thickness_stats']['median_mm']}
        tid,error=save_full_analysis(name+'.stp',name,'','stp',name,result,config.PP_TUBE_DB)
        if error:raise RuntimeError(error)
        did,error=save_design(display_name=name,vehicle_spec='回归测试',tube_number=name,pp_method='普通抽+TP抽+扩口',
                        source_tube_id=tid,source_file_name=name+'.stp',source_file_path='',solution=params,
                        fitness=0,area_change_rate=None,extension_ratio=None,centerline_perimeter_data=cp,
                        extra_params=params,db_path=config.PP_DESIGN_DB)
        if did<0:raise RuntimeError('fixture design save failed')
    print('Fixture databases ready',flush=True)


if __name__=='__main__':
    seed()
    app=Flask('mold_validation')
    app.register_blueprint(pp_bp,url_prefix='/api/pp')
    app.register_blueprint(rfm_bp)
    app.register_blueprint(tp_bp,url_prefix='/api/tp')
    app.run(host='127.0.0.1',port=5057,use_reloader=False,threaded=True)
