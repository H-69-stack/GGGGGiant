"""STEP file center line and cross-section analysis engine.

Algorithm (traditional brute-force approach):
1. Read STEP file
2. Find the long axis via PCA on shape vertices
3. Rotate the entire shape so the long axis aligns with Z
4. Slice with dense horizontal planes (parallel to XY) at 1mm step -> get centroids
5. Connect centroids = center line
6. Inverse-rotate center line points back to original coordinate system
7. Perimeter = total edge length of each cross-section curve (exact, not polygon approx)
"""

import numpy as np
import os

# OCP (PythonOCC / cadquery) is optional — only needed for STP analysis
OCP_AVAILABLE = False
try:
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pln, gp_Ax3, gp_Pnt, gp_Dir, gp_Trsf, gp_Ax1, gp_Vec
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.gp import gp_Lin
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.GeomAdaptor import GeomAdaptor_Curve
    from OCP.TColgp import TColgp_Array1OfPnt
    from OCP.GeomAPI import GeomAPI_PointsToBSpline
    from OCP.GeomLProp import GeomLProp_CurveTool
    OCP_AVAILABLE = True
except ImportError:
    pass


def read_step(filepath: str):
    """Read a STEP file and return the TopoDS_Shape."""
    reader = STEPControl_Reader()
    status = reader.ReadFile(filepath)
    if status != IFSelect_RetDone:
        raise ValueError(f"Failed to read STEP file: {filepath}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise ValueError("STEP file produced null shape")
    return shape


def get_bounding_box(shape):
    """Return (xmin, ymin, zmin, xmax, ymax, zmax) from shape vertices."""
    bbox = Bnd_Box()
    BRepBndLib.Add_s(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()

    # If bbox is uninitialized (±1e100), compute from sample points instead
    if xmax - xmin > 1e50 or ymax - ymin > 1e50 or zmax - zmin > 1e50:
        pts = _sample_points(shape, max_points=10000)
        if pts is not None and len(pts) > 0:
            xmin, ymin, zmin = pts.min(axis=0)
            xmax, ymax, zmax = pts.max(axis=0)

    return xmin, ymin, zmin, xmax, ymax, zmax


def _sample_points(shape, max_points: int = 5000) -> np.ndarray:
    """Sample points from the shape for PCA analysis.

    Uses edge midpoints and vertices to get a better representation of the
    shape's geometry, since STEP B-rep models may have few vertices on
    smooth curved surfaces.
    """
    pts = []
    seen = set()

    def _add(x, y, z):
        key = (round(x, 3), round(y, 3), round(z, 3))
        if key not in seen:
            seen.add(key)
            pts.append([x, y, z])

    # Sample vertices
    v_exp = TopExp_Explorer(shape, TopAbs_VERTEX)
    while v_exp.More():
        v = TopoDS.Vertex_s(v_exp.Current())
        pnt = BRep_Tool.Pnt_s(v)
        _add(pnt.X(), pnt.Y(), pnt.Z())
        v_exp.Next()
        if len(pts) >= max_points:
            break

    # Sample edge midpoints
    if len(pts) < max_points:
        e_exp = TopExp_Explorer(shape, TopAbs_EDGE)
        while e_exp.More():
            edge = TopoDS.Edge_s(e_exp.Current())
            from OCP.BRepAdaptor import BRepAdaptor_Curve
            from OCP.GCPnts import GCPnts_UniformAbscissa
            adaptor = BRepAdaptor_Curve(edge)
            umin = adaptor.FirstParameter()
            umax = adaptor.LastParameter()
            # Sample midpoint and quartile points
            for frac in [0.25, 0.5, 0.75]:
                u = umin + frac * (umax - umin)
                p = adaptor.Value(u)
                _add(p.X(), p.Y(), p.Z())
                if len(pts) >= max_points:
                    break
            e_exp.Next()
            if len(pts) >= max_points:
                break

    return np.array(pts) if pts else None


def _pca_long_axis(pts: np.ndarray) -> np.ndarray:
    """Return the unit vector of the principal (longest) axis via PCA."""
    centered = pts - pts.mean(axis=0)
    cov = np.cov(centered.T)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    # eigenvectors columns are sorted by ascending eigenvalue; take the last
    long_axis = eigenvectors[:, -1]
    return long_axis / np.linalg.norm(long_axis)


def _make_align_to_z_transform(long_axis: np.ndarray, center: np.ndarray):
    """Build an OCP gp_Trsf that rotates long_axis to align with +Z, rotating about center."""
    import math

    z = np.array([0.0, 0.0, 1.0])
    axis = long_axis / np.linalg.norm(long_axis)

    dot = float(np.clip(np.dot(axis, z), -1.0, 1.0))
    angle = math.acos(dot)

    trsf = gp_Trsf()

    if abs(angle) < 1e-9:
        # Already aligned with +Z, no rotation needed
        return trsf

    if abs(angle - math.pi) < 1e-9:
        # Anti-aligned (-Z): rotate 180 degrees around X axis
        rot_axis = gp_Ax1(gp_Pnt(*center), gp_Dir(1, 0, 0))
        trsf.SetRotation(rot_axis, math.pi)
        return trsf

    # General case: rotation axis = cross(long_axis, +Z), normalized
    rot_vec = np.cross(axis, z)
    rot_vec = rot_vec / np.linalg.norm(rot_vec)
    rot_axis = gp_Ax1(gp_Pnt(*center), gp_Dir(*rot_vec))
    trsf.SetRotation(rot_axis, angle)
    return trsf


def _apply_transform(shape, trsf):
    """Return a new shape that is the result of applying trsf to shape."""
    transformer = BRepBuilderAPI_Transform(shape, trsf, False, False)
    return transformer.Shape()


def _horizontal_section(shape, z: float):
    """Section shape with a horizontal plane at Z=z (parallel to XY).

    Samples points from all section edges, computes:
    - Outer envelope (convex hull of all points)
    - Inner envelope (convex hull of non-outer points)
    - Wall thickness (average radial distance between outer and inner)

    Returns ((centroid_x, centroid_y, z), perimeter, wall_thickness) or None.
    """
    from scipy.spatial import ConvexHull
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformAbscissa

    plane = gp_Pln(gp_Ax3(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)))
    sectioner = BRepAlgoAPI_Section(shape, plane)
    sectioner.Build()
    if not sectioner.IsDone():
        return None

    section_shape = sectioner.Shape()

    # Sample points from all edges (reduced sampling density for speed)
    points_2d = []
    exp = TopExp_Explorer(section_shape, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        try:
            adaptor = BRepAdaptor_Curve(edge)
            length_props = GProp_GProps()
            BRepGProp.LinearProperties_s(edge, length_props)
            edge_len = length_props.Mass()
            n_pts = max(3, int(edge_len / 1.0))  # 1mm spacing (was 0.5)
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

    # Centroid of convex hull polygon (area-weighted)
    hull_verts = pts[hull.vertices]
    n = len(hull_verts)
    # Vectorized cross products
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

    # Perimeter of convex hull (vectorized)
    diffs = np.roll(hull_verts, -1, axis=0) - hull_verts
    perimeter = float(np.sum(np.linalg.norm(diffs, axis=1)))

    # X/Y axis lengths (for ellipse flattening)
    x_coords = hull_verts[:, 0]
    y_coords = hull_verts[:, 1]
    x_length = float(np.max(x_coords) - np.min(x_coords))
    y_length = float(np.max(y_coords) - np.min(y_coords))

    # -- Inner envelope and wall thickness --
    wall_thickness = None
    inner_hull_verts = None
    outer_set = set(hull.vertices.tolist())
    inner_pt_indices = [i for i in range(len(pts)) if i not in outer_set]
    if len(inner_pt_indices) < 3:
        # No inner wall to compute
        pass
    else:
        inner_pts = pts[inner_pt_indices]
        try:
            inner_hull = ConvexHull(inner_pts)
            inner_hull_verts = inner_pts[inner_hull.vertices]

            center = np.array([cx, cy])
            n_angles = 36
            angles = np.linspace(0, 2 * np.pi, n_angles, endpoint=False)
            directions = np.column_stack([np.cos(angles), np.sin(angles)])
            # Vectorized ray casting
            outer_proj = hull_verts @ directions.T
            inner_proj = inner_hull_verts @ directions.T
            outer_r = np.max(outer_proj, axis=0)
            inner_r = np.max(inner_proj, axis=0)
            mask = (outer_r > inner_r) & (inner_r > 0)
            if np.any(mask):
                wall_thickness = float(np.median(outer_r[mask] - inner_r[mask]))
        except Exception:
            pass

    return (cx, cy, z), perimeter, wall_thickness, hull_verts, inner_hull_verts, x_length, y_length


def _perpendicular_section(shape, point: np.ndarray, normal: np.ndarray):
    """Cut shape with a plane at `point` perpendicular to `normal` (tangent direction).

    Returns (perimeter, wall_thickness, outer_2d, inner_2d, outline_3d) or None.
    - outer_2d: convex hull vertices in local 2D coords
    - inner_2d: inner hull vertices in local 2D coords
    - outline_3d: outer hull vertices in original 3D coords (for 3D display)
    """
    from scipy.spatial import ConvexHull
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformAbscissa

    normal = normal / max(np.linalg.norm(normal), 1e-12)

    plane = gp_Pln(
        gp_Ax3(
            gp_Pnt(float(point[0]), float(point[1]), float(point[2])),
            gp_Dir(float(normal[0]), float(normal[1]), float(normal[2]))
        )
    )
    sectioner = BRepAlgoAPI_Section(shape, plane)
    sectioner.Build()
    if not sectioner.IsDone():
        return None

    section_shape = sectioner.Shape()

    # Sample 3D points from all edges
    points_3d = []
    exp = TopExp_Explorer(section_shape, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        try:
            adaptor = BRepAdaptor_Curve(edge)
            length_props = GProp_GProps()
            BRepGProp.LinearProperties_s(edge, length_props)
            edge_len = length_props.Mass()
            n_pts = max(3, int(edge_len / 0.5))
            ua = GCPnts_UniformAbscissa(adaptor, n_pts)
            if ua.IsDone():
                for i in range(1, ua.NbPoints() + 1):
                    p = adaptor.Value(ua.Parameter(i))
                    points_3d.append(np.array([p.X(), p.Y(), p.Z()]))
        except Exception:
            pass
        exp.Next()

    if len(points_3d) < 3:
        return None

    pts_3d = np.array(points_3d)

    # Build local 2D coordinate system on the plane
    # u = any vector perpendicular to normal
    if abs(normal[0]) < 0.9:
        u = np.cross(normal, np.array([1.0, 0.0, 0.0]))
    else:
        u = np.cross(normal, np.array([0.0, 1.0, 0.0]))
    u = u / np.linalg.norm(u)
    v = np.cross(normal, u)
    v = v / np.linalg.norm(v)

    # Project 3D points to local 2D
    relative = pts_3d - point
    pts_2d = np.column_stack([relative @ u, relative @ v])

    # Outer convex hull
    try:
        hull = ConvexHull(pts_2d)
    except Exception:
        return None

    hull_verts_2d = pts_2d[hull.vertices]
    n = len(hull_verts_2d)

    # Perimeter
    perimeter = 0.0
    for i in range(n):
        d = hull_verts_2d[(i + 1) % n] - hull_verts_2d[i]
        perimeter += float(np.linalg.norm(d))

    # X/Y axis lengths (for ellipse flattening)
    x_coords = hull_verts_2d[:, 0]
    y_coords = hull_verts_2d[:, 1]
    x_length = float(np.max(x_coords) - np.min(x_coords))
    y_length = float(np.max(y_coords) - np.min(y_coords))

    # Centroid of outer hull (for wall thickness direction reference)
    cx, cy, area_sum = 0.0, 0.0, 0.0
    for i in range(n):
        x0, y0 = hull_verts_2d[i]
        x1, y1 = hull_verts_2d[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        area_sum += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(area_sum) > 1e-12:
        cx /= (3.0 * area_sum)
        cy /= (3.0 * area_sum)
    else:
        cx = float(np.mean(hull_verts_2d[:, 0]))
        cy = float(np.mean(hull_verts_2d[:, 1]))

    # Inner hull and wall thickness
    wall_thickness = None
    inner_hull_2d = None
    outer_set = set(hull.vertices.tolist())
    inner_pts = np.array([pts_2d[i] for i in range(len(pts_2d)) if i not in outer_set])

    if len(inner_pts) >= 3:
        try:
            inner_hull = ConvexHull(inner_pts)
            inner_hull_2d = inner_pts[inner_hull.vertices]

            center = np.array([cx, cy])
            n_angles = 36
            angles = np.linspace(0, 2 * np.pi, n_angles, endpoint=False)
            thicknesses = []
            for angle in angles:
                direction = np.array([np.cos(angle), np.sin(angle)])
                outer_r = float(np.max((hull_verts_2d - center) @ direction))
                inner_r = float(np.max((inner_hull_2d - center) @ direction))
                if outer_r > inner_r > 0:
                    thicknesses.append(outer_r - inner_r)
            if thicknesses:
                wall_thickness = float(np.median(thicknesses))
        except Exception:
            pass

    # 3D outline (outer hull vertices in original coordinates)
    outline_3d = []
    for idx in hull.vertices:
        pt_3d = pts_3d[idx]
        outline_3d.append([float(pt_3d[0]), float(pt_3d[1]), float(pt_3d[2])])
    outline_3d.append(outline_3d[0])  # close loop

    # 2D data for visualization
    outer_2d = hull_verts_2d.tolist()
    inner_2d = inner_hull_2d.tolist() if inner_hull_2d is not None else None

    return perimeter, wall_thickness, outer_2d, inner_2d, outline_3d, x_length, y_length



def _inverse_transform_point(point: np.ndarray, trsf) -> np.ndarray:
    """Apply the inverse of trsf to a 3D point."""
    inv = trsf.Inverted()
    p = gp_Pnt(float(point[0]), float(point[1]), float(point[2]))
    p.Transform(inv)
    return np.array([p.X(), p.Y(), p.Z()])


def compute_cumulative_lengths(positions):
    """Compute cumulative arc length along a polyline."""
    cumulative = [0.0]
    for i in range(1, len(positions)):
        p1 = np.array(positions[i - 1])
        p2 = np.array(positions[i])
        cumulative.append(cumulative[-1] + np.linalg.norm(p2 - p1))
    return cumulative


def _compute_section_perimeter_at(shape, point: np.ndarray, normal: np.ndarray):
    """Cut shape with a plane at `point` perpendicular to `normal`, return outer wire perimeter."""
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.TopTools import TopTools_HSequenceOfShape

    plane = gp_Pln(gp_Ax3(
        gp_Pnt(float(point[0]), float(point[1]), float(point[2])),
        gp_Dir(float(normal[0]), float(normal[1]), float(normal[2]))
    ))
    sectioner = BRepAlgoAPI_Section(shape, plane)
    sectioner.Build()
    if not sectioner.IsDone():
        return None

    section_shape = sectioner.Shape()
    edges = []
    exp = TopExp_Explorer(section_shape, TopAbs_EDGE)
    while exp.More():
        edges.append(TopoDS.Edge_s(exp.Current()))
        exp.Next()

    if not edges:
        return None

    edge_seq = TopTools_HSequenceOfShape()
    for e in edges:
        edge_seq.Append(e)
    wires_seq = TopTools_HSequenceOfShape()
    ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edge_seq, 1e-3, False, wires_seq)

    if wires_seq.Size() == 0:
        props = GProp_GProps()
        for e in edges:
            BRepGProp.LinearProperties_s(e, props)
        return props.Mass()

    wire_data = []
    for i in range(1, wires_seq.Size() + 1):
        wire = wires_seq.Value(i)
        p = GProp_GProps()
        w_exp = TopExp_Explorer(wire, TopAbs_EDGE)
        while w_exp.More():
            BRepGProp.LinearProperties_s(w_exp.Current(), p)
            w_exp.Next()
        wire_data.append(p.Mass())

    wire_data.sort(reverse=True)
    return wire_data[0]


def _compute_midpoint_centerline(shape, n_sections: int = 50, max_samples: int = 1500) -> dict:
    """Extract centerline using ray-based mid-surface method.

    Strategy:
    1. Triangulate shape, collect triangle centroids with normals
    2. Stratify triangles by axial position (along PCA axis) to ensure
       full coverage — naive area-sorted sampling can miss regions.
    3. For each triangle, shoot rays in BOTH normal directions
    4. If both directions hit a wall, midpoint = (hit1 + hit2) / 2 is on mid-surface
    5. Bin midpoints by axial position (PCA) to get centerline
    6. Fit B-spline and resample to n_sections
    7. At each centerline point, cut perpendicular plane for perimeter

    This method is unbiased for asymmetric cross-sections (D-shaped, water-drop profiles).
    No volume properties needed — works on hollow shells.
    """
    # -- Step 1: Triangulate and collect all triangles --
    mesher = BRepMesh_IncrementalMesh(shape, 0.5, False)
    mesher.Perform()

    # PCA for principal axis (to filter end caps and for stratification)
    pts = _sample_points(shape)
    if pts is not None and len(pts) >= 3:
        principal_axis = _pca_long_axis(pts)
    else:
        principal_axis = np.array([0.0, 0.0, 1.0])

    # Collect triangles with metadata
    triangles = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        surf = BRepAdaptor_Surface(face, True)
        surf_type = surf.GetType()

        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri:
            trans = loc.Transformation()
            nodes = []
            for idx in range(1, tri.NbNodes() + 1):
                p = tri.Node(idx).Transformed(trans)
                nodes.append(np.array([p.X(), p.Y(), p.Z()]))

            for idx in range(1, tri.NbTriangles() + 1):
                a_i, b_i, c_i = tri.Triangle(idx).Get()
                a, b, c = nodes[a_i - 1], nodes[b_i - 1], nodes[c_i - 1]
                centroid = (a + b + c) / 3.0
                normal = np.cross(b - a, c - a)
                nlen = float(np.linalg.norm(normal))
                if nlen > 1e-12:
                    normal = normal / nlen
                    area = 0.5 * nlen

                    # Filter end caps: planar faces with normal parallel to principal axis
                    is_end_cap = False
                    if surf_type == GeomAbs_Plane:
                        if abs(abs(np.dot(normal, principal_axis)) - 1.0) < 0.1:
                            is_end_cap = True

                    triangles.append({
                        "centroid": centroid,
                        "normal": normal,
                        "area": area,
                        "is_end_cap": is_end_cap,
                    })
        exp.Next()

    if not triangles:
        raise ValueError("No triangulation data found. Is this a valid 3D shape?")

    # -- Step 2: Bidirectional ray shooting for midpoints --
    # Select wall triangles (skip end caps)
    wall_tris = [t for t in triangles if not t["is_end_cap"]]

    # Stratify by axial position to ensure full coverage along tube length
    wall_tris.sort(key=lambda t: float(np.dot(t["centroid"], principal_axis)))

    if len(wall_tris) > max_samples:
        step = len(wall_tris) / max_samples
        wall_tris = [wall_tris[int(i * step)] for i in range(max_samples)]

    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(shape, 1e-6)

    midpoints = []
    offset = 0.08
    max_len = 30.0
    min_w = offset * 1.5

    for t in wall_tris:
        hit_distances = {}  # sign -> nearest hit distance

        for sign in (1.0, -1.0):
            direction = t["normal"] * sign
            start = t["centroid"] + direction * offset

            line = gp_Lin(
                gp_Pnt(float(start[0]), float(start[1]), float(start[2])),
                gp_Dir(float(direction[0]), float(direction[1]), float(direction[2]))
            )
            intersector.Perform(line, 0.0, max_len)

            if not intersector.IsDone() or intersector.NbPnt() == 0:
                continue

            hits = []
            for idx in range(1, intersector.NbPnt() + 1):
                w = float(intersector.WParameter(idx))
                if w > min_w:
                    hits.append(w)

            if hits:
                hit_distances[sign] = min(hits) + offset

        # Both directions must hit to get a valid midpoint
        if 1.0 in hit_distances and -1.0 in hit_distances:
            d1 = hit_distances[1.0]
            d2 = hit_distances[-1.0]
            # Total thickness
            thickness = d1 + d2
            if 0.2 <= thickness <= 15.0:
                # Midpoint = centroid + normal * (d1 - d2) / 2
                midpoint = t["centroid"] + t["normal"] * (d1 - d2) / 2.0
                midpoints.append(midpoint)

    if len(midpoints) < 10:
        raise ValueError(
            f"Only {len(midpoints)} valid midpoints found. "
            "Is this a hollow tube shape?"
        )

    # -- Step 3: PCA binning to extract centerline --
    # CRITICAL: Use the PCA axis from the ORIGINAL shape, not from midpoints.
    # For straight tubes, midpoint normals are all radial, so midpoint PCA
    # gives nearly equal eigenvalues and wrong axis. The shape's PCA axis
    # (computed earlier from _sample_points) is always correct.
    midpoint_arr = np.array(midpoints)

    # Try midpoint PCA first
    mid_center = midpoint_arr.mean(axis=0)
    centered = midpoint_arr - mid_center
    cov = np.cov(centered.T)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    mid_axis = eigenvectors[:, -1]
    mid_axis = mid_axis / np.linalg.norm(mid_axis)

    # Check if midpoint PCA is reliable (largest eigenvalue should dominate)
    eigenvalue_ratio = eigenvalues[-1] / max(eigenvalues[0], 1e-10)
    
    if eigenvalue_ratio > 2.0:
        # Midpoint PCA is reliable — midpoints are well-distributed along axis
        axis = mid_axis
    else:
        # Midpoint PCA is unreliable (e.g., straight tube with radial normals)
        # Fall back to shape PCA
        axis = principal_axis
        # Re-project onto shape axis direction (ensure consistent sign)
        if np.dot(axis, mid_center - midpoint_arr.mean(axis=0)) < 0:
            # Shouldn't happen since mid_center == midpoint_arr.mean(), but safety check
            pass

    # Re-center using shape axis
    scalars = np.dot(midpoint_arr - mid_center, axis)
    s_min, s_max = float(scalars.min()), float(scalars.max())

    # Bin by axial position
    n_bins = max(n_sections * 4, 100)
    bin_edges = np.linspace(s_min, s_max, n_bins + 1)
    bin_centers_list = []

    for i in range(n_bins):
        mask = (scalars >= bin_edges[i]) & (scalars < bin_edges[i + 1])
        if mask.sum() < 3:
            continue
        bin_points = midpoint_arr[mask]
        bin_centers_list.append(bin_points.mean(axis=0))

    if len(bin_centers_list) < 4:
        raise ValueError("Too few centerline points after binning")

    # -- Step 4: B-spline smoothing and resampling --
    pts_array = TColgp_Array1OfPnt(1, len(bin_centers_list))
    for i, p in enumerate(bin_centers_list):
        pts_array.SetValue(i + 1, gp_Pnt(float(p[0]), float(p[1]), float(p[2])))

    try:
        bspline = GeomAPI_PointsToBSpline(pts_array)
        curve = bspline.Curve()
        adaptor = GeomAdaptor_Curve(curve)
        t_min, t_max = adaptor.FirstParameter(), adaptor.LastParameter()
        params = np.linspace(t_min, t_max, n_sections)

        centerline_points = []
        for param in params:
            p = curve.Value(param)
            centerline_points.append(np.array([p.X(), p.Y(), p.Z()]))
    except Exception:
        # Fallback: linear interpolation through bin centers
        centerline_points = [np.array(p) for p in bin_centers_list]
        if len(centerline_points) > n_sections:
            indices = np.round(np.linspace(0, len(centerline_points) - 1, n_sections)).astype(int)
            centerline_points = [centerline_points[i] for i in indices]

    # -- Step 5: Cross-section perimeters at centerline points --
    sections = []
    for i, cp in enumerate(centerline_points):
        # Local tangent direction
        tangent_vec = axis  # default fallback
        if i > 0:
            diff = centerline_points[i] - centerline_points[i - 1]
            dlen = np.linalg.norm(diff)
            if dlen > 1e-9:
                tangent_vec = diff / dlen
        elif i < len(centerline_points) - 1:
            diff = centerline_points[i + 1] - centerline_points[i]
            dlen = np.linalg.norm(diff)
            if dlen > 1e-9:
                tangent_vec = diff / dlen

        perimeter = _compute_section_perimeter_at(shape, cp, tangent_vec)
        if perimeter is None or perimeter < 1e-6:
            continue

        sections.append({
            "position": [float(cp[0]), float(cp[1]), float(cp[2])],
            "perimeter": float(perimeter),
        })

    if len(sections) < 4:
        raise ValueError("Too few valid cross-sections found")

    # Cumulative lengths
    positions_list = [s["position"] for s in sections]
    cum_lengths = compute_cumulative_lengths(positions_list)
    for i, s in enumerate(sections):
        s["cumulative_length"] = cum_lengths[i]

    return {
        "sections": sections,
        "center_line_length": cum_lengths[-1] if cum_lengths else 0.0,
        "center_line_points": [s["position"] for s in sections],
        "bounding_box": list(get_bounding_box(shape)),
    }


def _compute_centerline_from_surface(shape, n_sections: int = 50, layer_thickness: float = 5.0) -> dict:
    """Extract centerline by sampling outer surface points and fitting circles.

    Strategy (no plane cutting):
    1. Dense sampling of all surface points from STEP faces
    2. PCA to find principal axis
    3. Layer points along the axis (every layer_thickness mm)
    4. For each layer, project points onto the perpendicular plane,
       fit a circle, use circle center as centerline point
    5. Filter outlier layers, smooth, fit B-spline, resample

    This method works purely from outer surface information — no inner wall
    or thickness info needed.
    """
    # -- Step 1: Dense surface sampling --
    all_points = []

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        surf = BRepAdaptor_Surface(face, True)
        surf_type = surf.GetType()

        # Skip planar faces (likely end caps or flat features)
        if surf_type == GeomAbs_Plane:
            exp.Next()
            continue

        u_min = surf.FirstUParameter()
        u_max = surf.LastUParameter()
        v_min = surf.FirstVParameter()
        v_max = surf.LastVParameter()
        u_span = u_max - u_min
        v_span = v_max - v_min
        n_u = max(10, int(u_span / 0.2))
        n_v = max(10, int(v_span / 0.2))

        for i in range(n_u):
            for j in range(n_v):
                u = u_min + (i + 0.5) * u_span / n_u
                v = v_min + (j + 0.5) * v_span / n_v
                p = surf.Value(u, v)
                all_points.append([p.X(), p.Y(), p.Z()])

        exp.Next()

    if len(all_points) < 100:
        raise ValueError(f"Too few surface points ({len(all_points)}). Is this a valid tube?")

    pts = np.array(all_points)

    # -- Step 2: PCA for principal axis --
    long_axis = _pca_long_axis(pts)
    center = pts.mean(axis=0)

    # Build coordinate system: Z' = long_axis, X', Y' = perpendicular plane
    z_axis = long_axis / np.linalg.norm(long_axis)
    if abs(np.dot(z_axis, np.array([1, 0, 0]))) < 0.9:
        x_axis = np.cross(z_axis, np.array([1, 0, 0]))
    else:
        x_axis = np.cross(z_axis, np.array([0, 1, 0]))
    x_axis = x_axis / np.linalg.norm(x_axis)
    y_axis = np.cross(z_axis, x_axis)

    # -- Step 3: Layer points along principal axis --
    z_projections = np.dot(pts - center, z_axis)
    z_min, z_max = z_projections.min(), z_projections.max()

    n_layers = max(10, int((z_max - z_min) / layer_thickness))
    layer_edges = np.linspace(z_min, z_max, n_layers + 1)

    circle_centers = []
    circle_radii = []
    layer_z_positions = []

    for i in range(n_layers):
        mask = (z_projections >= layer_edges[i]) & (z_projections < layer_edges[i + 1])
        layer_pts = pts[mask]
        if len(layer_pts) < 20:
            continue

        # Project layer points onto perpendicular plane
        local_z = np.dot(layer_pts - center, z_axis)
        local_x = np.dot(layer_pts - center, x_axis)
        local_y = np.dot(layer_pts - center, y_axis)
        plane_pts = np.column_stack([local_x, local_y])

        # Fit circle
        cx, cy, r = _fit_circle_2d(plane_pts)
        if cx is None or r is None or r <= 0:
            continue

        # -- Quality checks --
        # Check 1: Radius must be reasonable (use median of collected radii as reference)
        if len(circle_radii) > 5:
            median_r = float(np.median(circle_radii))
            # Reject if radius deviates more than 50% from median
            if abs(r - median_r) > median_r * 0.50:
                continue

        # Check 2: Circle center must be close to other centers (reject outliers)
        if len(circle_centers) > 5:
            prev_centers = np.array(circle_centers[-10:])
            avg_prev = prev_centers.mean(axis=0)
            # Project avg_prev onto the perpendicular plane to get 2D center
            prev_2d_x = np.dot(avg_prev - center, x_axis)
            prev_2d_y = np.dot(avg_prev - center, y_axis)
            dist = np.sqrt((cx - prev_2d_x) ** 2 + (cy - prev_2d_y) ** 2)
            if dist > median_r * 0.8:
                continue

        center_3d = center + cx * x_axis + cy * y_axis + float(np.mean(local_z)) * z_axis
        circle_centers.append(center_3d)
        circle_radii.append(r)
        layer_z_positions.append(float(np.mean(local_z)))

    if len(circle_centers) < 4:
        raise ValueError(f"Too few valid layers ({len(circle_centers)}). Is this a tube?")

    # -- Step 4: Additional outlier rejection using radius consistency --
    radii_arr = np.array(circle_radii)
    median_r = float(np.median(radii_arr))
    mad_r = float(np.median(np.abs(radii_arr - median_r)))
    radius_tolerance = 3.0 * max(mad_r, median_r * 0.05)

    valid_mask = np.abs(radii_arr - median_r) <= radius_tolerance
    centers_arr = np.array(circle_centers)[valid_mask]
    layer_z_arr = np.array(layer_z_positions)[valid_mask]

    if len(centers_arr) < 4:
        raise ValueError("Too few valid layers after outlier rejection.")

    # -- Step 5: B-spline fitting and resampling --
    # Smooth with moving average
    window = max(3, len(centers_arr) // 15)
    if len(centers_arr) > window:
        kernel = np.ones(window) / window
        smoothed = np.zeros_like(centers_arr)
        for dim in range(3):
            smoothed[:, dim] = np.convolve(centers_arr[:, dim], kernel, mode='same')
        centers_arr = smoothed

    # B-spline
    pts_array = TColgp_Array1OfPnt(1, len(centers_arr))
    for i, p in enumerate(centers_arr):
        pts_array.SetValue(i + 1, gp_Pnt(float(p[0]), float(p[1]), float(p[2])))

    try:
        bspline = GeomAPI_PointsToBSpline(pts_array)
        curve = bspline.Curve()
        adaptor = GeomAdaptor_Curve(curve)
        t_min, t_max = adaptor.FirstParameter(), adaptor.LastParameter()
        params = np.linspace(t_min, t_max, n_sections)

        centerline_points = []
        for param in params:
            p = curve.Value(param)
            centerline_points.append(np.array([p.X(), p.Y(), p.Z()]))
    except Exception:
        centerline_points = [np.array(p) for p in centers_arr]
        if len(centerline_points) > n_sections:
            indices = np.round(np.linspace(0, len(centerline_points) - 1, n_sections)).astype(int)
            centerline_points = [centerline_points[i] for i in indices]

    # Build output
    # Filter out centerline points outside the model bounding box
    bbox = get_bounding_box(shape)
    margin = 2.0  # small tolerance margin
    x_lo, y_lo, z_lo = bbox[0] - margin, bbox[1] - margin, bbox[2] - margin
    x_hi, y_hi, z_hi = bbox[3] + margin, bbox[4] + margin, bbox[5] + margin

    filtered_points = []
    for cp in centerline_points:
        if (x_lo <= cp[0] <= x_hi and
            y_lo <= cp[1] <= y_hi and
            z_lo <= cp[2] <= z_hi):
            filtered_points.append(cp)

    if len(filtered_points) < 4:
        raise ValueError(
            f"Too few valid centerline points after bounding box filter "
            f"({len(filtered_points)}). The algorithm may not be suitable for this shape."
        )

    sections = []
    for i, cp in enumerate(filtered_points):
        sections.append({
            "position": [float(cp[0]), float(cp[1]), float(cp[2])],
            "perimeter": 0.0,
        })

    positions_list = [s["position"] for s in sections]
    cum_lengths = compute_cumulative_lengths(positions_list)
    for i, s in enumerate(sections):
        s["cumulative_length"] = cum_lengths[i]

    return {
        "sections": sections,
        "center_line_length": cum_lengths[-1] if cum_lengths else 0.0,
        "center_line_points": [s["position"] for s in sections],
        "bounding_box": list(get_bounding_box(shape)),
    }


def _fit_circle_2d(points: np.ndarray):
    """Fit a circle to 2D points using least squares.
    Returns (cx, cy, radius) or (None, None, None) on failure.
    """
    if len(points) < 3:
        return None, None, None
    try:
        x = points[:, 0]
        y = points[:, 1]
        A = np.column_stack([2 * x, 2 * y, np.ones(len(points))])
        b = x ** 2 + y ** 2
        result, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        cx, cy, c = result
        r = float(np.sqrt(c + cx ** 2 + cy ** 2))
        return float(cx), float(cy), r
    except Exception:
        return None, None, None


def analyze_shape(shape, n_sections: int = 50, progress_callback=None, skip_secondary: bool = True) -> dict:
    """Analyze a tube shape: extract center line and cross-section perimeters.

    Strategy (user-requested approach):
    1. PCA to find approximate principal axis
    2. Rotate shape so the axis aligns with +Z
    3. Cut with horizontal planes (parallel to XY) at 1mm step
    4. For each section, compute the convex hull of all section points,
       use the hull's geometric centroid as the centerline point
    5. Inverse-rotate centerline points back to original coordinates
    6. Resample to n_sections output points

    Works for straight, tapered, bent, and flared tubes.

    progress_callback: optional callable(stage, progress_0_to_1, message)
    """
    if progress_callback:
        progress_callback("pca", 0.01, "PCA主轴分析...")

    # -- Step 1: PCA to find long axis --
    pts = _sample_points(shape)
    if pts is not None and len(pts) >= 3:
        long_axis = _pca_long_axis(pts)
        center = pts.mean(axis=0)
    else:
        bbox = get_bounding_box(shape)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        lengths = [xmax - xmin, ymax - ymin, zmax - zmin]
        axis_idx = int(np.argmax(lengths))
        long_axis = np.eye(3)[axis_idx]
        center = np.array([(xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2])

    long_axis = long_axis / np.linalg.norm(long_axis)

    # -- Step 2: Rotate shape to align long axis with +Z --
    if progress_callback:
        progress_callback("rotate", 0.03, "模型旋转对齐...")
    trsf = _make_align_to_z_transform(long_axis, center)
    rotated_shape = _apply_transform(shape, trsf)

    # -- Step 3: Get Z range of rotated shape --
    bbox = get_bounding_box(rotated_shape)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    z_min = zmin + 1.0
    z_max = zmax - 1.0
    z_span = z_max - z_min

    if z_span <= 0:
        raise ValueError("Shape has no extent along the long axis. Is this a tube?")

    # -- Step 4: Dense horizontal cuts at 1mm step --
    step = 1.0
    z_values = np.arange(z_min, z_max, step)
    total_cuts = len(z_values)

    raw_results = []
    for idx, z in enumerate(z_values):
        result = _horizontal_section(rotated_shape, float(z))
        if result is not None:
            centroid, perimeter, wall_thickness, hull_verts, inner_hull_verts, x_length, y_length = result
            if perimeter > 1e-6:
                raw_results.append((np.array(centroid), perimeter, wall_thickness, hull_verts, inner_hull_verts, x_length, y_length))
        if progress_callback and idx % 50 == 0:
            p = 0.05 + 0.35 * ((idx + 1) / total_cuts)
            progress_callback("cutting", p, f"一次切割 {idx + 1}/{total_cuts}")

    if len(raw_results) < 4:
        raise ValueError(
            f"Only {len(raw_results)} valid sections found. "
            "Is this a tube shape? Try uploading a single tube component."
        )

    # -- Step 5: Filter outlier sections (perimeter inflated > 140% of median) --
    if progress_callback:
        progress_callback("filtering", 0.42, "异常值过滤...")
    perimeters = [r[1] for r in raw_results]
    median_p = float(np.median(perimeters))
    if median_p > 0:
        raw_results = [r for r in raw_results if r[1] <= median_p * 1.40]

    if len(raw_results) < 4:
        raise ValueError(
            f"After filtering, only {len(raw_results)} valid sections remain. "
            "Is this a tube shape?"
        )

    # -- Step 6: Inverse-rotate centroids back to original coordinates --
    if progress_callback:
        progress_callback("transform", 0.44, "坐标反变换...")
    original_results = []
    for centroid, perimeter, wt, hull_verts, inner_hull_verts, x_length, y_length in raw_results:
        orig_centroid = _inverse_transform_point(centroid, trsf)
        # Transform hull vertices back to original coordinates
        z_val = centroid[2]
        orig_hull = []
        for hv in hull_verts:
            pt_3d = np.array([hv[0], hv[1], z_val])
            orig_hull.append(_inverse_transform_point(pt_3d, trsf))
        # Keep 2D hull points for cross-section view (in rotated XY plane)
        hull_2d = hull_verts.tolist()
        inner_2d = inner_hull_verts.tolist() if inner_hull_verts is not None else None
        original_results.append((orig_centroid, perimeter, wt, orig_hull, hull_2d, inner_2d, x_length, y_length))

    # -- Step 6.5: Trim ends and extrapolate smoothly --
    # The first/last ~10mm may have incomplete sections causing offset.
    # Trim 10 points from each end, then extrapolate along the local direction.
    if progress_callback:
        progress_callback("smoothing", 0.46, "端部平滑外推...")

    trim_count = 10  # 10mm at 1mm step
    n_total = len(original_results)

    if n_total > trim_count * 2 + 10:
        # Extract centerline positions
        all_positions = np.array([r[0] for r in original_results])
        all_perimeters = [r[1] for r in original_results]
        all_wt = [r[2] for r in original_results]
        all_hulls = [r[3] for r in original_results]
        all_hull_2d = [r[4] for r in original_results]
        all_inner_2d = [r[5] for r in original_results]
        all_x_lengths = [r[6] for r in original_results]
        all_y_lengths = [r[7] for r in original_results]

        # Keep the core (trimmed) section
        core_positions = all_positions[trim_count: n_total - trim_count]

        # Extrapolate the head (first trim_count points)
        # Use the first few core points to determine direction
        n_fit = min(5, len(core_positions) - 1)
        head_direction = core_positions[0] - core_positions[n_fit]
        head_direction = head_direction / max(np.linalg.norm(head_direction), 1e-12)
        # Average step size in the head region
        head_step = np.mean([np.linalg.norm(core_positions[i+1] - core_positions[i])
                           for i in range(n_fit)])
        head_points = []
        for i in range(trim_count, 0, -1):
            pt = core_positions[0] + head_direction * head_step * i
            head_points.append(pt)

        # Extrapolate the tail (last trim_count points)
        tail_direction = core_positions[-1] - core_positions[-1 - n_fit]
        tail_direction = tail_direction / max(np.linalg.norm(tail_direction), 1e-12)
        tail_step = np.mean([np.linalg.norm(core_positions[-i] - core_positions[-i-1])
                           for i in range(1, n_fit + 1)])
        tail_points = []
        for i in range(1, trim_count + 1):
            pt = core_positions[-1] + tail_direction * tail_step * i
            tail_points.append(pt)

        # Rebuild full centerline: extrapolated head + core + extrapolated tail
        smoothed_positions = np.vstack([head_points, core_positions, tail_points])

        # Rebuild original_results with smoothed positions
        original_results = []
        for i in range(len(smoothed_positions)):
            if i < trim_count:
                j = trim_count
            elif i >= len(smoothed_positions) - trim_count:
                j = n_total - trim_count - 1
            else:
                j = i
            original_results.append((smoothed_positions[i], all_perimeters[j], all_wt[j], all_hulls[j], all_hull_2d[j], all_inner_2d[j], all_x_lengths[j], all_y_lengths[j]))

    # -- Step 7: Secondary cuts (optional — skipped by default for speed) --
    if skip_secondary:
        if progress_callback:
            progress_callback("secondary", 0.50, "跳過二次切割(使用一次切割結果)...")
        # Build secondary_results directly from primary results
        secondary_results = []
        for i, r in enumerate(original_results):
            centroid, perimeter, wt, orig_hull, hull_2d, inner_2d, x_length, y_length = r
            secondary_results.append({
                "position": centroid,
                "perimeter": perimeter,
                "wall_thickness": wt,
                "outer_2d": hull_2d,
                "inner_2d": inner_2d,
                "outline": orig_hull,
                "x_length": x_length,
                "y_length": y_length,
            })
    else:
        if progress_callback:
            progress_callback("secondary", 0.50, "二次切割(垂直于中心线)...")
        centerline_pts = np.array([r[0] for r in original_results])
        n_cl = len(centerline_pts)
        tangents = np.zeros_like(centerline_pts)
        for i in range(n_cl):
            if i == 0:
                tangents[i] = centerline_pts[1] - centerline_pts[0]
            elif i == n_cl - 1:
                tangents[i] = centerline_pts[-1] - centerline_pts[-2]
            else:
                tangents[i] = centerline_pts[i + 1] - centerline_pts[i - 1]
            t_norm = np.linalg.norm(tangents[i])
            if t_norm > 1e-12:
                tangents[i] /= t_norm
        secondary_results = []
        for i in range(n_cl):
            result = _perpendicular_section(shape, centerline_pts[i], tangents[i])
            if result is not None:
                perim, wt, outer_2d, inner_2d, outline_3d, x_length, y_length = result
                secondary_results.append({
                    "position": centerline_pts[i],
                    "perimeter": perim,
                    "wall_thickness": wt,
                    "outer_2d": outer_2d,
                    "inner_2d": inner_2d,
                    "outline": outline_3d,
                    "x_length": x_length,
                    "y_length": y_length,
                })
            else:
                secondary_results.append({
                    "position": centerline_pts[i],
                    "perimeter": original_results[i][1],
                    "wall_thickness": None,
                    "outer_2d": None,
                    "inner_2d": None,
                    "outline": None,
                    "x_length": None,
                    "y_length": None,
                })
            if progress_callback and i % 50 == 0:
                p = 0.50 + 0.40 * (i / n_cl)
                progress_callback("secondary", p, f"二次切割 {i}/{n_cl}")

    # -- Step 8: End-trimming + extrapolation for secondary data --
    if progress_callback:
        progress_callback("trim2", 0.92, "二次切割端部平滑...")

    trim2 = 10
    if len(secondary_results) > trim2 * 2 + 10:
        all_perims = [r["perimeter"] for r in secondary_results]
        all_wts = [r["wall_thickness"] for r in secondary_results]

        # Trim and extrapolate perimeters
        core_perims = all_perims[trim2: -trim2]
        head_perim = core_perims[0]
        tail_perim = core_perims[-1]
        smoothed_perims = [head_perim] * trim2 + core_perims + [tail_perim] * trim2

        # Trim and extrapolate wall thickness
        core_wts = all_wts[trim2: -trim2]
        head_wt = core_wts[0]
        tail_wt = core_wts[-1]
        smoothed_wts = [head_wt] * trim2 + core_wts + [tail_wt] * trim2

        # Apply smoothed values
        for i in range(len(secondary_results)):
            secondary_results[i]["perimeter"] = smoothed_perims[i]
            secondary_results[i]["wall_thickness"] = smoothed_wts[i]
            # For trimmed ends, reuse nearest valid section's 2d/outline
            if i < trim2:
                secondary_results[i]["outer_2d"] = secondary_results[trim2]["outer_2d"]
                secondary_results[i]["inner_2d"] = secondary_results[trim2]["inner_2d"]
                secondary_results[i]["outline"] = secondary_results[trim2]["outline"]
            elif i >= len(secondary_results) - trim2:
                j = len(secondary_results) - trim2 - 1
                secondary_results[i]["outer_2d"] = secondary_results[j]["outer_2d"]
                secondary_results[i]["inner_2d"] = secondary_results[j]["inner_2d"]
                secondary_results[i]["outline"] = secondary_results[j]["outline"]

    # -- Step 9: Build output --
    if progress_callback:
        progress_callback("building", 0.94, "构建输出数据...")

    # sections_full: all 1mm data for charts
    sections_full = []
    for r in secondary_results:
        sec = {
            "position": [float(r["position"][0]), float(r["position"][1]), float(r["position"][2])],
            "perimeter": round(float(r["perimeter"]), 1),
        }
        if r["wall_thickness"] is not None:
            sec["wall_thickness"] = round(float(r["wall_thickness"]), 1)
        if r["outer_2d"] is not None:
            sec["outer_2d"] = [[round(p[0], 3), round(p[1], 3)] for p in r["outer_2d"]]
        if r["inner_2d"] is not None:
            sec["inner_2d"] = [[round(p[0], 3), round(p[1], 3)] for p in r["inner_2d"]]
        if r.get("x_length") is not None:
            sec["x_length"] = round(float(r["x_length"]), 2)
            sec["y_length"] = round(float(r["y_length"]), 2)
        sections_full.append(sec)

    # Cumulative lengths for full data
    full_positions = [s["position"] for s in sections_full]
    full_cum_lengths = compute_cumulative_lengths(full_positions)
    for i, s in enumerate(sections_full):
        s["cumulative_length"] = full_cum_lengths[i]

    # sections: sparse (n_sections) with 3D outline for 3D view
    raw_n = len(secondary_results)
    if raw_n <= n_sections:
        sparse_indices = list(range(raw_n))
    else:
        sparse_indices = np.round(np.linspace(0, raw_n - 1, n_sections)).astype(int).tolist()

    sections = []
    for i in sparse_indices:
        r = secondary_results[i]
        sec = {
            "position": [float(r["position"][0]), float(r["position"][1]), float(r["position"][2])],
            "perimeter": round(float(r["perimeter"]), 1),
        }
        if r["wall_thickness"] is not None:
            sec["wall_thickness"] = round(float(r["wall_thickness"]), 1)
        if r["outline"] is not None:
            sec["outline"] = r["outline"]
        sec["cumulative_length"] = full_cum_lengths[i]
        sections.append(sec)

    if progress_callback:
        progress_callback("done", 1.0, "分析完成")

    # -- Step 10: Wall thickness statistics --
    wt_values = [s["wall_thickness"] for s in sections_full if "wall_thickness" in s]
    wt_stats = None
    if wt_values:
        wt_arr = np.array(wt_values)
        wt_stats = {
            "median_mm": round(float(np.median(wt_arr)), 1),
            "mean_mm": round(float(np.mean(wt_arr)), 1),
            "min_mm": round(float(np.min(wt_arr)), 1),
            "max_mm": round(float(np.max(wt_arr)), 1),
            "count": len(wt_values),
        }

    return {
        "sections": sections,
        "sections_full": sections_full,
        "center_line_length": full_cum_lengths[-1] if full_cum_lengths else 0.0,
        "center_line_points": [s["position"] for s in sections],
        "bounding_box": list(get_bounding_box(shape)),
        "wall_thickness_stats": wt_stats,
    }


def _get_cutting_planes(shape, step: float = 20.0) -> list:
    """Compute cutting planes for visualization.

    Same logic as analyze_shape: PCA → rotate → cut with horizontal planes.
    Returns plane data every `step` mm (default 20mm) for 3D display.

    Each plane entry has:
    - position: [x, y, z] center of the section
    - normal: [nx, ny, nz] plane normal (in original coordinates)
    - outer_edge: list of [x, y, z] points on the outer wall curve
    """
    # PCA and rotation (same as analyze_shape)
    pts = _sample_points(shape)
    if pts is not None and len(pts) >= 3:
        long_axis = _pca_long_axis(pts)
        center = pts.mean(axis=0)
    else:
        bbox = get_bounding_box(shape)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        lengths = [xmax - xmin, ymax - ymin, zmax - zmin]
        axis_idx = int(np.argmax(lengths))
        long_axis = np.eye(3)[axis_idx]
        center = np.array([(xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2])

    long_axis = long_axis / np.linalg.norm(long_axis)
    trsf = _make_align_to_z_transform(long_axis, center)
    rotated_shape = _apply_transform(shape, trsf)

    bbox = get_bounding_box(rotated_shape)
    z_min = bbox[2] + 1.0
    z_max = bbox[5] - 1.0

    z_values = np.arange(z_min, z_max, step)
    planes = []

    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_UniformAbscissa

    for z in z_values:
        plane = gp_Pln(gp_Ax3(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)))
        sectioner = BRepAlgoAPI_Section(rotated_shape, plane)
        sectioner.Build()
        if not sectioner.IsDone():
            continue

        section_shape = sectioner.Shape()

        # Sample points from all edges
        all_points_2d = []
        all_points_3d = []
        exp = TopExp_Explorer(section_shape, TopAbs_EDGE)
        while exp.More():
            edge = TopoDS.Edge_s(exp.Current())
            try:
                adaptor = BRepAdaptor_Curve(edge)
                lp = GProp_GProps()
                BRepGProp.LinearProperties_s(edge, lp)
                edge_len = lp.Mass()
                n_pts = max(5, int(edge_len / 0.3))
                params = GCPnts_UniformAbscissa(adaptor, n_pts)
                if params.IsDone():
                    for i in range(1, params.NbPoints() + 1):
                        pt = adaptor.Value(params.Parameter(i))
                        all_points_2d.append([pt.X(), pt.Y()])
                        all_points_3d.append([pt.X(), pt.Y(), pt.Z()])
            except Exception:
                pass
            exp.Next()

        if len(all_points_2d) < 3:
            continue

        pts_2d = np.array(all_points_2d)
        pts_3d = np.array(all_points_3d)

        # Convex hull = outer envelope ("rubber band")
        try:
            from scipy.spatial import ConvexHull
            hull = ConvexHull(pts_2d)
        except Exception:
            continue

        # Get hull vertices in order, transform back to original coordinates
        hull_indices = hull.vertices
        outer_points = []
        for idx in hull_indices:
            pt_orig = _inverse_transform_point(pts_3d[idx], trsf)
            outer_points.append([float(pt_orig[0]), float(pt_orig[1]), float(pt_orig[2])])
        # Close the loop
        if outer_points:
            outer_points.append(outer_points[0])

        if len(outer_points) < 4:
            continue

        # Section center (centroid of convex hull polygon)
        hv = pts_2d[hull_indices]
        n_hv = len(hv)
        cx, cy, area_sum = 0.0, 0.0, 0.0
        for i in range(n_hv):
            x0, y0 = hv[i]
            x1, y1 = hv[(i + 1) % n_hv]
            cross = x0 * y1 - x1 * y0
            area_sum += cross
            cx += (x0 + x1) * cross
            cy += (y0 + y1) * cross
        if abs(area_sum) > 1e-12:
            cx /= (3.0 * area_sum)
            cy /= (3.0 * area_sum)
        else:
            cx = float(np.mean(hv[:, 0]))
            cy = float(np.mean(hv[:, 1]))

        sec_center_rot = np.array([cx, cy, z])
        sec_center = _inverse_transform_point(sec_center_rot, trsf)

        # Section normal (inverse transform of Z axis)
        sec_normal = _inverse_transform_point(np.array([0.0, 0.0, 1.0]), trsf) - center
        sn_len = float(np.linalg.norm(sec_normal))
        if sn_len > 1e-12:
            sec_normal = sec_normal / sn_len

        planes.append({
            "position": [float(sec_center[0]), float(sec_center[1]), float(sec_center[2])],
            "normal": [float(sec_normal[0]), float(sec_normal[1]), float(sec_normal[2])],
            "outer_edge": outer_points,
        })

    return planes


def _analyze_shape_legacy(shape, n_sections: int = 50) -> dict:
    """Legacy centerline extraction using plane sectioning + outer-wall centroid.

    Strategy (traditional brute-force):
    1. PCA to find the long axis of the tube
    2. Rotate the shape so the long axis aligns with +Z
    3. Cut with horizontal planes (parallel to XY) at 1mm step
    4. Compute centroid of each section's outer wall
    5. Connect centroids = center line (in rotated space)
    6. Inverse-rotate center line points back to original coordinates
    7. Resample to n_sections output points
    """
    # -- Step 1: PCA to find long axis --
    pts = _sample_points(shape)
    if pts is not None and len(pts) >= 3:
        long_axis = _pca_long_axis(pts)
        center = pts.mean(axis=0)
    else:
        bbox = get_bounding_box(shape)
        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        lengths = [xmax - xmin, ymax - ymin, zmax - zmin]
        axis_idx = int(np.argmax(lengths))
        long_axis = np.eye(3)[axis_idx]
        center = np.array([(xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2])

    long_axis = long_axis / np.linalg.norm(long_axis)

    # -- Step 2: Rotate shape to align long axis with +Z --
    trsf = _make_align_to_z_transform(long_axis, center)
    rotated_shape = _apply_transform(shape, trsf)

    # -- Step 3: Get Z range of rotated shape --
    bbox = get_bounding_box(rotated_shape)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    z_min = zmin + 1.0  # small margin to avoid end effects
    z_max = zmax - 1.0
    z_span = z_max - z_min

    if z_span <= 0:
        raise ValueError("Shape has no extent along the long axis. Is this a tube?")

    # -- Step 4: Dense horizontal cuts at 1mm step --
    step = 1.0  # 1mm step
    z_values = np.arange(z_min, z_max, step)

    raw_results = []
    for z in z_values:
        result = _horizontal_section(rotated_shape, z)
        if result is not None:
            centroid, perimeter = result[0], result[1]
            if perimeter > 1e-6:
                raw_results.append((np.array(centroid), perimeter))

    if len(raw_results) < 4:
        raise ValueError(
            f"Only {len(raw_results)} valid sections found. "
            "Is this a tube shape? Try uploading a single tube component."
        )

    # -- Step 5: Filter outlier sections (perimeter inflated > 140% of median) --
    perimeters = [r[1] for r in raw_results]
    median_p = float(np.median(perimeters))
    if median_p > 0:
        raw_results = [r for r in raw_results if r[1] <= median_p * 1.40]

    if len(raw_results) < 4:
        raise ValueError(
            f"After filtering, only {len(raw_results)} valid sections remain. "
            "Is this a tube shape?"
        )

    # -- Step 6: Inverse-rotate centroids back to original coordinates --
    original_results = []
    for centroid, perimeter in raw_results:
        orig_centroid = _inverse_transform_point(centroid, trsf)
        original_results.append((orig_centroid, perimeter))

    # -- Step 7: Resample to n_sections --
    raw_n = len(original_results)
    if raw_n <= n_sections:
        selected = original_results
    else:
        indices = np.round(np.linspace(0, raw_n - 1, n_sections)).astype(int)
        selected = [original_results[i] for i in indices]

    # -- Step 8: Build output sections --
    sections = []
    for centroid, perimeter in selected:
        sections.append({
            "position": [float(centroid[0]), float(centroid[1]), float(centroid[2])],
            "perimeter": float(perimeter),
        })

    # -- Step 9: Cumulative center line length --
    positions_list = [s["position"] for s in sections]
    cum_lengths = compute_cumulative_lengths(positions_list)
    for i, s in enumerate(sections):
        s["cumulative_length"] = cum_lengths[i]

    return {
        "sections": sections,
        "center_line_length": cum_lengths[-1] if cum_lengths else 0.0,
        "center_line_points": [s["position"] for s in sections],
        "bounding_box": list(get_bounding_box(shape)),
    }


def _compute_wall_thickness(shape, max_samples: int = 300, progress_callback=None) -> dict:
    """Analyze wall thickness using ray intersection on meshed shape.

    Strategy:
    1. Triangulate the shape with BRepMesh_IncrementalMesh
    2. For each triangle centroid, shoot a ray along its normal
    3. Find the distance to the opposite wall using IntCurvesFace_ShapeIntersector
    4. Filter outliers using MAD (Median Absolute Deviation)
    5. Return samples + statistics

    progress_callback: optional callable(stage, progress_0_to_1, message)
    """
    if progress_callback:
        progress_callback("mesh", 0.02, "网格化处理...")
    # Mesh the shape
    mesher = BRepMesh_IncrementalMesh(shape, 0.5, False)
    mesher.Perform()

    # Collect triangle centroids with normals
    samples = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri:
            trans = loc.Transformation()
            nodes = []
            for idx in range(1, tri.NbNodes() + 1):
                p = tri.Node(idx).Transformed(trans)
                nodes.append(np.array([p.X(), p.Y(), p.Z()]))

            for idx in range(1, tri.NbTriangles() + 1):
                a_i, b_i, c_i = tri.Triangle(idx).Get()
                a, b, c = nodes[a_i - 1], nodes[b_i - 1], nodes[c_i - 1]
                centroid = (a + b + c) / 3.0
                normal = np.cross(b - a, c - a)
                nlen = float(np.linalg.norm(normal))
                if nlen > 1e-12:
                    samples.append({
                        "centroid": centroid,
                        "normal": normal / nlen,
                        "area": 0.5 * nlen,
                    })
        exp.Next()

    if not samples:
        return {"samples": [], "stats": {"median_mm": None}}

    # Limit samples, prefer larger triangles (more likely on tube walls)
    samples.sort(key=lambda s: s["area"], reverse=True)
    candidates = [s for s in samples if s["area"] > 0.05]
    if not candidates:
        candidates = samples
    if len(candidates) > max_samples:
        step = len(candidates) / max_samples
        candidates = [candidates[int(i * step)] for i in range(max_samples)]

    total_samples = len(candidates)
    if progress_callback:
        progress_callback("thickness_start", 0.05, f"壁厚采样 0/{total_samples}")

    # Ray intersection for wall thickness
    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(shape, 1e-6)

    results = []
    offset = 0.08  # push start point slightly away from surface
    max_len = 30.0  # max expected wall thickness
    min_w = offset * 1.5  # minimum hit distance (avoid self-hit)

    for i, s in enumerate(candidates):
        for sign in (1.0, -1.0):
            direction = s["normal"] * sign
            start = s["centroid"] + direction * offset
            line = gp_Lin(
                gp_Pnt(float(start[0]), float(start[1]), float(start[2])),
                gp_Dir(float(direction[0]), float(direction[1]), float(direction[2])),
            )
            intersector.Perform(line, 0.0, max_len)
            if not intersector.IsDone() or intersector.NbPnt() == 0:
                continue
            hits = []
            for idx in range(1, intersector.NbPnt() + 1):
                w = float(intersector.WParameter(idx))
                if w > min_w:
                    hits.append(w)
            if hits:
                thickness = min(hits) + offset
                if 0.2 <= thickness <= 15.0:  # reasonable range for bike tubes
                    results.append({
                        "x": float(s["centroid"][0]),
                        "y": float(s["centroid"][1]),
                        "z": float(s["centroid"][2]),
                        "thickness_mm": round(float(thickness), 3),
                    })
        if progress_callback:
            p = 0.05 + 0.90 * ((i + 1) / total_samples)
            progress_callback("thickness", p, f"壁厚采样 {i + 1}/{total_samples}")

    # Filter outliers using MAD
    if not results:
        return {"samples": [], "stats": {"median_mm": None}}

    thicknesses = np.array([r["thickness_mm"] for r in results])
    med = float(np.median(thicknesses))
    mad = float(np.median(np.abs(thicknesses - med)))
    tolerance = 3.0 * max(mad, 0.05)

    core_values = []
    for r in results:
        t = r["thickness_mm"]
        is_core = abs(t - med) <= tolerance
        r["is_core"] = is_core
        if is_core:
            core_values.append(t)

    core_arr = np.array(core_values) if core_values else thicknesses
    stats = {
        "median_mm": round(float(np.median(core_arr)), 3),
        "mean_mm": round(float(np.mean(core_arr)), 3),
        "min_mm": round(float(np.min(core_arr)), 3),
        "max_mm": round(float(np.max(core_arr)), 3),
        "p10_mm": round(float(np.percentile(core_arr, 10)), 3),
        "p90_mm": round(float(np.percentile(core_arr, 90)), 3),
        "count": int(len(core_arr)),
        "mad_mm": round(mad, 3),
    }

    # Remove is_core flag from output (front-end uses color based on p10/p90)
    p10 = stats["p10_mm"]
    p90 = stats["p90_mm"]
    for r in results:
        t = r["thickness_mm"]
        if not r["is_core"]:
            r["color"] = "anomaly"
        elif p10 is not None and p90 is not None and (t < p10 or t > p90):
            r["color"] = "warning"
        else:
            r["color"] = "core"
        del r["is_core"]

    if progress_callback:
        progress_callback("thickness_done", 1.0, "壁厚分析完成")

    return {"samples": results, "stats": stats}
