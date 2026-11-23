"""Real-fixture circular belling/bending and measured inner contour smoke checks."""
import json
import sys
import time
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from core.rfm.processors import ShapingProcessor,BellingProcessor,BendingProcessor
from core.tp.mold_generator import TPMoldProcessor,select_tp_segment
from core.common.solid_geometry import valid_solid,cut_mold,volume,step_bytes
from api.rfm import check_measurement_quality


def main():
    out=ROOT/'reports/mold_validation_20260921';records=[]
    for source in sorted((ROOT/'reports/stp_validation_20260921/measured').glob('*.json')):
        raw=json.loads(source.read_text(encoding='utf8'))
        try:check_measurement_quality(raw)
        except ValueError:continue
        rows=[{'中心线长度(mm)':s['cumulative_length'],'外径周长(mm)':s['perimeter'],'壁厚(mm)':s['wall_thickness']} for s in raw['sections_full']]
        for kind,cls in [('inner',ShapingProcessor),('belling',BellingProcessor),('bending',BendingProcessor),('tp',TPMoldProcessor)]:
            print(source.stem,kind,flush=True);start=time.monotonic()
            record={'case':source.stem,'operation':kind}
            try:
                p=cls()
                if kind=='tp':
                    large=max(rows,key=lambda r:r['外径周长(mm)']);small=min(rows,key=lambda r:r['外径周长(mm)'])
                    length=abs(large['中心线长度(mm)']-small['中心线长度(mm)'])
                    p.set_tp_params(length,large['外径周长(mm)']/np.pi,small['外径周长(mm)']/np.pi)
                    p.set_profile_data(select_tp_segment(rows,large['中心线长度(mm)'],small['中心线长度(mm)'],p.T_D,p.T_d))
                    p.generate_model();shape=p.shape
                    # QA-selected endpoint diameters, not an approved production TP design.
                else:
                    p.set_session_data(raw['center_line_points'],rows);p.set_sections(raw['sections_full'])
                    if kind=='inner':
                        p.generate_inner_mold();shape=p.inner_shape
                    elif kind=='belling':
                        p.generate_cavity_model();shape=p.cavity_shape
                    else:p.generate_model();shape=p.shape
                valid_solid(shape)
                if kind in ('belling','bending'):cut_mold(shape,{'mold_margin':15})
                if kind=='tp' and source.stem=='24TK_1324-31':
                    (out/'24TK_1324-31_tp.step').write_bytes(p.get_step_bytes())
                    p.mesh.export(out/'24TK_1324-31_tp.stl')
                record.update(status='passed',volume_mm3=volume(shape),seconds=time.monotonic()-start)
            except Exception as exc:record.update(status='failed',error=str(exc),seconds=time.monotonic()-start)
            records.append(record);print(json.dumps(record,ensure_ascii=False),flush=True)
            (out/'extra_cases.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
