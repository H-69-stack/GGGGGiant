"""Audit actual files against independent CAD area integration and finer contours."""
import argparse
import json
import sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))


def reference_center(shape,origin,normal):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.gp import gp_Pln,gp_Pnt,gp_Dir
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopTools import TopTools_HSequenceOfShape
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopoDS import TopoDS
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    plane=gp_Pln(gp_Pnt(*origin),gp_Dir(*normal));op=BRepAlgoAPI_Section(shape,plane,True)
    edges=TopTools_HSequenceOfShape();exp=TopExp_Explorer(op.Shape(),TopAbs_EDGE)
    while exp.More():edges.Append(exp.Current());exp.Next()
    wires=TopTools_HSequenceOfShape();ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges,1e-5,False,wires)
    rows=[]
    for i in range(1,wires.Length()+1):
        wire=TopoDS.Wire_s(wires.Value(i));prop=GProp_GProps()
        BRepGProp.SurfaceProperties_s(BRepBuilderAPI_MakeFace(plane,wire,True).Face(),prop,1e-9)
        rows.append((abs(prop.Mass()),np.array(prop.CentreOfMass().Coord())))
    return max(rows,key=lambda row:row[0])[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('fixtures',type=Path);parser.add_argument('report',type=Path);args=parser.parse_args()
    from core.pp.tube.brep_sections import read_shape,section_shape
    results=[]
    for f in sorted((args.report/'measured').glob('*.json')):
        r=json.loads(f.read_text(encoding='utf-8'));shape=read_shape(args.fixtures/(f.stem+'.stp'))
        rows=r['sections_full'];points=np.array(r['center_line_points']);length=float(np.linalg.norm(np.diff(points,axis=0),axis=1).sum())
        assert abs(length-r['center_line_length'])<1e-7
        assert abs(rows[-1]['cumulative_length']-length)<1e-7
        max_plane=0.; max_frame=0.
        for row in rows:
            p=np.array(row['position']);n=np.array(row['normal']);o=np.array(row['outline_3d'])
            max_plane=max(max_plane,float(abs((o-p)@n).max()))
            frame=p+np.array(row['outer_2d'])@np.array([row['basis_u'],row['basis_v']])
            max_frame=max(max_frame,float(abs(o-frame).max()))
            assert row['perimeter']>0 and row['wall_thickness']>0
        assert max_plane<1e-6 and max_frame<1e-6
        samples=[]
        for fraction in [.08,.5,.92]:
            row=rows[int((len(rows)-1)*fraction)]
            center=reference_center(shape,row['position'],row['normal'])
            fine=section_shape(shape,row['position'],row['normal'],deflection=.0005)
            assert fine['valid'],fine
            dc=float(np.linalg.norm(center-np.array(row['position'])))
            dp=abs(fine['perimeter']-row['perimeter'])
            dt=abs(fine['thickness']['median_mm']-row['wall_thickness'])
            samples.append({'s_mm':row['cumulative_length'],'centroid_vs_cad_area_mm':dc,
                            'perimeter_recut_delta_mm':dp,'thickness_finer_contour_delta_mm':dt})
            assert dc<.02,(f.stem,dc)
            assert dp<.01,(f.stem,dp)
            assert dt<.012,(f.stem,dt)
        record={'case':f.stem,'plane_error_max_mm':max_plane,'frame_error_max_mm':max_frame,
                'samples':samples,'passed':True,'station_count':len(rows)}
        results.append(record);print(json.dumps(record,ensure_ascii=False),flush=True)
    (args.report/'audit.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
