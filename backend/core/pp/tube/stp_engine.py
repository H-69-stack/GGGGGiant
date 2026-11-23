"""Native STEP measurements; tessellation helpers are for display/export only."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


def _progress(cb, pct: int, msg: str) -> None:
    if cb is None:
        return
    try:
        cb(int(pct), str(msg))
    except Exception:
        pass


def _tessellate_to_stl_via_cadquery(stp_path: str, stl_path: str, tolerance: float = 0.3) -> bool:
    try:
        from cadquery import Shape
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
    except Exception as exc:
        logger.debug("cadquery 不可用: %s", exc)
        return False

    reader = STEPControl_Reader()
    if reader.ReadFile(stp_path) != IFSelect_RetDone:
        return False
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        return False
    cq_shape = Shape(shape)
    verts, tris = cq_shape.tessellate(tolerance, 0.2)

    # Write a binary STL using trimesh
    try:
        import trimesh
        vertices = np.array([[v.x, v.y, v.z] for v in verts], dtype=np.float32)
        faces = np.array(tris, dtype=np.int32)
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        mesh.export(stl_path, file_type="stl")
        return True
    except Exception as exc:
        logger.warning("trimesh 导出 STL 失败: %s", exc)
        return False


def _tessellate_to_stl_via_ocp(stp_path: str, stl_path: str, tolerance: float = 0.3) -> bool:
    try:
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.StlAPI import StlAPI_Writer
    except Exception as exc:
        logger.debug("OCP 不可用: %s", exc)
        return False

    reader = STEPControl_Reader()
    if reader.ReadFile(stp_path) != IFSelect_RetDone:
        return False
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        return False

    mesher = BRepMesh_IncrementalMesh(shape, tolerance, False, 0.2, True)
    mesher.Perform()
    if not mesher.IsDone():
        return False

    writer = StlAPI_Writer()
    # cadquery-ocp 7.7+ exposes ASCIIMode as a property; older bindings used SetASCIIMode().
    if hasattr(writer, "SetASCIIMode"):
        writer.SetASCIIMode(False)
    elif hasattr(writer, "ASCIIMode"):
        writer.ASCIIMode = False
    return bool(writer.Write(shape, stl_path))


def _tessellate_step(stp_path: str, tolerance: float = 0.3) -> Optional[str]:
    """Convert an STP file into a temporary binary STL. Returns the STL path."""
    out_fd, out_path = tempfile.mkstemp(suffix=".stl")
    os.close(out_fd)
    try:
        if _tessellate_to_stl_via_cadquery(stp_path, out_path, tolerance=tolerance):
            return out_path
        if _tessellate_to_stl_via_ocp(stp_path, out_path, tolerance=tolerance):
            return out_path
    except Exception as exc:
        logger.warning("STP→STL 失败: %s", exc)
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass
    return None


def analyze_stp_tube(
    file_path: str,
    *,
    algorithm: str = "A",
    sparse_step_mm: float = 25.0,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    tessellation_tolerance: float = 0.3,
    end_treatment: Optional[dict] = None,
) -> dict:
    # Keep tessellation_tolerance in the public signature for compatibility.
    # It must not change CAD measurement results.
    try:
        from .brep_engine import analyze_brep_tube
        return analyze_brep_tube(
            file_path,
            algorithm=algorithm,
            sparse_step_mm=sparse_step_mm,
            progress_callback=progress_callback,
            end_treatment=end_treatment,
        )
    except Exception as exc:
        logger.exception("STP 原生截面分析失败: %s", file_path)
        return {"error": f"STP 原生截面分析失败：{exc}"}
