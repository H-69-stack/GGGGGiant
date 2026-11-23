"""Rotation tool API - STP/STEP rotation with Euler/axis-angle."""
import io
import os
from flask import Blueprint, request, jsonify, send_file
import trimesh
import numpy as np

rotation_bp = Blueprint('rotation', __name__)

# In-memory session storage
_rotation_sessions = {}


def _rot_matrix(axis: str, degrees: float) -> np.ndarray:
    """Build rotation matrix for preview mesh vertices."""
    theta = np.deg2rad(float(degrees))
    c, s = float(np.cos(theta)), float(np.sin(theta))
    axis = axis.lower()
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)
    if axis == "z":
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)
    raise ValueError(f"Unsupported axis: {axis}")


def _apply_mesh_rotation(mesh: trimesh.Trimesh, axis: str, degrees: float) -> trimesh.Trimesh:
    """Apply the same rotation to the preview mesh as the STEP shape."""
    rotated = mesh.copy()
    r = _rot_matrix(axis, degrees)
    verts = np.asarray(rotated.vertices, dtype=np.float64)
    rotated.vertices = (verts @ r.T).astype(np.float64)
    rotated.remove_unreferenced_vertices()
    return rotated


def _shape_to_mesh(shape) -> trimesh.Trimesh:
    """Tessellate an OpenCascade shape into a cleaned Trimesh for preview."""
    from core.pp.mesh_converter import shape_to_mesh

    mesh_data = shape_to_mesh(shape, tolerance=0.3, angular_tolerance=0.2)
    vertices = np.array(mesh_data.get('vertices', []), dtype=np.float64).reshape(-1, 3)
    faces = np.array(mesh_data.get('faces', []), dtype=np.int64).reshape(-1, 3)
    if len(vertices) == 0 or len(faces) == 0:
        raise ValueError("STP/STEP网格化失败：没有生成有效网格")
    return _clean_mesh(trimesh.Trimesh(vertices=vertices, faces=faces, process=False))


def _clean_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    if isinstance(mesh, trimesh.Scene):
        mesh = trimesh.util.concatenate(tuple(mesh.dump()))
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError("无法解析模型：结果不是 Trimesh")
    mesh = mesh.copy()
    mesh.remove_duplicate_faces()
    mesh.remove_degenerate_faces()
    mesh.remove_unreferenced_vertices()
    mesh.process(validate=True)
    return mesh


def _load_step_shape(data: bytes, suffix: str):
    """Load STP/STEP bytes into an OpenCascade shape."""
    import tempfile
    try:
        from core.pp.step_analyzer import OCP_AVAILABLE, read_step
    except ImportError as exc:
        raise ValueError("STP/STEP旋转需要安装 cadquery/OCP") from exc

    if not OCP_AVAILABLE:
        raise ValueError("STP/STEP旋转需要安装 cadquery/OCP，当前环境不可用")

    tmp_path = ''
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        return read_step(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def _rotation_transform(axis: str, degrees: float):
    """Build an OpenCascade rotation transform around the world origin."""
    import math
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    axis = axis.lower()
    direction = {
        'x': (1.0, 0.0, 0.0),
        'y': (0.0, 1.0, 0.0),
        'z': (0.0, 0.0, 1.0),
    }.get(axis)
    if direction is None:
        raise ValueError(f"Unsupported axis: {axis}")

    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax1(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(*direction)), math.radians(float(degrees)))
    return trsf


def _apply_transform(shape, trsf):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    transformer = BRepBuilderAPI_Transform(shape, trsf, False, False)
    return transformer.Shape()


def _shape_to_step_bytes(shape) -> bytes:
    """Serialize an OpenCascade shape to STEP bytes."""
    import tempfile
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    tmp_path = ''
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.step') as tmp:
            tmp_path = tmp.name
        writer = STEPControl_Writer()
        writer.Transfer(shape, STEPControl_AsIs)
        status = writer.Write(tmp_path)
        if status != IFSelect_RetDone:
            raise ValueError("STEP写出失败")
        with open(tmp_path, 'rb') as f:
            return f.read()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@rotation_bp.route('/upload', methods=['POST'])
def upload_model():
    """Upload STP/STEP file for rotation."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '未上传文件'}), 400

    file = request.files['file']
    stem, ext = os.path.splitext(file.filename or '')
    if ext.lower() not in ('.stp', '.step'):
        return jsonify({'success': False, 'message': '请上传STP或STEP文件'}), 400

    try:
        shape = _load_step_shape(file.read(), ext)
        mesh = _shape_to_mesh(shape)
        session_id = f"rot_{os.urandom(4).hex()}"
        out_ext = '.stp' if ext.lower() == '.stp' else '.step'
        _rotation_sessions[session_id] = {
            'orig_shape': shape,
            'cur_shape': shape,
            'cur_mesh': mesh.copy(),
            'filename': f"{stem or 'model'}_rotated{out_ext}",
            'source_ext': ext.lower(),
        }
        return jsonify({
            'success': True,
            'session_id': session_id,
            'filename': _rotation_sessions[session_id]['filename'],
            'output_format': 'step',
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@rotation_bp.route('/transform', methods=['POST'])
def transform():
    """Apply rotation to the current STEP shape."""
    data = request.get_json()
    session_id = data.get('session_id')

    if session_id not in _rotation_sessions:
        return jsonify({'success': False, 'message': '会话不存在'}), 400

    axis = data.get('axis', 'z').lower()
    degrees = data.get('degrees', 0)

    if axis not in ('x', 'y', 'z'):
        return jsonify({'success': False, 'message': f'不支持的轴: {axis}'}), 400

    try:
        session = _rotation_sessions[session_id]
        trsf = _rotation_transform(axis, degrees)
        session['cur_shape'] = _apply_transform(session['cur_shape'], trsf)
        session['cur_mesh'] = _apply_mesh_rotation(session['cur_mesh'], axis, degrees)
        return jsonify({'success': True, 'message': '旋转完成'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@rotation_bp.route('/reset', methods=['POST'])
def reset():
    """Reset rotation to original."""
    data = request.get_json()
    session_id = data.get('session_id')

    if session_id not in _rotation_sessions:
        return jsonify({'success': False, 'message': '会话不存在'}), 400

    session = _rotation_sessions[session_id]
    session['cur_shape'] = session['orig_shape']
    session['cur_mesh'] = _shape_to_mesh(session['cur_shape'])
    return jsonify({'success': True, 'message': '已重置'})


@rotation_bp.route('/download/<session_id>', methods=['GET'])
def download_step(session_id):
    """Download rotated STEP file."""
    if session_id not in _rotation_sessions:
        return jsonify({'success': False, 'message': '会话不存在'}), 404

    session = _rotation_sessions[session_id]
    try:
        step_bytes = _shape_to_step_bytes(session['cur_shape'])
        return send_file(
            io.BytesIO(step_bytes),
            as_attachment=True,
            download_name=session['filename'],
            mimetype='application/step'
        )
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@rotation_bp.route('/preview/<session_id>', methods=['GET'])
def preview_stl(session_id):
    """Preview the current rotated mesh as STL."""
    if session_id not in _rotation_sessions:
        return jsonify({'success': False, 'message': '会话不存在'}), 404

    session = _rotation_sessions[session_id]
    try:
        stl_bytes = session['cur_mesh'].export(file_type='stl')
        response = send_file(
            io.BytesIO(stl_bytes),
            as_attachment=False,
            download_name=session['filename'],
            mimetype='model/stl'
        )
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
