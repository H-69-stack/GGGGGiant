"""Display-only STEP tessellation using OCP, independent of measurement."""


def shape_to_mesh(shape, tolerance: float = 0.3, angular_tolerance: float = 0.2) -> dict:
    """Absolute chordal deflection in mm and angular deflection in radians."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopoDS import TopoDS
    from OCP.TopLoc import TopLoc_Location
    from OCP.BRep import BRep_Tool
    mesher = BRepMesh_IncrementalMesh(shape, tolerance, False, angular_tolerance, False)
    mesher.Perform()
    if not mesher.IsDone():
        raise ValueError("STP preview meshing failed")
    vertices, faces = [], []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            raise ValueError("STP face has no preview triangulation")
        offset = len(vertices) // 3
        transform = location.Transformation()
        for i in range(1, triangulation.NbNodes() + 1):
            vertices.extend(triangulation.Node(i).Transformed(transform).Coord())
        for i in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(i).Get()
            if face.Orientation() == TopAbs_REVERSED:
                b, c = c, b
            faces.extend([offset + a - 1, offset + b - 1, offset + c - 1])
        exp.Next()
    return {"vertices": vertices, "faces": faces,
            "vertex_count": len(vertices) // 3, "face_count": len(faces) // 3}
