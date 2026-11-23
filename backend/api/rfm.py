"""Persistent RFM sessions: measured geometry, process dimensions and CAD outputs."""
import base64
import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid

import numpy as np
from flask import Blueprint, request, jsonify, send_file
from config import PP_TUBE_DB, PP_DESIGN_DB, DATA_DIR
from core.pp.thickness_db import list_database_tubes, get_full_analysis
from core.pp.design_db import get_design as get_pp_design
from core.pp.process_length import rfm_process_length
from core.pp.profile_qa.length_correction import (
    apply_split_length_correction_to_sections,
    correction_args_from_validation,
)
from core.rfm.processors import get_processor, PROCESSOR_MAP
from core.rfm.quality import inspect_solid
from core.common.solid_geometry import cut_mold, mesh_from_shape, step_bytes

rfm_bp = Blueprint('rfm', __name__, url_prefix='/api/rfm')
logger = logging.getLogger(__name__)
_SESS_DIR = os.path.join(DATA_DIR, 'rfm_sessions')
os.makedirs(_SESS_DIR, exist_ok=True)
_processor_cache = {}


def _sess_file(sid):
    if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', sid):
        raise ValueError('无效会话 ID')
    return os.path.join(_SESS_DIR, sid+'.json')


def _load_sess(sid):
    path = _sess_file(sid)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


def _save_sess(sid, data):
    _write_json(_sess_file(sid), data)

_RFM_OP_LABELS = {'shaping': '成型', 'belling': '扩口', 'flattening': '压扁', 'bending': '压弯'}
_RFM_MODEL_NAMES = {
    'shaping': '外径形腔',
    'belling': '扩口腔体',
    'flattening': '压扁模型',
    'bending': '压弯模型',
}


def _part_label(sess):
    """件号：优先会话里记录的 part_label，其次设计来源文件名。"""
    label = str(sess.get('part_label') or '').strip()
    if label:
        return label
    name = str(sess.get('source_file_name') or '').strip()
    if name:
        stem = os.path.splitext(os.path.basename(name))[0].strip()
        if stem:
            return stem
    design_id = sess.get('design_id')
    return f'design{design_id}' if design_id else '管件'


def _download_name(sess, kind='', preview=False):
    """`件号_工序_模型__路线.step`；反切模具为 `件号_工序_反切模具.step`。"""
    part = _part_label(sess)
    op = sess.get('operation') or ''
    label = _RFM_OP_LABELS.get(op, op or '模型')
    ext = '.stl' if preview else '.step'
    if sess.get('mold_info'):
        return f'{part}_{label}_反切模具{ext}'
    if kind == 'mandrel' or (not kind and op == 'belling'):
        model = '扩口芯轴'
    else:
        model = _RFM_MODEL_NAMES.get(op, '模型')
    entity = sess.get((kind + '_' if kind else '') + 'entity') or {}
    route = entity.get('route') if isinstance(entity, dict) else None
    suffix = f'__{route}' if entity.get('applied') and route in ('c1', 'pipe') else ''
    return f'{part}_{label}_{model}{suffix}{ext}'

def _write_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@rfm_bp.errorhandler(ValueError)
def invalid_request(exc):
    return jsonify(success=False, message=str(exc)), 400


@rfm_bp.errorhandler(RuntimeError)
def geometry_failure(exc):
    return jsonify(success=False, message='CAD 生成失败：'+str(exc)), 500


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('请求必须是 JSON 对象')
    return data


def _require(sid):
    sess = _load_sess(sid)
    if sess is None:
        raise ValueError('会话不存在，请重新加载数据')
    return sess


# ---------------------------------------------------------------------------
# v1 口径：成型页选 121 时，先确保该件有 v1 测量缓存（没有就后台测）
# ---------------------------------------------------------------------------
_V1_TASKS = {}
_V1_LOCK = threading.Lock()
_V1_KEEP = 30


def _v1_source(sess):
    design = get_pp_design(int(sess.get('design_id') or 0), PP_DESIGN_DB)
    if not design:
        raise ValueError('工艺设计不存在，无法准备 v1 口径')
    src = design.get('source_file_path') or ''
    if not src or not os.path.isfile(src):
        raise ValueError('工艺设计缺少原始管件文件，无法测量 v1 口径')
    name = design.get('source_file_name') or os.path.basename(src)
    part = os.path.splitext(os.path.basename(name))[0] or os.path.splitext(os.path.basename(src))[0]
    return str(part), src, design


def _v1_cache(part):
    from core.entity import settings as entity_settings
    return str(entity_settings.measured_path(part, 'v1'))


def _v1_view(task):
    return {k: task.get(k) for k in (
        'task_id', 'status', 'progress', 'message', 'seconds', 'part',
        'cached', 'error', 'started_at', 'measured_path',
    )}


def _v1_update(task_id, **fields):
    with _V1_LOCK:
        task = _V1_TASKS.get(task_id)
        if task is not None:
            task.update(fields)


def _v1_worker(task_id, part, source, cache, force):
    from core.entity import prepare_proc, settings as entity_settings
    stop = threading.Event()
    with _V1_LOCK:
        task = _V1_TASKS.get(task_id)
        if task is not None:
            task['_stop'] = stop
    state = {'pct': 0}

    def log(line):
        text = str(line or '')
        pct = None
        match = re.search(r'(\d+)\s*/\s*(\d+)', text)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            if total >= 10 and 0 <= done <= total:
                pct = int(100.0 * done / total)
        if pct is None:
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
            if match:
                pct = int(float(match.group(1)))
        fields = {'message': text[-200:]} if text else {}
        if pct is not None and pct > state['pct']:
            state['pct'] = min(99, pct)
            fields['progress'] = state['pct']
        if fields:
            _v1_update(task_id, **fields)

    started = time.time()
    try:
        result = prepare_proc.run_prepare(
            part=part,
            step=source,
            end_treatment='v1',
            out_root=str(entity_settings.OUT_ROOT),
            models=(),
            force=bool(force),
            log=log,
            stop_event=stop,
            timeout=7200.0,
            measure_only=True,
        )
        _v1_update(
            task_id, status='done', progress=100,
            seconds=round(time.time() - started, 1),
            measured_path=result.get('measured_path'),
            cached=bool(result.get('cached')),
            message='v1 测量完成',
        )
    except Exception as exc:  # noqa: BLE001 - 任务状态里报告
        cancelled = type(exc).__name__ == 'PrepareCancelled'
        _v1_update(
            task_id,
            status='cancelled' if cancelled else 'error',
            seconds=round(time.time() - started, 1),
            error=None if cancelled else str(exc),
            message='已取消' if cancelled else f'v1 测量失败：{exc}',
        )


def _start_v1(sess, force=False):
    if sess.get('operation') != 'shaping':
        raise ValueError('只有成型/外径形腔需要 v1 口径')
    part, source, _design = _v1_source(sess)
    cache = _v1_cache(part)
    if os.path.isfile(cache) and not force:
        return {
            'status': 'cached', 'cached': True, 'progress': 100,
            'part': part, 'measured_path': cache,
            'message': '已有 v1 测量缓存，直接复用',
        }
    with _V1_LOCK:
        for existing in _V1_TASKS.values():
            if existing.get('status') in ('running', 'cancelling'):
                raise ValueError('已有 v1 测量在进行，请等它结束或先取消')
        task_id = 'v1_' + uuid.uuid4().hex[:12]
        task = {
            'task_id': task_id, 'status': 'running', 'progress': 0,
            'message': '正在启动 v1 测量…', 'part': part, 'cached': False,
            'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        _V1_TASKS[task_id] = task
        finished = [k for k, v in _V1_TASKS.items()
                    if v.get('status') in ('done', 'error', 'cancelled')]
        for key in finished[:-_V1_KEEP]:
            _V1_TASKS.pop(key, None)
    threading.Thread(
        target=_v1_worker, args=(task_id, part, source, cache, force), daemon=True,
    ).start()
    return _v1_view(task)


@rfm_bp.route('/v1/prepare', methods=['POST'])
def v1_prepare():
    data = _body()
    sess = _require(data.get('session_id'))
    return jsonify(success=True, **_start_v1(sess, bool(data.get('force'))))


@rfm_bp.route('/v1/status/<task_id>')
def v1_status(task_id):
    with _V1_LOCK:
        task = _V1_TASKS.get(task_id)
    if task is None:
        raise ValueError('v1 测量任务不存在或已过期')
    return jsonify(success=True, **_v1_view(task))


@rfm_bp.route('/v1/cancel/<task_id>', methods=['POST'])
def v1_cancel(task_id):
    with _V1_LOCK:
        task = _V1_TASKS.get(task_id)
        stop = task.get('_stop') if task else None
    if task is None:
        raise ValueError('v1 测量任务不存在或已过期')
    if stop is not None:
        stop.set()
    _v1_update(task_id, status='cancelling', message='正在取消 v1 测量…')
    return jsonify(success=True, **_v1_view(task))


_QA_AUTO_PASS_MAX_STATIONS = 3
_QA_AUTO_PASS_MAX_NORMAL_DEG = 5.0
_QA_STRICT = os.environ.get('GTAPP_QA_STRICT', '').strip().lower() in ('1', 'true', 'yes', 'on')


def measurement_review_summary(results):
    """Describe the review flags on a stored measurement (no exception raised)."""
    quality = results.get('analysis_quality') or {}
    sections = results.get('measured_sections_full') or results.get('sections_full') or []
    flagged = [s for s in sections if s.get('measurement_status') == 'needs_review']
    rejected = quality.get('rejected_sections') or []
    try:
        count = int(quality.get('review_station_count') or 0)
    except (TypeError, ValueError):
        count = 0
    count = max(count, len(flagged))
    max_normal = quality.get('max_normal_deviation_deg')
    for section in flagged:
        value = section.get('normal_deviation_deg')
        if value is not None and (max_normal is None or float(value) > float(max_normal)):
            max_normal = value
    max_residual = quality.get('max_centroid_residual_mm')
    for section in flagged:
        value = section.get('centroid_residual_mm')
        if value is not None and (max_residual is None or float(value) > float(max_residual)):
            max_residual = value
    wall_bad = any(
        float(s.get('wall_thickness_min') or 0) < 0.1 * float(s.get('wall_thickness') or 0)
        for s in flagged
    )
    try:
        normal_value = float(max_normal) if max_normal is not None else None
    except (TypeError, ValueError):
        normal_value = None
    try:
        residual_value = float(max_residual) if max_residual is not None else None
    except (TypeError, ValueError):
        residual_value = None
    blocked = bool(count or rejected or flagged)
    auto_passable = bool(
        blocked
        and not rejected
        and 0 < count <= _QA_AUTO_PASS_MAX_STATIONS
        and normal_value is not None
        and normal_value <= _QA_AUTO_PASS_MAX_NORMAL_DEG
        and not wall_bad
        and (residual_value is None or residual_value <= 0.2)
    )
    return {
        'blocked': blocked,
        'review_station_count': count,
        'rejected_count': len(rejected),
        'max_normal_deviation_deg': normal_value,
        'max_centroid_residual_mm': residual_value,
        'wall_thickness_abnormal': wall_bad,
        'acknowledged': bool(quality.get('review_acknowledged')),
        'auto_passable': auto_passable,
        'auto_pass_max_stations': _QA_AUTO_PASS_MAX_STATIONS,
        'auto_pass_max_normal_deg': _QA_AUTO_PASS_MAX_NORMAL_DEG,
    }


def check_measurement_quality(results, *, auto_pass=True):
    """Block clearly unreliable measurements, warn through tiny ones.

    A single station at 2.1° used to block the whole design with no way for the
    operator to continue. Stations that only squeak past the 2° normal threshold
    (<=3 stations, <=3°, no rejected/thin/centroid problems) now pass with a
    warning; the UI can also persist an explicit operator acknowledgement.
    """
    info = measurement_review_summary(results)
    if not info['blocked']:
        return info
    if info['acknowledged'] and not info['rejected_count']:
        logger.info('管件测量待复核截面已由操作员确认放行：%s 站', info['review_station_count'])
        return info
    if auto_pass and not _QA_STRICT and info['auto_passable']:
        logger.warning(
            '管件测量存在 %s 个微小超差截面（最大法向偏差 %.3f°），按微小偏差自动放行',
            info['review_station_count'],
            info['max_normal_deviation_deg'] or 0.0,
        )
        info['auto_passed'] = True
        return info
    detail = ''
    if info['review_station_count']:
        detail = f'（{info["review_station_count"]} 个待复核截面'
        if info['max_normal_deviation_deg'] is not None:
            detail += f'，最大法向偏差 {info["max_normal_deviation_deg"]:.2f}°'
        detail += '）'
    elif info['rejected_count']:
        detail = f'（{info["rejected_count"]} 个截面被剔除）'
    error = ValueError('管件测量未通过校核，存在待复核或缺失截面；请先修正读取结果' + detail)
    error.details = info
    raise error


def _sections_for_rfm(results):
    lv = results.get('length_validation') or (results.get('qa') or {}).get('length_validation') or {}
    args = correction_args_from_validation(lv)
    source = results.get('stp_sections_full') or results.get('sections_full') or results.get('measured_sections_full') or []
    if args and len(source) >= 2:
        try:
            return apply_split_length_correction_to_sections(source, **args)
        except ValueError:
            pass
    return list(results.get('sections_full') or source or [])


def _config(config):
    if not isinstance(config, dict):
        raise ValueError('config 必须是对象')
    ranges = {
        'belling_length': (0.01, 100000), 'wall_thickness': (0.001, 1000),
        'length_allowance': (0, 1000), 'resample_step_mm': (.2, 5),
        'revolve_segments': (32, 256), 'flattening_length': (.01, 100000),
        'transition_length': (0, 100000), 'flattening_allowance': (0, 50),
        'total_length': (.01, 100000), 'num_sections': (2, 1000),
        'section_points': (32, 512), 'loft_axial_subdiv': (1, 4),
        'ellipse_x': (.01, 1000), 'ellipse_y': (.01, 1000),
        'mold_margin': (1, 500), 'mold_size_x': (.01, 100000),
        'mold_size_y': (.01, 100000), 'mold_size_z': (.01, 100000),
    }
    result = {}
    for key, value in config.items():
        if key == 'wall_thickness_source':
            if value not in ('data', 'constant'):
                raise ValueError('芯轴壁厚来源必须是 data 或 constant')
            result[key] = value
        elif key in ('mold_enabled', 'mold_auto_size', 'flattening_executed'):
            if not isinstance(value, bool):
                raise ValueError(key+' 必须是布尔值')
            result[key] = value
        elif key in ('ellipse_x', 'ellipse_y', 'total_length') and value is None:
            result[key] = None
        elif key in ranges and value is not None and value != '':
            if isinstance(value, bool):
                raise ValueError(key+' 必须是数值')
            try:
                v = float(value)
            except (ValueError, TypeError):
                raise ValueError(key+' 必须是数值')
            lo, hi = ranges[key]
            if not np.isfinite(v) or not lo <= v <= hi:
                raise ValueError(f'{key} 必须在 {lo}–{hi} 范围内')
            result[key] = int(v) if key in ('num_sections', 'section_points', 'loft_axial_subdiv', 'revolve_segments') else v
    if bool(result.get('ellipse_x')) != bool(result.get('ellipse_y')):
        raise ValueError('椭圆 X/Y 全轴长必须一起填写')
    return result


def _build_processor_from_sess(sess):
    proc = get_processor(sess['operation'])
    proc.set_session_data(sess.get('centerline') or None, sess.get('perimeter_data', []))
    proc.set_sections(sess.get('sections_full', []))
    for key, value in _config(sess.get('config', {})).items():
        if hasattr(proc, key):
            setattr(proc, key, value)
    return proc


def _get_proc(sid, op=None):
    sess = _require(sid)
    if op and sess['operation'] != op:
        raise ValueError('会话类型不匹配')
    # Rebuild from persisted data: consistent after restart or in another worker.
    return _build_processor_from_sess(sess)


def _init(results, operation, design=None, source=None):
    if operation not in PROCESSOR_MAP:
        raise ValueError('未知操作')
    design = design or {}
    sections = _sections_for_rfm(results)
    rows = design.get('centerline_perimeter_data') or results.get('centerline_perimeter_data') or results.get('wall_thickness_data') or []
    if not rows and sections:
        rows = [{'中心线长度(mm)': s['cumulative_length'], '外径周长(mm)': s['perimeter'],
                 '壁厚(mm)': s.get('wall_thickness')} for s in sections]
    if len(rows) < 2:
        raise ValueError('缺少工艺周长数据')
    if operation in ('shaping', 'bending') and not sections:
        raise ValueError('缺少原始截面坐标，请重新读取并保存管件')
    centerline = [s['position'] for s in sections] if sections else results.get('center_line', {}).get('points', [])
    config = {}
    params = design.get('params') or {}
    length = rfm_process_length(results, rows, design)
    if length <= 0:
        length = float(rows[-1]['中心线长度(mm)']) if rows else 0.0
    if operation == 'belling':
        config['belling_length'] = min(float(params.get('L1') or 200), length)
        config['wall_thickness'] = float(params.get('T3') or 2)
        config['wall_thickness_source'] = 'constant' if params.get('T3') else 'data'
    if operation in ('flattening', 'bending'):
        config['flattening_length'] = min(float(params.get('L1') or 200), length)
        config['transition_length'] = min(150., max(0., length-config['flattening_length']))
        config['total_length'] = length
    sid = 'rfm_'+uuid.uuid4().hex
    sess = {'operation': operation, 'config': config, 'source': source,
            'design_id': design.get('id'), 'design_params': params,
            'pp_method': design.get('pp_method', ''), 'vehicle_spec': design.get('vehicle_spec', ''),
            'tube_number': design.get('tube_number', ''), 'centerline': centerline,
            'source_file_name': design.get('source_file_name') or design.get('display_name') or '',
            'sections_full': sections, 'perimeter_data': rows,
            'analysis_quality': results.get('analysis_quality', {}),
            'generated': False, 'cavity_generated': False, 'mandrel_generated': False,
            'part_label': _part_label({'source_file_name': design.get('source_file_name') or design.get('display_name') or '',
                                        'design_id': design.get('id')})}
    _save_sess(sid, sess)
    return jsonify(success=True, session_id=sid, operation=operation, design_params=params,
                   part_label=sess['part_label'],
                   pp_method=sess['pp_method'], vehicle_spec=sess['vehicle_spec'], tube_number=sess['tube_number'],
                   centerline_length=length, num_sections=len(sections), wall_thickness_data_rows=len(rows),
                   config=config, analysis_quality=sess['analysis_quality'])


@rfm_bp.route('/init-from-design', methods=['POST'])
def init_from_design():
    data = _body()
    design = get_pp_design(int(data.get('design_id') or 0), PP_DESIGN_DB)
    if not design:
        raise ValueError('工艺设计不存在')
    tube = get_full_analysis(int(design.get('source_tube_id') or 0), PP_TUBE_DB)
    if not tube:
        raise ValueError('工艺设计缺少关联管件，请重新保存工艺设计')
    return _init(tube.get('results') or {}, data.get('operation', 'belling'), design, {'tube_id': tube['id']})


@rfm_bp.route('/init-from-pp', methods=['POST'])
def init_from_pp():
    from api.pp import _pp_sessions, _session_lock
    data = _body()
    with _session_lock:
        sess = _pp_sessions.get(data.get('pp_session_id'))
    if not sess or not sess.get('results'):
        raise ValueError('PP 会话不存在或分析未完成')
    return _init(sess['results'], data.get('operation', 'belling'), source={'pp_session_id': data.get('pp_session_id')})


@rfm_bp.route('/db-tubes/load', methods=['POST'])
def load_db_tube():
    data = _body()
    record = get_full_analysis(int(data.get('tube_id') or 0), PP_TUBE_DB)
    if not record:
        raise ValueError('管件记录不存在')
    return _init(record.get('results') or {}, data.get('operation', 'flattening'), source={'tube_id': data.get('tube_id')})


@rfm_bp.route('/db-tubes')
def list_db_tubes():
    tubes = []
    for row in list_database_tubes(PP_TUBE_DB).to_dict(orient='records'):
        length = float(row.get('中心线长度(mm)') or 0)
        tubes.append({'id': int(row['ID']),
                      'display_name': str(row.get('显示名称', row.get('文件名', ''))),
                      'file_name': str(row.get('文件名', '')),
                      'centerline_length': length if np.isfinite(length) else 0})
    return jsonify(success=True, tubes=tubes)


def _clear_outputs(sess):
    for key in list(sess):
        if key.endswith('_b64') or key == 'mold_info':
            sess.pop(key)
    for key in ('generated', 'cavity_generated', 'mandrel_generated'):
        sess[key] = False


@rfm_bp.route('/process', methods=['POST'])
def process():
    data = _body()
    sid = data.get('session_id')
    sess = _require(sid)
    config = _config(data.get('config', {}))
    sess['config'] = {**sess.get('config', {}), **config}
    _clear_outputs(sess)
    _save_sess(sid, sess)
    return jsonify(success=True, message='配置已保存，原生成结果已失效')


def _v1_121_shape(sess, log):
    """用该件的 v1 测量缓存导出成型外径形腔，再跑 121 保形。"""
    from core.entity import checks, giant_adapter, rfm_bridge

    part, _source, _design = _v1_source(sess)
    cache = _v1_cache(part)
    if not os.path.isfile(cache):
        raise ValueError('该件还没有 v1 测量缓存，请先准备 v1 口径（后台测量约 5–30 分钟）')
    root = os.path.join(str(DATA_DIR), 'rfm_v1', uuid.uuid4().hex[:10])
    os.makedirs(root, exist_ok=True)
    records = giant_adapter.export_models(
        part=part, analysis=cache, out_dir=root,
        models=['成型_外径形腔'], log=log, naming='key',
    )
    record = records.get('成型_外径形腔') or {}
    if record.get('status') != 'ok' or not record.get('step'):
        raise ValueError('v1 模型导出失败：' + str(record.get('reason') or '未知原因'))
    out_step = os.path.join(root, 'shaping_cavity_v1_121.step')
    built = rfm_bridge._build_selfref(
        record['step'], out_step, '成型_外径形腔', 'shaping', log,
    )
    shape = checks.read_step(built.get('step') or out_step)
    info = dict(built.get('info') or {})
    info.update({
        'route': '121', 'requested_route': '121', 'applied': True,
        'caliber': 'v1', 'source_part': part,
        'measured_path': cache,
    })
    return shape, info


def _generate(kind):
    data = _body()
    sid = data.get('session_id')
    sess = _require(sid)
    op = sess['operation']
    proc = _build_processor_from_sess(sess)
    if kind in ('cavity', 'mandrel') and op != 'belling':
        raise ValueError('此工序不支持所选模型')
    prefix = kind+'_' if kind else ''
    sess[prefix+'generated'] = False
    for key in (prefix+'mesh_bytes_b64', prefix+'step_bytes_b64'):
        sess.pop(key, None)
    _save_sess(sid, sess)
    if kind == 'mandrel':
        mesh = proc.generate_mandrel()
        shape = proc.shape
    elif kind == 'cavity' or (not kind and op == 'belling' and sess['config'].get('mold_enabled')):
        mesh = proc.generate_cavity_model()
        shape = proc.cavity_shape
    else:
        mesh = proc.generate_model()
        shape = proc.shape
    # Judge the surface before the mould cut: a boolean would bury a folded
    # band under box faces and the seam sweep could no longer see it.
    quality = inspect_solid(shape)
    if quality['status'] == 'reject':
        raise ValueError(quality['warnings'][0] if quality['warnings'] else '生成曲面未通过几何自检')
    mold_info = None
    if not kind and sess['config'].get('mold_enabled'):
        shape, mold_info = cut_mold(shape, sess['config'])
        mesh = mesh_from_shape(shape)

    # 实体化：把刚生成的 shape 直接送进流水线，用 4 面实体替换预览/体积/下载。
    # 反切模具是整块切出来的，不做 4 面化。
    entity_info = None
    if mold_info is None:
        requested = str(data.get('entity_route') or data.get('route') or '').strip().lower()
        # 成型页选 121 = v1 测量口径 + 121 保形；扩口/压扁/压弯仍按当前工序外形。
        if op == 'shaping' and not kind and requested in ('121', 'selfref', 'reference', '保形'):
            shape, entity_info = _v1_121_shape(
                sess, lambda msg: logger.info('[v1] %s', msg)
            )
            mesh = mesh_from_shape(shape)
            quality = inspect_solid(shape)
            from core.entity import rfm_bridge
            logger.info('v1+121 实体化完成：%s', rfm_bridge.describe(entity_info))
        else:
            try:
                from core.entity import rfm_bridge
                conversion = rfm_bridge.convert_shape(
                    shape, op,
                    sections=sess.get('sections_full'),
                    kind=kind,
                    entity_route=data.get('entity_route') or data.get('route'),
                    log=lambda msg: logger.info('[entity] %s', msg),
                )
                shape = conversion['shape']
                mesh = mesh_from_shape(shape)
                entity_info = conversion['info']
                quality = inspect_solid(shape)
                logger.info('实体化完成：%s', rfm_bridge.describe(entity_info))
            except Exception as exc:  # noqa: BLE001 - 实体化失败不拖垮原流程
                entity_info = {'applied': False, 'error': str(exc)}
                logger.warning('实体化失败，保留原模型：%s', exc)
    else:
        entity_info = {'applied': False, 'reason': '反切模具不转 4 面实体'}

    step = step_bytes(shape)
    stl = mesh.export(file_type='stl')
    sess[prefix+'mesh_bytes_b64'] = base64.b64encode(stl).decode()
    sess[prefix+'step_bytes_b64'] = base64.b64encode(step).decode()
    sess[prefix+'entity'] = entity_info
    sess[prefix+'generated'] = True
    if not kind and op == 'belling' and not mold_info:
        # Keep the legacy primary output, but always expose the mandrel through
        # its own stable route, even when another output is generated afterward.
        for key in ('mesh_bytes_b64', 'step_bytes_b64', 'generated'):
            sess['mandrel_'+key] = sess[key]
    if not kind:
        sess['mold_info'] = mold_info
    _save_sess(sid, sess)
    out_dir = os.path.join(DATA_DIR, 'rfm_'+op)
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for fmt, content in (('stl', stl), ('step', step)):
        path = os.path.join(out_dir, sid+('_'+kind if kind else '')+'.'+fmt)
        with open(path, 'wb') as stream:
            stream.write(content)
        paths[fmt+'_path'] = path
    if isinstance(entity_info, dict):
        entity_info = dict(entity_info, download_name=_download_name(sess, kind))
    return jsonify(success=True, vertices=len(mesh.vertices), faces=len(mesh.faces),
                   mold_info=mold_info, entity=entity_info,
                   download_name=_download_name(sess, kind),
                   part_label=_part_label(sess),
                   model_kind='mold' if mold_info else ('mandrel' if op == 'belling' and not kind else (kind or op)),
                   quality=quality,
                   belling_profile=(getattr(proc, 'mandrel_profile', None) or getattr(proc, 'cavity_profile', None)) if op == 'belling' else None,
                   section_summary={
                       'source': '原始外轮廓' if op == 'shaping' else '前序工艺周长/轴长',
                       'stations': len(sess.get('sections_full') or sess['perimeter_data']),
                       'full_axes_min_mm': (2*proc.generated_axes.min(axis=0)).tolist() if hasattr(proc, 'generated_axes') else None,
                       'full_axes_max_mm': (2*proc.generated_axes.max(axis=0)).tolist() if hasattr(proc, 'generated_axes') else None,
                   }, **paths)


@rfm_bp.route('/generate', methods=['POST'])
def generate():
    return _generate('')


@rfm_bp.route('/generate-cavity', methods=['POST'])
def generate_cavity():
    return _generate('cavity')


@rfm_bp.route('/generate-mandrel', methods=['POST'])
def generate_mandrel():
    return _generate('mandrel')


def _download(sid, kind='', preview=False):
    sess = _require(sid)
    prefix = kind+'_' if kind else ''
    if not sess.get(prefix+'generated'):
        raise ValueError('请先生成模型')
    key = prefix+('mesh_bytes_b64' if preview else 'step_bytes_b64')
    if not sess.get(key):
        raise ValueError('旧会话缺少 CAD 实体，请重新生成')
    content = base64.b64decode(sess[key])
    response = send_file(io.BytesIO(content), as_attachment=True,
                         download_name=_download_name(sess, kind, preview),
                         mimetype='application/octet-stream' if preview else 'application/step')
    response.headers['Cache-Control'] = 'no-store'
    return response


@rfm_bp.route('/preview/<session_id>')
def preview_stl(session_id):
    return _download(session_id, preview=True)


@rfm_bp.route('/download/<session_id>')
def download_step(session_id):
    return _download(session_id)


@rfm_bp.route('/preview-cavity/<session_id>')
def preview_cavity_stl(session_id):
    return _download(session_id, 'cavity', True)


@rfm_bp.route('/download-cavity/<session_id>')
def download_cavity_step(session_id):
    return _download(session_id, 'cavity')


@rfm_bp.route('/preview-mandrel/<session_id>')
def preview_mandrel_stl(session_id):
    return _download(session_id, 'mandrel', True)


@rfm_bp.route('/download-mandrel/<session_id>')
def download_mandrel_step(session_id):
    return _download(session_id, 'mandrel')


@rfm_bp.route('/save', methods=['POST'])
def save():
    sid = _body().get('session_id')
    sess = _require(sid)
    if not sess.get('generated'):
        raise ValueError('请先生成模型')
    # Keep the existing catalog contract; geometry and all inputs remain in the
    # complete persistent session referenced by each catalog record.
    op = sess['operation']
    out_dir = os.path.join(DATA_DIR, 'rfm_'+op)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, op+'_database.json')
    catalog = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as stream:
            catalog = json.load(stream)
        if not isinstance(catalog, dict):
            raise ValueError('已有模型目录格式无效，未覆盖历史目录')
    catalog[sid] = {'session_id': sid, 'operation': op, 'config': sess['config'],
                    'generated': True, 'design_id': sess.get('design_id'),
                    'source': sess.get('source'), 'mold_info': sess.get('mold_info')}
    if op == 'belling':
        catalog[sid].update(mandrel_generated=sess.get('mandrel_generated', False),
                            cavity_generated=sess.get('cavity_generated', False))
    _write_json(path, catalog)
    sess['saved'] = True
    _save_sess(sid, sess)
    return jsonify(success=True, message='模型、参数和来源数据已保存')
