"""Regression tests for STEP export.

Guards the bugs fixed in the 2.0.1 STEP rework:
  * mesh_utils.py (the hand-written POLYLOOP exporter that no CAD app could
    open) is gone — RFM/TP now route through OCP and never silently serve a
    malformed STEP (failure raises ``RuntimeError``).
  * ``trimesh_to_step_bytes`` / ``rings_to_step_bspline_bytes`` round-trip
    through the OCP ``STEPControl_Reader`` (i.e. the files actually open in
    SolidWorks / Fusion 360 / FreeCAD / CATIA).

The OCP round-trips skip on hosts without ``cadquery-ocp`` (e.g. a bare dev
machine); they run in the packaged environment where OCP is bundled.
"""
import os
import sys

import numpy as np
import pytest
import trimesh

# Make `from core...` / `from config...` resolve when pytest runs from the
# repo root or from backend/.
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.tp.mold_generator import TPMoldProcessor  # noqa: E402
from core.rfm.processors import BellingProcessor  # noqa: E402
from core.common.cad_io import (  # noqa: E402
    trimesh_to_step_bytes,
    rings_to_step_bspline_bytes,
)


def _make_tp():
    tp = TPMoldProcessor()
    tp.set_profile_data([
        {"中心线长度(mm)": 0.0, "外径周长(mm)": 94.0},
        {"中心线长度(mm)": 200.0, "外径周长(mm)": 125.0},
    ])
    # T_D/T_d are bore diameters, not the fixed 125/95 mm outside diameters.
    tp.set_tp_params(T_L=200.0, T_D=125.0/np.pi, T_d=94.0/np.pi)
    return tp


def _make_belling():
    bp = BellingProcessor()
    bp.belling_length = 74.0
    bp.wall_thickness = 2.0
    bp.set_session_data(
        None,
        [
            {"中心线长度(mm)": 0.0, "外径周长(mm)": 62.8},
            {"中心线长度(mm)": 628.0, "外径周长(mm)": 62.8},
        ],
    )
    return bp


def test_tp_revolution_rings_aligned():
    """_build_revolution_mesh stashes angularly-aligned rings for NURBS."""
    tp = _make_tp()
    tp.generate_model()
    rings = tp._rings_3d
    assert rings and len(rings) >= 2
    n = np.asarray(rings[0]).shape[0]
    # every ring must be a full (n, 3) circle — index k aligned by polar angle
    assert all(np.asarray(r).shape == (n, 3) for r in rings)


def test_belling_revolution_rings_aligned():
    """Belling mandrel + cavity stash rings for the NURBS path."""
    if not hasattr(trimesh.Trimesh, "remove_duplicate_faces"):
        pytest.skip("trimesh pinned to 4.6.3 in production; dev 4.12.x removed this API")
    bp = _make_belling()
    bp.generate_model()
    bp.generate_cavity_model()
    for rings in (bp._mandrel_rings_3d, bp._cavity_rings_3d):
        assert rings and len(rings) >= 2
        n = np.asarray(rings[0]).shape[0]
        assert all(np.asarray(r).shape == (n, 3) for r in rings)


def test_step_failure_surfaces_not_silent():
    """No OCP -> RuntimeError; OCP -> valid STEP header. Never silent None."""
    tp = _make_tp()
    tp.generate_model()
    try:
        b = tp.get_step_bytes()
        assert b and b.startswith(b"ISO-10303-21")
    except RuntimeError:
        pass  # acceptable in envs without OCP — the point is it didn't return None


def _ocp_reader():
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    return STEPControl_Reader, IFSelect_RetDone


def test_trimesh_step_roundtrip(tmp_path):
    """Triangular B-rep STEP round-trips through the OCP reader."""
    mesh = trimesh.creation.box((10.0, 20.0, 30.0))
    step = trimesh_to_step_bytes(mesh)
    if step is None:
        pytest.skip("OCP unavailable in this environment")
    p = tmp_path / "box.step"
    p.write_bytes(step)
    STEPControl_Reader, IFSelect_RetDone = _ocp_reader()
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(p)) == IFSelect_RetDone
    reader.TransferRoots()


def test_nurbs_step_roundtrip(tmp_path):
    """NURBS STEP from revolve rings round-trips through the OCP reader."""
    n = 48
    theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rings = [
        np.column_stack([np.cos(theta) * 10, np.sin(theta) * 10, np.full(n, 0.0)]),
        np.column_stack([np.cos(theta) * 20, np.sin(theta) * 20, np.full(n, 50.0)]),
    ]
    step = rings_to_step_bspline_bytes(rings, is_solid=True)
    if step is None:
        pytest.skip("OCP unavailable in this environment")
    p = tmp_path / "loft.step"
    p.write_bytes(step)
    STEPControl_Reader, IFSelect_RetDone = _ocp_reader()
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(p)) == IFSelect_RetDone
    reader.TransferRoots()


def test_nurbs_hollow_shell_roundtrip(tmp_path):
    """is_solid=False NURBS (TP-style hollow mold) round-trips too."""
    n = 48
    theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rings = [
        np.column_stack([np.cos(theta) * r, np.sin(theta) * r, np.full(n, z)])
        for z, r in [(0.0, 47.5), (50.0, 50.0), (100.0, 55.0), (150.0, 60.0)]
    ]
    step = rings_to_step_bspline_bytes(rings, is_solid=False)
    if step is None:
        pytest.skip("OCP unavailable in this environment")
    p = tmp_path / "hollow.step"
    p.write_bytes(step)
    STEPControl_Reader, IFSelect_RetDone = _ocp_reader()
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(p)) == IFSelect_RetDone
    reader.TransferRoots()
