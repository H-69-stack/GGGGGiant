"""PP formline QA API — incremental endpoints on top of existing /api/pp."""
from __future__ import annotations

import io
import logging
from typing import Optional

from flask import Blueprint, jsonify, request, send_file

from core.pp.profile_qa.pipeline import (
    apply_manual_xlsx,
    choose_profile,
    confirm_smooth,
    confirm_straight,
    confirm_wall_and_validate_length,
    delete_key_section_record,
    finalize_straight,
    get_final_profile,
    backfill_section_dims,
    initialize_qa_from_records,
    preview_smooth,
    qa_summary,
    reject_smooth,
    reject_straight,
    rerun_qa,
    resolve_chosen_profile,
    save_key_section_record,
    search_key_sections_in_bundle,
    working_to_wall_thickness_records,
)
from core.pp.process_length import process_length
from core.pp.profile_qa.key_section import records_with_section_sizes
from core.pp.profile_qa.manual_profile_parser import build_xlsx_template_bytes
from core.pp.profile_qa.state import QA_PASS, QaStateError

logger = logging.getLogger(__name__)

pp_qa_bp = Blueprint("pp_qa", __name__)


def _sessions():
    from api.pp import _pp_sessions, _session_lock
    return _pp_sessions, _session_lock


def _load_session(session_id: Optional[str]):
    if not session_id:
        return None, (jsonify({"success": False, "message": "缺少session_id", "code": "QA_NO_SESSION"}), 400)
    sessions, lock = _sessions()
    with lock:
        sess = sessions.get(session_id)
    if not sess or not sess.get("results"):
        return None, (jsonify({"success": False, "message": "分析尚未完成", "code": "QA_SESSION_NOT_READY"}), 400)
    return sess, None


def _sized_records(sess: dict) -> list:
    results = sess.setdefault("results", {})
    if not (results.get("wall_thickness_data") or results.get("centerline_perimeter_data")):
        # 只有 sections_full 的会话（一体化流水线上传入库）在这里补齐，
        # 否则 records 为空 → QA_INVALID_INPUT / empty_profile。
        try:
            from core.pp.section_records import ensure_results_records
            ensure_results_records(results)
        except Exception:  # noqa: BLE001 - 补记录失败按空形线处理
            logger.debug("section records fallback failed", exc_info=True)
    records = records_with_section_sizes(
        results.get("wall_thickness_data") or results.get("centerline_perimeter_data") or [],
        results.get("sections_full"),
    )
    if records:
        results["wall_thickness_data"] = records
        results["centerline_perimeter_data"] = records
    return records


def ensure_qa(sess: dict, *, force: bool = False) -> dict:
    """Attach QA to a session using already-extracted formline records (no STP re-parse)."""
    results = sess.setdefault("results", {})
    if results.get("qa") and not force:
        return results["qa"]
    wt = _sized_records(sess)
    geometry_error = None
    if not wt:
        geometry_error = "形线数据为空"
    qa = initialize_qa_from_records(
        wt,
        centerline_length=results.get("centerline_length") or results.get("center_line", {}).get("total_length"),
        geometry_error=geometry_error,
        profile_source="stp_raw",
        original_filename=sess.get("original_filename") or "",
    )
    results["qa"] = qa
    return qa


def _store(sess: dict, qa: dict) -> dict:
    from core.pp.profile_qa.pipeline import apply_qa_lengths_to_results
    results = sess.setdefault("results", {})
    apply_qa_lengths_to_results(results, qa)
    results["qa"] = qa
    original = sess["results"].get("wall_thickness_data") or []
    try:
        overlay = working_to_wall_thickness_records(qa, original)
        if overlay:
            sess["results"]["wall_thickness_data"] = overlay
            sess["results"]["centerline_perimeter_data"] = overlay
    except Exception:
        logger.debug("QA overlay skipped", exc_info=True)
    sess["results"]["qa_final_ready"] = bool(qa.get("profile_choice") or qa.get("status") == QA_PASS)
    return qa


def _err(exc: Exception, status_http: int = 400):
    if isinstance(exc, QaStateError):
        return jsonify({"success": False, **exc.as_dict()}), 409
    logger.exception("PP QA error")
    return jsonify({
        "success": False,
        "code": "QA_INTERNAL_ERROR",
        "message": "形线质量检查失败，请重试或改用手工 XLSX。",
        "details": [{"reason": str(exc)}],
    }), 500


@pp_qa_bp.route("/qa", methods=["GET"])
def qa_get():
    sess, err = _load_session(request.args.get("id") or request.args.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc, 500)


@pp_qa_bp.route("/qa/rerun", methods=["POST"])
def qa_rerun():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        qa = rerun_qa(qa)
        _store(sess, qa)
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/preview-smooth", methods=["POST"])
def qa_preview_smooth():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        qa = preview_smooth(qa)
        sess["results"]["qa"] = qa
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/confirm-smooth", methods=["POST"])
def qa_confirm_smooth():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        qa = confirm_smooth(qa, confirmed_by=data.get("confirmed_by") or "")
        _store(sess, qa)
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/reject-smooth", methods=["POST"])
def qa_reject_smooth():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        qa = reject_smooth(qa, confirmed_by=data.get("confirmed_by") or "")
        sess["results"]["qa"] = qa
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/confirm-straight", methods=["POST"])
def qa_confirm_straight():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        seg_id = data.get("segment_id")
        if not seg_id:
            return jsonify({"success": False, "code": "QA_MISSING_SEGMENT", "message": "缺少 segment_id"}), 400
        qa = confirm_straight(qa, seg_id, confirmed_by=data.get("confirmed_by") or "")
        sess["results"]["qa"] = qa
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/reject-straight", methods=["POST"])
def qa_reject_straight():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        seg_id = data.get("segment_id")
        if not seg_id:
            return jsonify({"success": False, "code": "QA_MISSING_SEGMENT", "message": "缺少 segment_id"}), 400
        qa = reject_straight(qa, seg_id, confirmed_by=data.get("confirmed_by") or "")
        sess["results"]["qa"] = qa
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/finalize-straight", methods=["POST"])
def qa_finalize_straight():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        qa = finalize_straight(
            qa,
            confirmed_ids=data.get("confirmed_ids"),
            confirmed_by=data.get("confirmed_by") or "",
        )
        _store(sess, qa)
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/xlsx-template", methods=["GET"])
def qa_xlsx_template():
    try:
        payload = build_xlsx_template_bytes()
        return send_file(
            io.BytesIO(payload),
            as_attachment=True,
            download_name="manual_profile_template.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as exc:
        return _err(exc, 500)


@pp_qa_bp.route("/qa/upload-xlsx", methods=["POST"])
def qa_upload_xlsx():
    sess, err = _load_session(request.form.get("session_id"))
    if err:
        return err
    if "file" not in request.files:
        return jsonify({"success": False, "code": "QA_MANUAL_XLSX_INVALID", "message": "未上传文件", "details": []}), 400
    file = request.files["file"]
    try:
        data = file.read()
        qa = ensure_qa(sess)
        clen = sess["results"].get("centerline_length")
        qa = apply_manual_xlsx(
            qa, data, file.filename or "upload.xlsx",
            centerline_length_mm=clen,
            confirmed_by=request.form.get("confirmed_by") or "",
        )
        _store(sess, qa)
        if qa.get("manual_override") and not qa["manual_override"].get("ok"):
            return jsonify({
                "success": False,
                "code": qa.get("code") or "QA_MANUAL_XLSX_INVALID",
                "message": qa.get("message") or "人工形线数据格式错误",
                "details": qa.get("details") or [],
                "qa": qa,
                "summary": qa_summary(qa),
            }), 400
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/confirm-wall-length", methods=["POST"])
def qa_confirm_wall_length():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        small_raw = data.get("small_end_correction_mm")
        if isinstance(small_raw, str) and not small_raw.strip():
            small_raw = None
        qa = confirm_wall_and_validate_length(
            qa,
            data.get("values") or data,
            correction_side=data.get("correction_side"),
            small_end_correction_mm=small_raw,
            confirmed_by=data.get("confirmed_by") or "",
        )
        _store(sess, qa)
        payload = {
            "success": True,
            "qa": qa,
            "summary": qa_summary(qa),
            "length_validation": qa.get("length_validation"),
            "centerline_length": process_length(sess["results"]),
        }
        if (qa.get("length_validation") or {}).get("status") == "ambiguous_end":
            payload["needs_side"] = True
        return jsonify(payload)
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/search-key-section", methods=["POST"])
def qa_search_key_section():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        qa = backfill_section_dims(qa, _sized_records(sess))
        qa = search_key_sections_in_bundle(
            qa,
            data.get("length") if data.get("length") is not None else data.get("target_length"),
            data.get("width") if data.get("width") is not None else data.get("target_width"),
            expected_s_mm=data.get("expected_s") if data.get("expected_s") is not None else data.get("expected_s_mm"),
        )
        sess["results"]["qa"] = qa
        search = qa.get("key_section_search") or {}
        return jsonify({
            "success": True,
            "qa": qa,
            "summary": qa_summary(qa),
            "candidates": search.get("candidates") or [],
            "message": search.get("message") or "",
            "profile_version": search.get("profile_version"),
        })
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/save-key-section", methods=["POST"])
def qa_save_key_section():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        qa = save_key_section_record(
            qa,
            candidate=data.get("candidate"),
            index=data.get("index"),
            save_all=bool(data.get("save_all")),
            expected_s_mm=data.get("expected_s") if data.get("expected_s") is not None else data.get("expected_s_mm"),
        )
        sess["results"]["qa"] = qa
        return jsonify({
            "success": True,
            "qa": qa,
            "summary": qa_summary(qa),
            "records": qa.get("key_section_records") or [],
            "stale_message": qa.get("key_section_stale_message") or "",
        })
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/delete-key-section", methods=["POST"])
def qa_delete_key_section():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        rec_id = data.get("record_id")
        if rec_id is None:
            return jsonify({"success": False, "code": "QA_MISSING_RECORD", "message": "缺少 record_id"}), 400
        qa = ensure_qa(sess)
        qa = delete_key_section_record(qa, rec_id)
        sess["results"]["qa"] = qa
        return jsonify({
            "success": True,
            "qa": qa,
            "summary": qa_summary(qa),
            "records": qa.get("key_section_records") or [],
        })
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/select-profile", methods=["POST"])
def qa_select_profile():
    data = request.get_json(silent=True) or {}
    sess, err = _load_session(data.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        clen = process_length(sess["results"]) or None
        qa = choose_profile(qa, data.get("choice") or "", centerline_length=clen)
        _store(sess, qa)
        return jsonify({"success": True, "qa": qa, "summary": qa_summary(qa)})
    except Exception as exc:
        return _err(exc)


@pp_qa_bp.route("/qa/final-profile", methods=["GET"])
def qa_final_profile():
    sess, err = _load_session(request.args.get("id") or request.args.get("session_id"))
    if err:
        return err
    try:
        qa = ensure_qa(sess)
        payload = get_final_profile(qa)
        return jsonify({"success": True, **payload})
    except Exception as exc:
        return _err(exc)
