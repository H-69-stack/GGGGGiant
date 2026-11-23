"""Prepare real CAD visualization meshes and drawing pages for the report."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    from core.pp.mesh_converter import shape_to_mesh
    from core.pp.tube.brep_sections import read_shape
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS
    import trimesh
    out = args.output / "assets"
    out.mkdir(parents=True, exist_ok=True)
    for f in sorted(args.fixtures.glob("*.stp")):
        shape = read_shape(f)
        data = shape_to_mesh(shape, .08, .12)
        v = np.array(data['vertices']).reshape(-1, 3)
        triangles = np.array(data['faces']).reshape(-1, 3)
        np.savez_compressed(out / (f.stem + ".npz"), vertices=v, faces=triangles)
        trimesh.Trimesh(vertices=v, faces=triangles, process=False).export(out / (f.stem + ".stl"))
        mesh = {"vertices": v.ravel().tolist(), "faces": triangles.ravel().tolist(),
                "vertex_count": len(v), "face_count": len(triangles)}
        (out / (f.stem + "_mesh.json")).write_text(json.dumps(mesh), encoding="utf-8")
        exp = TopExp_Explorer(shape, TopAbs_VERTEX)
        tols = []
        while exp.More():
            tols.append(BRep_Tool.Tolerance_s(TopoDS.Vertex_s(exp.Current())))
            exp.Next()
        metadata = {"file": f.name, "mesh_tolerance_mm": .08, "max_vertex_tolerance_mm": max(tols),
                    "mesh_bounds": [v.min(axis=0).tolist(), v.max(axis=0).tolist()]}
        (out / (f.stem + "_metadata.json")).write_text(json.dumps(metadata), encoding="utf-8")
        print(f.stem, len(v), len(triangles), metadata, flush=True)


if __name__ == "__main__":
    main()
