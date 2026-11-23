"""QA workflow orchestrator. Pure Python — no Flask / file-upload coupling.

STP geometry is parsed once by the existing tube engine. This module only
consumes the already-extracted (s, C) series.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .config import (
    ALGORITHM_VERSION,
    LENGTH_COMPARE_TOLERANCE_MM,
    QA_QUANTITY,
    QA_STORAGE_QUANTITY,
    SECTION_MATCH_TOLERANCE_MM,
    WALL_LENGTH_KEYS,
    WALL_THICKNESS_KEYS,
)
from .key_section import (
    build_saved_record,
    extract_section_dims,
    mark_records_stale,
    next_record_id,
    profile_signature,
    search_key_sections,
    section_dims_usable,
)
from .length_correction import (
    apply_split_length_correction_to_sections,
    correction_args_from_validation,
    validate_profile_length,
)
from .manual_profile_parser import parse_manual_xlsx
from .models import ProfileArrays, empty_qa_bundle
from .monotonic_validator import validate_monotonic_profile
from .profile_smoothing import preview_local_smooth
from .profile_validation import records_to_arrays, validate_profile_input
from .wall_thickness_confirm import calculate_drawing_length, validate_wall_thickness
from .state import (
    ACTION_CONFIRM_SMOOTH,
    ACTION_CONFIRM_STRAIGHT,
    ACTION_CONFIRM_WALL_LENGTH,
    ACTION_DELETE_KEY_SECTION,
    ACTION_FINALIZE_STRAIGHT,
    ACTION_GET_FINAL,
    ACTION_PREVIEW_SMOOTH,
    ACTION_REJECT_SMOOTH,
    ACTION_REJECT_STRAIGHT,
    ACTION_RERUN,
    ACTION_SAVE_KEY_SECTION,
    ACTION_SEARCH_KEY_SECTION,
    ACTION_SELECT_PROFILE,
    ACTION_UPLOAD_XLSX,
    QA_INVALID_INPUT,
    QA_LOCAL_ANOMALY,
    QA_MANUAL_DATA_ACCEPTED,
    QA_MANUAL_DATA_INVALID,
    QA_MANUAL_REQUIRED,
    QA_MONOTONIC_PASS,
    QA_PASS,
    QA_RECHECK_FAILED,
    QA_SMOOTHING_APPLIED,
    QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION,
    QaStateError,
    require_action,
)
from .straight_segment_detector import detect_straight_segments


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _profile_to_dict(profile: Optional[ProfileArrays]) -> Optional[dict]:
    return None if profile is None else profile.to_dict()


def _dict_to_profile(payload: Optional[dict], default_source: str) -> Optional[ProfileArrays]:
    if not payload:
        return None
    s = np.asarray(payload.get("s_mm") or [], dtype=float)
    c = np.asarray(payload.get("outer_circumference_mm") or [], dtype=float)
    d = payload.get("outer_diameter_mm")
    if d is None or len(d) != len(s):
        d = c / np.pi if c.size else np.asarray([], dtype=float)
    else:
        d = np.asarray(d, dtype=float)
    return ProfileArrays(s=s, circumference=c, diameter=d, source=payload.get("source") or default_source)


def _audit(bundle: dict, event: str, **extra) -> None:
    bundle.setdefault("audit", []).append({"at": _now(), "event": event, **extra})


def _params_snapshot() -> dict:
    from . import config
    keys = [
        "ALGORITHM_VERSION",
        "STRAIGHT_TOLERANCE_MM",
        "STRAIGHT_MAX_SLOPE",
        "MIN_STRAIGHT_LENGTH_MM",
        "STRAIGHT_GROW_WINDOW_MM",
        "MONOTONIC_NOISE_TOLERANCE_MM",
        "ANOMALY_RESIDUAL_K",
        "ANOMALY_RESIDUAL_ABS_MIN_MM",
        "MAX_LOCAL_ANOMALY_POINT_RATIO",
        "MAX_LOCAL_ANOMALY_LENGTH_RATIO",
        "MIN_ANOMALY_SEGMENT_LENGTH_MM",
        "BUMP_RETURN_EPS_MM",
        "BUMP_PEAK_PROMINENCE_MM",
        "LOCAL_RISE_MIN_MM",
        "LOCAL_RISE_MIN_LENGTH_MM",
        "SMOOTHING_METHOD",
        "SMOOTHING_TREND_WINDOW_MM",
        "SMOOTHING_MAX_EXPAND_MM",
        "ROLLING_MEDIAN_WINDOW",
        "MAX_LOCAL_ANOMALY_SEGMENTS",
        "TREND_MAD_MAX_MM",
        "QA_QUANTITY",
        "LENGTH_COMPARE_TOLERANCE_MM",
        "SECTION_MATCH_TOLERANCE_MM",
        "SMALL_END_SIZE_DELTA_MM",
        "SMALL_END_WINDOW_MM",
        "LENGTH_EXTEND_TREND_WINDOW_MM",
    ]
    return {k: getattr(config, k) for k in keys}


def profile_is_nonempty(profile: Optional[dict]) -> bool:
    """True when a formline has at least two samples and a non-zero circumference."""
    if not isinstance(profile, dict):
        return False
    s = profile.get("s_mm") or []
    c = profile.get("outer_circumference_mm") or []
    d = profile.get("outer_diameter_mm") or []
    n = len(s)
    if n < 2:
        return False
    if len(c) == n:
        vals = c
    elif len(d) == n:
        vals = [(float(v or 0) * np.pi) for v in d]
    else:
        return False
    return any(abs(float(v or 0)) > 1e-9 for v in vals)


def profile_point_count(profile: Optional[dict]) -> int:
    if not isinstance(profile, dict):
        return 0
    return int(len(profile.get("s_mm") or []))


def confirmed_straight_length_mm(bundle: dict) -> float:
    total = 0.0
    for seg in bundle.get("straight_segments") or []:
        if seg.get("status") == "confirmed":
            try:
                total += float(seg.get("length") or 0)
            except (TypeError, ValueError):
                continue
    return round(max(0.0, total), 3)


def _profile_span_mm(profile: Optional[dict]) -> float:
    if not isinstance(profile, dict):
        return 0.0
    s = profile.get("s_mm") or []
    if not s:
        return 0.0
    try:
        return float(s[-1]) - float(s[0] if len(s) > 1 else 0.0)
    except (TypeError, ValueError, IndexError):
        return 0.0


def _centerline_from_bundle(bundle: dict) -> float:
    """QA-effective axial total: chosen/corrected formline, else drawing length."""
    if bundle.get("profile_choice"):
        span = _profile_span_mm(bundle.get("final_profile"))
        if span > 0:
            return span
    lv = bundle.get("length_validation") or {}
    if lv.get("status") in ("matched", "trimmed", "extended"):
        try:
            drawing = float(lv.get("drawing_length") or 0)
        except (TypeError, ValueError):
            drawing = 0.0
        if drawing > 0:
            return drawing
        span = _profile_span_mm(bundle.get("working_profile") or bundle.get("corrected_profile"))
        if span > 0:
            return span
    for key in (
        "working_profile",
        "corrected_profile",
        "smoothed_profile",
        "auto_profile",
        "raw_profile",
        "manual_profile",
    ):
        span = _profile_span_mm(bundle.get(key))
        if span > 0:
            return span
    return 0.0


def refresh_length_metrics(bundle: dict, *, centerline_length: Optional[float] = None) -> dict:
    """L4 = confirmed straight length; formline length = QA total − L4.

    Caller-supplied ``centerline_length`` is only a fallback. After length
    validation it must not override drawing length / the corrected formline.
    """
    L4 = confirmed_straight_length_mm(bundle)
    total = _centerline_from_bundle(bundle)
    if total <= 0 and centerline_length is not None:
        try:
            total = float(centerline_length)
        except (TypeError, ValueError):
            total = 0.0
    bundle["L4"] = L4
    bundle["centerline_length_mm"] = round(max(0.0, total), 3)
    bundle["formline_length"] = round(max(0.0, total - L4), 3)
    return bundle


def apply_qa_lengths_to_results(results: dict, bundle: dict) -> dict:
    """Write QA-effective total / L4 / formline length onto the PP session payload.

    Subsequent process design uses ``centerline_length`` (drawing length after
    correction). ``actual_z_length`` stays the STP endpoint measurement.
    """
    refresh_length_metrics(bundle)
    total = float(bundle.get("centerline_length_mm") or 0)
    results["L4"] = float(bundle.get("L4") or 0)
    results["formline_length"] = bundle.get("formline_length")
    if total > 0:
        results["centerline_length"] = total
    wt_info = bundle.get("wall_thickness") or {}
    qa_vals = wt_info.get("parsed") or wt_info.get("values") or {}
    if isinstance(qa_vals, dict):
        seg = dict(results.get("segments") or {})
        for key in WALL_LENGTH_KEYS + WALL_THICKNESS_KEYS:
            raw = qa_vals.get(key)
            if raw in (None, ""):
                continue
            try:
                fv = float(raw)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                seg[key] = fv
        results["segments"] = seg
    original = results.get("wall_thickness_data") or results.get("centerline_perimeter_data") or []
    overlay = working_to_wall_thickness_records(bundle, original)
    if overlay:
        results["wall_thickness_data"] = overlay
        results["centerline_perimeter_data"] = overlay
    _apply_length_correction_to_sections(results, bundle)
    return results


def _sync_centerline_from_sections(results: dict, sections: list) -> None:
    points = [s.get("position") for s in sections if s.get("position")]
    if not points:
        return
    last_s = 0.0
    try:
        last_s = float(sections[-1].get("cumulative_length") or 0)
    except (TypeError, ValueError, IndexError):
        last_s = 0.0
    cl = dict(results.get("center_line") or {})
    cl["points"] = points
    if last_s > 0:
        cl["total_length"] = last_s
        results["center_line_length"] = last_s
    results["center_line"] = cl
    if results.get("center_line_points") is not None:
        results["center_line_points"] = points


def _apply_length_correction_to_sections(results: dict, bundle: dict) -> None:
    """Remap 3D stations with the same small/large-end deltas as the formline."""
    lv = bundle.get("length_validation") or {}
    results["length_validation"] = lv
    args = correction_args_from_validation(lv)
    for key, raw_key in (("sections_full", "stp_sections_full"), ("sections", "stp_sections")):
        current = results.get(key) or []
        if not current:
            continue
        if raw_key not in results:
            results[raw_key] = deepcopy(current)
        if not args:
            continue
        try:
            results[key] = apply_split_length_correction_to_sections(results[raw_key], **args)
        except ValueError:
            continue
    remapped = results.get("sections_full") or []
    if remapped:
        _sync_centerline_from_sections(results, remapped)


def _remember_auto_profile(bundle: dict) -> None:
    src = str(bundle.get("profile_source") or "")
    if src.startswith("manual"):
        return
    working = bundle.get("working_profile") or bundle.get("raw_profile")
    if working:
        bundle["auto_profile"] = deepcopy(working)


def auto_profile_of(bundle: dict) -> Optional[dict]:
    return bundle.get("auto_profile") or bundle.get("working_profile") or bundle.get("raw_profile")


def resolve_chosen_profile(bundle: dict, choice: Optional[str] = None):
    chosen = str(choice or bundle.get("profile_choice") or "").strip().lower()
    if chosen not in ("auto", "manual"):
        raise QaStateError(
            "QA_PROFILE_CHOICE_REQUIRED",
            "请先选择采用自动读取形线或人工测量形线，再保存或进入工艺规划。",
            bundle.get("status") or "",
        )
    if chosen == "auto":
        prof = auto_profile_of(bundle)
        label = "自动读取形线"
        source = (prof or {}).get("source") or bundle.get("profile_source") or "stp_raw"
    else:
        prof = bundle.get("manual_profile")
        label = "人工测量形线"
        source = "manual_xlsx"
    if not profile_is_nonempty(prof):
        raise QaStateError(
            "QA_PROFILE_EMPTY",
            f"{label}为空，不能传入后续工艺规划。请先完成形线读取或上传有效的人工测量文件。",
            bundle.get("status") or "",
            [{"choice": chosen, "point_count": profile_point_count(prof)}],
        )
    return chosen, prof, source


def choose_profile(bundle: dict, choice: str, *, centerline_length: Optional[float] = None) -> dict:
    require_action(bundle.get("status"), ACTION_SELECT_PROFILE)
    chosen, prof, source = resolve_chosen_profile(bundle, choice)
    bundle["profile_choice"] = chosen
    bundle["final_profile"] = deepcopy(prof)
    bundle["profile_source"] = source
    refresh_length_metrics(bundle, centerline_length=centerline_length)
    _audit(bundle, "select_profile", choice=chosen, points=profile_point_count(prof), source=source)
    return bundle


def initialize_qa_from_records(
    records: Sequence[dict],
    *,
    centerline_length: Optional[float] = None,
    geometry_error: Optional[str] = None,
    profile_source: str = "stp_raw",
    original_filename: str = "",
) -> dict:
    bundle = empty_qa_bundle()
    bundle["params"] = _params_snapshot()
    bundle["qa_time"] = _now()
    bundle["original_filename"] = original_filename
    bundle["profile_source"] = profile_source
    _audit(bundle, "initialize", source=profile_source)

    s, c = records_to_arrays(records)
    validated = validate_profile_input(
        s, c, centerline_length=centerline_length, geometry_error=geometry_error,
    )
    bundle["validation"] = {
        "ok": bool(validated.get("ok")),
        "reason": validated.get("reason"),
        "message": validated.get("message"),
        "metrics": validated.get("metrics") or {},
    }
    bundle["input_fixes"] = validated.get("log") or []
    bundle["warnings"] = list(validated.get("warnings") or [])

    if not validated.get("ok"):
        bundle["status"] = QA_INVALID_INPUT
        bundle["reason"] = validated.get("reason") or "invalid_input"
        _audit(bundle, "invalid_input", reason=bundle["reason"])
        return bundle

    profile: ProfileArrays = validated["profile"]
    profile.source = profile_source
    bundle["raw_profile"] = profile.to_dict()
    bundle["working_profile"] = profile.copy().to_dict()
    bundle["raw_section_dims"] = extract_section_dims(records, profile.s)
    bundle["corrected_profile"] = None
    bundle["smoothed_profile"] = None
    bundle["corrected_section_dims"] = None
    return _run_monotonic_and_followup(bundle)


def _run_monotonic_and_followup(bundle: dict) -> dict:
    working = _dict_to_profile(bundle.get("working_profile"), bundle.get("profile_source") or "stp_raw")
    if working is None:
        bundle["status"] = QA_INVALID_INPUT
        bundle["reason"] = "empty_working_profile"
        return bundle

    mono = validate_monotonic_profile(working.s, diameter=working.diameter)
    bundle["suspect_segments"] = mono.get("suspect_segments") or []
    bundle["anomaly_mask"] = mono.get("anomaly_mask") or []
    bundle["anomaly_indices"] = mono.get("anomaly_indices") or []
    bundle["anomaly_point_ratio"] = mono.get("anomaly_point_ratio") or 0.0
    bundle["anomaly_length_ratio"] = mono.get("anomaly_length_ratio") or 0.0
    bundle["reason"] = mono.get("reason") or ""
    bundle["metrics"] = mono.get("metrics") or {}
    bundle["monotonic"] = {
        "status": mono.get("status"),
        "reason": mono.get("reason"),
        "metrics": mono.get("metrics"),
    }
    # Do not persist the full reference arrays into the session (size); keep summary.
    bundle.pop("processed_profile", None)

    status = mono.get("status")
    if status == QA_MONOTONIC_PASS:
        bundle["status"] = QA_MONOTONIC_PASS
        _audit(bundle, "monotonic_pass")
        return _finish_auto_stage(_attach_straight_candidates(bundle))
    if status == QA_LOCAL_ANOMALY:
        bundle["status"] = QA_LOCAL_ANOMALY
        _audit(bundle, "local_anomaly", segments=len(bundle["suspect_segments"]))
        return _finish_auto_stage(bundle)
    bundle["status"] = QA_MANUAL_REQUIRED
    _audit(bundle, "manual_required", reason=bundle["reason"])
    return _finish_auto_stage(bundle)


def _finish_auto_stage(bundle: dict) -> dict:
    _remember_auto_profile(bundle)
    if bundle.get("length_validation") and bundle.get("status") not in (
        QA_LOCAL_ANOMALY, QA_INVALID_INPUT, QA_MANUAL_REQUIRED, QA_RECHECK_FAILED,
    ):
        # No extra smoothing step needed: length-corrected formline is the effective one.
        if bundle.get("working_profile"):
            bundle["smoothed_profile"] = deepcopy(bundle.get("working_profile"))
    refresh_length_metrics(bundle)
    return bundle


def _attach_straight_candidates(bundle: dict) -> dict:
    working = _dict_to_profile(bundle.get("working_profile"), "working")
    det = detect_straight_segments(working.s, working.diameter)
    bundle["straight_segments"] = det.get("segments") or []
    bundle["straight_metrics"] = det.get("metrics") or {}
    bundle["status"] = QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION
    _audit(bundle, "straight_candidates", count=len(bundle["straight_segments"]))
    return bundle


def preview_smooth(bundle: dict) -> dict:
    require_action(bundle.get("status"), ACTION_PREVIEW_SMOOTH)
    working = _dict_to_profile(bundle.get("working_profile"), "working")
    result = preview_local_smooth(working, bundle.get("suspect_segments") or [])
    preview = result["profile"].to_dict()
    bundle["processed_profile"] = preview
    bundle["smooth_preview"] = {
        "ok": result["ok"],
        "method": result["method"],
        "changed_ranges": result["changed_ranges"],
        "failed_segments": result["failed_segments"],
    }
    _audit(bundle, "preview_smooth", ok=result["ok"])
    out = deepcopy(bundle)
    out["processed_profile"] = preview
    return out


def confirm_smooth(bundle: dict, *, confirmed_by: Optional[str] = None) -> dict:
    require_action(bundle.get("status"), ACTION_CONFIRM_SMOOTH)
    if bundle.get("status") not in (QA_LOCAL_ANOMALY, "QA_ENGINEER_REVIEW_REQUIRED", QA_MANUAL_REQUIRED):
        return bundle
    working = _dict_to_profile(bundle.get("working_profile"), "working")
    raw = _dict_to_profile(bundle.get("raw_profile"), "stp_raw")
    result = preview_local_smooth(working, bundle.get("suspect_segments") or [])
    bundle["smooth_before"] = working.to_dict()
    bundle["smooth_after"] = result["profile"].to_dict()
    bundle["processed_profile"] = result["profile"].to_dict()
    if not result["ok"] or result["failed_segments"]:
        bundle["status"] = QA_RECHECK_FAILED
        bundle["reason"] = "smoothing_anchors_incompatible"
        bundle["warnings"] = list(bundle.get("warnings") or []) + [
            "自动平滑后形线仍未通过质量检查，建议上传手工测量数据。"
        ]
        _audit(bundle, "smooth_failed", failed=result["failed_segments"], confirmed_by=confirmed_by)
        return bundle

    bundle["working_profile"] = result["profile"].to_dict()
    bundle["smoothed_profile"] = deepcopy(bundle["working_profile"])
    bundle["profile_source"] = "stp_smoothed"
    bundle["status"] = QA_SMOOTHING_APPLIED
    bundle["engineer_smooth_choice"] = {
        "action": "apply_suggested_smooth",
        "confirmed_by": confirmed_by or "",
        "confirmed_at": _now(),
        "algorithm_version": ALGORITHM_VERSION,
        "changed_ranges": result["changed_ranges"],
    }
    _audit(bundle, "confirm_smooth", confirmed_by=confirmed_by or "")
    # Re-validate; never PASS just because smoothing ran.
    recheck = _run_monotonic_and_followup(bundle)
    if recheck.get("monotonic", {}).get("status") != QA_MONOTONIC_PASS:
        recheck["status"] = QA_RECHECK_FAILED
        recheck["reason"] = recheck.get("reason") or "smooth_recheck_failed"
        recheck["warnings"] = list(recheck.get("warnings") or []) + [
            "自动平滑后形线仍未通过质量检查，建议上传手工测量数据。"
        ]
        _audit(recheck, "recheck_failed")
    return recheck


def reject_smooth(bundle: dict, *, confirmed_by: Optional[str] = None) -> dict:
    require_action(bundle.get("status"), ACTION_REJECT_SMOOTH)
    bundle["processed_profile"] = None
    bundle["engineer_smooth_choice"] = {
        "action": "keep_raw",
        "confirmed_by": confirmed_by or "",
        "confirmed_at": _now(),
        "algorithm_version": ALGORITHM_VERSION,
    }
    prev_status = bundle.get("status")
    lv = bundle.get("length_validation") or {}
    if lv.get("status") in ("trimmed", "extended") and bundle.get("corrected_profile"):
        bundle["working_profile"] = deepcopy(bundle.get("corrected_profile"))
        bundle["profile_source"] = "stp_length_corrected"
        bundle["smoothed_profile"] = None
    else:
        bundle["profile_source"] = bundle.get("raw_profile", {}).get("source") or "stp_raw"
    # Raw monotonic violation is a hard geometry constraint — stay in review/fail.
    if prev_status == QA_MANUAL_REQUIRED:
        bundle["status"] = QA_MANUAL_REQUIRED
        bundle["reason"] = bundle.get("reason") or "engineer_kept_raw"
    else:
        bundle["status"] = QA_LOCAL_ANOMALY
        bundle["reason"] = "engineer_kept_raw_local_anomaly"
        bundle["warnings"] = list(bundle.get("warnings") or []) + [
            "工程师选择保留原始数据。原始形线仍违反单调递减约束，不能标记为 QA_PASS。"
        ]
    _audit(bundle, "reject_smooth", confirmed_by=confirmed_by or "")
    return _finish_auto_stage(bundle)


def _straight_by_id(bundle: dict, segment_id: str) -> Optional[dict]:
    for seg in bundle.get("straight_segments") or []:
        if seg.get("segment_id") == segment_id:
            return seg
    return None


def confirm_straight(bundle: dict, segment_id: str, *, confirmed_by: Optional[str] = None) -> dict:
    require_action(bundle.get("status"), ACTION_CONFIRM_STRAIGHT)
    if bundle.get("status") == QA_PASS:
        return bundle
    seg = _straight_by_id(bundle, segment_id)
    if seg is None:
        raise QaStateError("QA_STRAIGHT_NOT_FOUND", f"未找到直管段 {segment_id}", bundle.get("status"), [{"segment_id": segment_id}])
    if seg.get("status") == "confirmed":
        return bundle
    seg["status"] = "confirmed"
    seg["confirmed_by"] = confirmed_by or ""
    seg["confirmed_at"] = _now()
    seg["algorithm_version"] = ALGORITHM_VERSION
    _audit(bundle, "confirm_straight", segment_id=segment_id, confirmed_by=confirmed_by or "")
    return refresh_length_metrics(bundle)


def reject_straight(bundle: dict, segment_id: str, *, confirmed_by: Optional[str] = None) -> dict:
    require_action(bundle.get("status"), ACTION_REJECT_STRAIGHT)
    if bundle.get("status") == QA_PASS:
        return bundle
    seg = _straight_by_id(bundle, segment_id)
    if seg is None:
        raise QaStateError("QA_STRAIGHT_NOT_FOUND", f"未找到直管段 {segment_id}", bundle.get("status"), [{"segment_id": segment_id}])
    if seg.get("status") == "cancelled":
        return bundle
    seg["status"] = "cancelled"
    seg["confirmed_by"] = confirmed_by or ""
    seg["confirmed_at"] = _now()
    _audit(bundle, "reject_straight", segment_id=segment_id, confirmed_by=confirmed_by or "")
    return refresh_length_metrics(bundle)


def finalize_straight(
    bundle: dict,
    *,
    confirmed_ids: Optional[List[str]] = None,
    confirmed_by: Optional[str] = None,
) -> dict:
    require_action(bundle.get("status"), ACTION_FINALIZE_STRAIGHT)
    if bundle.get("status") == QA_PASS:
        return bundle
    ids = set(confirmed_ids) if confirmed_ids is not None else None
    for seg in bundle.get("straight_segments") or []:
        if ids is not None:
            if seg.get("segment_id") in ids:
                seg["status"] = "confirmed"
                seg["confirmed_by"] = confirmed_by or ""
                seg["confirmed_at"] = _now()
            elif seg.get("status") == "candidate":
                seg["status"] = "cancelled"
                seg["confirmed_by"] = confirmed_by or ""
                seg["confirmed_at"] = _now()
        elif seg.get("status") == "candidate":
            # No explicit list: leave undecided as cancelled so the engineer
            # cannot silently inherit algorithm candidates as final.
            seg["status"] = "cancelled"
            seg["confirmed_by"] = confirmed_by or ""
            seg["confirmed_at"] = _now()
    working = _dict_to_profile(bundle.get("working_profile"), bundle.get("profile_source") or "stp_raw")
    if bundle.get("profile_choice") == "manual" and profile_is_nonempty(bundle.get("manual_profile")):
        bundle["final_profile"] = deepcopy(bundle.get("manual_profile"))
    else:
        bundle["final_profile"] = working.to_dict() if working else None
    bundle["status"] = QA_PASS
    bundle["reason"] = "engineer_confirmed"
    bundle["qa_time"] = _now()
    _audit(bundle, "qa_pass", confirmed_by=confirmed_by or "", profile_source=bundle.get("profile_source"))
    return refresh_length_metrics(bundle)


def apply_manual_xlsx(
    bundle: dict,
    data: bytes,
    filename: str,
    *,
    centerline_length_mm: Optional[float] = None,
    confirmed_by: Optional[str] = None,
) -> dict:
    require_action(bundle.get("status"), ACTION_UPLOAD_XLSX)
    parsed = parse_manual_xlsx(data, filename, centerline_length_mm=centerline_length_mm)
    bundle["manual_override"] = {
        "filename": filename,
        "uploaded_at": _now(),
        "uploaded_by": confirmed_by or "",
        "ok": bool(parsed.get("ok")),
        "sheet": parsed.get("sheet"),
        "point_count": parsed.get("point_count"),
        "details": parsed.get("details") or [],
        "message": parsed.get("message"),
    }
    if not parsed.get("ok"):
        bundle["manual_profile"] = None
        bundle["xlsx_errors"] = parsed.get("details") or []
        bundle["code"] = parsed.get("code")
        bundle["message"] = parsed.get("message")
        bundle["details"] = parsed.get("details") or []
        if bundle.get("profile_choice") == "manual":
            bundle["profile_choice"] = None
            bundle["final_profile"] = None
        _audit(bundle, "manual_xlsx_invalid", details=parsed.get("details") or [])
        return bundle

    raw_xlxs = parsed.get("raw_xlxs")
    if not raw_xlxs:
        parsed_profile: ProfileArrays = parsed["profile"]
        raw_xlxs = [
            [float(a), float(b)]
            for a, b in zip(parsed_profile.s, parsed_profile.diameter)
        ]
    from core.pp.manual_profile.generator import generate_manual_profile
    generated = generate_manual_profile(raw_xlxs)
    if not generated.get("ok"):
        details = [{
            "row": None,
            "column": "manual_profile",
            "reason": generated.get("code") or "MANUAL_PROFILE_INVALID",
            "how_to_fix": generated.get("message") or "人工形线生成失败。",
        }]
        bundle["manual_profile"] = None
        bundle["manual_generation"] = None
        bundle["xlsx_errors"] = details
        bundle["code"] = generated.get("code") or "QA_MANUAL_XLSX_INVALID"
        bundle["message"] = generated.get("message") or "人工形线生成失败。"
        bundle["details"] = details
        bundle["manual_override"]["ok"] = False
        bundle["manual_override"]["details"] = details
        bundle["manual_override"]["message"] = bundle["message"]
        if bundle.get("profile_choice") == "manual":
            bundle["profile_choice"] = None
            bundle["final_profile"] = None
        _audit(bundle, "manual_xlsx_invalid", details=details)
        return bundle

    mp = generated["manual_profile"]
    profile = ProfileArrays(
        s=np.asarray(mp["s_mm"], dtype=float),
        circumference=np.asarray(mp["outer_circumference_mm"], dtype=float),
        diameter=np.asarray(mp["outer_diameter_mm"], dtype=float),
        source=mp.get("source") or "manual_xlsx",
    )
    _remember_auto_profile(bundle)
    bundle["manual_profile"] = mp
    bundle["manual_generation"] = {
        "raw_xlxs": generated.get("raw_xlxs"),
        "normalized_control_points": generated.get("normalized_control_points"),
        "constrained_control_points": generated.get("constrained_control_points"),
        "segments": generated.get("segments"),
        "reversed": generated.get("reversed"),
        "n_clipped": generated.get("n_clipped"),
        "monotone": generated.get("monotone"),
        "control_point_count": parsed.get("point_count"),
        "generated_point_count": int(len(profile.s)),
    }
    bundle["manual_override"]["ok"] = True
    bundle["manual_override"]["point_count"] = int(len(profile.s))
    bundle["xlsx_errors"] = []
    bundle["details"] = []
    bundle["warnings"] = list(bundle.get("warnings") or []) + list(parsed.get("warnings") or [])
    _audit(bundle, "manual_xlsx_accepted", filename=filename, points=int(len(profile.s)))
    refresh_length_metrics(bundle, centerline_length=centerline_length_mm)
    return bundle


def rerun_qa(bundle: dict) -> dict:
    require_action(bundle.get("status"), ACTION_RERUN)
    working = bundle.get("working_profile")
    if not working:
        bundle["status"] = QA_INVALID_INPUT
        return bundle
    _audit(bundle, "rerun")
    return _run_monotonic_and_followup(bundle)


def _stale_key_section_message() -> str:
    return "管件长度或形线数据已更新，请重新确认已保存的关键截面位置。"


def confirm_wall_and_validate_length(
    bundle: dict,
    values: Optional[dict],
    *,
    correction_side: Optional[str] = None,
    small_end_correction_mm: Optional[float] = None,
    confirmed_by: Optional[str] = None,
) -> dict:
    """Confirm T/TL, compute drawing length, correct formline, THEN re-run QA/smoothing."""
    require_action(bundle.get("status"), ACTION_CONFIRM_WALL_LENGTH)
    if isinstance(small_end_correction_mm, str) and not str(small_end_correction_mm).strip():
        small_end_correction_mm = None
    check = validate_wall_thickness(values)
    if not check["ok"]:
        raise QaStateError(
            "QA_WALL_THICKNESS_INVALID",
            check["message"] or "壁厚确认数据不完整。",
            bundle.get("status") or "",
            [{"errors": check["errors"]}],
        )
    drawing = calculate_drawing_length(values)
    if not drawing["ok"]:
        raise QaStateError(
            "QA_DRAWING_LENGTH_INVALID",
            drawing["message"] or "无法计算图纸总长。",
            bundle.get("status") or "",
            [{"errors": drawing["errors"]}],
        )

    raw = _dict_to_profile(bundle.get("raw_profile"), "stp_raw")
    if raw is None:
        raise QaStateError(
            "QA_RAW_PROFILE_MISSING",
            "缺少 STP 原始形线，无法进行长度校验。",
            bundle.get("status") or "",
        )

    previous_sig = profile_signature(bundle.get("working_profile") or bundle.get("raw_profile"))
    previous_drawing = (bundle.get("length_validation") or {}).get("drawing_length")
    had_records = bool(bundle.get("key_section_records"))
    result = validate_profile_length(
        raw,
        drawing["drawing_length"],
        section_dims=bundle.get("raw_section_dims"),
        correction_side=correction_side,
        small_end_correction_mm=small_end_correction_mm,
        length_tolerance_mm=LENGTH_COMPARE_TOLERANCE_MM,
    )
    bundle["wall_thickness"] = {
        "values": dict(values or {}),
        "parsed": drawing.get("parsed") or check.get("parsed") or {},
        "terms": drawing.get("terms") or {},
        "confirmed_by": confirmed_by or "",
        "confirmed_at": _now(),
    }
    bundle["length_validation"] = result.get("length_validation")
    if result.get("status") == "ambiguous_end":
        _audit(bundle, "length_validation_ambiguous", side=correction_side)
        return bundle
    if not result.get("ok") or result.get("profile") is None:
        raise QaStateError(
            "QA_LENGTH_VALIDATION_FAILED",
            (result.get("length_validation") or {}).get("message") or "长度校验失败。",
            bundle.get("status") or "",
        )

    corrected: ProfileArrays = result["profile"]
    bundle["corrected_profile"] = corrected.to_dict()
    bundle["corrected_section_dims"] = result.get("section_dims") or bundle.get("raw_section_dims")
    bundle["working_profile"] = deepcopy(bundle["corrected_profile"])
    bundle["processed_profile"] = None
    bundle["smoothed_profile"] = None
    bundle["smooth_preview"] = None
    bundle["engineer_smooth_choice"] = None
    if result["status"] in ("trimmed", "extended"):
        bundle["profile_source"] = "stp_length_corrected"
    else:
        bundle["profile_source"] = bundle.get("raw_profile", {}).get("source") or "stp_raw"

    new_sig = profile_signature(bundle["working_profile"])
    drawing_changed = previous_drawing is not None and abs(float(previous_drawing) - float(drawing["drawing_length"])) > LENGTH_COMPARE_TOLERANCE_MM
    if had_records and (new_sig != previous_sig or drawing_changed):
        bundle["key_section_records"] = mark_records_stale(
            bundle.get("key_section_records"),
            reason=_stale_key_section_message(),
        )
        bundle["key_section_stale_message"] = _stale_key_section_message()
    _audit(
        bundle, "length_validation",
        status=result["status"],
        drawing_length=drawing["drawing_length"],
        confirmed_by=confirmed_by or "",
        small_end_correction_mm=(result.get("length_validation") or {}).get("small_end_correction_mm"),
        large_end_correction_mm=(result.get("length_validation") or {}).get("large_end_correction_mm"),
    )
    # Length correction is complete; monotonic check / smoothing run on corrected_profile.
    return _run_monotonic_and_followup(bundle)


def _effective_key_section_source(bundle: dict) -> tuple:
    """Key-section search uses the length-corrected formline, before smoothing."""
    if profile_is_nonempty(bundle.get("corrected_profile")):
        return "corrected", bundle.get("corrected_profile")
    if profile_is_nonempty(bundle.get("working_profile")):
        return "working", bundle.get("working_profile")
    return "raw", bundle.get("raw_profile")


def _dims_as_records(dims: dict) -> List[dict]:
    return [
        {
            "中心线长度(mm)": s,
            "x_length": x,
            "y_length": y,
        }
        for s, x, y in zip(
            dims.get("s_mm") or [],
            dims.get("x_length_mm") or [],
            dims.get("y_length_mm") or [],
        )
    ]


def _effective_section_dims(bundle: dict) -> Optional[dict]:
    dims = bundle.get("corrected_section_dims")
    if not section_dims_usable(dims):
        dims = bundle.get("raw_section_dims")
    _version, prof = _effective_key_section_source(bundle)
    if section_dims_usable(dims) and prof and prof.get("s_mm"):
        if len(dims["s_mm"]) != len(prof["s_mm"]):
            return extract_section_dims(_dims_as_records(dims), prof.get("s_mm"))
    return dims if section_dims_usable(dims) else None


def backfill_section_dims(bundle: dict, records: Sequence[dict]) -> dict:
    """Fill raw/corrected section sizes when STP records omitted x_length."""
    if section_dims_usable(bundle.get("raw_section_dims")):
        if not section_dims_usable(bundle.get("corrected_section_dims")):
            _version, prof = _effective_key_section_source(bundle)
            if prof and prof.get("s_mm"):
                bundle["corrected_section_dims"] = extract_section_dims(
                    _dims_as_records(bundle["raw_section_dims"]),
                    prof.get("s_mm"),
                )
            else:
                bundle["corrected_section_dims"] = bundle.get("raw_section_dims")
        return bundle
    dims = extract_section_dims(records)
    bundle["raw_section_dims"] = dims
    _version, prof = _effective_key_section_source(bundle)
    if dims and prof and prof.get("s_mm"):
        bundle["corrected_section_dims"] = extract_section_dims(_dims_as_records(dims), prof.get("s_mm"))
    else:
        bundle["corrected_section_dims"] = dims
    return bundle


def search_key_sections_in_bundle(
    bundle: dict,
    target_length: float,
    target_width: float,
    *,
    expected_s_mm: Optional[float] = None,
) -> dict:
    require_action(bundle.get("status"), ACTION_SEARCH_KEY_SECTION)
    version, _prof = _effective_key_section_source(bundle)
    result = search_key_sections(
        _effective_section_dims(bundle),
        target_length,
        target_width,
        tolerance_mm=SECTION_MATCH_TOLERANCE_MM,
        expected_s_mm=expected_s_mm,
    )
    result["profile_version"] = version
    bundle["key_section_search"] = result
    _audit(bundle, "search_key_section", count=result.get("count") or 0, version=version)
    return bundle


def save_key_section_record(
    bundle: dict,
    candidate: Optional[dict] = None,
    *,
    index: Optional[int] = None,
    save_all: bool = False,
    expected_s_mm: Optional[float] = None,
) -> dict:
    require_action(bundle.get("status"), ACTION_SAVE_KEY_SECTION)
    search = bundle.get("key_section_search") or {}
    candidates = search.get("candidates") or []
    chosen_list: List[dict] = []
    if isinstance(candidate, dict) and candidate.get("s_mm") is not None:
        chosen_list = [candidate]
    elif index is not None:
        try:
            chosen_list = [candidates[int(index)]]
        except (TypeError, ValueError, IndexError):
            chosen_list = []
    elif save_all or candidates:
        chosen_list = [row for row in candidates if isinstance(row, dict)]
    if not chosen_list:
        raise QaStateError(
            "QA_KEY_SECTION_NOT_SELECTED",
            "请选择一个候选位置后再保存校验记录。",
            bundle.get("status") or "",
        )
    version, prof = _effective_key_section_source(bundle)
    records = list(bundle.get("key_section_records") or [])
    for chosen in chosen_list:
        rec = build_saved_record(
            search.get("target_length"),
            search.get("target_width"),
            chosen,
            record_id=next_record_id(records),
            profile_version=version,
            profile_signature=profile_signature(prof),
            expected_s_mm=expected_s_mm if expected_s_mm is not None else search.get("expected_s_mm"),
        )
        records.append(rec)
    bundle["key_section_records"] = records
    bundle["key_section_search"] = None
    _audit(bundle, "save_key_section", record_id=records[-1]["id"], count=len(records))
    return bundle


def delete_key_section_record(bundle: dict, record_id: int) -> dict:
    require_action(bundle.get("status"), ACTION_DELETE_KEY_SECTION)
    records = [r for r in (bundle.get("key_section_records") or []) if int(r.get("id") or 0) != int(record_id)]
    bundle["key_section_records"] = records
    _audit(bundle, "delete_key_section", record_id=record_id)
    return bundle


def get_final_profile(bundle: dict) -> dict:
    require_action(bundle.get("status"), ACTION_GET_FINAL)
    return {
        "status": QA_PASS,
        "profile_source": bundle.get("profile_source"),
        "final_profile": bundle.get("final_profile"),
        "straight_segments": [
            s for s in (bundle.get("straight_segments") or []) if s.get("status") == "confirmed"
        ],
        "qa_quantity": QA_QUANTITY,
        "storage_quantity": QA_STORAGE_QUANTITY,
        "algorithm_version": ALGORITHM_VERSION,
    }


def qa_summary(bundle: Optional[dict]) -> dict:
    if not bundle:
        return {"status": None, "present": False}
    return {
        "present": True,
        "status": bundle.get("status"),
        "reason": bundle.get("reason"),
        "profile_source": bundle.get("profile_source"),
        "anomaly_point_ratio": bundle.get("anomaly_point_ratio"),
        "anomaly_length_ratio": bundle.get("anomaly_length_ratio"),
        "suspect_count": len(bundle.get("suspect_segments") or []),
        "straight_count": len(bundle.get("straight_segments") or []),
        "profile_choice": bundle.get("profile_choice"),
        "L4": bundle.get("L4") or 0.0,
        "formline_length": bundle.get("formline_length"),
        "auto_points": profile_point_count(auto_profile_of(bundle)),
        "manual_points": profile_point_count(bundle.get("manual_profile")),
        "algorithm_version": bundle.get("algorithm_version") or ALGORITHM_VERSION,
        "qa_quantity": bundle.get("qa_quantity") or QA_QUANTITY,
        "blocking": bundle.get("status") != QA_PASS,
        "length_validation_status": (bundle.get("length_validation") or {}).get("status"),
        "key_section_record_count": len(bundle.get("key_section_records") or []),
    }


def working_to_wall_thickness_records(bundle: dict, original_rows: Optional[List[dict]] = None) -> List[dict]:
    prof = bundle.get("final_profile") or bundle.get("working_profile")
    if bundle.get("profile_choice"):
        try:
            _, chosen, _ = resolve_chosen_profile(bundle)
            prof = chosen
        except QaStateError:
            pass
    working = _dict_to_profile(prof, "working")
    if working is None:
        return list(original_rows or [])
    return working.to_wall_thickness_overlay(original_rows)
