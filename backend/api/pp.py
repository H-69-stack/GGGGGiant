"""Process Planning API — Upload, analyze, optimize."""
import math
import json
import os
import shutil
import threading
import time
import logging
import uuid

import numpy as np
from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename
from config import PP_UPLOADS_DIR, PP_TUBE_DB, PP_MESH_CACHE_DIR, PP_TUBE_SAVED_DIR
from core.pp.tube import analyze_tube
from core.pp.step_analyzer import OCP_AVAILABLE
from core.pp.if_bended import is_tube_bended, calculate_bend_angle
from core.pp.formline_conclude import analyze_formline_from_data
from core.pp.wall_thickness_segments import (
    analyze_uploaded_wall,
    detect_wall_thickness_segments,
    thickness_curve_from_tl_t,
)
from core.pp.process_length import process_length
from core.pp.grind_allowance import apply_grind_allowance
from core.pp.thickness_db import (
    init_database, list_database_tubes, get_tube_by_id,
    save_tube_data, delete_multiple_tube_data, query_tube_data,
    save_full_analysis, get_full_analysis, rename_tube, get_source_file_path,
    update_centerline_length, acknowledge_review,
)
from core.pp.design_db import (
    save_design as save_pp_design,
    list_designs as list_pp_designs,
    get_design as get_pp_design,
    rename_design as rename_pp_design,
    delete_designs as delete_pp_designs,
    DESIGN_PARAM_META,
    DESIGN_PARAM_ORDER,
    DESIGN_PARAM_DESC,
)
from core.pp.modify_thick import apply_modify_thick
from core.pp.mandrel_d0 import (
    DRAW_BLANK_METHODS,
    decimal_places,
    route_max_d0,
    scale_blank_length,
    solve_mandrel_d0,
)
from core.pp.optimization import (
    optimize_process_parameters,
    optimize_process_parameters_by_method,
    recalc_solution_by_method,
    available_pp_methods,
    recommend_pp_method,
)

logger = logging.getLogger(__name__)

pp_bp = Blueprint('pp', __name__)


def _build_segments_for_optimization(results: dict) -> dict:
    """Build/repair a ``segments`` dict suitable for legacy optimizers.

    Legacy optimizers require ``T1..T4`` plus ``D1/D3`` and ``TL4``. When the
    record was saved via the manual wall-thickness panel, ``wall_thickness_data``
    already contains the user-confirmed curve, but ``segments`` may not carry
    explicit T values. This helper derives them from ``manual_segments`` first,
    and otherwise falls back to per-cumulative-length statistics of
    ``wall_thickness_data``.
    """
    seg = dict(results.get('segments') or {})
    wt = results.get('wall_thickness_data') or []
    manual = results.get('manual_segments') or {}
    qa = results.get('qa') or {}
    wt_info = qa.get('wall_thickness') if isinstance(qa, dict) else {}
    qa_vals = {}
    if isinstance(wt_info, dict):
        qa_vals = wt_info.get('parsed') or wt_info.get('values') or {}
    if isinstance(qa_vals, dict):
        for key in ('TL1', 'TLM1', 'TL2', 'TLM2', 'TL3', 'TLM3', 'TL4', 'T1', 'T2', 'T3', 'T4'):
            raw = qa_vals.get(key)
            if raw in (None, ''):
                continue
            try:
                fv = float(raw)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                seg[key] = fv

    # ----- Diameters (D1: max perimeter, D3: min perimeter) -----
    if not seg.get('D1') or not seg.get('D3'):
        try:
            perimeters = [
                float(row.get('外径周长(mm)', 0))
                for row in wt
                if float(row.get('外径周长(mm)', 0)) > 0
            ]
            if perimeters:
                seg.setdefault('D1', round(max(perimeters) / np.pi, 2))
                seg.setdefault('D3', round(min(perimeters) / np.pi, 2))
        except Exception:
            pass

    # ----- Wall thicknesses T1..T4 -----
    has_valid_t = any(
        float(seg.get(k, 0) or 0) > 0 for k in ('T1', 'T2', 'T3', 'T4')
    )
    if not has_valid_t:
        thicknesses = []
        try:
            mt = manual.get('thicknesses') if isinstance(manual, dict) else None
            if isinstance(mt, list) and mt:
                thicknesses = [
                    float(v) for v in mt
                    if v is not None and float(v) > 0
                ]
        except Exception:
            thicknesses = []

        if not thicknesses:
            try:
                vals = [
                    float(row.get('壁厚(mm)', 0))
                    for row in wt
                    if row.get('壁厚(mm)') is not None
                    and float(row.get('壁厚(mm)', 0)) > 0
                ]
                if vals:
                    unique = sorted(set(round(v, 2) for v in vals))
                    if len(unique) >= 3:
                        thicknesses = [unique[-1], unique[0], unique[len(unique) // 2]]
                    elif len(unique) == 2:
                        thicknesses = [unique[1], unique[0]]
                    else:
                        thicknesses = [unique[0]]
            except Exception:
                thicknesses = []

        for idx in range(4):
            key = f'T{idx + 1}'
            seg[key] = float(thicknesses[idx]) if idx < len(thicknesses) else 0.0

    # ----- Segment lengths from manual boundaries -----
    # ModelUpload 分段确认：序列为 L1, TL1, L2, TL2, …（界面 TL = 过渡）
    # 对应总表 / legacy：TL1..TL4 = 平台长，TLM1..TLM3 = 过渡长
    has_tl = any(float(seg.get(k) or 0) > 0 for k in ('TL1', 'TL2', 'TL3', 'TL4'))
    has_tlm = any(float(seg.get(k) or 0) > 0 for k in ('TLM1', 'TLM2', 'TLM3'))
    if isinstance(manual, dict) and (not has_tl or not has_tlm):
        try:
            count = int(manual.get('count') or len(manual.get('thicknesses') or []))
            boundaries = [float(b) for b in (manual.get('boundaries') or [])]
            total = float(
                manual.get('centerline_length_mm')
                or process_length(results)
                or 0
            )
            tl = [0.0, 0.0, 0.0, 0.0]
            tlm = [0.0, 0.0, 0.0]
            if count == 1:
                tl[0] = total
            elif count == 2 and len(boundaries) >= 2:
                # L1 | TL1 | L2
                tl[0] = boundaries[0]
                tlm[0] = max(0.0, boundaries[1] - boundaries[0])
                tl[1] = max(0.0, total - boundaries[1])
            elif count == 3 and len(boundaries) >= 4:
                # L1 | TL1 | L2 | TL2 | L3
                tl[0] = boundaries[0]
                tlm[0] = max(0.0, boundaries[1] - boundaries[0])
                tl[1] = max(0.0, boundaries[2] - boundaries[1])
                tlm[1] = max(0.0, boundaries[3] - boundaries[2])
                tl[2] = max(0.0, total - boundaries[3])
            elif count == 4 and len(boundaries) >= 6:
                # L1 | TL1 | L2 | TL2 | L3 | TL3 | L4
                tl[0] = boundaries[0]
                tlm[0] = max(0.0, boundaries[1] - boundaries[0])
                tl[1] = max(0.0, boundaries[2] - boundaries[1])
                tlm[1] = max(0.0, boundaries[3] - boundaries[2])
                tl[2] = max(0.0, boundaries[4] - boundaries[3])
                tlm[2] = max(0.0, boundaries[5] - boundaries[4])
                tl[3] = max(0.0, total - boundaries[5])
            if not has_tl:
                for i in range(4):
                    seg[f'TL{i + 1}'] = round(tl[i], 2)
            if not has_tlm:
                for i in range(3):
                    seg[f'TLM{i + 1}'] = round(tlm[i], 2)
        except Exception:
            pass

    seg.setdefault('TL1', 0.0)
    seg.setdefault('TL2', 0.0)
    seg.setdefault('TL3', 0.0)
    seg.setdefault('TL4', 0.0)
    seg.setdefault('TLM1', 0.0)
    seg.setdefault('TLM2', 0.0)
    seg.setdefault('TLM3', 0.0)
    return seg


def _estimate_volume_from_wt(results: dict) -> float:
    """Fallback wall-material volume estimate using thin-wall approximation.

    Used when the saved record has ``volume == 0`` (old DB records pre-volume).
    V ≈ Σ perimeter * wall_thickness * Δs over centerline.
    """
    wt = results.get('wall_thickness_data') or []
    if not wt:
        return 0.0
    try:
        s = np.asarray(
            [float(r.get('中心线长度(mm)', 0)) for r in wt],
            dtype=float,
        )
        per = np.asarray(
            [float(r.get('外径周长(mm)', 0)) for r in wt],
            dtype=float,
        )
        thk = np.asarray(
            [float(r.get('壁厚(mm)', 0) or 0) for r in wt],
            dtype=float,
        )
    except Exception:
        return 0.0
    if s.size < 2:
        return 0.0
    order = np.argsort(s)
    s = s[order]
    per = per[order]
    thk = thk[order]
    ring_area = per * thk
    avg = (ring_area[:-1] + ring_area[1:]) * 0.5
    ds = np.diff(s)
    vol = float(np.sum(avg * ds))
    return max(0.0, vol)

def _qa_summary_safe(qa):
    try:
        from core.pp.profile_qa.pipeline import qa_summary
        return qa_summary(qa)
    except Exception:
        return {'present': bool(qa), 'status': (qa or {}).get('status')}


# In-memory sessions (follows rfm.py / normal.py pattern)
_pp_sessions = {}
_session_lock = threading.Lock()

# 工艺设计预览等功能开关（进程内全局，可通过 API 切换）
_pp_design_feature_flags = {
    'modified_wt_area_check': True,
}


def _modified_wt_area_check_enabled() -> bool:
    with _session_lock:
        return bool(_pp_design_feature_flags.get('modified_wt_area_check', True))


def _next_session_id(filename):
    """Generate a unique session ID."""
    base = secure_filename(filename).replace('.stl', '').replace('.STL', '').replace('.stp', '').replace('.STP', '')
    sid = base
    i = 1
    with _session_lock:
        while sid in _pp_sessions:
            sid = f"{base}_{i}"
            i += 1
    return sid


# ─────────────────────────── Upload ───────────────────────────

@pp_bp.route('/upload', methods=['POST'])
def upload():
    """Upload STL/STP file and run analysis."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未上传文件'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'message': '文件名为空'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    is_stp = ext in ('.stp', '.step')
    is_stl = ext in ('.stl',)

    if not is_stl and not is_stp:
        return jsonify({'success': False, 'message': '请上传STL或STP文件'}), 400

    if is_stp and not OCP_AVAILABLE:
        return jsonify({'success': False, 'message': 'STP分析需要安装cadquery/OCP，当前环境不可用。请上传STL文件或安装cadquery。'}), 400

    # Save uploaded file
    os.makedirs(PP_UPLOADS_DIR, exist_ok=True)
    save_path = os.path.join(PP_UPLOADS_DIR, secure_filename(file.filename))
    file.save(save_path)

    # Algorithm selection (A=PCA-rotate-first, B=no-rotation Z-slice)
    algorithm = (request.form.get('algorithm') or 'A').upper()
    if algorithm not in ('A', 'B'):
        algorithm = 'A'
    try:
        from core.pp.tube.end_treatment import end_options
        endpoint_options = end_options(json.loads(request.form.get('end_treatment') or '{}'))
    except (ValueError, TypeError) as exc:
        return jsonify({'success': False, 'message': f'端部修正参数无效：{exc}'}), 400
    try:
        sparse_step_mm = float(request.form.get('sparse_step_mm', 25.0))
    except (TypeError, ValueError):
        sparse_step_mm = 25.0

    session_id = _next_session_id(file.filename)

    with _session_lock:
        _pp_sessions[session_id] = {
            'file_path': save_path,
            'file_type': 'stp' if is_stp else 'stl',
            'algorithm': algorithm,
            'sparse_step_mm': sparse_step_mm,
            'original_filename': file.filename,
            'progress': {'pct': 0, 'message': '开始分析...', 'done': False, 'error': None},
            'results': None,
            'ready_for_tab2': False,
        }

    def run_analysis():
        try:
            def progress_cb(pct, msg):
                with _session_lock:
                    sess = _pp_sessions.get(session_id)
                    if sess:
                        sess['progress'] = {'pct': pct, 'message': msg, 'done': False, 'error': None}

            file_type = 'stp' if is_stp else 'stl'
            t_stp = time.perf_counter()
            analysis = analyze_tube(
                save_path,
                file_type=file_type,
                algorithm=algorithm,
                sparse_step_mm=sparse_step_mm,
                progress_callback=progress_cb,
                end_treatment=endpoint_options,
            )
            stp_elapsed_s = time.perf_counter() - t_stp

            if 'error' in analysis:
                with _session_lock:
                    _pp_sessions[session_id]['progress'] = {
                        'pct': 0, 'message': analysis['error'], 'done': True, 'error': analysis['error']
                    }
                return

            mesh_source = 'stl_file'
            mesh_data = None
            if is_stp:
                # STP: also tessellate once for the 3D viewer payload
                try:
                    from core.pp.step_analyzer import read_step
                    from core.pp.mesh_converter import shape_to_mesh
                    shape = read_step(save_path)
                    mesh_data = shape_to_mesh(shape, tolerance=0.3)
                    mesh_source = 'generated'
                except Exception as exc:
                    logger.warning("STP 网格生成失败: %s", exc)
                    mesh_source = 'generated'

            session_results = {
                'algorithm': analysis.get('algorithm', algorithm),
                'sparse_step_mm': sparse_step_mm,
                'center_line': {
                    'points': analysis.get('center_line_points', []),
                    'total_length': analysis.get('center_line_length', 0),
                },
                'sections': analysis.get('sections', []),
                'sections_full': analysis.get('sections_full', []),
                'thickness_stats': analysis.get('wall_thickness_stats'),
                'analysis_quality': analysis.get('analysis_quality'),
                'bounding_box': analysis.get('bounding_box', []),
                'mesh_source': mesh_source,
                'mesh_data': mesh_data,
                'centerline_length': analysis.get('center_line_length', 0),
                # actual_z_length 语义已被替换为中心线两端点直线距离（end-to-end）。
                # 旧版用 mesh 包围盒 Z 跨度，对弯管会显著偏短；新值对直/弯管都成立。
                'actual_z_length': analysis.get('actual_z_length', analysis.get('center_line_length', 0)),
                # 明示字段名：中心线两端点距离
                'centerline_endpoint_length': analysis.get(
                    'centerline_endpoint_length',
                    analysis.get('actual_z_length', analysis.get('center_line_length', 0)),
                ),
                'volume': analysis.get('volume', 0),
            }

            # Bend detection from center_line_points
            cl_pts = session_results['center_line']['points']
            is_bended = False
            bend_angle = 0.0
            if cl_pts and len(cl_pts) > 10:
                pts_arr = np.array(cl_pts)
                is_bended = is_tube_bended(pts_arr, angle_threshold=2.0)
                bend_angle = calculate_bend_angle(pts_arr)

            session_results['is_bended'] = is_bended
            session_results['bend_angle'] = bend_angle
            session_results['engineer_bending_decision'] = None
            session_results['final_bending_decision'] = 'program'
            session_results['from_database'] = False

            # Build wall_thickness_data for optimization compatibility.
            # 归档网格（GTAPP_STATION_GRID）可以粗于 1 mm，但计算层必须仍是 1 mm：
            # 分析结果里带 sections_dense 时用它，否则走 records_from_sections 的自动铺密。
            from core.pp.section_records import records_from_sections
            sections_full = session_results.get('sections_full', [])
            dense_sections = analysis.get('sections_dense') or sections_full
            wt_data = records_from_sections(dense_sections)
            session_results['wall_thickness_data'] = wt_data
            session_results['centerline_perimeter_data'] = wt_data

            # Formline QA (monotonic / straight / manual). Never overwrites raw records.
            try:
                from core.pp.profile_qa.pipeline import initialize_qa_from_records
                session_results['qa'] = initialize_qa_from_records(
                    wt_data,
                    centerline_length=session_results.get('centerline_length'),
                    profile_source='stp_raw',
                    original_filename=file.filename,
                )
            except Exception as qa_exc:
                logger.warning("PP profile QA failed (analysis kept): %s", qa_exc, exc_info=True)
                session_results['qa'] = {
                    'status': 'QA_INVALID_INPUT',
                    'reason': 'qa_internal_error',
                    'warnings': ['形线质量检查内部异常，请重试或改用手工数据。'],
                    'raw_profile': None,
                }

            # Formline analysis
            fl_result = analyze_formline_from_data(
                wt_data, tolerance=1.0, min_segment_length=50.0,
                trend_tolerance=0.01, min_point_count=30
            )
            session_results['L4'] = 0.0
            session_results['formline_length'] = session_results.get('centerline_length') or session_results.get('actual_z_length') or 0.0
            session_results['segments'] = {}

            progress_cb(100, '分析完成')

            with _session_lock:
                sess = _pp_sessions[session_id]
                sess['results'] = session_results
                sess['progress'] = {'pct': 100, 'message': '分析完成', 'done': True, 'error': None}
                try:
                    from core.pp.dev_cache import persist_from_session
                    persist_from_session(
                        session_id, sess,
                        stp_elapsed_s=stp_elapsed_s,
                        increment_stp=True, increment_qa=True,
                    )
                except Exception:
                    logger.debug("DEV STP cache persist skipped", exc_info=True)

        except Exception as e:
            logger.error(f"PP analysis error: {e}", exc_info=True)
            with _session_lock:
                _pp_sessions[session_id]['progress'] = {
                    'pct': 0, 'message': str(e), 'done': True, 'error': str(e)
                }

    thread = threading.Thread(target=run_analysis, daemon=True)
    thread.start()

    return jsonify({'success': True, 'session_id': session_id})


@pp_bp.route('/upload-status', methods=['GET'])
def upload_status():
    """Poll analysis progress."""
    session_id = request.args.get('id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess:
        return jsonify({'success': False, 'message': '会话不存在'}), 404
    return jsonify({'success': True, 'progress': sess['progress']})


# ─────────────────────────── Session info ───────────────────────────

@pp_bp.route('/session-info', methods=['GET'])
def session_info():
    """Get current session summary — returns all analysis data directly."""
    session_id = request.args.get('id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '分析尚未完成'}), 400
    r = sess['results']

    # 兼容：本进程重启后可能持有旧 session（修复前遗留的 _pp_sessions 残留），
    # 它们没有 centerline_endpoint_length 字段。如果能从 center_line.points 重算，
    # 就用端到端长度兜底，避免响应里 actual_z_length 还是错的 Z 跨度。
    endpoint_length = r.get('centerline_endpoint_length')
    if endpoint_length is None:
        try:
            pts = r.get('center_line', {}).get('points') or []
            if len(pts) >= 2:
                p0 = np.asarray(pts[0], dtype=float)
                pn = np.asarray(pts[-1], dtype=float)
                if np.all(np.isfinite(p0)) and np.all(np.isfinite(pn)):
                    ep = float(np.linalg.norm(pn - p0))
                    if np.isfinite(ep) and ep >= 0:
                        endpoint_length = round(ep, 2)
        except Exception:
            endpoint_length = None
    if endpoint_length is None:
        endpoint_length = r.get('actual_z_length', 0)

    try:
        prepared_wall = analyze_uploaded_wall(r.get('sections_full') or [])
        wall_recommendations = prepared_wall.get('fields')
        wall_curve = prepared_wall.get('curve') or []
    except Exception:
        logger.exception("session_info wall recommendation failed")
        wall_recommendations = None
        wall_curve = []

    return jsonify({
        'success': True,
        'session_id': session_id,
        'filename': sess.get('original_filename', ''),
        'algorithm': r.get('algorithm', sess.get('algorithm', 'A')),
        'sparse_step_mm': r.get('sparse_step_mm', sess.get('sparse_step_mm', 25.0)),
        'centerline_length': r.get('centerline_length', 0),
        'actual_z_length': r.get('actual_z_length', 0),
        'centerline_endpoint_length': endpoint_length,
        'volume': r.get('volume', 0),
        'is_bended': r.get('is_bended', False),
        'bend_angle': r.get('bend_angle', 0),
        'engineer_bending_decision': r.get('engineer_bending_decision'),
        'final_bending_decision': r.get('final_bending_decision', 'program'),
        'L4': r.get('L4', 0),
        'formline_length': r.get('formline_length'),
        'segments': r.get('segments', {}),
        'center_line': r.get('center_line', {'points': [], 'total_length': 0}),
        'sections': r.get('sections', []),
        'sections_full': r.get('sections_full', []),
        'thickness_stats': r.get('thickness_stats'),
        'analysis_quality': r.get('analysis_quality'),
        'mesh_source': r.get('mesh_source', 'stl_file'),
        'mesh_data': r.get('mesh_data'),
        'manual_segments': r.get('manual_segments'),
        'ready_for_tab2': sess.get('ready_for_tab2', False),
        'qa_summary': _qa_summary_safe(r.get('qa')),
        'wall_recommendations': wall_recommendations,
        'wall_curve': wall_curve,
    })


@pp_bp.route('/clear-session', methods=['POST'])
def clear_session():
    """Destroy a session."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        _pp_sessions.pop(session_id, None)
    return jsonify({'success': True, 'message': '会话已清除'})


# ─────────────────────────── Section at position ───────────────────────────

@pp_bp.route('/section-at', methods=['POST'])
def section_at():
    """Get cross-section at a centerline position."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    position_mm = data.get('position_mm', 0)
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('analyzer'):
        return jsonify({'success': False, 'message': '分析会话不存在'}), 400

    analyzer = sess['analyzer']
    try:
        section_data = analyzer.get_cross_section_at_position(position_mm)
        if 'error' in section_data:
            return jsonify({'success': False, 'message': section_data['error']}), 400
        # Generate plotly figure as dict
        fig_dict = analyzer.create_section_visualization(position_mm).to_dict()
        return jsonify({'success': True, 'section_data': section_data, 'figure': fig_dict})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ─────────────────────────── Regenerate perimeter ───────────────────────────

@pp_bp.route('/regenerate-perimeter', methods=['POST'])
def regenerate_perimeter():
    """Re-analyze perimeter at given interval."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    sample_interval_mm = data.get('sample_interval_mm', 1.0)
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess:
        return jsonify({'success': False, 'message': '会话不存在'}), 400

    analyzer = sess.get('analyzer')
    if not analyzer:
        return jsonify({'success': False, 'message': '分析器不存在'}), 400

    try:
        stl_path = sess.get('file_path', '')
        new_results = analyzer.analyze_stl_comprehensive(stl_path, sample_interval_mm=sample_interval_mm)
        if 'error' in new_results:
            return jsonify({'success': False, 'message': new_results['error']}), 500

        wt_data = new_results.get('centerline_perimeter_data', [])
        fig_dict = analyzer.create_perimeter_visualization(
            wt_data if isinstance(wt_data, list) else []
        ).to_dict()

        # Update session results
        if sess.get('results'):
            sess['results']['wall_thickness_data'] = wt_data
            sess['results']['centerline_perimeter_data'] = wt_data

        return jsonify({
            'success': True,
            'wall_thickness_data': wt_data,
            'figure': fig_dict,
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ─────────────────────────── Formline ───────────────────────────

@pp_bp.route('/formline', methods=['POST'])
def formline():
    """Run formline analysis for straight segment detection (L4)."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '分析尚未完成'}), 400

    wt_data = sess['results'].get('centerline_perimeter_data', [])
    fl = analyze_formline_from_data(
        wt_data,
        tolerance=data.get('tolerance', 1.0),
        min_segment_length=data.get('min_segment_length', 50.0),
        trend_tolerance=data.get('trend_tolerance', 0.01),
        min_point_count=data.get('min_point_count', 30),
    )

    # Update L4 in session only when QA has not already confirmed straight segments.
    qa = sess['results'].get('qa') or {}
    confirmed = [s for s in (qa.get('straight_segments') or []) if s.get('status') == 'confirmed']
    if confirmed:
        sess['results']['L4'] = float(qa.get('L4') or 0)
        cl = float(process_length(sess['results']) or 0)
        sess['results']['formline_length'] = max(0.0, cl - sess['results']['L4'])
    elif fl.get('has_segments'):
        sess['results']['L4'] = fl['statistics']['total_length']
    else:
        sess['results']['L4'] = 0.0

    return jsonify({'success': True, **fl})


# ─────────────────────────── Engineer bending override ───────────────────────────

@pp_bp.route('/engineer-bending', methods=['POST'])
def engineer_bending():
    """Engineer override for bend decision."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    decision = data.get('decision')  # true | false | null
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '分析尚未完成'}), 400

    sess['results']['engineer_bending_decision'] = decision
    if decision is not None:
        sess['results']['is_bended'] = decision
        sess['results']['final_bending_decision'] = 'engineer'
    else:
        sess['results']['is_bended'] = sess['results'].get('bend_angle', 0) > 2.0
        sess['results']['final_bending_decision'] = 'program'

    return jsonify({'success': True, 'is_bended': sess['results']['is_bended']})


@pp_bp.route('/thickness-data/<session_id>', methods=['GET'])
def thickness_data(session_id):
    """Return thickness sampling points with color coding for 3D visualization."""
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '会话不存在'}), 404

    r = sess['results']
    wt_data = r.get('wall_thickness_data') or r.get('centerline_perimeter_data') or []

    # Build samples list
    samples = []
    thickness_values = []

    if isinstance(wt_data, list):
        for item in wt_data:
            pos = item.get('中心线长度(mm)', 0)
            thickness = item.get('壁厚(mm)', 0)
            if thickness:
                thickness_values.append(thickness)

        # Calculate P10/P90 for color coding
        if thickness_values:
            import numpy as np
            p10 = np.percentile(thickness_values, 10)
            p90 = np.percentile(thickness_values, 90)

            for item in wt_data:
                thickness = item.get('壁厚(mm)', 0)
                if thickness:
                    # Determine color based on P10/P90 range
                    if thickness < p10 or thickness > p90:
                        color = 'Warning'
                    else:
                        color = 'Core'

                    samples.append({
                        'x': item.get('x', 0),
                        'y': item.get('y', 0),
                        'z': item.get('z', 0),
                        'value': thickness,
                        'color': color,
                    })

    # Calculate stats
    stats = {}
    if thickness_values:
        import numpy as np
        arr = np.array(thickness_values)
        stats = {
            'median_mm': float(np.median(arr)),
            'mean_mm': float(np.mean(arr)),
            'min_mm': float(np.min(arr)),
            'max_mm': float(np.max(arr)),
            'count': len(thickness_values),
        }

    return jsonify({
        'success': True,
        'samples': samples,
        'stats': stats,
    })


@pp_bp.route('/stl-file/<session_id>', methods=['GET'])
def serve_stl_file(session_id):
    """Serve STL bytes, including for STEP sessions (never send STEP as STL)."""
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess:
        return jsonify({'success': False, 'message': '会话不存在'}), 404

    if sess.get('file_type') in ('stp', 'step'):
        try:
            import io
            import trimesh
            results = sess.get('results') or {}
            mesh_data = results.get('mesh_data')
            if not mesh_data:
                from core.pp.step_analyzer import read_step
                from core.pp.mesh_converter import shape_to_mesh
                mesh_data = shape_to_mesh(read_step(sess['file_path']))
                results['mesh_data'] = mesh_data
            mesh = trimesh.Trimesh(
                vertices=np.asarray(mesh_data['vertices']).reshape(-1, 3),
                faces=np.asarray(mesh_data['faces']).reshape(-1, 3), process=False,
            )
            return send_file(io.BytesIO(mesh.export(file_type='stl')), mimetype='model/stl',
                             download_name='preview.stl')
        except Exception as exc:
            logger.exception('STP 预览网格生成失败')
            return jsonify({'success': False, 'message': f'STP 预览失败：{exc}'}), 500
    
    file_path = sess.get('file_path', '')
    if not file_path or not os.path.exists(file_path):
        return jsonify({'success': False, 'message': 'STL文件不存在'}), 404
    
    return send_file(file_path, mimetype='application/octet-stream')


# ─────────────────────────── 3D visualization ───────────────────────────

@pp_bp.route('/viz/3d', methods=['GET'])
def viz_3d():
    """Return plotly 3D figure as dict."""
    session_id = request.args.get('id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('analyzer'):
        return jsonify({'success': False, 'message': '分析会话不存在'}), 400

    try:
        fig_dict = sess['analyzer'].create_3d_visualization().to_dict()
        return jsonify({'success': True, 'figure': fig_dict})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ─────────────────────────── Apply to optimization ───────────────────────────

def _measurement_blocks_optimization(results):
    # Process planning now proceeds even if native STP sections are flagged
    # for review or recorded as missing. Quality warnings remain on the
    # analysis page; they no longer block load / optimize.
    return False


@pp_bp.route('/apply-to-optimization', methods=['POST'])
def apply_to_optimization():
    """Mark session ready for TAB2. Requires an explicit non-empty formline choice."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '分析尚未完成'}), 400
    qa = sess['results'].get('qa')
    if not qa:
        try:
            from core.pp.profile_qa.pipeline import initialize_qa_from_records
            wt = sess['results'].get('wall_thickness_data') or sess['results'].get('centerline_perimeter_data') or []
            qa = initialize_qa_from_records(
                wt,
                centerline_length=sess['results'].get('centerline_length'),
                profile_source='stp_raw',
            )
            sess['results']['qa'] = qa
        except Exception as qa_exc:
            logger.warning("QA before apply-to-optimization failed: %s", qa_exc)
            return jsonify({
                'success': False,
                'code': 'QA_INTERNAL_ERROR',
                'message': '形线质量检查失败，不能进入工艺优化。',
            }), 409
    try:
        from core.pp.profile_qa.pipeline import (
            apply_qa_lengths_to_results,
            choose_profile,
            working_to_wall_thickness_records,
        )
        clen = process_length(sess['results']) or None
        choice = data.get('profile_choice') or qa.get('profile_choice')
        qa = choose_profile(qa, choice, centerline_length=clen)
        overlay = working_to_wall_thickness_records(qa, sess['results'].get('wall_thickness_data') or [])
        if not overlay:
            return jsonify({
                'success': False,
                'code': 'QA_PROFILE_EMPTY',
                'message': '所选形线为空，不能传入后续工艺规划。',
            }), 409
        sess['results']['qa'] = qa
        sess['results']['wall_thickness_data'] = overlay
        sess['results']['centerline_perimeter_data'] = overlay
        apply_qa_lengths_to_results(sess['results'], qa)
        sess['results']['qa_final_ready'] = True
    except Exception as exc:
        from core.pp.profile_qa.state import QaStateError
        if isinstance(exc, QaStateError):
            return jsonify({'success': False, **exc.as_dict()}), 409
        logger.exception("apply-to-optimization profile commit failed")
        return jsonify({'success': False, 'code': 'QA_INTERNAL_ERROR', 'message': str(exc)}), 500
    sess['ready_for_tab2'] = True
    return jsonify({
        'success': True,
        'message': '已解锁工艺优化',
        'L4': sess['results']['L4'],
        'formline_length': sess['results']['formline_length'],
        'profile_choice': qa.get('profile_choice'),
    })


# ─────────────────────────── Clear mesh cache ───────────────────────────

@pp_bp.route('/clear-mesh-cache', methods=['POST'])
def clear_mesh_cache():
    """Clear mesh subdivision cache."""
    import shutil
    if os.path.exists(PP_MESH_CACHE_DIR):
        shutil.rmtree(PP_MESH_CACHE_DIR)
        os.makedirs(PP_MESH_CACHE_DIR, exist_ok=True)
    return jsonify({'success': True, 'message': '网格缓存已清除'})


# ─────────────────────────── Database: tubes ───────────────────────────

@pp_bp.route('/tubes', methods=['GET'])
def tubes_list():
    """List all saved tube analyses (with enriched metadata).

    查询参数 ``origin``：
      - ``pp``（工艺优化页用）：只列「管件模型分析」页保存的分析；
      - ``entity``：只列一体化流水线「导入主流程」写进来的；
      - 省略：全部（「管件模型分析」的历史列表就是全部）。
    """
    search = request.args.get('search', '')
    origin = (request.args.get('origin') or '').strip() or None
    try:
        df = list_database_tubes(PP_TUBE_DB, origin=origin)
        if search:
            mask = df['显示名称'].str.contains(search, case=False, na=False) | \
                df['文件名'].str.contains(search, case=False, na=False)
            df = df[mask]
        return jsonify({
            'success': True,
            'tubes': df.to_dict(orient='records'),
            'total': len(df),
            'origin': origin or 'all',
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@pp_bp.route('/tubes/<int:tube_id>', methods=['GET'])
def tubes_get(tube_id):
    """Get a single tube by ID (legacy, mostly diagnostic)."""
    try:
        result = get_tube_by_id(tube_id, PP_TUBE_DB)
        if result is None:
            return jsonify({'success': False, 'message': '未找到记录'}), 404
        return jsonify({'success': True, 'tube': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@pp_bp.route('/tubes/load', methods=['POST'])
def tubes_load():
    """Load a saved tube from DB into a fresh session.

    The restored session is a 1:1 mirror of an upload-analysis session,
    so the front-end's normal post-analysis flow (``/session-info`` →
    render charts + 3D + stats) works without any special-casing.
    """
    data = request.get_json(silent=True) or {}
    tube_id = data.get('tube_id')
    if not tube_id:
        return jsonify({'success': False, 'message': '缺少tube_id'}), 400

    try:
        record = get_full_analysis(int(tube_id), PP_TUBE_DB)
        if record is None:
            return jsonify({'success': False, 'message': '未找到记录'}), 404

        results = record.get('results')
        if not isinstance(results, dict):
            return jsonify({
                'success': False,
                'message': '记录缺少完整分析数据，请重新上传分析后保存。',
            }), 400

        results['from_database'] = True
        results.setdefault('is_bended', False)
        results.setdefault('bend_angle', 0)
        results.setdefault('L4', 0)
        results.setdefault('segments', record.get('segments') or {})

        # 一体化流水线上传入库的记录只有 sections_full（测量结束直接 pickle），
        # 这里把逐站记录补出来：否则下面的 QA 拿到空的形线，页面就会显示
        # QA_INVALID_INPUT「基础数据不合法（empty_profile）」。
        try:
            from core.pp.section_records import ensure_results_records
            ensure_results_records(results)
        except Exception as _rec_err:  # noqa: BLE001 - 补记录失败不阻塞加载
            logger.warning("tubes_load: 逐站记录重建失败 (err=%s)", _rec_err)

        # 顺手把列表页那一列（centerline_length）修好：老记录存的是 0。
        try:
            _length = float(results.get('centerline_length') or 0.0)
            if _length > 0 and not float(record.get('centerline_length') or 0.0):
                update_centerline_length(int(tube_id), _length, PP_TUBE_DB)
        except Exception as _len_err:  # noqa: BLE001
            logger.debug("tubes_load: centerline_length 回填跳过 (%s)", _len_err)

        # 迁移兼容：旧 DB 记录没有 centerline_endpoint_length 字段，且 actual_z_length
        # 是 mesh Z 跨度（对弯管会显著偏短）。如果能从保存的中心线点两端点重算
        # 端到端长度，就把旧字段同步为新语义，避免后续流程拿到错误值。
        if 'centerline_endpoint_length' not in results:
            try:
                pts = results.get('center_line', {}).get('points') or []
                if len(pts) >= 2:
                    p0 = np.asarray(pts[0], dtype=float)
                    pn = np.asarray(pts[-1], dtype=float)
                    if np.all(np.isfinite(p0)) and np.all(np.isfinite(pn)):
                        ep = float(np.linalg.norm(pn - p0))
                        if np.isfinite(ep) and ep >= 0:
                            results['centerline_endpoint_length'] = round(ep, 2)
                            # 把 actual_z_length 也升级为新语义（端到端长度），
                            # 保持和 stl_engine 当前输出完全一致
                            results['actual_z_length'] = round(ep, 2)
            except Exception as _mig_err:
                logger.warning("tubes_load: 旧记录端到端长度迁移失败，沿用原 actual_z_length (err=%s)", _mig_err)

        ready_for_tab2 = True
        session_id = f"db_{tube_id}"
        with _session_lock:
            _pp_sessions[session_id] = {
                'algorithm': results.get('algorithm', 'A'),
                'sparse_step_mm': results.get('sparse_step_mm', 25.0),
                'file_path': record.get('source_file_path') or '',
                'file_type': (record.get('source_file_type') or '').lower(),
                'original_filename': record.get('display_name') or record.get('file_name') or '',
                'progress': {
                    'pct': 100, 'message': '已从数据库加载',
                    'done': True, 'error': None,
                },
                'results': results,
                'ready_for_tab2': ready_for_tab2,
                'tube_id': int(tube_id),
            }

        try:
            if not results.get('qa'):
                from core.pp.profile_qa.pipeline import initialize_qa_from_records
                wt = results.get('wall_thickness_data') or results.get('centerline_perimeter_data') or []
                results['qa'] = initialize_qa_from_records(
                    wt,
                    centerline_length=results.get('centerline_length'),
                    profile_source='stp_raw',
                    original_filename=record.get('display_name') or record.get('file_name') or '',
                )
        except Exception as qa_exc:
            logger.warning("PP QA on DB load failed: %s", qa_exc)

        return jsonify({
            'success': True,
            'session_id': session_id,
            'tube_id': int(tube_id),
            'display_name': record.get('display_name'),
            'file_name': record.get('file_name'),
            'algorithm': results.get('algorithm', 'A'),
            'centerline_length': results.get('centerline_length', 0),
            'actual_z_length': results.get('actual_z_length', 0),
            'centerline_endpoint_length': results.get('centerline_endpoint_length', results.get('actual_z_length', 0)),
            'sections_count': len(results.get('sections_full') or []),
            'ready_for_tab2': ready_for_tab2,
        })
    except Exception as e:
        logger.error(f"PP tubes_load error: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


@pp_bp.route('/tubes/rename', methods=['POST'])
def tubes_rename():
    """Rename a saved tube (updates display_name only, file_name is the
    DB key and stays intact)."""
    data = request.get_json(silent=True) or {}
    tube_id = data.get('tube_id')
    new_name = (data.get('name') or '').strip()
    if not tube_id:
        return jsonify({'success': False, 'message': '缺少tube_id'}), 400
    if not new_name:
        return jsonify({'success': False, 'message': '新名称不能为空'}), 400
    try:
        ok = rename_tube(int(tube_id), new_name, PP_TUBE_DB)
        if not ok:
            return jsonify({'success': False, 'message': '未找到记录或更新失败'}), 404
        return jsonify({'success': True, 'tube_id': int(tube_id), 'display_name': new_name})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@pp_bp.route('/tubes/delete', methods=['POST'])
def tubes_delete():
    """Delete multiple tubes from DB."""
    data = request.get_json(silent=True) or {}
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'success': False, 'message': '缺少ids'}), 400

    try:
        deleted = delete_multiple_tube_data(ids, PP_TUBE_DB)
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@pp_bp.route('/tubes/<int:tube_id>/ack-review', methods=['POST'])
def tubes_ack_review(tube_id):
    """Operator override for minor measurement-review flags on one tube."""
    data = request.get_json(silent=True) or {}
    note = str(data.get('note') or '').strip()
    try:
        if not acknowledge_review(int(tube_id), note=note, db_path=PP_TUBE_DB):
            return jsonify({'success': False, 'message': '管件不存在或没有分析数据'}), 404
        record = get_full_analysis(int(tube_id), db_path=PP_TUBE_DB)
        from api.rfm import measurement_review_summary
        summary = measurement_review_summary((record or {}).get('results') or {})
        return jsonify({'success': True, 'tube_id': int(tube_id), 'review': summary})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@pp_bp.route('/tubes/save', methods=['POST'])
def tubes_save():
    """Save the current session's full analysis to the DB.

    Persists the complete ``results`` payload (sections_full, sparse
    sections, mesh_data, bounding_box, etc.) so a subsequent ``/tubes/
    load`` can rehydrate a session that behaves identically to a fresh
    upload + analysis.
    """
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    display_name = (data.get('display_name') or '').strip() or None
    manual_segments = data.get('manual_segments') or None
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400

    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '分析尚未完成'}), 400

    results = sess['results']

    qa = results.get('qa')
    if qa:
        try:
            from core.pp.profile_qa.pipeline import (
                apply_qa_lengths_to_results,
                choose_profile,
                working_to_wall_thickness_records,
            )
            clen = process_length(results) or None
            choice = data.get('profile_choice') or qa.get('profile_choice')
            qa = choose_profile(qa, choice, centerline_length=clen)
            overlay = working_to_wall_thickness_records(qa, results.get('wall_thickness_data') or [])
            if not overlay:
                return jsonify({
                    'success': False,
                    'code': 'QA_PROFILE_EMPTY',
                    'message': '所选形线为空，不能保存到后续工艺规划。',
                }), 409
            results['qa'] = qa
            results['wall_thickness_data'] = overlay
            results['centerline_perimeter_data'] = overlay
            apply_qa_lengths_to_results(results, qa)
            sess['results'] = results
        except Exception as exc:
            from core.pp.profile_qa.state import QaStateError
            if isinstance(exc, QaStateError):
                return jsonify({'success': False, **exc.as_dict()}), 409
            logger.exception("tubes_save profile commit failed")
            return jsonify({'success': False, 'code': 'QA_INTERNAL_ERROR', 'message': str(exc)}), 500

    # Bundle the user-confirmed wall-thickness segmentation alongside the
    # auto-analysis. This is what "把长度-壁厚对应关系" actually persists.
    if manual_segments:
        try:
            results = dict(results)
            results['manual_segments'] = manual_segments

            # If the front-end provides the finalized per-length wall-thickness
            # curve, upgrade wall_thickness_data/centerline_perimeter_data so
            # downstream optimization consumes the user-confirmed values.
            final_curve = manual_segments.get('final_curve') if isinstance(manual_segments, dict) else None
            perimeter_curve = manual_segments.get('perimeter_curve') if isinstance(manual_segments, dict) else None
            if isinstance(final_curve, list) and final_curve:
                sec_full = results.get('sections_full') or []
                xs = []
                ps = []
                if isinstance(perimeter_curve, list) and perimeter_curve:
                    for pt in perimeter_curve:
                        try:
                            s = float(pt.get('s', 0))
                            per = float(pt.get('perimeter', 0))
                        except (TypeError, ValueError):
                            continue
                        xs.append(s)
                        ps.append(per)

                    # The confirmed process curve must not overwrite the
                    # measured section perimeter/geometry used by RFM shaping.
                for sf in ([] if xs else results.get('sections_full') or []):
                    cl = sf.get('cumulative_length')
                    per = sf.get('perimeter')
                    if cl is None or per is None:
                        continue
                    try:
                        xs.append(float(cl))
                        ps.append(float(per))
                    except (TypeError, ValueError):
                        continue

                wt_records = []
                if xs and ps:
                    order = np.argsort(xs)
                    x_arr = np.asarray(xs, dtype=float)[order]
                    p_arr = np.asarray(ps, dtype=float)[order]
                    for pt in final_curve:
                        try:
                            s = float(pt.get('s', 0))
                            t = pt.get('wall_thickness')
                            t = float(t) if t is not None else None
                        except (TypeError, ValueError):
                            continue
                        per = float(np.interp(s, x_arr, p_arr))
                        wt_records.append({
                            '中心线长度(mm)': round(s, 2),
                            '外径周长(mm)': round(per, 2),
                            '壁厚(mm)': round(t, 3) if t is not None else None,
                        })
                else:
                    for pt in final_curve:
                        try:
                            s = float(pt.get('s', 0))
                            t = pt.get('wall_thickness')
                            t = float(t) if t is not None else None
                        except (TypeError, ValueError):
                            continue
                        wt_records.append({
                            '中心线长度(mm)': round(s, 2),
                            '外径周长(mm)': 0.0,
                            '壁厚(mm)': round(t, 3) if t is not None else None,
                        })

                if wt_records:
                    # Keep the latest QA key-section dimensions when wall
                    # thickness confirmation rebuilds the process rows.
                    from core.pp.profile_qa.key_section import records_with_section_sizes
                    wt_records = records_with_section_sizes(wt_records, results.get('sections_full'))
                    results['wall_thickness_data'] = wt_records
                    results['centerline_perimeter_data'] = wt_records
            sess['results'] = results
        except Exception as _enrich_err:
            logger.exception(
                "tubes_save: manual_segments 富化失败，将回退到原始 results 保存 (err=%s)",
                _enrich_err,
            )
    apply_grind_allowance(results, data.get('grind_allowance_mm'))
    sess['results'] = results
    original_name = sess.get('original_filename') or 'unknown'
    src_path = sess.get('file_path') or ''
    src_type = (sess.get('file_type') or os.path.splitext(original_name)[1].lstrip('.') or '').lower()
    file_hash = ''

    # Copy the source file into a permanent location so subsequent loads
    # can re-display the original mesh even after the upload cache is purged.
    saved_path = None
    if src_path and os.path.exists(src_path):
        try:
            os.makedirs(PP_TUBE_SAVED_DIR, exist_ok=True)
            safe_orig = secure_filename(original_name) or 'tube'
            unique_prefix = uuid.uuid4().hex[:8]
            saved_path = os.path.join(
                PP_TUBE_SAVED_DIR, f"{unique_prefix}_{safe_orig}"
            )
            shutil.copy2(src_path, saved_path)
            try:
                import hashlib
                h = hashlib.sha256()
                with open(saved_path, 'rb') as fh:
                    for chunk in iter(lambda: fh.read(1 << 16), b''):
                        h.update(chunk)
                file_hash = h.hexdigest()
            except Exception:
                file_hash = ''
        except Exception as exc:
            logger.warning("复制源文件失败，将跳过源文件保存: %s", exc)
            saved_path = None

    try:
        tube_id, err = save_full_analysis(
            file_name=original_name,
            display_name=display_name or original_name,
            source_file_path=saved_path or '',
            source_file_type=src_type,
            file_hash=file_hash,
            payload=results,
            db_path=PP_TUBE_DB,
        )
        if tube_id < 0:
            logger.error("tubes_save: save_full_analysis failed (db=%s): %s", PP_TUBE_DB, err)
            return jsonify({
                'success': False,
                'message': f'保存失败：{err or "未知错误"}',
            }), 500
        return jsonify({
            'success': True,
            'message': '保存成功',
            'tube_id': tube_id,
            'display_name': display_name or original_name,
        })
    except Exception as e:
        logger.exception("tubes_save unexpected error")
        return jsonify({'success': False, 'message': f'保存异常：{type(e).__name__}: {e}'}), 500


# ─────────────────────────── TAB2: Wall Thickness Segments ───────────────────────────

@pp_bp.route('/thickness-segments', methods=['GET'])
def thickness_segments():
    """Auto-detect wall-thickness segmentation from the current analysis.

    Returns a 1mm-sampled curve plus an initial suggested TL/T breakdown
    that the front-end can present for visual correction.
    """
    session_id = request.args.get('id') or request.args.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '分析尚未完成'}), 400

    sections_full = sess['results'].get('sections_full') or []
    detection = detect_wall_thickness_segments(sections_full)
    if 'error' in detection:
        return jsonify({'success': False, 'message': detection['error']}), 400

    return jsonify({
        'success': True,
        'session_id': session_id,
        **detection,
    })


# ─────────────────────────── TAB2: Manual Input ───────────────────────────

@pp_bp.route('/save-manual-params', methods=['POST'])
def save_manual_params():
    """Persist the user-corrected wall-thickness segmentation.

    Accepts the canonical TL/T values produced by the interactive curve
    editor in ``frontend/src/views/upload/ManualInput.vue``. Generates a
    1 mm-step thickness curve, fuses it with the previously analysed
    perimeter samples and stores the result in the session.
    """
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400

    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '会话不存在或分析尚未完成'}), 400

    def _f(key: str) -> float:
        try:
            return float(data.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    tl_t_values = {
        'TL1': _f('TL1'), 'TLM1': _f('TLM1'),
        'TL2': _f('TL2'), 'TLM2': _f('TLM2'),
        'TL3': _f('TL3'), 'TLM3': _f('TLM3'),
        'TL4': _f('TL4'),
        'T1': _f('T1'), 'T2': _f('T2'),
        'T3': _f('T3'), 'T4': _f('T4'),
    }

    # Cascading zero rule: an interior zero collapses everything after it
    cascade_keys = ['TL1', 'TLM1', 'TL2', 'TLM2', 'TL3', 'TLM3', 'TL4']
    collapse_from = None
    for i, k in enumerate(cascade_keys):
        if tl_t_values[k] <= 0 and i > 0:
            collapse_from = i
            break
    if collapse_from is not None:
        for k in cascade_keys[collapse_from:]:
            tl_t_values[k] = 0.0

    total_input_length = sum(tl_t_values[k] for k in cascade_keys)

    # Validation
    results = sess['results']
    # 用修正后的中心线长度（图纸长度）校验 TL 之和，不用 actual_z_length。
    model_length = process_length(results) or 0.0
    errors = []
    try:
        from core.pp.profile_qa.wall_thickness_confirm import validate_wall_thickness
        wt_check = validate_wall_thickness(tl_t_values)
        if not wt_check.get('ok'):
            errors.extend(wt_check.get('errors') or [wt_check.get('message') or '壁厚确认数据不完整'])
    except Exception:
        if tl_t_values['TL1'] <= 0:
            errors.append('请填写壁厚 T1 对应的长度 TL1。')
        if tl_t_values['T1'] <= 0:
            errors.append('T1 壁厚必须大于 0')
    if tl_t_values['T1'] <= 0 and 'T1 壁厚必须大于 0' not in errors:
        errors.append('T1 壁厚必须大于 0')
    if tl_t_values['TLM1'] > 0 and tl_t_values['T2'] <= 0:
        errors.append('当 TLM1 ≠ 0 时，T2 必须有值')
    if tl_t_values['TLM2'] > 0 and tl_t_values['T3'] <= 0:
        errors.append('当 TLM2 ≠ 0 时，T3 必须有值')
    if tl_t_values['TLM3'] > 0 and tl_t_values['T4'] <= 0:
        errors.append('当 TLM3 ≠ 0 时，T4 必须有值')
    if model_length > 0:
        diff = abs(total_input_length - model_length)
        if diff > 5:
            errors.append(
                f'总长度 {total_input_length:.1f} mm 与模型中心线长度 '
                f'{model_length:.1f} mm 相差 {diff:.1f} mm，超过 ±5mm'
            )
    if errors:
        return jsonify({
            'success': False,
            'errors': errors,
            'total_input_length': total_input_length,
        })

    curve = thickness_curve_from_tl_t(tl_t_values, step_mm=1.0)
    lengths_grid = np.asarray(curve['length_mm'], dtype=float)
    thickness_grid = np.asarray(curve['thickness_mm'], dtype=float)

    # Interpolate the perimeter onto the new length grid, falling back to
    # the original ``sections_full`` if no curated curve exists yet.
    sections_full = results.get('sections_full') or []
    orig_lengths = []
    orig_perims = []
    for sf in sections_full:
        cl = sf.get('cumulative_length')
        per = sf.get('perimeter')
        if cl is None or per is None:
            continue
        orig_lengths.append(float(cl))
        orig_perims.append(float(per))

    if orig_lengths:
        outer_circ = np.interp(
            lengths_grid,
            np.asarray(orig_lengths, dtype=float),
            np.asarray(orig_perims, dtype=float),
        )
    else:
        outer_circ = np.zeros_like(lengths_grid)

    wt_records = [
        {
            '中心线长度(mm)': round(float(l), 2),
            '壁厚(mm)': round(float(t), 3),
            '外径周长(mm)': round(float(p), 2),
        }
        for l, t, p in zip(lengths_grid, thickness_grid, outer_circ)
    ]

    sess['results']['wall_thickness_data'] = wt_records
    sess['results']['centerline_perimeter_data'] = wt_records
    sess['results']['segments'] = tl_t_values
    sess['results']['centerline_length'] = total_input_length
    sess['manual_params'] = tl_t_values

    return jsonify({
        'success': True,
        'message': '数据已保存',
        'total_length': total_input_length,
        'data_points': len(wt_records),
        'segments': tl_t_values,
    })


@pp_bp.route('/upload-manual-file', methods=['POST'])
def upload_manual_file():
    """Upload CSV/XLSX file with wall thickness data."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未上传文件'}), 400

    file = request.files['file']
    session_id = request.form.get('session_id')

    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400

    if not file.filename:
        return jsonify({'success': False, 'message': '文件名为空'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.csv', '.xlsx', '.xls'):
        return jsonify({'success': False, 'message': '请上传CSV或XLSX文件'}), 400

    try:
        import pandas as pd
        if ext == '.csv':
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)

        # Validate required columns
        required = ['中心线长度(mm)', '外径周长(mm)']
        for col in required:
            if col not in df.columns:
                return jsonify({'success': False, 'message': f'缺少必需列: {col}'}), 400

        # Convert to list[dict]
        data_records = df.to_dict(orient='records')

        with _session_lock:
            sess = _pp_sessions.get(session_id)
        if not sess or not sess.get('results'):
            return jsonify({'success': False, 'message': '会话不存在'}), 400

        sess['results']['wall_thickness_data'] = data_records
        sess['results']['centerline_perimeter_data'] = data_records

        total_length = df['中心线长度(mm)'].max() if not df.empty else 0
        sess['results']['centerline_length'] = total_length

        return jsonify({
            'success': True,
            'message': '文件已上传并解析',
            'data_points': len(data_records),
            'total_length': total_length,
        })
    except Exception as e:
        logger.error(f"PP upload_manual_file error: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'文件解析失败: {str(e)}'}), 500


@pp_bp.route('/apply-to-tab3', methods=['POST'])
def apply_to_tab3():
    """Mark session ready for TAB3 optimization."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400

    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '分析尚未完成'}), 400

    if _measurement_blocks_optimization(sess['results']):
        return jsonify({'success': False, 'message': '存在缺测或未通过校核的截面，不能用于自动工艺优化。'}), 400

    sess['ready_for_tab3'] = True
    return jsonify({'success': True, 'message': '已解锁工艺参数优化'})


# ─────────────────────────── TAB3: Optimization ───────────────────────────

@pp_bp.route('/optimize/recommend', methods=['GET'])
def optimize_recommend():
    """根据已加载管件的几何参数，自动推荐最佳工艺路线。"""
    session_id = request.args.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '会话不存在或分析尚未完成'}), 400
    results = sess['results']

    cl = float(process_length(results) or 0)
    wt = results.get('wall_thickness_data') or []
    perimeters = [float(r.get('外径周长(mm)', 0)) for r in wt if float(r.get('外径周长(mm)', 0)) > 0]
    max_d = max(perimeters) / np.pi if perimeters else 0
    min_d = min(perimeters) / np.pi if perimeters else 0
    L4 = float(results.get('L4') or 0)

    rec = recommend_pp_method(cl, max_d, min_d, L4)
    return jsonify({'success': True, **rec})

# In-memory optimization task tracking
_opt_tasks = {}
_opt_task_lock = threading.Lock()


@pp_bp.route('/optimize', methods=['POST'])
def run_optimize():
    """Start process parameter optimization (TAB3)."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    population_size = data.get('population_size', 300)
    generations = data.get('generations', 200)
    repetitions = data.get('repetitions', 1)
    add1 = data.get('add1', 10)
    add2 = data.get('add2', 10)
    add3 = data.get('add3', 0)
    material_allowance = data.get('material_allowance', 1.3)
    pp_method = data.get('pp_method', 'Taper+TP抽+扩口')
    d_precision = data.get('d_precision', 0)
    t_precision = data.get('t_precision', 1)
    l_precision = data.get('l_precision', 0)
    raw_weights = data.get('weights')
    weights = None
    if isinstance(raw_weights, dict):
        weights = {}
        for k, v in raw_weights.items():
            try:
                weights[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
        if not weights:
            weights = None

    # Coerce numeric params: handle Vue3 reactive proxy objects, nested dicts, etc.
    def _num(val, default):
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, dict):
            return default
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return default

    population_size = _num(population_size, 300)
    generations = _num(generations, 200)
    repetitions = _num(repetitions, 1)
    add1 = _num(add1, 10)
    add2 = _num(add2, 10)
    add3 = _num(add3, 0)
    try:
        material_allowance = float(material_allowance)
        if material_allowance <= 0:
            material_allowance = 1.3
    except (TypeError, ValueError):
        material_allowance = 1.3
    d_precision = _num(d_precision, 1)   # default 1 (小数点后1位)
    t_precision = _num(t_precision, 1)
    l_precision = _num(l_precision, 0)

    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400

    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '分析会话不存在'}), 400

    if _measurement_blocks_optimization(sess['results']):
        return jsonify({'success': False, 'message': '存在缺测或未通过校核的截面，不能用于自动工艺优化。'}), 400

    results = sess['results']

    # Build optimization input. Legacy optimizers require ``segments`` to
    # carry valid T1..T4 / D1 / D3 / TL4, which the analysis pipeline may
    # not populate when the user finalized values via the manual panel.
    # Derive a usable segments dict on the fly here.
    derived_segments = _build_segments_for_optimization(results)
    sess['results']['segments'] = derived_segments

    volume = float(results.get('volume') or 0)
    if volume <= 0:
        volume = _estimate_volume_from_wt(results)
        if volume > 0:
            sess['results']['volume'] = round(volume, 2)

    analysis_input = {
        'wall_thickness_data': results.get('wall_thickness_data', []),
        'segments': derived_segments,
        'centerline_length': process_length(results),
        'volume': volume,
        'L4': float(results.get('L4') or 0),
    }

    task_id = f"opt_{session_id}_{int(time.time())}"
    with _opt_task_lock:
        _opt_tasks[task_id] = {
            'progress': {'pct': 0, 'message': '启动优化...', 'done': False, 'error': None},
            'result': None,
        }

    def run_optimization():
        try:
            def progress_cb(pct, info):
                msg = info.get('status', '') if isinstance(info, dict) else str(info)
                with _opt_task_lock:
                    task = _opt_tasks.get(task_id)
                    if task:
                        task['progress'] = {'pct': pct, 'message': msg, 'done': False, 'error': None}

            opt_result = optimize_process_parameters_by_method(
                analysis_input,
                pp_method=pp_method,
                progress_callback=progress_cb,
                population_size=population_size,
                generations=generations,
                repetitions=repetitions,
                d_precision=d_precision,
                t_precision=t_precision,
                l_precision=l_precision,
                add1=add1,
                add2=add2,
                add3=add3,
                weights=weights,
                material_allowance=material_allowance,
            )

            with _opt_task_lock:
                task = _opt_tasks.get(task_id)
                if task:
                    task['result'] = opt_result
                    task['progress'] = {
                        'pct': 100, 'message': '优化完成',
                        'done': True, 'error': opt_result.get('error')
                    }
                    # Store in PP session for later use
                    sess['tab3_result'] = opt_result

        except Exception as e:
            logger.error(f"PP optimization error: {e}", exc_info=True)
            with _opt_task_lock:
                task = _opt_tasks.get(task_id)
                if task:
                    task['progress'] = {'pct': 0, 'message': str(e), 'done': True, 'error': str(e)}

    thread = threading.Thread(target=run_optimization, daemon=True)
    thread.start()

    return jsonify({'success': True, 'task_id': task_id})


@pp_bp.route('/optimize-status', methods=['GET'])
def optimize_status():
    """Poll optimization task progress."""
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'success': False, 'message': '缺少task_id'}), 400
    with _opt_task_lock:
        task = _opt_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'message': '任务不存在'}), 404
    return jsonify({'success': True, 'progress': _sanitize_for_json(task['progress'])})


def _sanitize_for_json(obj):
    """递归将所有 numpy 类型转换为 Python 原生类型，避免 jsonify 序列化失败。"""
    import numpy as np
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return [_sanitize_for_json(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(x) for x in obj]
    return obj


@pp_bp.route('/optimize-result', methods=['GET'])
def optimize_result():
    """Get final optimization result."""
    task_id = request.args.get('task_id')
    if not task_id:
        return jsonify({'success': False, 'message': '缺少task_id'}), 400
    with _opt_task_lock:
        task = _opt_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'message': '任务不存在'}), 404
    if not task.get('result'):
        return jsonify({'success': False, 'message': '优化尚未完成'}), 400

    result = _sanitize_for_json(task['result'])
    return jsonify({'success': True, 'result': result})


@pp_bp.route('/optimize-methods', methods=['GET'])
def optimize_methods():
    """Return available process-optimization methods (legacy-compatible)."""
    return jsonify({'success': True, 'methods': available_pp_methods()})


@pp_bp.route('/optimize-recalc', methods=['POST'])
def optimize_recalc():
    """Recalculate one edited solution by method + manual params."""
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    pp_method = data.get('pp_method')
    params = data.get('params') or {}
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    if not pp_method:
        return jsonify({'success': False, 'message': '缺少pp_method'}), 400
    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '分析会话不存在'}), 400

    if _measurement_blocks_optimization(sess['results']):
        return jsonify({'success': False, 'message': '存在缺测或未通过校核的截面，不能用于自动工艺优化。'}), 400

    results = sess['results']
    derived_segments = _build_segments_for_optimization(results)
    sess['results']['segments'] = derived_segments
    volume = float(results.get('volume') or 0)
    if volume <= 0:
        volume = _estimate_volume_from_wt(results)
        if volume > 0:
            sess['results']['volume'] = round(volume, 2)
    analysis_input = {
        'wall_thickness_data': results.get('wall_thickness_data', []),
        'segments': derived_segments,
        'centerline_length': process_length(results),
        'volume': volume,
        'L4': float(results.get('L4') or 0),
    }
    def _n(val, default):
        if isinstance(val, (int, float)):
            return int(val)
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return default

    d_precision = _n(data.get('d_precision'), 1)
    t_precision = _n(data.get('t_precision'), 1)
    l_precision = _n(data.get('l_precision'), 0)
    add1_val = _n(data.get('add1'), 10)
    add2_val = _n(data.get('add2'), 10)
    add3_val = _n(data.get('add3'), 0)
    try:
        material_allowance = float(data.get('material_allowance', 1.3))
        if material_allowance <= 0:
            material_allowance = 1.3
    except (TypeError, ValueError):
        material_allowance = 1.3

    raw_weights = data.get('weights')
    weights = None
    if isinstance(raw_weights, dict):
        weights = {}
        for k, v in raw_weights.items():
            try:
                weights[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
        if not weights:
            weights = None

    recalc = recalc_solution_by_method(
        analysis_input,
        pp_method=pp_method,
        params=params,
        d_precision=d_precision,
        t_precision=t_precision,
        l_precision=l_precision,
        add1=float(add1_val),
        add2=float(add2_val),
        add3=float(add3_val),
        weights=weights,
        material_allowance=material_allowance,
    )
    if not recalc.get('success'):
        return jsonify({'success': False, 'message': recalc.get('error', '重算失败')}), 400
    return jsonify({'success': True, **recalc})


# ---------------------------------------------------------------------------
# 工艺设计数据库 API（pp_design.db）
# ---------------------------------------------------------------------------


def _evaluate_segments_for_method(pp_method: str, solution: dict) -> dict:
    """为某个工艺方案产出"分段评估"信息（与老程序 show_process_parameters 等价）。

    返回字段：
        sections: list of {key, title, items, evals}
          - items：每段要展示的具体参数 [{label, value, key, editable}]
          - evals：每段的评估提示 [{level, text}]  level ∈ success/warning/error
    """
    method = (pp_method or '').strip()
    s = solution or {}

    def get(k, default=0):
        try:
            v = s.get(k)
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def round2(v):
        try:
            return round(float(v), 2)
        except Exception:
            return 0

    # 共同的"素材基础参数"块
    base_section = {
        'key': 'material',
        'title': '1. 素材基础参数',
        'items': [
            {'label': '外径 D0 (mm)', 'value': round2(get('D0')), 'key': 'D0', 'editable': True},
            {'label': '壁厚 T0 (mm)', 'value': round2(get('T0')), 'key': 'T0', 'editable': True},
            {'label': '长度 L0 (mm)', 'value': round2(get('L0')), 'key': 'L0', 'editable': True},
        ],
        'evals': [],
    }
    sections = [base_section]

    # area_change_rate 可能是 number 也可能是 {area_change | area_change1 | area_change2}
    # 注意：不能用 `or {}`，否则数值 0 会被误判为空
    acr = s.get('area_change_rate')
    if acr is None:
        acr = {}
    elif isinstance(acr, (int, float)):
        acr = {'area_change': float(acr)}
    elif not isinstance(acr, dict):
        acr = {}

    L1 = get('L1')
    L2 = get('L2')
    Ln = get('Ln')

    def length_warn(name, val, lo=20, hi=150):
        evals = []
        if val > hi:
            evals.append({'level': 'error', 'text': f'⚠️ {name}异常大：{val:.2f}mm（标准{hi}mm以内）'})
        elif 0 < val < lo:
            evals.append({'level': 'error', 'text': f'⚠️ {name}异常小：{val:.2f}mm'})
        return evals

    def area_warn(rate, lo=20, hi=50, label=''):
        """rate 为百分比数值；label 如 'T1' / '普通抽' 用于多组变化率标注。"""
        if rate is None:
            return []
        try:
            rate = float(rate)
        except Exception:
            return []
        tag = f'{label} ' if label else ''
        if rate < lo:
            return [{'level': 'warning', 'text': f'⚠️ {tag}截面积变化率 ({rate:.2f}%) 小于{lo}%，成型可能不稳定'}]
        if rate > hi:
            return [{'level': 'warning', 'text': f'⚠️ {tag}截面积变化率 ({rate:.2f}%) 大于{hi}%，成型难度较大'}]
        return [{'level': 'success', 'text': f'✅ {tag}截面积变化率正常 ({rate:.2f}%，理想 {lo}% ~ {hi}%）'}]

    def area_warns_from_acr(prefer_keys=None, labels=None):
        """从 solution.area_change_rate 提取并校验。

        兼容：
          - number
          - {area_change}
          - {area_change1, area_change2, area_change3}  （普通抽+扩口 等）
        """
        if isinstance(acr, (int, float)):
            return area_warn(acr)
        if not isinstance(acr, dict) or not acr:
            return [{'level': 'info', 'text': '- 截面积变化率：暂无数据'}]

        keys = prefer_keys
        if not keys:
            if any(k in acr for k in ('area_change1', 'area_change2', 'area_change3')):
                keys = ('area_change1', 'area_change2', 'area_change3')
            elif 'area_change' in acr:
                keys = ('area_change',)
            else:
                keys = tuple(acr.keys())

        label_map = labels or {
            'area_change': '',
            'area_change1': 'T1',
            'area_change2': 'T2',
            'area_change3': 'T3',
        }
        out = []
        for k in keys:
            if k not in acr or acr[k] is None:
                continue
            out.extend(area_warn(acr[k], label=label_map.get(k, k)))
        if not out:
            out.append({'level': 'info', 'text': '- 截面积变化率：暂无数据'})
        return out

    def ext_warn(ratio, lo=0, hi=30):
        if ratio is None:
            return []
        try:
            ratio = float(ratio)
        except Exception:
            return []
        if ratio > hi:
            return [{'level': 'warning', 'text': f'⚠️ 外延伸率 ({ratio:.2f}%) 大于{hi}%'}]
        return [{'level': 'success', 'text': f'✅ 外延伸率正常 ({ratio:.2f}%)'}]

    # extension_ratio 可能是 number / {outer} / {outer1, outer2}
    ext_raw = s.get('extension_ratio') or s.get('outer_extension_ratio')
    if isinstance(ext_raw, (int, float)):
        ext_map = {'outer': float(ext_raw)}
    elif isinstance(ext_raw, dict):
        ext_map = ext_raw
    else:
        ext_map = {}

    if method == 'Taper+TP抽+扩口':
        # 基本型走 ProcessOptimizer，L1/L2/L3 语义与 _calculate_fitness 一致：
        #   L1 = 扩口段 (D0→D1, 扩口率) / L2 = TP抽段 (D0→D2, 截面积变化率) /
        #   L3 = Taper段 (D2→D3, 外延伸率)。三段必须分别展示，否则 TP抽段会丢失。
        D1 = get('D1')
        D0v = get('D0')
        D2 = get('D2')
        D3 = get('D3')
        L3 = get('L3')
        # 2. 扩口段
        flaring = ((D1 - D0v) / D0v * 100) if D0v > 0 else 0
        kuokou_evals = []
        if L1 > 0:
            if flaring > 60:
                kuokou_evals.append({'level': 'warning', 'text': f'⚠️ 扩口率较高 ({flaring:.2f}%) ，成型难度较大'})
            else:
                kuokou_evals.append({'level': 'success', 'text': f'✅ 扩口率正常 ({flaring:.2f}%)'})
            kuokou_evals.extend(length_warn('L1', L1))
        else:
            kuokou_evals.append({'level': 'info', 'text': '- 扩口段：无需扩口处理 (L1 = 0)'})
        sections.append({
            'key': 'kuokou', 'title': '2. 扩口段',
            'items': [
                {'label': '大径 D1 (mm)', 'value': round2(D1), 'key': 'D1', 'editable': False},
                {'label': '小径 D0 (mm)', 'value': round2(D0v), 'key': 'D0', 'editable': False},
                {'label': '长度 L1 (mm)', 'value': round2(L1), 'key': 'L1', 'editable': False},
            ],
            'evals': kuokou_evals,
        })
        # 3. TP抽段（截面积变化率 + 长度）
        tp_evals = []
        if L2 > 0:
            tp_evals.extend(area_warns_from_acr(labels={'area_change': 'TP抽'}))
            tp_evals.extend(length_warn('L2', L2))
        else:
            tp_evals.append({'level': 'info', 'text': '- TP抽段：无需 (L2 = 0)'})
        sections.append({
            'key': 'tp', 'title': '3. TP抽段',
            'items': [
                {'label': '大径 D0 (mm)', 'value': round2(D0v), 'key': 'D0', 'editable': False},
                {'label': '小径 D2 (mm)', 'value': round2(D2), 'key': 'D2', 'editable': True},
                {'label': '长度 L2 (mm)', 'value': round2(L2), 'key': 'L2', 'editable': False},
            ],
            'evals': tp_evals,
        })
        # 4. Taper段（外延伸率 + 长度）
        taper_evals = []
        if L3 > 0:
            if ext_map:
                for ek, ev in ext_map.items():
                    taper_evals.extend(ext_warn(ev))
            taper_evals.extend(length_warn('L3', L3, hi=700))
        else:
            taper_evals.append({'level': 'info', 'text': '- Taper段：无需 (L3 = 0)'})
        sections.append({
            'key': 'taper', 'title': '4. Taper段',
            'items': [
                {'label': '大径 D2 (mm)', 'value': round2(D2), 'key': 'D2', 'editable': False},
                {'label': '小径 D3 (mm)', 'value': round2(D3), 'key': 'D3', 'editable': False},
                {'label': '长度 L3 (mm)', 'value': round2(L3), 'key': 'L3', 'editable': False},
            ],
            'evals': taper_evals,
        })

    elif method == '加长Taper+TP抽+扩口':
        # 加长型走 opt_replenish，解含 D20 / outer1 / outer2，L3=L-L1-L4 与 L2 区间重叠，
        # 结构与基本型不同，保留原有两段展示（扩口 + TAPER抽）。
        D1 = get('D1')
        D0v = get('D0')
        flaring = ((D1 - D0v) / D0v * 100) if D0v > 0 else 0
        kuokou_evals = []
        if L1 > 0:
            if flaring > 60:
                kuokou_evals.append({'level': 'warning', 'text': f'⚠️ 扩口率较高 ({flaring:.2f}%) ，成型难度较大'})
            else:
                kuokou_evals.append({'level': 'success', 'text': f'✅ 扩口率正常 ({flaring:.2f}%)'})
            kuokou_evals.extend(length_warn('L1', L1))
        else:
            kuokou_evals.append({'level': 'info', 'text': '- 扩口段：无需扩口处理 (L1 = 0)'})
        sections.append({
            'key': 'kuokou', 'title': '2. 扩口段',
            'items': [
                {'label': '大径 D1 (mm)', 'value': round2(D1), 'key': 'D1', 'editable': False},
                {'label': '小径 D0 (mm)', 'value': round2(D0v), 'key': 'D0', 'editable': False},
                {'label': '长度 L1 (mm)', 'value': round2(L1), 'key': 'L1', 'editable': False},
            ],
            'evals': kuokou_evals,
        })
        # TAPER抽段（截面积变化率 + 长度）
        D2 = get('D2')
        taper_evals = []
        if L2 > 0:
            taper_evals.extend(area_warns_from_acr())
            taper_evals.extend(length_warn('L2', L2))
            if ext_map:
                for ek, ev in ext_map.items():
                    taper_evals.extend(ext_warn(ev))
        else:
            taper_evals.append({'level': 'info', 'text': '- TAPER抽段：无需 (L2 = 0)'})
        sections.append({
            'key': 'taper', 'title': '3. TAPER抽段',
            'items': [
                {'label': '大径 D0 (mm)', 'value': round2(D0v), 'key': 'D0', 'editable': False},
                {'label': '小径 D2 (mm)', 'value': round2(D2), 'key': 'D2', 'editable': True},
                {'label': '长度 L2 (mm)', 'value': round2(L2), 'key': 'L2', 'editable': False},
            ],
            'evals': taper_evals,
        })

    elif method == '普通抽+TP抽+Taper+扩口':
        # 老程序模式 2：四段 [扩口] + [TP抽] + [taper] + [普通抽]
        D1 = get('D1')
        Dn = get('Dn')
        D2 = get('D2')
        D3 = get('D3')
        L3 = get('L3')
        # 扩口
        flaring = ((D1 - Dn) / Dn * 100) if Dn > 0 else 0
        evals1 = []
        if L1 > 0:
            evals1.append({'level': 'success' if flaring <= 60 else 'warning',
                           'text': f'扩口率 {flaring:.2f}%'})
            evals1.extend(length_warn('L1', L1))
        sections.append({
            'key': 'kuokou', 'title': '2. 扩口段',
            'items': [
                {'label': '大径 D1 (mm)', 'value': round2(D1), 'key': 'D1', 'editable': False},
                {'label': '小径 Dn (mm)', 'value': round2(Dn), 'key': 'Dn', 'editable': False},
                {'label': '长度 L1 (mm)', 'value': round2(L1), 'key': 'L1', 'editable': False},
            ],
            'evals': evals1,
        })
        # TP抽
        sections.append({
            'key': 'tp', 'title': '3. TP抽段',
            'items': [
                {'label': '大径 Dn (mm)', 'value': round2(Dn), 'key': 'Dn', 'editable': False},
                {'label': '小径 D2 (mm)', 'value': round2(D2), 'key': 'D2', 'editable': True},
                {'label': '长度 L2 (mm)', 'value': round2(L2), 'key': 'L2', 'editable': False},
            ],
            'evals': area_warns_from_acr(
                prefer_keys=('area_change4',),
                labels={'area_change4': 'TP抽'},
            ),
        })
        # 普通抽
        sections.append({
            'key': 'normal', 'title': '4. 普通抽段',
            'items': [
                {'label': '外径 Dn (mm)', 'value': round2(Dn), 'key': 'Dn', 'editable': False},
                {'label': '裁剪前长度Ln(mm)', 'value': round2(Ln), 'key': 'Ln', 'editable': False},
            ],
            'evals': area_warns_from_acr(
                prefer_keys=('area_change1', 'area_change2', 'area_change3'),
                labels={'area_change1': 'T1', 'area_change2': 'T2', 'area_change3': 'T3'},
            ),
        })
        # Taper段
        sections.append({
            'key': 'taper', 'title': '5. Taper段',
            'items': [
                {'label': '大径 D2 (mm)', 'value': round2(D2), 'key': 'D2', 'editable': False},
                {'label': '小径 D3 (mm)', 'value': round2(D3), 'key': 'D3', 'editable': False},
                {'label': '长度 L3 (mm)', 'value': round2(L3), 'key': 'L3', 'editable': False},
            ],
            'evals': ext_warn(ext_map.get('outer')) + length_warn('L3', L3, hi=700),
        })

    elif method == '仅TP抽':
        sections.append({
            'key': 'tp', 'title': '2. TP抽段',
            'items': [
                {'label': '大径 D0 (mm)', 'value': round2(get('D0')), 'key': 'D0', 'editable': False},
                {'label': '小径 D3 (mm)', 'value': round2(get('D3')), 'key': 'D3', 'editable': False},
                {'label': '长度 L2 (mm)', 'value': round2(L2), 'key': 'L2', 'editable': False},
            ],
            'evals': area_warns_from_acr() + length_warn('L2', L2),
        })

    elif method == '普通抽+TP抽':
        # (1) 普通抽 (2) TP抽
        sections.append({
            'key': 'normal', 'title': '2. 普通抽段',
            'items': [
                {'label': '直径 Dn (mm)', 'value': round2(get('Dn')), 'key': 'Dn', 'editable': False},
                {'label': '裁剪前长度Ln(mm)', 'value': round2(Ln), 'key': 'Ln', 'editable': False},
            ],
            'evals': area_warns_from_acr(
                prefer_keys=('area_change1', 'area_change2', 'area_change3'),
                labels={'area_change1': 'T1', 'area_change2': 'T2', 'area_change3': 'T3'},
            ),
        })
        sections.append({
            'key': 'tp', 'title': '3. TP抽段',
            'items': [
                {'label': '大径 Dn (mm)', 'value': round2(get('Dn')), 'key': 'Dn', 'editable': False},
                {'label': '小径 D2 (mm)', 'value': round2(get('D2') or get('D3')), 'key': 'D2', 'editable': True},
                {'label': '长度 L2 (mm)', 'value': round2(L2), 'key': 'L2', 'editable': True},
            ],
            'evals': area_warns_from_acr(
                prefer_keys=('area_change4',),
                labels={'area_change4': 'TP抽'},
            ) + length_warn('L2', L2),
        })

    elif method == '仅扩口':
        D1 = get('D1')
        D3 = get('D3') or get('D0')
        flaring = ((D1 - D3) / D3 * 100) if D3 > 0 else 0
        sections.append({
            'key': 'kuokou', 'title': '2. 扩口段',
            'items': [
                {'label': '大径 D1 (mm)', 'value': round2(D1), 'key': 'D1', 'editable': False},
                {'label': '小径 D3 (mm)', 'value': round2(D3), 'key': 'D3', 'editable': False},
                {'label': '长度 L1 (mm)', 'value': round2(L1), 'key': 'L1', 'editable': False},
            ],
            'evals': ([{'level': 'success' if flaring <= 60 else 'warning',
                        'text': f'扩口率 {flaring:.2f}%'}] +
                      length_warn('L1', L1)),
        })

    elif method == '普通抽+扩口':
        D1 = get('D1')
        Dn = get('Dn')
        flaring = ((D1 - Dn) / Dn * 100) if Dn > 0 else 0
        # 优化器输出 area_change1/2/3（对应 T1/T2/T3），不是 area_change
        sections.append({
            'key': 'normal', 'title': '2. 普通抽段',
            'items': [
                {'label': '直径 Dn (mm)', 'value': round2(Dn), 'key': 'Dn', 'editable': False},
                {'label': '裁剪前长度Ln(mm)', 'value': round2(Ln), 'key': 'Ln', 'editable': False},
            ],
            'evals': area_warns_from_acr(
                prefer_keys=('area_change1', 'area_change2', 'area_change3'),
                labels={
                    'area_change1': 'T1',
                    'area_change2': 'T2',
                    'area_change3': 'T3',
                },
            ),
        })
        # 扩口
        sections.append({
            'key': 'kuokou', 'title': '3. 扩口段',
            'items': [
                {'label': '大径 D1 (mm)', 'value': round2(D1), 'key': 'D1', 'editable': False},
                {'label': '小径 Dn (mm)', 'value': round2(Dn), 'key': 'Dn', 'editable': False},
                {'label': '长度 L1 (mm)', 'value': round2(L1), 'key': 'L1', 'editable': False},
            ],
            'evals': ([{'level': 'success' if flaring <= 60 else 'warning',
                        'text': f'扩口率 {flaring:.2f}%'}] +
                      length_warn('L1', L1)),
        })

    elif method == 'TP抽+扩口':
        D1 = get('D1')
        D0v = get('D0')
        D2 = get('D2')
        flaring = ((D1 - D0v) / D0v * 100) if D0v > 0 else 0
        sections.append({
            'key': 'kuokou', 'title': '2. 扩口段',
            'items': [
                {'label': '大径 D1 (mm)', 'value': round2(D1), 'key': 'D1', 'editable': False},
                {'label': '小径 D0 (mm)', 'value': round2(D0v), 'key': 'D0', 'editable': False},
                {'label': '长度 L1 (mm)', 'value': round2(L1), 'key': 'L1', 'editable': False},
            ],
            'evals': ([{'level': 'success' if flaring <= 60 else 'warning',
                        'text': f'扩口率 {flaring:.2f}%'}] +
                      length_warn('L1', L1)),
        })
        sections.append({
            'key': 'tp', 'title': '3. TP抽段',
            'items': [
                {'label': '大径 D0 (mm)', 'value': round2(D0v), 'key': 'D0', 'editable': False},
                {'label': '小径 D2 (mm)', 'value': round2(D2), 'key': 'D2', 'editable': True},
                {'label': '长度 L2 (mm)', 'value': round2(L2), 'key': 'L2', 'editable': False},
            ],
            'evals': area_warns_from_acr() + length_warn('L2', L2),
        })

    elif method in ('普通抽+Taper+扩口', '普通抽+Taper'):
        D1 = get('D1')
        Dn = get('Dn')
        D2 = get('D2')
        D3 = get('D3')
        L3 = get('L3')
        # 普通抽：多组截面积（1/2/3）或单值 area_change
        sections.append({
            'key': 'normal', 'title': '2. 普通抽段',
            'items': [
                {'label': '直径 Dn (mm)', 'value': round2(Dn), 'key': 'Dn', 'editable': False},
                {'label': '裁剪前长度Ln(mm)', 'value': round2(Ln), 'key': 'Ln', 'editable': False},
            ],
            'evals': area_warns_from_acr(),
        })
        # Taper：用外延伸率，不用截面积 change2（避免字段错配）
        taper_evals = ext_warn(ext_map.get('outer')) + length_warn('L3', L3, hi=700)
        sections.append({
            'key': 'taper', 'title': '3. Taper段',
            'items': [
                {'label': '大径 D2 (mm)', 'value': round2(D2), 'key': 'D2', 'editable': False},
                {'label': '小径 D3 (mm)', 'value': round2(D3), 'key': 'D3', 'editable': False},
                {'label': '长度 L3 (mm)', 'value': round2(L3), 'key': 'L3', 'editable': False},
            ],
            'evals': taper_evals,
        })
        if method == '普通抽+Taper+扩口':
            flaring = ((D1 - Dn) / Dn * 100) if Dn > 0 else 0
            sections.append({
                'key': 'kuokou', 'title': '4. 扩口段',
                'items': [
                    {'label': '大径 D1 (mm)', 'value': round2(D1), 'key': 'D1', 'editable': False},
                    {'label': '小径 Dn (mm)', 'value': round2(Dn), 'key': 'Dn', 'editable': False},
                    {'label': '长度 L1 (mm)', 'value': round2(L1), 'key': 'L1', 'editable': False},
                ],
                'evals': ([{'level': 'success' if flaring <= 60 else 'warning',
                            'text': f'扩口率 {flaring:.2f}%'}] +
                          length_warn('L1', L1)),
            })

    return {'sections': sections}


def _ring_cross_section_area(d: float, t: float) -> float:
    if d <= 0 or t <= 0:
        return 0.0
    r = d / 2.0
    return math.pi * (r * r - max(r - t, 0.0) ** 2)


def _compute_modified_wt_area_checks(pp_method: str, solution: dict, segments2: dict) -> list:
    """修改后壁厚截面积变化率验算。

    原截面积：D0 × T0（素材）
    修改后各档：Dn × T1/T2/T3（modify_thick 输出的 segments2）
    仅当 Dn、Ln 均有效时验算。
    """
    sol = solution or {}
    seg = segments2 or {}

    def _f(key: str, *, prefer_seg: bool = False) -> float:
        raw = seg.get(key) if prefer_seg else None
        if raw in (None, ''):
            raw = sol.get(key)
        if raw in (None, ''):
            raw = seg.get(key) if not prefer_seg else None
        try:
            return float(raw or 0)
        except (TypeError, ValueError):
            return 0.0

    d0 = _f('D0')
    t0 = _f('T0')
    dn = _f('Dn')
    ln = _f('Ln')
    if dn <= 0 or ln <= 0:
        return []
    area_0 = _ring_cross_section_area(d0, t0)
    if area_0 <= 0:
        return [{'level': 'info', 'text': '- 无法验算：素材截面积无效（请检查 D0、T0）'}]

    checks = []
    for label, tk in (('T1', 'T1'), ('T2', 'T2'), ('T3', 'T3')):
        t = _f(tk, prefer_seg=True)
        if t <= 0:
            checks.append({
                'key': tk,
                'label': label,
                'rate': None,
                'level': 'info',
                'text': f'- {label}：无有效修改后壁厚，跳过验算',
            })
            continue
        area_n = _ring_cross_section_area(dn, t)
        rate = (area_0 - area_n) / area_0 * 100.0
        level = 'success' if 20.0 <= rate <= 50.0 else 'warning'
        tag = f'{label} '
        if rate < 20.0:
            text = f'⚠️ {tag}修改后壁厚截面积变化率 ({rate:.2f}%) 小于20%，成型可能不稳定'
        elif rate > 50.0:
            text = f'⚠️ {tag}修改后壁厚截面积变化率 ({rate:.2f}%) 大于50%，成型难度较大'
        else:
            text = f'✅ {tag}修改后壁厚截面积变化率正常 ({rate:.2f}%，理想 20% ~ 50%)'
        checks.append({
            'key': tk,
            'label': label,
            'rate': round(rate, 2),
            'd': dn,
            't': round(t, 3),
            'level': level,
            'text': text,
        })
    return checks


def _build_alldata_table(solution: dict, segments2: dict, total_length: float) -> list:
    """构造老项目 All_data 风格的总参数表（30 项），按 DESIGN_PARAM_ORDER 排列。"""
    out = []
    seg = segments2 or {}
    sol = solution or {}
    for key in DESIGN_PARAM_ORDER:
        val = ''
        if key in seg and seg[key] not in (None, ''):
            val = seg[key]
        elif key in sol and sol[key] not in (None, ''):
            val = sol[key]
        elif key == 'LL':
            val = total_length or 0
        # 数值整理
        try:
            if val != '':
                fv = float(val)
                val = round(fv, 3)
        except Exception:
            pass
        out.append({
            '参数': key,
            '含义': DESIGN_PARAM_DESC.get(key, ''),
            '值': val,
        })
    return out


def _modified_wt_records(modified_df, original_wt, *, force_same_as_original: bool = False) -> list:
    """把 modify_thick 输出的 DataFrame 与原始 wall_thickness_data 合并成前端可显示的行列表。

    按中心线长度对齐，避免 DataFrame 与 list 行序不一致时「原壁厚 / 修改后」错位。
    ``force_same_as_original``：仅TP抽等不改壁厚的工艺，强制两列相同。
    """
    rows = []
    if isinstance(original_wt, list):
        rows = [r for r in original_wt if isinstance(r, dict)]
    elif original_wt is not None:
        try:
            import pandas as pd
            if isinstance(original_wt, pd.DataFrame):
                rows = original_wt.to_dict(orient='records')
        except Exception:
            rows = []

    if modified_df is None or len(modified_df) == 0:
        out = []
        for row in rows:
            wt = row.get('壁厚(mm)')
            try:
                wt_f = round(float(wt), 3) if wt is not None else None
            except Exception:
                wt_f = None
            out.append({
                '中心线长度(mm)': round(float(row.get('中心线长度(mm)', 0) or 0), 2),
                '壁厚(mm)': wt_f,
                '修改后壁厚(mm)': wt_f,
                '外径周长(mm)': round(float(row.get('外径周长(mm)', 0) or 0), 3),
            })
        return out

    out = []
    try:
        mod_map = {}
        for cl, wt in zip(
            modified_df['中心线长度(mm)'].values,
            modified_df['壁厚(mm)'].values,
        ):
            try:
                mod_map[round(float(cl), 2)] = float(wt)
            except Exception:
                continue

        for row in rows:
            cl = round(float(row.get('中心线长度(mm)', 0) or 0), 2)
            try:
                wt_orig = float(row.get('壁厚(mm)')) if row.get('壁厚(mm)') is not None else None
            except Exception:
                wt_orig = None
            if force_same_as_original or wt_orig is None:
                wt_mod = wt_orig
            elif cl in mod_map:
                wt_mod = mod_map[cl]
            else:
                wt_mod = wt_orig
            out.append({
                '中心线长度(mm)': cl,
                '壁厚(mm)': round(wt_orig, 3) if wt_orig is not None else None,
                '修改后壁厚(mm)': round(float(wt_mod), 3) if wt_mod is not None else None,
                '外径周长(mm)': round(float(row.get('外径周长(mm)', 0) or 0), 3),
            })
    except Exception:
        out = []
        for row in rows:
            try:
                wt_f = round(float(row.get('壁厚(mm)')), 3) if row.get('壁厚(mm)') is not None else None
            except Exception:
                wt_f = None
            out.append({
                '中心线长度(mm)': round(float(row.get('中心线长度(mm)', 0) or 0), 2),
                '壁厚(mm)': wt_f,
                '修改后壁厚(mm)': wt_f,
                '外径周长(mm)': round(float(row.get('外径周长(mm)', 0) or 0), 3),
            })
    return out


@pp_bp.route('/design/param-meta', methods=['GET'])
def design_param_meta():
    """返回所有工艺设计参数的中文含义（与老项目 All_data 完全一致）。"""
    return jsonify({'success': True, 'meta': DESIGN_PARAM_META})


@pp_bp.route('/design/feature-flags', methods=['GET'])
def design_feature_flags_get():
    """返回工艺设计相关功能开关当前状态。"""
    with _session_lock:
        flags = dict(_pp_design_feature_flags)
    return jsonify({'success': True, 'flags': flags})


@pp_bp.route('/design/feature-flags', methods=['POST'])
def design_feature_flags_set():
    """设置工艺设计相关功能开关。"""
    data = request.get_json(silent=True) or {}
    if 'modified_wt_area_check' not in data:
        return jsonify({'success': False, 'message': '缺少 modified_wt_area_check'}), 400
    enabled = bool(data['modified_wt_area_check'])
    with _session_lock:
        _pp_design_feature_flags['modified_wt_area_check'] = enabled
        flags = dict(_pp_design_feature_flags)
    return jsonify({'success': True, 'flags': flags})


@pp_bp.route('/design/preview', methods=['POST'])
def design_preview():
    """根据 solution + pp_method + Taper_thick 计算：

    - 修改后壁厚曲线 modified_wt_data
    - 重算后的 segments2（T1..T4 / TL1..TL4 / TLM1..TLM3）
    - 老项目 All_data 风格的总参数表
    - 各工艺段评估提示
    """
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    pp_method = (data.get('pp_method') or '').strip()
    solution = data.get('solution') or {}
    taper_thick = bool(data.get('taper_thick', False))

    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400
    if not isinstance(solution, dict) or not solution:
        return jsonify({'success': False, 'message': '缺少 solution'}), 400

    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess or not sess.get('results'):
        return jsonify({'success': False, 'message': '会话不存在或没有分析结果'}), 400
    results = sess['results']

    original_wt = results.get('wall_thickness_data') or []
    # 从 manual_segments.boundaries 补全 TL（平台）与 TLM（过渡）；含已有 TL 但缺 TLM 的情况
    seg_in = _build_segments_for_optimization(results)
    # Tab2 手工 TL/T 参数优先（字段名已是总表语义）
    manual_params = sess.get('manual_params') or results.get('manual_params') or {}
    if isinstance(manual_params, dict) and any(
        float(manual_params.get(k) or 0) > 0 for k in ('TLM1', 'TLM2', 'TLM3', 'TL1', 'TL2', 'TL3')
    ):
        merged = dict(seg_in)
        merged.update({k: v for k, v in manual_params.items() if v is not None and v != ''})
        seg_in = merged
    # 回写，避免后续仍读到缺 TLM 的旧 segments
    results['segments'] = seg_in
    total_length = float(process_length(results) or 0)
    seg_for_thick = seg_in
    if pp_method in DRAW_BLANK_METHODS:
        seg_for_thick = dict(seg_in)
        for key in ('T1', 'T2', 'T3', 'T4'):
            seg_for_thick.pop(key, None)

    try:
        modified_df, segments2 = apply_modify_thick(
            wall_thickness_data=original_wt,
            solution=solution,
            pp_method=pp_method,
            Taper_thick=taper_thick,
            segments=seg_for_thick,
        )
    except Exception as exc:
        logger.error("design_preview 计算修改后壁厚失败: %s", exc, exc_info=True)
        modified_df, segments2 = None, dict(seg_for_thick)

    solution_updates = {}
    if pp_method in DRAW_BLANK_METHODS:
        precision = decimal_places(solution.get('D0'), 1)
        try:
            new_d0 = solve_mandrel_d0(
                original_wt,
                pp_method=pp_method,
                dn=solution.get('Dn'),
                t0=solution.get('T0'),
                d1=solution.get('D1'),
                d3=solution.get('D3'),
                d2=solution.get('D2') or 0,
                l1=solution.get('L1') or 0,
                l2=solution.get('L2') or 0,
                l3=solution.get('L3') or 0,
                segments=seg_for_thick,
                taper_thick=taper_thick,
                precision=precision,
                max_d0=route_max_d0(pp_method, solution.get('D1') or 0, precision),
                provisional_d0=solution.get('D0'),
            )
        except Exception as exc:
            logger.error("design_preview 反推素材外径失败: %s", exc, exc_info=True)
            new_d0 = None
        try:
            old_d0 = float(solution.get('D0') or 0)
        except (TypeError, ValueError):
            old_d0 = 0.0
        if new_d0 is not None and abs(new_d0 - old_d0) > 10 ** (-(precision + 1)):
            solution = dict(solution)
            solution['D0'] = new_d0
            scaled_l0 = scale_blank_length(solution.get('L0'), old_d0, new_d0, solution.get('T0'))
            try:
                if float(scaled_l0 or 0) > 0:
                    solution['L0'] = round(float(scaled_l0), decimal_places(solution.get('L0'), 0))
                    solution_updates['L0'] = solution['L0']
            except (TypeError, ValueError):
                pass
            solution_updates['D0'] = new_d0
            try:
                modified_df, segments2 = apply_modify_thick(
                    wall_thickness_data=original_wt,
                    solution=solution,
                    pp_method=pp_method,
                    Taper_thick=taper_thick,
                    segments=seg_for_thick,
                )
            except Exception as exc:
                logger.error("design_preview 按新素材外径重算壁厚失败: %s", exc, exc_info=True)

    # 修改后的壁厚和平台/过渡长度优先；该项没有改后值时才保留上传页原值
    segments2 = segments2 or {}
    for key in ('T1', 'T2', 'T3', 'T4', 'TL1', 'TLM1', 'TL2', 'TLM2', 'TL3', 'TLM3', 'TL4'):
        try:
            modified = float(segments2.get(key) or 0)
        except (TypeError, ValueError):
            modified = 0.0
        if modified > 0:
            continue
        try:
            if float(seg_in.get(key) or 0) > 0:
                segments2[key] = seg_in[key]
        except (TypeError, ValueError):
            continue

    # 仅TP抽 / 普通抽+TP抽：工艺不改壁厚，表格两列必须一致
    no_thick_change = pp_method in ('仅TP抽', '普通抽+TP抽')
    wt_table = _modified_wt_records(
        modified_df, original_wt, force_same_as_original=no_thick_change,
    )
    sections = _evaluate_segments_for_method(pp_method, solution)['sections']
    segments2 = segments2 or {}
    all_data = _build_alldata_table(solution, segments2, total_length)
    area_check_enabled = _modified_wt_area_check_enabled()
    modified_wt_area_checks = (
        _compute_modified_wt_area_checks(pp_method, solution, segments2)
        if area_check_enabled else []
    )

    return jsonify({
        'success': True,
        'modified_wt_data': wt_table,
        'segments2': segments2,
        'all_data': all_data,
        'sections': sections,
        'total_length': total_length,
        'modified_wt_area_checks': modified_wt_area_checks,
        'modified_wt_area_check_enabled': area_check_enabled,
        'solution_updates': solution_updates,
    })


@pp_bp.route('/design/save', methods=['POST'])
def design_save():
    """保存当前优化方案到工艺设计数据库。

    优先使用 ``all_data``（前端总参数表的最终编辑结果）；
    若未提供，则回退到 ``solution`` 的原始值。
    """
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'success': False, 'message': '缺少session_id'}), 400

    with _session_lock:
        sess = _pp_sessions.get(session_id)
    if not sess:
        return jsonify({'success': False, 'message': '会话不存在'}), 400

    solution = data.get('solution') or {}
    if not isinstance(solution, dict) or not solution:
        return jsonify({'success': False, 'message': '缺少要保存的方案数据 (solution)'}), 400

    pp_method = (data.get('pp_method') or '').strip()
    if not pp_method:
        return jsonify({'success': False, 'message': '缺少 pp_method'}), 400

    display_name = (data.get('display_name') or '').strip()
    vehicle_spec = (data.get('vehicle_spec') or '').strip()
    tube_number = (data.get('tube_number') or '').strip()
    taper_thick = bool(data.get('taper_thick', False))
    if not display_name:
        return jsonify({'success': False, 'message': '缺少设计名称'}), 400
    if not vehicle_spec:
        return jsonify({'success': False, 'message': '缺少车种号'}), 400
    if not tube_number:
        return jsonify({'success': False, 'message': '缺少管件号'}), 400

    # 取出用户在总参数表里最终敲定的值（如果有）
    edited_all = data.get('all_data') or []
    edited_map = {}
    for row in edited_all:
        if isinstance(row, dict) and row.get('参数'):
            edited_map[row['参数']] = row.get('值')

    # 合并：solution + segments2 + 用户编辑值，编辑值优先
    merged_solution = dict(solution)
    for k, v in edited_map.items():
        if v in (None, ''):
            continue
        merged_solution[k] = v

    results = sess.get('results') or {}
    source_file_name = sess.get('original_filename') or results.get('file_name') or ''
    source_file_path = sess.get('file_path') or ''
    source_tube_id = sess.get('tube_id')

    cp_data = results.get('centerline_perimeter_data') or []

    try:
        fitness = float(merged_solution.get('fitness') or solution.get('fitness') or 0)
    except Exception:
        fitness = 0.0
    area_change = merged_solution.get('area_change_rate')
    if area_change is None:
        area_change = solution.get('area_change_rate')
    extension = (
        merged_solution.get('extension_ratio')
        or merged_solution.get('outer_extension_ratio')
        or solution.get('extension_ratio')
        or solution.get('outer_extension_ratio')
    )

    extra_params = {'Taper段壁厚过渡': taper_thick}
    # 用户编辑的壁厚数据表（含修改后壁厚列）
    modified_wt = data.get('modified_wt_data')
    if modified_wt:
        extra_params['壁厚数据'] = modified_wt
    elif results.get('wall_thickness_data'):
        extra_params['壁厚数据'] = results.get('wall_thickness_data')
    seg = results.get('segments')
    if seg:
        extra_params['原始segments'] = seg

    new_id, err = save_pp_design(
        display_name=display_name,
        vehicle_spec=vehicle_spec,
        tube_number=tube_number,
        pp_method=pp_method,
        source_tube_id=source_tube_id,
        source_file_name=source_file_name,
        source_file_path=source_file_path,
        solution=merged_solution,
        fitness=fitness,
        area_change_rate=area_change,
        extension_ratio=extension,
        centerline_perimeter_data=cp_data,
        extra_params=extra_params,
    )
    if new_id < 0:
        return jsonify({'success': False, 'message': f'保存工艺设计失败：{err or "未知错误"}'}), 500
    return jsonify({
        'success': True,
        'design_id': new_id,
        'display_name': display_name,
        'message': '工艺设计已保存到工艺设计数据库',
    })


@pp_bp.route('/design/list', methods=['GET'])
def design_list():
    """列出所有已保存的工艺设计。"""
    try:
        rows = list_pp_designs()
        return jsonify({'success': True, 'designs': rows})
    except Exception as exc:
        logger.error("design_list error: %s", exc, exc_info=True)
        return jsonify({'success': False, 'message': str(exc)}), 500


@pp_bp.route('/design/<int:design_id>', methods=['GET'])
def design_get(design_id):
    """读取一条完整的工艺设计（含 tube_params_df_compatible 给下游模块）。"""
    try:
        rec = get_pp_design(int(design_id))
        if rec is None:
            return jsonify({'success': False, 'message': '未找到设计'}), 404
        # raw_payload 体积较大，前端列表/详情不需要，剥离掉
        rec.pop('raw_payload', None)
        return jsonify({'success': True, 'design': rec})
    except Exception as exc:
        logger.error("design_get error: %s", exc, exc_info=True)
        return jsonify({'success': False, 'message': str(exc)}), 500


@pp_bp.route('/design/rename', methods=['POST'])
def design_rename():
    data = request.get_json(silent=True) or {}
    design_id = data.get('design_id')
    new_name = (data.get('name') or '').strip()
    if not design_id:
        return jsonify({'success': False, 'message': '缺少 design_id'}), 400
    if not new_name:
        return jsonify({'success': False, 'message': '缺少新的设计名称'}), 400
    ok = rename_pp_design(int(design_id), new_name)
    if not ok:
        return jsonify({'success': False, 'message': '重命名失败'}), 500
    return jsonify({'success': True, 'design_id': int(design_id), 'display_name': new_name})


@pp_bp.route('/design/delete', methods=['POST'])
def design_delete():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if isinstance(ids, (int, str)):
        ids = [ids]
    if not ids:
        return jsonify({'success': False, 'message': '未提供要删除的 ids'}), 400
    count = delete_pp_designs(ids)
    return jsonify({'success': True, 'deleted': count})
