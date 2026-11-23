"""Independent normal-plane CAD cuts of exported forming solids vs input outlines."""
import json
import sys
from pathlib import Path
import numpy as np
from shapely.geometry import Polygon
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from core.pp.tube.brep_sections import read_shape, _wire_points
from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
from OCP.gp import gp_Pnt,gp_Dir,gp_Pln
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.TopTools import TopTools_HSequenceOfShape
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps


def audit(shape, section, key='outer_2d'):
    pos=np.array(section['position']);n=np.array(section['normal'])
    u=np.array(section['basis_u']);v=np.array(section['basis_v'])
    op=BRepAlgoAPI_Section(shape,gp_Pln(gp_Pnt(*pos),gp_Dir(*n)),False)
    op.SetFuzzyValue(1e-6);op.Build()
    edges=TopTools_HSequenceOfShape();wires=TopTools_HSequenceOfShape()
    exp=TopExp_Explorer(op.Shape(),TopAbs_EDGE)
    while exp.More():edges.Append(exp.Current());exp.Next()
    ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges,1e-5,False,wires)
    polys=[]
    for i in range(1,wires.Length()+1):
        w=TopoDS.Wire_s(wires.Value(i));xy=_wire_points(w,pos,u,v,.001)
        poly=Polygon(xy);props=GProp_GProps();BRepGProp.LinearProperties_s(w,props)
        if poly.is_valid:polys.append((poly,props.Mass()))
    if not polys:raise ValueError('CAD audit plane has no closed contour')
    actual,perim=max(polys,key=lambda pair:pair[0].area)
    expected=Polygon(section[key])
    return {'station_mm':section['cumulative_length'],
            'contour_hausdorff_mm':expected.boundary.hausdorff_distance(actual.boundary),
            'centroid_error_mm':expected.centroid.distance(actual.centroid),
            'perimeter_error_mm':perim-expected.length,
            'area_relative_error':abs(actual.area-expected.area)/expected.area}


def main():
    out=ROOT/'reports/mold_validation_20260921'
    summary=[]
    for path in sorted(out.glob('*_cavity.step')):
        case=path.stem.removesuffix('_cavity')
        print(case,flush=True)
        raw=json.loads((ROOT/'reports/stp_validation_20260921/measured'/f'{case}.json').read_text(encoding='utf8'))
        shape=read_shape(path);sections=raw['sections_full']
        indices=np.unique(np.linspace(2,len(sections)-3,13).astype(int))
        values=[audit(shape,sections[i]) for i in indices]
        result={'case':case,'samples':values,'max_contour_error_mm':max(v['contour_hausdorff_mm'] for v in values),
                'max_centroid_error_mm':max(v['centroid_error_mm'] for v in values)}
        summary.append(result);print(result['max_contour_error_mm'],result['max_centroid_error_mm'],flush=True)
    (out/'section_audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
