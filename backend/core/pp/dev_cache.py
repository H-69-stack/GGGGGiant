"""DEV-only persistence for the current STP session across Flask reloads.

Never imported by production request paths except through api.pp_dev, which
is not registered when tools are off. The JSON lives under DATA_DIR/dev/
(already gitignored via backend/data/).
"""
from __future__ import annotations

import json
import gzip
import logging
import os
import time
import uuid
from typing import Any, Optional

from config import BASE_DIR, DATA_DIR
from core.common.build_info import is_release

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(DATA_DIR, "dev")
CACHE_PATH = os.path.join(CACHE_DIR, "current_stp.json")
FIXTURE_DIR = os.path.abspath(os.path.join(BASE_DIR, "tests", "fixtures", "stp"))


def is_dev_tools_enabled() -> bool:
    if is_release():
        return False
    return os.environ.get("GTAPP_DEV_TOOLS", "0") == "1"


def file_fingerprint(path: str) -> dict:
    try:
        st = os.stat(path)
        return {"size": int(st.st_size), "mtime": float(st.st_mtime)}
    except OSError:
        return {"size": None, "mtime": None}


def fingerprint_matches(path: str, cached: Optional[dict]) -> bool:
    if not path or not cached:
        return False
    now = file_fingerprint(path)
    return now.get("size") == cached.get("file_size") and now.get("mtime") == cached.get("file_mtime")


def resolve_fixture_name(name: str) -> Optional[str]:
    """Return an absolute path inside FIXTURE_DIR, or None if unsafe/missing."""
    if not name or not isinstance(name, str):
        return None
    raw = name.strip()
    if not raw or raw != os.path.basename(raw):
        return None
    if raw in (".", "..") or "/" in raw or "\\" in raw:
        return None
    if not raw.lower().endswith((".stp", ".step")):
        return None
    candidate = os.path.abspath(os.path.join(FIXTURE_DIR, raw))
    try:
        common = os.path.commonpath([FIXTURE_DIR, candidate])
    except ValueError:
        return None
    if common != FIXTURE_DIR:
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def list_fixtures() -> list:
    if not os.path.isdir(FIXTURE_DIR):
        return []
    names = []
    for name in sorted(os.listdir(FIXTURE_DIR)):
        if resolve_fixture_name(name):
            names.append(name)
    return names


def _compact_sections(sections) -> list:
    from core.pp.profile_qa.key_section import xy_from_section_row

    compact = []
    for row in sections or []:
        # Native section geometry is required after a reload by shaping and
        # inner-cavity generation. A size-only cache loses that information.
        item = dict(row)
        size = xy_from_section_row(row)
        if size:
            item["x_length"] = round(float(size[0]), 4)
            item["y_length"] = round(float(size[1]), 4)
        compact.append(item)
    return compact


def _geometry_path(name):
    if not isinstance(name, str) or name != os.path.basename(name) or not name.startswith('sections-') or not name.endswith('.json.gz'):
        raise ValueError('Invalid DEV geometry cache name')
    return os.path.join(CACHE_DIR, name)


def load_cache(*, with_geometry=True) -> Optional[dict]:
    if not os.path.isfile(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get('geometry_file') and with_geometry:
            with gzip.open(_geometry_path(data['geometry_file']), 'rt', encoding='utf-8') as f:
                data['sections_full'] = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, EOFError) as exc:
        logger.warning("DEV STP cache unreadable: %s", exc)
        return None


def save_cache(payload: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=_json_default)
    os.replace(tmp, CACHE_PATH)


def _json_default(value: Any):
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def persist_from_session(
    session_id: str,
    sess: Optional[dict],
    *,
    stp_elapsed_s: Optional[float] = None,
    qa_elapsed_ms: Optional[float] = None,
    increment_stp: bool = False,
    increment_qa: bool = False,
) -> Optional[dict]:
    if not is_dev_tools_enabled() or not sess:
        return None
    results = sess.get("results") or {}
    path = sess.get("file_path") or ""
    fp = file_fingerprint(path) if path else {}
    prev = load_cache(with_geometry=False) or {}
    same = (
        prev.get("file_path") == path
        and prev.get("filename") == (sess.get("original_filename") or "")
    )
    if same:
        stp_count = int(prev.get("stp_read_count") or 0)
        qa_count = int(prev.get("qa_count") or 0)
        if increment_stp:
            stp_count += 1
        if increment_qa:
            qa_count += 1
    else:
        stp_count = 1 if increment_stp else 0
        qa_count = 1 if increment_qa else 0
    # Native contours are large and immutable during QA-only runs. Write them
    # once per STP read, then update only the small metadata/profile JSON.
    # UUID sidecars ensure an interrupted write cannot pair new geometry with
    # old metadata. Legacy all-in-one JSON remains readable.
    geometry_file = prev.get('geometry_file') if same and not increment_stp and fingerprint_matches(path, prev) else None
    if not geometry_file or not os.path.isfile(_geometry_path(geometry_file)):
        os.makedirs(CACHE_DIR, exist_ok=True)
        geometry_file = 'sections-'+uuid.uuid4().hex+'.json.gz'
        geometry_path = _geometry_path(geometry_file)
        with gzip.open(geometry_path+'.tmp', 'wt', encoding='utf-8', compresslevel=1) as stream:
            json.dump(_compact_sections(results.get('sections_full')), stream, ensure_ascii=False, default=_json_default)
        os.replace(geometry_path+'.tmp', geometry_path)
    payload = {
        "session_id": session_id,
        "filename": sess.get("original_filename") or "",
        "file_path": path,
        "file_size": fp.get("size"),
        "file_mtime": fp.get("mtime"),
        "algorithm": sess.get("algorithm") or (results.get("algorithm") or "A"),
        "sparse_step_mm": sess.get("sparse_step_mm") or results.get("sparse_step_mm") or 25.0,
        "centerline_length": results.get("centerline_length") or 0,
        "centerline_endpoint_length": results.get("centerline_endpoint_length"),
        "actual_z_length": results.get("actual_z_length"),
        "analysis_quality": results.get("analysis_quality"),
        "thickness_stats": results.get("thickness_stats"),
        "volume": results.get("volume", 0),
        "is_bended": results.get("is_bended"),
        "bend_angle": results.get("bend_angle"),
        "wall_thickness_data": results.get("wall_thickness_data") or [],
        "sections": results.get("sections") or [],
        "geometry_file": geometry_file,
        "center_line": results.get("center_line") or {"points": [], "total_length": 0},
        "qa": results.get("qa"),
        "stp_read_s": stp_elapsed_s if stp_elapsed_s is not None else prev.get("stp_read_s"),
        "qa_ms": qa_elapsed_ms if qa_elapsed_ms is not None else prev.get("qa_ms"),
        "stp_read_count": stp_count,
        "qa_count": qa_count,
        "updated_at": time.time(),
    }
    save_cache(payload)
    return payload


def cache_status(payload: Optional[dict] = None) -> dict:
    data = payload if payload is not None else load_cache(with_geometry=False)
    if not data:
        return {
            "enabled": True,
            "present": False,
            "cache_valid": False,
        }
    path = data.get("file_path") or ""
    valid = bool(path) and os.path.isfile(path) and fingerprint_matches(path, data)
    qa = data.get("qa") or {}
    summary = qa if isinstance(qa, dict) else {}
    return {
        "enabled": True,
        "present": True,
        "cache_valid": valid,
        "session_id": data.get("session_id"),
        "filename": data.get("filename"),
        "file_path": path,
        "centerline_mm": data.get("centerline_length"),
        "samples": len(data.get("wall_thickness_data") or []),
        "profile_source": summary.get("profile_source"),
        "qa_status": summary.get("status"),
        "anomaly_point_ratio": summary.get("anomaly_point_ratio"),
        "anomaly_length_ratio": summary.get("anomaly_length_ratio"),
        "suspect_count": len(summary.get("suspect_segments") or []),
        "straight_count": len(summary.get("straight_segments") or []),
        "stp_read_s": data.get("stp_read_s"),
        "qa_ms": data.get("qa_ms"),
        "stp_read_count": data.get("stp_read_count") or 0,
        "qa_count": data.get("qa_count") or 0,
    }


def session_dict_from_cache(data: dict) -> dict:
    results = {
        "algorithm": data.get("algorithm") or "A",
        "sparse_step_mm": data.get("sparse_step_mm") or 25.0,
        "center_line": data.get("center_line") or {"points": [], "total_length": 0},
        "sections": data.get("sections") or [],
        "sections_full": data.get("sections_full") or [],
        "centerline_length": data.get("centerline_length") or 0,
        "actual_z_length": data.get("actual_z_length", data.get("centerline_length")) or 0,
        "centerline_endpoint_length": data.get("centerline_endpoint_length", data.get("centerline_length")) or 0,
        "analysis_quality": data.get("analysis_quality"),
        "thickness_stats": data.get("thickness_stats"),
        "volume": data.get("volume", 0),
        "is_bended": data.get("is_bended"),
        "bend_angle": data.get("bend_angle") or 0,
        "wall_thickness_data": data.get("wall_thickness_data") or [],
        "centerline_perimeter_data": data.get("wall_thickness_data") or [],
        "qa": data.get("qa"),
        "mesh_source": "generated",
        "mesh_data": None,
        "from_database": False,
    }
    return {
        "file_path": data.get("file_path"),
        "file_type": "stp",
        "algorithm": data.get("algorithm") or "A",
        "sparse_step_mm": data.get("sparse_step_mm") or 25.0,
        "original_filename": data.get("filename") or "",
        "progress": {"pct": 100, "message": "分析完成", "done": True, "error": None},
        "results": results,
        "ready_for_tab2": False,
        "from_dev_cache": True,
    }
