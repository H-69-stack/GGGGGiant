"""TP Shrink Tube API."""
import os
import uuid
import json
import tempfile
import io
import base64
import numpy as np
from flask import Blueprint, request, jsonify, send_file
import pandas as pd
from core.tp.shrink_tube import TPShrinkTubeCalculator, TP_MOLDS
from core.normal.tube_model import TubeModelClass
from core.common.db import MoldDB
from core.common.license_check import require_license
from core.common.display_params import normalize_params_table, format_mold_summary
from core.common.profile_compute import compute_tp_profile
from core.pp.design_db import list_designs, get_design
from config import DATA_DIR

tp_bp = Blueprint('tp', __name__)

# Session-based calculator storage
_tp_sessions = {}
_tp_stl_analyzers = {}  # session_id -> STLAnalyzer instance for 3D preview
_tp_profile_data = {}   # session_id -> profile_data list for export


def _format_calc_response(calc: TPShrinkTubeCalculator) -> dict:
    return format_mold_summary(
        calc.get_molds_params_summary(),
        calc.param_description,
        calc.param_calc_method,
    )


def _profile_payload(centerline_data, params_table):
    if not centerline_data:
        return {}
    result = compute_tp_profile(centerline_data, params_table)
    if not result.get('success'):
        return {'profile_error': result.get('message')}
    return {
        'profile_ready': True,
        'profile_data': result.get('profile_data', []),
        'T_D_length': result.get('T_D_length'),
        'D_length': result.get('D_length'),
        'T_D_diam': result.get('T_D_diam'),
        'D_diam': result.get('D_diam'),
        'T_L': result.get('T_L'),
        'TP_segment_length': result.get('TP_segment_length'),
        'TP_segment_source': result.get('TP_segment_source'),
    }


def _tp_params_from_design(design: dict) -> list[dict]:
    """Map 工艺设计参数 -> TP模块 tube_params."""
    params = design.get('params') or {}

    def f(key: str, default: float = 0.0) -> float:
        try:
            return float(params.get(key) or default)
        except Exception:
            return default

    vehicle = str(params.get('车种规格') or design.get('vehicle_spec') or '/')
    dn = f('Dn')
    d0 = f('D0')
    t_d = dn if dn != 0 else d0
    mapped = [
        {'参数': 'T_D', '值': t_d},
        {'参数': 'T1', '值': f('T1')},
        {'参数': 'T2', '值': f('T2')},
        {'参数': 'T3', '值': f('T3')},
        {'参数': 'L1', '值': f('TL1')},
        {'参数': 'L2', '值': f('TL2')},
        {'参数': 'L3', '值': f('TL3')},
        {'参数': 'M1', '值': f('TLM1')},
        {'参数': 'M2', '值': f('TLM2')},
        {'参数': 'L', '值': f('LL')},
        {'参数': 'D', '值': f('D2')},
        {'参数': 'T_LR', '值': f('LL1')},
        {'参数': 'T_L', '值': f('L2')},
        {'参数': 'L4', '值': f('L4')},
        {'参数': '车种规格', '值': vehicle},
    ]
    return mapped


@tp_bp.route('/mold-options', methods=['GET'])
def get_mold_options():
    """Get available TP mold configurations."""
    return jsonify({'success': True, 'molds': TP_MOLDS})


@tp_bp.route('/design-options', methods=['GET'])
def design_options():
    """List usable 工艺设计 records for TP."""
    rows = list_designs()
    usable = [r for r in rows if 'TP抽' in str(r.get('pp_method', ''))]
    return jsonify({'success': True, 'designs': usable})


@tp_bp.route('/load-design', methods=['POST'])
def load_design():
    """Load tube params from 工艺设计数据库."""
    data = request.get_json(silent=True) or {}
    design_id = data.get('design_id')
    if not design_id:
        return jsonify({'success': False, 'message': '缺少design_id'}), 400
    design = get_design(int(design_id))
    if not design:
        return jsonify({'success': False, 'message': '设计不存在'}), 404

    if design.get('source_tube_id'):
        from core.pp.thickness_db import get_full_analysis
        from api.rfm import check_measurement_quality
        source = get_full_analysis(int(design['source_tube_id']))
        if source:
            try:
                check_measurement_quality(source.get('results') or {})
            except ValueError as exc:
                return jsonify(
                    success=False,
                    message=str(exc),
                    code='measurement_review',
                    tube_id=int(design['source_tube_id']),
                    review=getattr(exc, 'details', None),
                ), 400

    params_table = _tp_params_from_design(design)
    params = {row['参数']: row['值'] for row in params_table}

    # Generate session_id for 3D preview
    session_id = str(uuid.uuid4())

    # Load STL analyzer from source tube for 3D preview
    source_tube_id = design.get('source_tube_id')
    if source_tube_id:
        try:
            from core.pp.thickness_db import get_full_analysis
            tube_record = get_full_analysis(int(source_tube_id))
            if tube_record and tube_record.get('results'):
                results = tube_record['results']
                mesh_data = results.get('mesh_data')
                stl_path = tube_record.get('source_file_path', '')
                if (not mesh_data and stl_path and os.path.exists(stl_path)
                        and os.path.splitext(stl_path)[1].lower() in ('.stp', '.step')):
                    # Native STEP records need CAD tessellation for this legacy
                    # optional viewer; STLAnalyzer cannot directly read STEP.
                    from core.pp.step_analyzer import read_step
                    from core.pp.mesh_converter import shape_to_mesh
                    mesh_data = shape_to_mesh(read_step(stl_path), tolerance=.3)
                if mesh_data and mesh_data.get('vertices'):
                    from core.pp.verify.stl_analyzer import STLAnalyzer
                    analyzer = STLAnalyzer(subdivision_level=2)
                    verts = np.array(mesh_data['vertices'], dtype=np.float64).reshape(-1, 3)
                    faces = np.array(mesh_data['faces'], dtype=np.int32).reshape(-1, 3)
                    import trimesh
                    analyzer.mesh = trimesh.Trimesh(vertices=verts, faces=faces)
                    _tp_stl_analyzers[session_id] = analyzer
                elif stl_path and os.path.exists(stl_path):
                    from core.pp.verify.stl_analyzer import STLAnalyzer
                    analyzer = STLAnalyzer(subdivision_level=2)
                    analyzer.load_stl(stl_path)
                    _tp_stl_analyzers[session_id] = analyzer
        except Exception:
            pass  # 3D preview is optional; don't fail the whole load

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
        'params_table': normalize_params_table(params_table),
        'centerline_perimeter_data': design.get('centerline_perimeter_data') or [],
        'session_id': session_id,
    }
    payload.update(_profile_payload(payload['centerline_perimeter_data'], params_table))
    return jsonify(payload)


@tp_bp.route('/load', methods=['POST'])
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


@tp_bp.route('/compute-profile', methods=['POST'])
def compute_profile():
    """Compute TP profile from centerline-perimeter data."""
    data = request.get_json(silent=True) or {}
    centerline_data = data.get('centerline_perimeter_data') or []
    tube_params = data.get('tube_params') or []
    if not centerline_data:
        return jsonify({'success': False, 'message': '缺少中心线周长数据'}), 400
    result = compute_tp_profile(centerline_data, tube_params)
    if not result.get('success'):
        return jsonify({'success': False, 'message': result.get('message', '形线计算失败')}), 400
    return jsonify({
        'success': True,
        'profile_ready': True,
        'profile_data': result.get('profile_data', []),
        'T_D_length': result.get('T_D_length'),
        'D_length': result.get('D_length'),
        'T_D_diam': result.get('T_D_diam'),
        'D_diam': result.get('D_diam'),
        'T_L': result.get('T_L'),
        'TP_segment_length': result.get('TP_segment_length'),
        'TP_segment_source': result.get('TP_segment_source'),
    })


@tp_bp.route('/analyze-stl', methods=['POST'])
def analyze_stl():
    """Analyze uploaded STL and compute TP profile."""
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
    suffix = '.stl' if file.filename.lower().endswith('.stl') else '.stl'
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        from core.pp.verify.stl_analyzer import STLAnalyzer
        analyzer = STLAnalyzer(subdivision_level=2)
        results = analyzer.analyze_stl_comprehensive(tmp_path, sample_interval_mm=1.0)
        if results.get('error'):
            return jsonify({'success': False, 'message': results['error']}), 500

        # Compute profile
        centerline_data = results.get('centerline_perimeter_data') or []
        profile = compute_tp_profile(centerline_data, tube_params)
        if not profile.get('success'):
            return jsonify({'success': False, 'message': profile.get('message', '形线计算失败')}), 400

        # Store analyzer and profile_data for 3D preview and export
        if session_id:
            _tp_stl_analyzers[session_id] = analyzer
            _tp_profile_data[session_id] = profile.get('profile_data', [])

        return jsonify({
            'success': True,
            'session_id': session_id,
            'centerline_perimeter_data': centerline_data,
            'profile_ready': True,
            'profile_data': profile.get('profile_data', []),
            'T_D_length': profile.get('T_D_length'),
            'D_length': profile.get('D_length'),
            'T_D_diam': profile.get('T_D_diam'),
            'D_diam': profile.get('D_diam'),
            'T_L': profile.get('T_L'),
            'TP_segment_length': profile.get('TP_segment_length'),
            'TP_segment_source': profile.get('TP_segment_source'),
        })
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500
    finally:
        os.unlink(tmp_path)


@tp_bp.route('/viz-3d', methods=['GET'])
def viz_3d():
    """Return interactive 3D STL preview as HTML string (embedded iframe)."""
    session_id = request.args.get('session_id')
    if not session_id or session_id not in _tp_stl_analyzers:
        return jsonify({'success': False, 'message': '无3D预览数据，请重新上传STL'}), 404
    try:
        analyzer = _tp_stl_analyzers[session_id]
        fig = analyzer.create_3d_visualization(max_faces=15000)
        html = fig.to_html(full_html=False, include_plotlyjs='cdn')
        return jsonify({'success': True, 'html': html})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@tp_bp.route('/calculate', methods=['POST'])
@require_license
def calculate():
    """Calculate TP mold parameters."""
    data = request.get_json()
    tube_params = data.get('tube_params')
    user_name = data.get('user_name', '/')
    apply_taper = data.get('apply_taper', False)
    mold_list = data.get('mold_list', [])
    special_thickness = data.get('special_thickness', False)
    interference_length = data.get('interference_length', 0)
    profile_data = data.get('profile_data', [])
    session_id = data.get('session_id')

    if not tube_params or not mold_list:
        return jsonify({'success': False, 'message': '缺少参数'}), 400

    try:
        df = pd.DataFrame(tube_params)
        calc = TPShrinkTubeCalculator()
        calc.load_tube_from_params(df)
        calc.calculate(user_name, apply_taper, mold_list, special_thickness, interference_length)
        summary = _format_calc_response(calc)

        # Store session
        if not session_id:
            session_id = str(uuid.uuid4())
        _tp_sessions[session_id] = calc

        return jsonify({'success': True, 'molds': summary, 'session_id': session_id})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@tp_bp.route('/modify', methods=['POST'])
def modify_parameter():
    """Modify a calculated mold parameter."""
    data = request.get_json()
    session_id = data.get('session_id')
    mold_name = data.get('mold_name')
    key = data.get('key')
    value = data.get('value')

    if not session_id or session_id not in _tp_sessions:
        return jsonify({'success': False, 'message': '会话不存在，请重新计算'}), 400
    if not mold_name or not key:
        return jsonify({'success': False, 'message': '缺少参数'}), 400

    try:
        calc = _tp_sessions[session_id]
        calc.modify_parameter(mold_name, key, value)
        summary = _format_calc_response(calc)
        return jsonify({'success': True, 'molds': summary, 'message': '参数已修改'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@tp_bp.route('/save', methods=['POST'])
def save_all():
    """Save all parameters to database."""
    data = request.get_json()
    session_id = data.get('session_id')

    if not session_id or session_id not in _tp_sessions:
        return jsonify({'success': False, 'message': '会话不存在，请重新计算'}), 400

    try:
        calc = _tp_sessions[session_id]
        calc.save_all()
        return jsonify({'success': True, 'message': '所有参数已保存到数据库'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@tp_bp.route('/export', methods=['POST'])
def export():
    """Export results as ZIP."""
    data = request.get_json()
    session_id = data.get('session_id')

    # Try to use existing session first
    if session_id and session_id in _tp_sessions:
        try:
            calc = _tp_sessions[session_id]
            profile_data = _tp_profile_data.get(session_id, data.get('profile_data', []))
            zip_path = calc.export_zip(profile_data=profile_data)
            return send_file(zip_path, as_attachment=True, download_name=os.path.basename(zip_path))
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500

    # Fallback: recalculate from params
    try:
        df = pd.DataFrame(data.get('tube_params', []))
        calc = TPShrinkTubeCalculator()
        calc.load_tube_from_params(df)
        calc.calculate(
            data.get('user_name', '/'),
            data.get('apply_taper', False),
            data.get('mold_list', []),
            data.get('special_thickness', False),
            data.get('interference_length', 0)
        )
        zip_path = calc.export_zip(profile_data=data.get('profile_data', []))
        return send_file(zip_path, as_attachment=True, download_name=os.path.basename(zip_path))
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@tp_bp.route('/search', methods=['GET'])
def search():
    """Search TP mold data."""
    mold_type = request.args.get('mold_type', 'AD01')
    db = MoldDB('tp')
    df = db.query('DC0128', mold_type)
    if df.empty:
        return jsonify({'success': True, 'data': [], 'message': '未找到数据'})
    return jsonify({'success': True, 'data': df.to_dict(orient='records')})

# ─── 3D Mold Generation (抽套模) ─────────────────────────────────────────────

_tp_mold_sessions = {}  # session_id -> dict with step_path, stl_path, vertices, faces


@tp_bp.route('/generate-mold', methods=['POST'])
def generate_mold():
    """
    Generate 3D draw-mold (抽套模) mesh from profile data.
    Returns preview HTML + STEP/STL download URLs.
    """
    data = request.get_json() or {}
    session_id = data.get('session_id')
    profile_data = data.get('profile_data', [])
    centerline_perimeter_data = data.get('centerline_perimeter_data', [])
    T_D_length = data.get('T_D_length')
    D_length = data.get('D_length')
    T_D_diam = data.get('T_D_diam')   # large-end inner diameter (mm)
    D_diam = data.get('D_diam')       # small-end inner diameter (mm)

    session_id = str(uuid.uuid4())

    try:
        if data.get('design_id'):
            from core.pp.thickness_db import get_full_analysis
            from api.rfm import check_measurement_quality
            design = get_design(int(data['design_id']))
            if not design or not design.get('source_tube_id'):
                raise ValueError('工艺设计缺少来源管件')
            source = get_full_analysis(int(design['source_tube_id']))
            if not source:
                raise ValueError('来源管件不存在')
            check_measurement_quality(source.get('results') or {})
        from core.tp.mold_generator import TPMoldProcessor, select_tp_segment
        import numpy as np

        if centerline_perimeter_data and len(centerline_perimeter_data) > 0:
            perimeter_data = centerline_perimeter_data
        elif profile_data and len(profile_data) > 0:
            perimeter_data = [
                {'中心线长度(mm)': float(row['X']), '外径周长(mm)': float(row['Y']) * np.pi}
                for row in profile_data
                if row.get('X') is not None and row.get('Y') is not None
            ]
        else:
            return jsonify({'success': False, 'message': '无外径周长数据'}), 400

        proc = TPMoldProcessor()

        # Determine T_L, T_D, T_d
        if T_D_length is not None and D_length is not None:
            T_L = abs(float(T_D_length) - float(D_length))
        else:
            z_vals = [float(row['中心线长度(mm)']) for row in perimeter_data]
            if len(z_vals) >= 2:
                T_L = max(z_vals) - min(z_vals)
            else:
                return jsonify({'success': False, 'message': '无法确定TP段长度'}), 400

        if T_D_diam is not None and float(T_D_diam) > 0:
            large_id = float(T_D_diam)
        elif T_D_length is not None and perimeter_data:
            import numpy as np
            z_arr = np.array([float(r['中心线长度(mm)']) for r in perimeter_data])
            p_arr = np.array([float(r['外径周长(mm)']) for r in perimeter_data])
            idx = int(np.argmin(np.abs(z_arr - float(T_D_length))))
            large_id = p_arr[idx] / np.pi
        else:
            return jsonify({'success': False, 'message': '无法确定大端内径'}), 400

        if D_diam is not None and float(D_diam) > 0:
            small_id = float(D_diam)
        elif D_length is not None and perimeter_data:
            import numpy as np
            z_arr = np.array([float(r['中心线长度(mm)']) for r in perimeter_data])
            p_arr = np.array([float(r['外径周长(mm)']) for r in perimeter_data])
            idx = int(np.argmin(np.abs(z_arr - float(D_length))))
            small_id = p_arr[idx] / np.pi
        else:
            return jsonify({'success': False, 'message': '无法确定小端内径'}), 400

        if centerline_perimeter_data:
            if T_D_length is None or D_length is None:
                raise ValueError('使用管件周长数据时必须提供 TP 段的两个站位')
            perimeter_data = select_tp_segment(perimeter_data, T_D_length, D_length, large_id, small_id)
        # Profile-only requests already contain the selected drawing profile;
        # its station origin (usually 9 mm) is normalized by the processor.
        proc.set_profile_data(perimeter_data)
        proc.set_tp_params(T_L=T_L, T_D=large_id, T_d=small_id)

        mesh = proc.generate_model()

        # Generate files
        stl_bytes = proc.get_stl_bytes()
        step_bytes = proc.get_step_bytes()

        # Save STEP to disk for download
        out_dir = os.path.join(DATA_DIR, 'tp_mold')
        os.makedirs(out_dir, exist_ok=True)
        step_path = os.path.join(out_dir, f'{session_id}.step')
        stl_path = os.path.join(out_dir, f'{session_id}.stl')
        with open(step_path, 'wb') as f:
            f.write(step_bytes)
        with open(stl_path, 'wb') as f:
            f.write(stl_bytes)

        # Cache in session
        _tp_mold_sessions[session_id] = {
            'step_path': step_path,
            'stl_path': stl_path,
            'vertices': len(mesh.vertices),
            'faces': len(mesh.faces),
        }

        return jsonify({
            'success': True,
            'session_id': session_id,
            'vertices': len(mesh.vertices),
            'faces': len(mesh.faces),
            'step_download_url': f'/api/tp/download-mold/{session_id}',
            'stl_download_url': f'/api/tp/download-mold/{session_id}?format=stl',
        })

    except ValueError as e:
        payload = {'success': False, 'message': str(e)}
        details = getattr(e, 'details', None)
        if details:
            payload['code'] = 'measurement_review'
            payload['review'] = details
            design_id = data.get('design_id')
            if design_id:
                design_row = get_design(int(design_id))
                if design_row and design_row.get('source_tube_id'):
                    payload['tube_id'] = int(design_row['source_tube_id'])
        return jsonify(payload), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'success': False, 'message': f'生成失败: {e}'}), 500


@tp_bp.route('/download-mold/<session_id>', methods=['GET'])
def download_mold(session_id):
    """Download the generated mold STEP or STL file."""
    fmt = request.args.get('format', 'step')
    sess = _tp_mold_sessions.get(session_id)
    if not sess:
        return jsonify({'success': False, 'message': '无已生成的模具数据'}), 404

    if fmt == 'stl':
        return send_file(
            sess['stl_path'],
            as_attachment=True,
            download_name=f'TPDrawMold_{session_id[:8]}.stl',
            mimetype='application/octet-stream',
        )
    else:
        return send_file(
            sess['step_path'],
            as_attachment=True,
            download_name=f'TPDrawMold_{session_id[:8]}.step',
            mimetype='application/step',
        )
