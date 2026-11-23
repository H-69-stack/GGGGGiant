"""Normal Shrink Tube API."""
import os
import re
import uuid
import tempfile
from flask import Blueprint, request, jsonify, send_file
import pandas as pd
from core.normal.shrink_tube import ShrinkTubeCalculator, MOLDS
from core.common.db import MoldDB
from core.common.license_check import require_license
from core.common.display_params import normalize_params_table, format_mold_summary
from core.pp.design_db import list_designs, get_design

normal_bp = Blueprint('normal', __name__)

# Session-based calculator storage
_normal_sessions = {}


def positive_wall_count(tube_params) -> int:
    """大于 0 的壁厚段数。只统计 T1、T2 这类壁厚，长度不计入。"""
    count = 0
    for row in tube_params or []:
        name = str((row or {}).get('参数', '')).strip()
        if not re.fullmatch(r'T\d+', name):
            continue
        try:
            value = float(row.get('值') or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            count += 1
    return count


def _format_calc_response(calc: ShrinkTubeCalculator) -> dict:
    summary = calc.get_molds_params_summary()
    return format_mold_summary(
        summary,
        calc.param_description,
        calc.param_calc_method,
    )


def _normal_params_from_design(design: dict) -> list[dict]:
    """Map 工艺设计参数 -> 普通抽模块 tube_params."""
    params = design.get('params') or {}

    def f(key: str, default: float = 0.0) -> float:
        try:
            return float(params.get(key) or default)
        except Exception:
            return default

    vehicle = str(params.get('车种规格') or design.get('vehicle_spec') or '/')
    mapped = [
        {'参数': 'T1', '值': f('T1')},
        {'参数': 'T2', '值': f('T2')},
        {'参数': 'T3', '值': f('T3')},
        {'参数': 'T4', '值': f('T4')},
        {'参数': 'L1', '值': f('TL1')},
        {'参数': 'L2', '值': f('TL2')},
        {'参数': 'L3', '值': f('TL3')},
        {'参数': 'L4', '值': f('TL4')},
        {'参数': 'M1', '值': f('TLM1')},
        {'参数': 'M2', '值': f('TLM2')},
        {'参数': 'L', '值': f('Ln')},
        {'参数': 'D', '值': f('Dn')},
        {'参数': '车种规格', '值': vehicle},
    ]
    return mapped


@normal_bp.route('/mold-options', methods=['GET'])
def get_mold_options():
    """Get available mold configurations."""
    return jsonify({'success': True, 'molds': MOLDS})


@normal_bp.route('/design-options', methods=['GET'])
def design_options():
    """List usable 工艺设计 records for Normal."""
    rows = list_designs()
    usable = [r for r in rows if '普通抽' in str(r.get('pp_method', ''))]
    return jsonify({'success': True, 'designs': usable})


@normal_bp.route('/load-design', methods=['POST'])
def load_design():
    """Load tube params from 工艺设计数据库."""
    data = request.get_json(silent=True) or {}
    design_id = data.get('design_id')
    if not design_id:
        return jsonify({'success': False, 'message': '缺少design_id'}), 400
    design = get_design(int(design_id))
    if not design:
        return jsonify({'success': False, 'message': '设计不存在'}), 404

    params_table = _normal_params_from_design(design)
    params = {row['参数']: row['值'] for row in params_table}
    return jsonify({
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
    })


@normal_bp.route('/load', methods=['POST'])
def load_tube():
    """Load tube parameters from uploaded DXF file."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未上传文件'}), 400

    file = request.files['file']
    if not file.filename.endswith('.dxf'):
        return jsonify({'success': False, 'message': '请上传DXF文件'}), 400

    # Save temp file
    with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        calc = ShrinkTubeCalculator()
        calc.load_tube_from_file(tmp_path)
        params_df = calc.get_tube_params_df()
        return jsonify({
            'success': True,
            'params': calc.get_tube_params(),
            'params_table': normalize_params_table(
                params_df.to_dict(orient='records') if not params_df.empty else []
            ),
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        os.unlink(tmp_path)


@normal_bp.route('/calculate', methods=['POST'])
@require_license
def calculate():
    """Calculate mold parameters."""
    data = request.get_json()
    tube_params = data.get('tube_params')
    user_name = data.get('user_name', '/')
    normal_add = bool(data.get('normal_add', False)) and positive_wall_count(tube_params) > 2
    mold_list = data.get('mold_list', [])
    machine_type = data.get('machine_type', 'DC0124')
    session_id = data.get('session_id')

    if not tube_params or not mold_list:
        return jsonify({'success': False, 'message': '缺少参数'}), 400

    try:
        df = pd.DataFrame(tube_params)
        calc = ShrinkTubeCalculator()
        calc.load_tube_from_params(df)
        calc.calculate(user_name, normal_add, mold_list, machine_type)
        summary = _format_calc_response(calc)

        # Store session
        if not session_id:
            session_id = str(uuid.uuid4())
        _normal_sessions[session_id] = calc

        return jsonify({'success': True, 'molds': summary, 'session_id': session_id})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@normal_bp.route('/modify', methods=['POST'])
def modify_parameter():
    """Modify a calculated mold parameter."""
    data = request.get_json()
    session_id = data.get('session_id')
    mold_name = data.get('mold_name')
    key = data.get('key')
    value = data.get('value')

    if not session_id or session_id not in _normal_sessions:
        return jsonify({'success': False, 'message': '会话不存在，请重新计算'}), 400
    if not mold_name or not key:
        return jsonify({'success': False, 'message': '缺少参数'}), 400

    try:
        calc = _normal_sessions[session_id]
        calc.modify_parameter(mold_name, key, value)
        summary = _format_calc_response(calc)
        return jsonify({'success': True, 'molds': summary, 'message': '参数已修改'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@normal_bp.route('/save', methods=['POST'])
def save_all():
    """Save all parameters to database."""
    data = request.get_json()
    session_id = data.get('session_id')

    if not session_id or session_id not in _normal_sessions:
        return jsonify({'success': False, 'message': '会话不存在，请重新计算'}), 400

    try:
        calc = _normal_sessions[session_id]
        calc.save_all()
        return jsonify({'success': True, 'message': '所有参数已保存到数据库'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@normal_bp.route('/export', methods=['POST'])
def export():
    """Export DXF and Excel as ZIP."""
    data = request.get_json()
    session_id = data.get('session_id')

    # Try to use existing session first
    if session_id and session_id in _normal_sessions:
        try:
            calc = _normal_sessions[session_id]
            zip_path = calc.export_zip()
            return send_file(zip_path, as_attachment=True, download_name=os.path.basename(zip_path))
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    # Fallback: recalculate from params
    try:
        tube_params = data.get('tube_params')
        df = pd.DataFrame(tube_params)
        calc = ShrinkTubeCalculator()
        calc.load_tube_from_params(df)
        calc.calculate(
            data.get('user_name', '/'),
            bool(data.get('normal_add', False)) and positive_wall_count(tube_params) > 2,
            data.get('mold_list', []),
            data.get('machine_type', 'DC0124')
        )
        zip_path = calc.export_zip()
        return send_file(zip_path, as_attachment=True, download_name=os.path.basename(zip_path))
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@normal_bp.route('/search', methods=['GET'])
def search():
    """Search mold data in database."""
    machine_type = request.args.get('machine_type', 'DC0124')
    mold_type = request.args.get('mold_type', 'AD03')

    db = MoldDB('normal')
    df = db.query(machine_type, mold_type)
    if df.empty:
        return jsonify({'success': True, 'data': [], 'message': '未找到数据'})
    return jsonify({'success': True, 'data': df.to_dict(orient='records')})
