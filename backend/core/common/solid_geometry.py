"""Validated CAD solids shared by RFM and TP; preview and STEP use one shape."""
import os
import tempfile

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeRevol
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.TColgp import TColgp_HArray1OfPnt
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax1, gp_Ax2, gp_Circ, gp_Elips


def volume(shape):
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props, 1e-8, False, False)
    return float(props.Mass())


def valid_solid(shape):
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    while exp.More():
        count += 1
        exp.Next()
    if count != 1 or not BRepCheck_Analyzer(shape).IsValid() or volume(shape) <= 1e-7:
        raise ValueError('CAD 实体无效或发生自交，请检查截面、中心线及模具尺寸')
    return shape


def bounds(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    values = box.Get()
    return np.array(values[:3]), np.array(values[3:])


def mesh_from_shape(shape):
    import trimesh
    from core.pp.mesh_converter import shape_to_mesh
    data = shape_to_mesh(shape, tolerance=0.04, angular_tolerance=0.16)
    return trimesh.Trimesh(np.array(data['vertices']).reshape(-1, 3),
                           np.array(data['faces']).reshape(-1, 3), process=True)


def step_bytes(shape):
    valid_solid(shape)
    writer = STEPControl_Writer()
    if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError('CAD STEP 转换失败')
    fd, path = tempfile.mkstemp(suffix='.step')
    os.close(fd)
    try:
        if writer.Write(path) != IFSelect_RetDone:
            raise RuntimeError('CAD STEP 写出失败')
        with open(path, 'rb') as stream:
            return stream.read()
    finally:
        os.remove(path)


def ellipse_wire(center, normal, u, a, b):
    center, normal, u = map(lambda x: np.asarray(x, float), (center, normal, u))
    if b > a:
        a, b = b, a
        u = np.cross(normal, u)
    axes = gp_Ax2(gp_Pnt(*center), gp_Dir(*normal), gp_Dir(*u))
    curve = gp_Circ(axes, float(a)) if abs(a-b) < 1e-9 else gp_Elips(axes, float(a), float(b))
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(curve).Edge()).Wire()


def contour_wire(points):
    pts = np.asarray(points, float)
    arr = TColgp_HArray1OfPnt(1, len(pts))
    for i, p in enumerate(pts):
        arr.SetValue(i+1, gp_Pnt(*p))
    curve = GeomAPI_Interpolate(arr, True, 1e-7)
    curve.Perform()
    if not curve.IsDone():
        raise ValueError('截面曲线拟合失败')
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(curve.Curve()).Edge()).Wire()


def loft(wires):
    if len(wires) < 2:
        raise ValueError('至少需要两个截面')
    builder = BRepOffsetAPI_ThruSections(True, True, 1e-6)
    builder.CheckCompatibility(False)
    for wire in wires:
        builder.AddWire(wire)
    builder.Build()
    if not builder.IsDone():
        raise ValueError('截面放样失败')
    return valid_solid(builder.Shape())


def revolve_profile(z, r):
    from shapely.geometry import Polygon
    points = np.column_stack((z, r))
    points = points[np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-8]]
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area <= 1e-6 or np.min(r) < 0:
        raise ValueError('旋转截面未闭合、发生自交或半径无效')
    wire = BRepBuilderAPI_MakePolygon()
    for axial, radius in points:
        wire.Add(gp_Pnt(float(radius), 0, float(axial)))
    wire.Close()
    face = BRepBuilderAPI_MakeFace(wire.Wire()).Face()
    builder = BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)))
    return valid_solid(builder.Shape())


def cut_mold(cavity, config):
    """Centered XYZ block. Subtract the full outer envelope, never the tube wall."""
    low, high = bounds(cavity)
    extent = high-low
    margin = float(config.get('mold_margin', 15))
    if not np.isfinite(margin) or margin < 1 or margin > 500:
        raise ValueError('模具单侧余量必须在 1–500 mm 之间')
    recommended = np.ceil((extent+2*margin)*10)/10
    size = recommended.copy()
    if not config.get('mold_auto_size', True):
        size = np.array([float(config.get('mold_size_'+axis, 0)) for axis in 'xyz'])
        if not np.all(np.isfinite(size)) or np.any(size < extent+2):
            raise ValueError('模具 XYZ 尺寸必须比腔体包围盒至少大 2 mm（单侧 1 mm）')
    origin = (low+high-size)/2
    block = BRepPrimAPI_MakeBox(gp_Pnt(*origin), *map(float, size)).Shape()
    cutter = BRepAlgoAPI_Cut(block, cavity)
    cutter.Build()
    if not cutter.IsDone():
        raise ValueError('模具反切失败')
    result = valid_solid(cutter.Shape())
    expected = float(np.prod(size))-volume(cavity)
    if abs(volume(result)-expected) > max(0.01, expected*1e-7):
        raise ValueError('反切体积校验失败')
    return result, {'size_xyz_mm': size.tolist(), 'recommended_xyz_mm': recommended.tolist(),
                    'cavity_bounds_xyz_mm': [low.tolist(), high.tolist()],
                    'cavity_volume_mm3': volume(cavity), 'mold_volume_mm3': volume(result),
                    'minimum_margin_mm': float(np.min((size-extent)/2))}
