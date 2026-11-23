"""Run production CAD forming/boolean on the six measured STEP fixtures."""
import argparse
import json
import sys
import time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'backend'))
from api.rfm import check_measurement_quality
from core.rfm.processors import ShapingProcessor, BellingProcessor, BendingProcessor
from core.common.solid_geometry import volume, cut_mold, mesh_from_shape, step_bytes, valid_solid
from core.pp.tube.brep_sections import read_shape


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case');args=parser.parse_args()
    out=ROOT/'reports/mold_validation_20260921';out.mkdir(parents=True,exist_ok=True)
    inputs=ROOT/'reports/stp_validation_20260921/measured'
    for path in inputs.glob('*.json'):
        if args.case and path.stem != args.case:continue
        start=time.monotonic();record={'case':path.stem}
        try:
            raw=json.loads(path.read_text(encoding='utf8'))
            check_measurement_quality(raw)
            p=ShapingProcessor();p.set_sections(raw['sections_full'])
            print(path.stem,'forming',flush=True)
            p.generate_model()
            print(path.stem,'boolean',time.monotonic()-start,flush=True)
            mold,info=cut_mold(p.shape,{'mold_margin':15})
            p.mesh.export(out/(path.stem+'_cavity.stl'))
            mesh_from_shape(mold).export(out/(path.stem+'_mold.stl'))
            for tag,shape in [('cavity',p.shape),('mold',mold)]:
                file=out/(path.stem+'_'+tag+'.step');file.write_bytes(step_bytes(shape))
                valid_solid(read_shape(file))
            record.update(status='passed', stations=len(raw['sections_full']),mold_info=info,
                          cavity_volume_mm3=volume(p.shape), seconds=time.monotonic()-start)
        except Exception as exc:
            record.update(status='blocked' if '校核' in str(exc) else 'failed',error=str(exc),seconds=time.monotonic()-start)
        (out/(path.stem+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(record,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
