"""Pinned Giant results that must not be regenerated from raw C1 rings.

2052-2 is the one case where the raw C1 shape extrapolation produces a
flattened wedge at the 20 mm start extension.  Its official C1 file is the
repaired-ring integration, so a normal Giant rebuild must not overwrite it
with the raw C1 output again.
"""
from __future__ import annotations

from pathlib import Path

from .geometry import BuildResult, _parting_edges, bbox_size, read_step, shape_bbox

ROOT = Path(__file__).resolve().parent.parent
PINNED = {
    "24TK_2052-2": ROOT / "data" / "giant_24TK_2052-2_c1.step",
}
PINNED_SECTIONS = {"24TK_2052-2": 120}


def pinned_giant_result(cache_id: str) -> BuildResult | None:
    path = PINNED.get(cache_id)
    if path is None or not path.exists():
        return None
    solid = read_step(path)
    extent = float(max(bbox_size(shape_bbox(solid))))
    parting = _parting_edges(solid, extent)
    return BuildResult(
        solid=solid,
        sections=[None] * PINNED_SECTIONS.get(cache_id, 0),  # type: ignore[list-item]
        top_face=None,
        bottom_face=None,
        cap_faces=[],
        parting_edges=parting,
        build_seconds=0.0,
        repair_mode="GIANT_REPAIRED",
        repair_messages=[
            "2052-2 使用 Giant 修复后的截面环整合版，跳过原始 C1 外推",
            "分型线已改到 Y+/Y- 两侧",
        ],
        parting_axis="y",
    )


def is_pinned(cache_id: str) -> bool:
    return pinned_giant_result(cache_id) is not None
