"""DEV-only PP QA shortcuts. Registered only when GTAPP_DEV_TOOLS=1 and not release."""
from __future__ import annotations

import logging
import os
import time

from flask import Blueprint, jsonify, request

from core.pp.dev_cache import (
    cache_status,
    fingerprint_matches,
    is_dev_tools_enabled,
    list_fixtures,
    load_cache,
    persist_from_session,
    resolve_fixture_name,
    session_dict_from_cache,
)
from core.pp.profile_qa.pipeline import initialize_qa_from_records, qa_summary
from core.pp.tube import analyze_tube

logger = logging.getLogger(__name__)

pp_dev_bp = Blueprint("pp_dev", __name__)


def _deny():
    return jsonify({"success": False, "message": "Not Found"}), 404


def _sessions():
    from api.pp import _pp_sessions, _session_lock
    return _pp_sessions, _session_lock


def hydrate_dev_session() -> None:
    if not is_dev_tools_enabled():
        return
    data = load_cache()
    if not data or not data.get("session_id"):
        return
    sessions, lock = _sessions()
    sid = data["session_id"]
    with lock:
        if sid not in sessions:
            sessions[sid] = session_dict_from_cache(data)
            logger.info("DEV STP session restored: %s (%s)", sid, data.get("filename"))


def _ensure_session_from_cache():
    data = load_cache(with_geometry=False)
    if not data:
        return None, None, (jsonify({
            "success": False, "code": "DEV_NO_CURRENT_STP",
            "message": "没有当前 STP。请先上传或加载测试样本。",
        }), 400)
    sessions, lock = _sessions()
    sid = data["session_id"]
    with lock:
        sess = sessions.get(sid)
        if not sess:
            data = load_cache()
            if not data:
                return None, None, (jsonify({'success': False, 'message': '几何缓存不完整，请重新读取 STP。'}), 400)
            sess = session_dict_from_cache(data)
            sessions[sid] = sess
    return sid, sess, None


def _compact_analysis_results(analysis: dict, filename: str, algorithm: str, sparse_step_mm: float, qa_t0: float):
    from core.pp.if_bended import is_tube_bended, calculate_bend_angle
    import numpy as np

    cl_pts = analysis.get("center_line_points") or []
    is_bended = False
    bend_angle = 0.0
    if cl_pts and len(cl_pts) > 10:
        pts_arr = np.array(cl_pts)
        is_bended = is_tube_bended(pts_arr, angle_threshold=2.0)
        bend_angle = calculate_bend_angle(pts_arr)

    from core.pp.profile_qa.key_section import xy_from_section_row

    wt = []
    sections_full = []
    for sf in analysis.get("sections_full") or []:
        size = xy_from_section_row(sf)
        # This session can be saved and used by RFM. Retain the native contours,
        # local bases and measurement status, not only its formline summary.
        compact = dict(sf)
        row = {
            "中心线长度(mm)": sf.get("cumulative_length", 0),
            "外径周长(mm)": sf.get("perimeter", 0),
            "壁厚(mm)": sf.get("wall_thickness"),
        }
        if sf.get('position') is not None:
            row.update(zip(('x', 'y', 'z'), sf['position']))
        row['measurement_status'] = sf.get('measurement_status', 'measured')
        if size:
            compact["x_length"] = round(float(size[0]), 4)
            compact["y_length"] = round(float(size[1]), 4)
            row["x_length"] = compact["x_length"]
            row["y_length"] = compact["y_length"]
        sections_full.append(compact)
        wt.append(row)
    qa = initialize_qa_from_records(
        wt,
        centerline_length=analysis.get("center_line_length"),
        profile_source="stp_raw",
        original_filename=filename,
    )
    qa_ms = (time.perf_counter() - qa_t0) * 1000.0
    results = {
        "algorithm": analysis.get("algorithm", algorithm),
        "sparse_step_mm": sparse_step_mm,
        "center_line": {
            "points": cl_pts,
            "total_length": analysis.get("center_line_length", 0),
        },
        "sections": analysis.get("sections") or [],
        "sections_full": sections_full,
        "thickness_stats": analysis.get("wall_thickness_stats"),
        "analysis_quality": analysis.get("analysis_quality"),
        "centerline_length": analysis.get("center_line_length", 0),
        "actual_z_length": analysis.get("actual_z_length", analysis.get("center_line_length", 0)),
        "centerline_endpoint_length": analysis.get(
            "centerline_endpoint_length",
            analysis.get("actual_z_length", analysis.get("center_line_length", 0)),
        ),
        "volume": analysis.get("volume", 0),
        "is_bended": is_bended,
        "bend_angle": bend_angle,
        "wall_thickness_data": wt,
        "centerline_perimeter_data": wt,
        "qa": qa,
        "mesh_source": "generated",
        "mesh_data": None,
        "from_database": False,
    }
    return results, qa_ms


def _put_session(session_id: str, file_path: str, filename: str, algorithm: str, sparse: float, results: dict):
    sessions, lock = _sessions()
    with lock:
        sessions[session_id] = {
            "file_path": file_path,
            "file_type": "stp",
            "algorithm": algorithm,
            "sparse_step_mm": sparse,
            "original_filename": filename,
            "progress": {"pct": 100, "message": "分析完成", "done": True, "error": None},
            "results": results,
            "ready_for_tab2": False,
        }
        sess = sessions[session_id]
    return sess


@pp_dev_bp.before_request
def _dev_only():
    if not is_dev_tools_enabled():
        return _deny()
    return None


@pp_dev_bp.route("/status", methods=["GET"])
def dev_status():
    return jsonify({"success": True, **cache_status(), "fixtures": list_fixtures()})


@pp_dev_bp.route("/fixtures", methods=["GET"])
def dev_fixtures():
    return jsonify({"success": True, "fixtures": list_fixtures()})


@pp_dev_bp.route("/rerun-qa", methods=["POST"])
def dev_rerun_qa():
    """Re-run QA from cached raw wall-thickness records. Does not parse STP."""
    sid, sess, err = _ensure_session_from_cache()
    if err:
        return err
    results = sess.get("results") or {}
    from core.pp.profile_qa.key_section import records_with_section_sizes
    wt = records_with_section_sizes(
        results.get("wall_thickness_data") or results.get("centerline_perimeter_data") or [],
        results.get("sections_full"),
    )
    if not wt:
        return jsonify({
            "success": False, "code": "DEV_NO_RAW_PROFILE",
            "message": "缓存中没有 raw profile，请先「重新读取 STP + QA」。",
        }), 400
    results["wall_thickness_data"] = wt
    results["centerline_perimeter_data"] = wt
    t0 = time.perf_counter()
    qa = initialize_qa_from_records(
        wt,
        centerline_length=results.get("centerline_length"),
        profile_source="stp_raw",
        original_filename=sess.get("original_filename") or "",
    )
    qa_ms = (time.perf_counter() - t0) * 1000.0
    results["qa"] = qa
    persist_from_session(sid, sess, qa_elapsed_ms=qa_ms, increment_qa=True)
    status = cache_status()
    return jsonify({
        "success": True,
        "mode": "qa_only",
        "session_id": sid,
        "qa": qa,
        "summary": qa_summary(qa),
        "stp_read": False,
        "elapsed_ms": qa_ms,
        **status,
    })


@pp_dev_bp.route("/reread-stp", methods=["POST"])
def dev_reread_stp():
    """Re-parse the current STP file, then QA."""
    data = load_cache(with_geometry=False)
    if not data:
        return jsonify({
            "success": False, "code": "DEV_NO_CURRENT_STP",
            "message": "没有当前 STP。请先上传或加载测试样本。",
        }), 400
    path = data.get("file_path") or ""
    if not path or not os.path.isfile(path):
        return jsonify({
            "success": False, "code": "DEV_STP_MISSING",
            "message": "当前 STP 文件已不存在，请重新上传或加载 fixture。",
        }), 400
    if not fingerprint_matches(path, data):
        logger.info("DEV STP fingerprint changed; re-reading anyway: %s", path)
    return _analyze_and_store(path, data.get("filename") or os.path.basename(path),
                              data.get("algorithm") or "A",
                              float(data.get("sparse_step_mm") or 25.0),
                              session_id=data.get("session_id"),
                              end_treatment=(data.get('analysis_quality') or {}).get('end_treatment', {}).get('options'))


@pp_dev_bp.route("/load-fixture", methods=["POST"])
def dev_load_fixture():
    body = request.get_json(silent=True) or {}
    path = resolve_fixture_name(body.get("filename") or "")
    if not path:
        return jsonify({
            "success": False, "code": "DEV_BAD_FIXTURE",
            "message": "只允许加载 tests/fixtures/stp 下的 .stp/.step 文件名。",
        }), 400
    algorithm = (body.get("algorithm") or "A").upper()
    if algorithm not in ("A", "B"):
        algorithm = "A"
    try:
        sparse = float(body.get("sparse_step_mm") or 25.0)
    except (TypeError, ValueError):
        sparse = 25.0
    sid = os.path.splitext(os.path.basename(path))[0]
    return _analyze_and_store(path, os.path.basename(path), algorithm, sparse, session_id=sid,
                              end_treatment=body.get('end_treatment'))


def _analyze_and_store(path: str, filename: str, algorithm: str, sparse: float, session_id: str, end_treatment=None):
    t0 = time.perf_counter()
    analysis = analyze_tube(path, file_type="stp", algorithm=algorithm, sparse_step_mm=sparse, end_treatment=end_treatment)
    stp_s = time.perf_counter() - t0
    if "error" in analysis:
        return jsonify({"success": False, "code": "DEV_STP_READ_FAILED", "message": analysis["error"]}), 400
    qa_t0 = time.perf_counter()
    results, qa_ms = _compact_analysis_results(analysis, filename, algorithm, sparse, qa_t0)
    sess = _put_session(session_id, path, filename, algorithm, sparse, results)
    persist_from_session(
        session_id, sess,
        stp_elapsed_s=stp_s, qa_elapsed_ms=qa_ms,
        increment_stp=True, increment_qa=True,
    )
    status = cache_status()
    return jsonify({
        "success": True,
        "mode": "stp_and_qa",
        "session_id": session_id,
        "qa": results.get("qa"),
        "summary": qa_summary(results.get("qa")),
        "stp_read": True,
        "stp_read_s": stp_s,
        "elapsed_ms": qa_ms,
        **status,
    })
