"""Independent CAD re-read and plane sections of the merged API exports."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopTools import TopTools_HSequenceOfShape
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt, gp_Dir, gp_Pln

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'backend'))
from tools.mold_validation.audit_sections import audit
from core.common.solid_geometry import bounds, valid_solid, volume
from core.pp.tube.brep_sections import read_shape

OUT = ROOT/'reports/merge_validation_20260921'


def cut_diameters(shape, z):
    op = BRepAlgoAPI_Section(shape, gp_Pln(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), False)
    op.Build()
    assert op.IsDone()
    edges, wires = TopTools_HSequenceOfShape(), TopTools_HSequenceOfShape()
    exp = TopExp_Explorer(op.Shape(), TopAbs_EDGE)
    while exp.More():
        edges.Append(exp.Current())
        exp.Next()
    ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges, 1e-5, False, wires)
    diameters = []
    for i in range(1, wires.Length()+1):
        props = GProp_GProps()
        wire = TopoDS.Wire_s(wires.Value(i))
        assert wire.Closed()
        BRepGProp.LinearProperties_s(wire, props)
        diameters.append(props.Mass()/np.pi)
    assert len(diameters) == 2, (z, diameters)
    return sorted(diameters)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', default='*')
    args = parser.parse_args()
    results = []
    for path in sorted(OUT.glob(args.case+'.json')):
        if not path.stem.startswith(('24TK_', 'H0A2')):
            continue
        record = json.loads(path.read_text(encoding='utf8'))
        if not all(record['operations'].get(k, {}).get('status') == 'passed' for k in ('forming', 'mold', 'tp')):
            continue
        case = record['case']
        print('AUDIT', case, flush=True)
        raw = json.loads((OUT/'measured'/(case+'.json')).read_text(encoding='utf8'))
        shapes = {kind: valid_solid(read_shape(OUT/'models'/(case+'_'+kind+'.step')))
                  for kind in ('forming', 'mold', 'tp')}
        sections = raw['sections_full']
        indices = np.unique(np.linspace(2, len(sections)-3, 13).astype(int))
        cuts = [audit(shapes['forming'], sections[i]) for i in indices]
        size = record['operations']['mold']['mold_info']['size_xyz_mm']
        conservation = abs(volume(shapes['mold']) + volume(shapes['forming']) - np.prod(size))
        assert conservation < max(.01, np.prod(size)*1e-7), conservation
        tp = record['tp_inputs']
        station_s = np.array([s['cumulative_length'] for s in sections])
        station_p = np.array([s['perimeter'] for s in sections])
        tp_cuts = []
        for fraction in np.linspace(.05, .95, 9):
            z = 9 + fraction*tp['T_L_mm']
            source_s = tp['small_station_mm'] + fraction*(tp['large_station_mm']-tp['small_station_mm'])
            expected_d = np.interp(source_s, station_s, station_p)/np.pi
            actual_d, outer_d = cut_diameters(shapes['tp'], z)
            tp_cuts.append({'z_mm': z, 'source_station_mm': source_s,
                            'expected_inner_diameter_mm': expected_d, 'actual_inner_diameter_mm': actual_d,
                            'diameter_error_mm': actual_d-expected_d, 'outer_diameter_mm': outer_d})
            assert abs(actual_d-expected_d) < .001, tp_cuts[-1]
            assert abs(outer_d-95) < .001, tp_cuts[-1]
        low, high = bounds(shapes['tp'])
        assert np.allclose(high-low, [125, 125, tp['T_L_mm']+27], atol=.001)
        result = {'case': case, 'forming_sections': cuts,
                  'max_contour_error_mm': max(v['contour_hausdorff_mm'] for v in cuts),
                  'max_centroid_error_mm': max(v['centroid_error_mm'] for v in cuts),
                  'mold_volume_conservation_error_mm3': conservation,
                  'tp_sections': tp_cuts,
                  'tp_max_diameter_error_mm': max(abs(c['diameter_error_mm']) for c in tp_cuts),
                  'tp_extents_xyz_mm': (high-low).tolist()}
        assert result['max_contour_error_mm'] < .1, result
        results.append(result)
        print(case, 'PASS', result['max_contour_error_mm'], result['tp_max_diameter_error_mm'], flush=True)
    destination = OUT/'export_audit.json'
    previous = json.loads(destination.read_text(encoding='utf8')) if destination.exists() else []
    by_case = {r['case']: r for r in previous+results}
    destination.write_text(json.dumps(list(by_case.values()), ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
