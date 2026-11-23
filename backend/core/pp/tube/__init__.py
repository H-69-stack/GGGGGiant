"""Tube analysis engine — A/B algorithm dispatcher.

STL Algorithm A (rotate-first):
    1. PCA on vertices to find the long axis.
    2. Rotate the model so the long axis aligns with +Z.
    3. Slice with planes parallel to XY at 1 mm step → polygon centroids
       form an initial centerline (in rotated coordinates).
    4. Trim outlier ends, smoothly extrapolate.
    5. Secondary cut: planes perpendicular to the centerline at 1 mm step.
       Record perimeter / wall thickness per section.
    6. Trim outlier ends of secondary results, smoothly extrapolate.
    7. Inverse-rotate everything back to original coordinates.

Algorithm B (no rotation):
    Same as A, but skip step 1–2. Assumes the user already placed the
    model with its principal axis aligned to +Z (or close enough). The
    first slicing therefore happens in the original coordinate frame and
    no inverse rotation is needed.

STEP inputs use native CAD sections via brep_engine, with measured quality
metadata and no STL endpoint flattening. Both formats produce a compatible
output schema, suitable for the
existing front-end & downstream optimization code.
"""

from typing import Callable, Optional

from .stl_engine import analyze_stl_tube
from .stp_engine import analyze_stp_tube


def analyze_tube(
    file_path: str,
    file_type: str,
    algorithm: str = "A",
    *,
    n_sections: int = 30,
    sparse_step_mm: float = 25.0,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    end_treatment: Optional[dict] = None,
):
    """Dispatch to the correct engine.

    Args:
        file_path: absolute path to the uploaded STL/STP file.
        file_type: 'stl' or 'stp'.
        algorithm: 'A' (rotate-first) or 'B' (no rotation).
        n_sections: deprecated; kept for backwards-compatibility.
        sparse_step_mm: arc-length step for the sparse sections used by
            the 3D viewport (default 25 mm).
        progress_callback: optional callable(pct:int, message:str).
    """
    algorithm = (algorithm or "A").upper()
    if algorithm not in ("A", "B"):
        return {"error": f"未知算法: {algorithm}"}

    file_type = (file_type or "").lower()
    if file_type == "stl":
        return analyze_stl_tube(
            file_path,
            algorithm=algorithm,
            sparse_step_mm=sparse_step_mm,
            progress_callback=progress_callback,
        )
    if file_type in ("stp", "step"):
        return analyze_stp_tube(
            file_path,
            algorithm=algorithm,
            sparse_step_mm=sparse_step_mm,
            progress_callback=progress_callback,
            end_treatment=end_treatment,
        )
    return {"error": f"不支持的文件类型: {file_type}"}


__all__ = ["analyze_tube", "analyze_stl_tube", "analyze_stp_tube"]
