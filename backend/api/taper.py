"""Taper Mold API."""
import os
import uuid
import json
import tempfile
import numpy as np
from flask import Blueprint, request, jsonify, send_file
import pandas as pd
from core.taper.taper import TaperCalculator, TAPER_MOLDS
from core.normal.tube_model import TubeModelClass
from core.common.db import MoldDB
from core.common.license_check import require_license
from core.common.display_params import normalize_params_table, format_mold_summary
from core.common.profile_compute import compute_taper_profile
from core.pp.design_db import list_designs, get_design

taper_bp = Blueprint('taper', __name__)

# Session-based calculator storage
_taper_sessions = {}


def _format_calc_response(calc: TaperCalculator) -> dict:
    return format_mold_summary(
        calc.get_molds_params_summary(),
        calc.param_description,
        calc.param_calc_method,
    )


def _profile_payload(centerline_data, params_table, design_params=None):
    if not centerline_data:
        return {}
    result = compute_taper_profile(centerline_data, params_table)
    if not result.get('success'):
        return {'profile_error': result.get('message')}
    tp_length = 25.0
    if design_params:
        try:
            tp_length = float(design_params.get('L2') or 25)
        except (TypeError, ValueError):
            tp_length = 25.0
    return {
        'profile_ready': True,
        'line_data': result.get('line_data', []),
        'm_D_length': result.get('m_D_length'),
        'B_D_length': result.get('B_D_length'),
        'tp_length_suggested': tp_length,
    }


def _taper_params_from_design(design: dict) -> list[dict]:
    """Map 工艺设计参数 -> Taper模块 tube_params."""
    params = design.get('params') or {}

    def f(key: str, default: float = 0.0) -> float:
        try:
            return float(params.get(key) or default)
        except Exception:
            return default

    vehicle = str(params.get('车种规格') or design.get('vehicle_spec') or '/')
    mapped = [
        {'参数': 'D', '值': f('D2')},
        {'参数': 'TL', '值': f('L3')},
        {'参数': 'm_D', '值': f('D3')},
        {'参数': 'BL', '值': f('BL')},
        {'参数': 'B_D', '值': f('D2')},
        {'参数': '车种规格', '值': vehicle},
    ]
    return mapped


@taper_bp.route('/mold-options', methods=['GET'])
def get_mold_options():
    """Get available Taper mold configurations."""
    return jsonify({'success': True, 'molds': TAPER_MOLDS})


@taper_bp.route('/design-options', methods=['GET'])
def design_options():
    """List usable 工艺设计 records for Taper."""
    rows = list_designs()
    usable = [r for r in rows if 'Taper' in str(r.get('pp_method', ''))]
    return jsonify({'success': True, 'designs': usable})


@taper_bp.route('/load-design', methods=['POST'])
def load_design():
    """Load tube params from 工艺设计数据库."""
    data = request.get_json(silent=True) or {}
    design_id = data.get('design_id')
    if not design_id:
        return jsonify({'success': False, 'message': '缺少design_id'}), 400
    design = get_design(int(design_id))
    if not design:
        return jsonify({'success': False, 'message': '设计不存在'}), 404

    params_table = _taper_params_from_design(design)
    params = {row['参数']: row['值'] for row in params_table}
    design_params = design.get('params') or {}

    payload = {
        'success': True,
        'design': {
            'id': design.get('id'),
            'display_name': design.get('display_name'),
            'pp_method': design.get('pp_method'),
            'vehicle_spec': design.get('vehicle_spec'),
            'tube_number': design.get('tube_number'),
        },
        'params': params,
        'params_table': params_table,
        'centerline_perimeter_data': design.get('centerline_perimeter_data') or [],
    }
    payload.update(_profile_payload(
        payload['centerline_perimeter_data'],
        params_table,
        design_params,
    ))
    return jsonify(payload)


@taper_bp.route('/load', methods=['POST'])
def load_tube():
    """Load tube parameters from uploaded DXF file."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未上传文件'}), 400

    file = request.files['file']
    if not file.filename.endswith('.dxf'):
        return jsonify({'success': False, 'message': '请上传DXF文件'}), 400

    with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        tube = TubeModelClass(tmp_path)
        df_params = tube.get_params()
        params_dict = {}
        if df_params is not None and not df_params.empty:
            for _, row in df_params.iterrows():
                params_dict[row.iloc[0]] = row.iloc[1]
        return jsonify({
            'success': True,
            'params': params_dict,
            'params_table': normalize_params_table(
                df_params.to_dict(orient='records') if df_params is not None else []
            ),
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        os.unlink(tmp_path)


@taper_bp.route('/compute-profile', methods=['POST'])
def compute_profile():
    """Compute Taper line_data from centerline-perimeter data."""
    data = request.get_json(silent=True) or {}
    centerline_data = data.get('centerline_perimeter_data') or []
    tube_params = data.get('tube_params') or []
    if not centerline_data:
        return jsonify({'success': False, 'message': '缺少中心线周长数据'}), 400
    result = compute_taper_profile(centerline_data, tube_params)
    if not result.get('success'):
        return jsonify({'success': False, 'message': result.get('message', '形线计算失败')}), 400
    tp_length = data.get('tp_length')
    if tp_length is None:
        tp_length = 25
    return jsonify({
        'success': True,
        'profile_ready': True,
        'line_data': result.get('line_data', []),
        'm_D_length': result.get('m_D_length'),
        'B_D_length': result.get('B_D_length'),
        'tp_length_suggested': tp_length,
    })


@taper_bp.route('/analyze-stl', methods=['POST'])
def analyze_stl():
    """Analyze uploaded STL and compute Taper line_data."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未上传STL文件'}), 400
    tube_params_raw = request.form.get('tube_params', '[]')
    session_id = request.form.get('session_id', None)
    if not session_id:
        session_id = str(uuid.uuid4())
    try:
        tube_params = json.loads(tube_params_raw)
    except json.JSONDecodeError:
        tube_params = []

    file = request.files['file']
    with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        from core.pp.verify.stl_analyzer import STLAnalyzer
        analyzer = STLAnalyzer(subdivision_level=2)
        results = analyzer.analyze_stl_comprehensive(tmp_path, sample_interval_mm=1.0)
        if results.get('error'):
            return jsonify({'success': False, 'message': results['error']}), 500

        centerline_data = results.get('centerline_perimeter_data') or []
        profile = compute_taper_profile(centerline_data, tube_params)
        if not profile.get('success'):
            return jsonify({'success': False, 'message': profile.get('message', '形线计算失败')}), 400
        return jsonify({
            'success': True,
            'session_id': session_id,
            'centerline_perimeter_data': centerline_data,
            'profile_ready': True,
            'line_data': profile.get('line_data', []),
            'm_D_length': profile.get('m_D_length'),
            'B_D_length': profile.get('B_D_length'),
            'tp_length_suggested': 25,
        })
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500
    finally:
        os.unlink(tmp_path)


@taper_bp.route('/calculate', methods=['POST'])
@require_license
def calculate():
    """Calculate Taper mold parameters."""
    data = request.get_json()
    tube_params = data.get('tube_params')
    user_name = data.get('user_name', '/')
    machine_type = data.get('machine_type', 'EC0120')
    mold_list = data.get('mold_list', [])
    tp_length = data.get('tp_length', 0)
    line_data = data.get('line_data', None)
    session_id = data.get('session_id')

    if not tube_params or not mold_list:
        return jsonify({'success': False, 'message': '缺少参数'}), 400

    try:
        df = pd.DataFrame(tube_params)
        calc = TaperCalculator()
        calc.load_tube_from_params(df)
        calc.calculate(user_name, machine_type, mold_list, tp_length, line_data)
        summary = _format_calc_response(calc)

        # Store session
        if not session_id:
            session_id = str(uuid.uuid4())
        _taper_sessions[session_id] = calc

        return jsonify({'success': True, 'molds': summary, 'session_id': session_id})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@taper_bp.route('/modify', methods=['POST'])
def modify_parameter():
    """Modify a calculated mold parameter."""
    data = request.get_json()
    session_id = data.get('session_id')
    mold_name = data.get('mold_name')
    key = data.get('key')
    value = data.get('value')

    if not session_id or session_id not in _taper_sessions:
        return jsonify({'success': False, 'message': '会话不存在，请重新计算'}), 400
    if not mold_name or not key:
        return jsonify({'success': False, 'message': '缺少参数'}), 400

    try:
        calc = _taper_sessions[session_id]
        calc.modify_parameter(mold_name, key, value)
        summary = _format_calc_response(calc)
        return jsonify({'success': True, 'molds': summary, 'message': '参数已修改'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@taper_bp.route('/save', methods=['POST'])
def save_all():
    """Save all parameters to database."""
    data = request.get_json()
    session_id = data.get('session_id')

    if not session_id or session_id not in _taper_sessions:
        return jsonify({'success': False, 'message': '会话不存在，请重新计算'}), 400

    try:
        calc = _taper_sessions[session_id]
        calc.save_all()
        return jsonify({'success': True, 'message': '所有参数已保存到数据库'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@taper_bp.route('/export', methods=['POST'])
def export():
    """Export results as ZIP."""
    data = request.get_json()
    session_id = data.get('session_id')

    # Try existing calculator session; STL analyze dict must not block fallback
    calc = _taper_sessions.get(session_id) if session_id else None
    if calc is not None and hasattr(calc, 'export_zip'):
        try:
            zip_path = calc.export_zip()
            return send_file(zip_path, as_attachment=True, download_name=os.path.basename(zip_path))
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    # Fallback: recalculate from params
    try:
        df = pd.DataFrame(data.get('tube_params', []))
        calc = TaperCalculator()
        calc.load_tube_from_params(df)
        calc.calculate(
            data.get('user_name', '/'),
            data.get('machine_type', 'EC0120'),
            data.get('mold_list', []),
            data.get('tp_length', 0),
            data.get('line_data', None)
        )
        zip_path = calc.export_zip()
        return send_file(zip_path, as_attachment=True, download_name=os.path.basename(zip_path))
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@taper_bp.route('/search', methods=['GET'])
def search():
    """Search Taper mold data."""
    machine_type = request.args.get('machine_type', 'EC0120')
    mold_type = request.args.get('mold_type', 'B000')
    db = MoldDB('taper')
    df = db.query(machine_type, mold_type)
    if df.empty:
        return jsonify({'success': True, 'data': [], 'message': '未找到数据'})
    return jsonify({'success': True, 'data': df.to_dict(orient='records')})
