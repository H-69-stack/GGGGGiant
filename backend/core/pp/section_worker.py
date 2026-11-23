"""Subprocess worker for parallel STEP tube section cutting.

Usage:
    conda run -n giant python section_worker.py --brep <path> --z-chunk <json> --output <path>

Loads a BREP file, processes a chunk of Z values, writes results as JSON.
Designed to be spawned by analyze_shape() as independent subprocesses,
bypassing Flask's multiprocessing limitations on Windows.
"""
import sys
import json
import numpy as np


def _horizontal_section(shape, z: float):
    """Same as step_analyzer._horizontal_section — duplicated for subprocess isolation."""
    from scipy.spatial import ConvexHull
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformAbscissa
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pln, gp_Ax3, gp_Pnt, gp_Dir
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS

    plane = gp_Pln(gp_Ax3(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)))
    sectioner = BRepAlgoAPI_Section(shape, plane)
    sectioner.Build()
    if not sectioner.IsDone():
        return None

    section_shape = sectioner.Shape()

    points_2d = []
    exp = TopExp_Explorer(section_shape, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        try:
            adaptor = BRepAdaptor_Curve(edge)
            length_props = GProp_GProps()
            BRepGProp.LinearProperties_s(edge, length_props)
            edge_len = length_props.Mass()
            n_pts = max(3, int(edge_len / 1.0))
            ua = GCPnts_UniformAbscissa(adaptor, max(n_pts, 3))
            if ua.IsDone():
                for i in range(1, ua.NbPoints() + 1):
                    p = adaptor.Value(ua.Parameter(i))
                    points_2d.append([p.X(), p.Y()])
        except Exception:
            pass
        exp.Next()

    if len(points_2d) < 3:
        return None

    pts = np.array(points_2d)
    try:
        hull = ConvexHull(pts)
    except Exception:
        return None

    hull_verts = pts[hull.vertices]
    x0, y0 = hull_verts[:, 0], hull_verts[:, 1]
    x1 = np.roll(x0, -1); y1 = np.roll(y0, -1)
    cross = x0 * y1 - x1 * y0
    area_sum = float(np.sum(cross))
    if abs(area_sum) < 1e-12:
        cx = float(np.mean(x0))
        cy = float(np.mean(y0))
    else:
        cx = float(np.sum((x0 + x1) * cross)) / (3.0 * area_sum)
        cy = float(np.sum((y0 + y1) * cross)) / (3.0 * area_sum)

    diffs = np.roll(hull_verts, -1, axis=0) - hull_verts
    perimeter = float(np.sum(np.linalg.norm(diffs, axis=1)))

    wall_thickness = None
    inner_hull_verts = None
    outer_set = set(hull.vertices.tolist())
    inner_pt_indices = [i for i in range(len(pts)) if i not in outer_set]
    if len(inner_pt_indices) >= 3:
        inner_pts = pts[inner_pt_indices]
        try:
            inner_hull = ConvexHull(inner_pts)
            inner_hull_verts = inner_pts[inner_hull.vertices]
            center = np.array([cx, cy])
            n_angles = 36
            angles = np.linspace(0, 2 * np.pi, n_angles, endpoint=False)
            directions = np.column_stack([np.cos(angles), np.sin(angles)])
            outer_proj = hull_verts @ directions.T
            inner_proj = inner_hull_verts @ directions.T
            outer_r = np.max(outer_proj, axis=0)
            inner_r = np.max(inner_proj, axis=0)
            mask = (outer_r > inner_r) & (inner_r > 0)
            if np.any(mask):
                wall_thickness = float(np.median(outer_r[mask] - inner_r[mask]))
        except Exception:
            pass

    # Convert to JSON-serializable format
    return {
        'centroid': [cx, cy, float(z)],
        'perimeter': perimeter,
        'wall_thickness': wall_thickness,
        'hull_verts': hull_verts.tolist(),
        'inner_hull_verts': inner_hull_verts.tolist() if inner_hull_verts is not None else None,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--brep', required=True)
    parser.add_argument('--z-chunk', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    z_values = json.loads(args.z_chunk)

    # Load BREP
    from OCP.BRepTools import BRepTools
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Shape
    shape = TopoDS_Shape()
    builder = BRep_Builder()
    BRepTools.Read_s(shape, args.brep, builder)
    if shape.IsNull():
        print(json.dumps({'error': 'Failed to read BREP file'}))
        sys.exit(1)

    results = []
    for z in z_values:
        r = _horizontal_section(shape, float(z))
        if r is not None:
            results.append(r)

    with open(args.output, 'w') as f:
        json.dump(results, f)


if __name__ == '__main__':
    main()
