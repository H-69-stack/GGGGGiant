"""一体化流水线 Web API（蓝图 ``/api/entity``）。

前端「一体化流水线」页面用到的全部接口：

* ``GET  /status``            运行状态、可选件/模型/路线、输出根目录
* ``GET  /parts``             扫 ``settings.SRC_PARTS_DIR``（含 ``uploads/``）的原始管件
* ``GET  /parts/<part>``      单件全流程（含 ``process_summary`` / ``route_pref`` / ``c1_impl``；
  别名 ``/parts/<part>/flow``）
* ``GET  /plan``              静态计划预览（件 × 模型 × 路线）
* ``POST /run``               后台线程执行 ``core.entity.orchestrator.run(ctx)``
* ``GET  /jobs``              任务列表（内存）
* ``GET  /jobs/<id>``         任务进度：每件 × 每类 × 每路线的状态/体积/面数/耗时
* ``GET  /jobs/<id>/files``   该任务已产出的文件清单
* ``POST /stop``              两档停止：``{"mode":"graceful"|"force"}``
* ``POST /upload``            上传 1..N 个 .stp/.step → 子进程并行测量 + 入库
* ``GET  /uploads``           上传任务列表（含并行度 / 活跃数）
* ``GET  /uploads/<id>``      单个上传任务
* ``POST /uploads/clear``     清空已结束的上传任务记录（仅内存表，不动缓存/数据库）
* ``GET  /import``            已导入主流程的管件队列（``_config/import_queue.json``）
* ``POST /import``            把并行测量完的上传件一键导入主流程（只登记队列，不动几何）
* ``GET|POST /parts/<part>/route-pref``  外径形腔 c1 / 121 与分型方向偏好
* ``GET  /download?path=``    下载 ``out_root`` 白名单内的 STEP / 报告

orchestrator 由 S7 并行开发，本文件只在函数内部 import，缺失时返回可读的
503，不阻塞蓝图注册。
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

entity_bp = Blueprint('entity', __name__)

#: 内存中最多保留的任务数
_MAX_JOBS = 20
#: 单个任务最多保留的日志行数
_MAX_LOG_LINES = 2000
#: 每次返回给前端的日志行数
_LOG_TAIL = 200
#: 允许下载的后缀（STEP / 报告 / 日志）
_DOWNLOAD_SUFFIXES = frozenset({
    '.step', '.stp', '.json', '.md', '.csv', '.log', '.stl', '.txt',
})
_SOURCE_SUFFIXES = frozenset({'.stp', '.step'})
#: 上传件子目录（相对 ``settings.SRC_PARTS_DIR``）
_UPLOAD_SUBDIR = 'uploads'
#: 上传任务保留条数
_MAX_UPLOAD_TASKS = 50
#: 上传后台线程等待「当前任务结束」的轮询间隔（秒）与上限
_UPLOAD_POLL_SECONDS = 2.0
_UPLOAD_WAIT_LIMIT = 4 * 3600.0

#: 路线偏好：目前只有外径形腔可选 c1 / 121
ROUTE_PREF_MODEL = '成型_外径形腔'
ROUTE_PREF_IMPLS = ('c1', '121')
ROUTE_PREF_DIRECTIONS = ('auto', 'X', 'Y', 'Z')
#: 手动角度写法：``angle:-27`` / ``angle=-27`` / ``angle_z:-27`` / ``angle:-27°``
_ANGLE_RE = re.compile(
    r"^angle(?:\s*[_ -]?\s*z)?\s*[:=]\s*([-+]?\d+(?:\.\d+)?)\s*°?$",
    re.IGNORECASE,
)

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.RLock()

#: 上传任务（内存）：{task_id: task}
_UPLOAD_TASKS: dict[str, dict] = {}
_UPLOAD_TASKS_LOCK = threading.RLock()
#: 上传测量并行数：环境变量 ``GTAPP_UPLOAD_PARALLEL`` 优先，缺省按核数自动分配
#: （``max(1, 物理核数 // 单件测量进程数)``，见 :func:`_auto_upload_parallel`）。
_UPLOAD_PARALLEL_ENV = 'GTAPP_UPLOAD_PARALLEL'
#: 自动分配不可用时的兜底值（1 表示一次只测一件）。
_UPLOAD_PARALLEL_FALLBACK = 1
_UPLOAD_PARALLEL_MAX = 8
#: 单件测量的建议进程数上限（站内并行实测拐点：8 个进程最快，12/16 反而更慢）。
_UPLOAD_PER_MEASURE_CAP = 8
#: 每个并行度一个线程池（测量跑子进程，线程只做等待/解析/入库）
_UPLOAD_EXECUTORS: dict[int, ThreadPoolExecutor] = {}
_UPLOAD_EXECUTOR_LOCK = threading.Lock()
#: 入库互斥：测量已经并行，SQLite 写入仍串行，避免 database is locked
_UPLOAD_DB_LOCK = threading.Lock()

#: ``process_summary`` 的小缓存：{part: (key, summary)}
_SUMMARY_CACHE: dict[str, tuple] = {}
_SUMMARY_CACHE_LOCK = threading.RLock()
#: 没有登记设计时，工艺摘要「道次」区的固定提示（设计由人工导入，不再自动补算）
_MISSING_DESIGN_NOTE = (
    '未导入工艺设计；请人工判断后导入数据库，流水线只给推荐路线（本区暂无道次）')


# ---------------------------------------------------------------- 通用小工具


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _settings():
    from core.entity import settings
    return settings


def _contracts():
    from core.entity import contracts
    return contracts


def _import_orchestrator():
    from core.entity import orchestrator
    return orchestrator


def _orchestrator_path() -> Path:
    return Path(__file__).resolve().parents[1] / 'core' / 'entity' / 'orchestrator.py'


def _error(message: str, code: int = 400):
    return jsonify({'success': False, 'message': message}), code


def _rel_to(out_root, path):
    """绝对路径 → 相对 out_root 的 posix 路径；不在 out_root 内返回 None。"""
    if not path:
        return None
    try:
        root = Path(out_root).resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        return candidate.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def _step_rel(out_root, part, step):
    """把 RouteResult.step（绝对路径或相对路径）转成 out_root 相对路径。"""
    if not step:
        return None
    candidate = Path(step)
    if candidate.is_absolute():
        return _rel_to(out_root, candidate)
    root = Path(out_root)
    for base in (root / candidate, root / part / candidate):
        try:
            if base.is_file():
                return _rel_to(out_root, base)
        except OSError:
            continue
    return None


def _size_of(path):
    try:
        return path.stat().st_size
    except OSError:
        return None


def _mtime_of(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(
            timespec='seconds')
    except OSError:
        return None


# ---------------------------------------------------------------- 路线偏好
# route_prefs.py 由 A2 维护；它还没落地时，本文件的等价实现接管（同一份 JSON、
# 同一套语义），保证本接口在并行开发期也能读写/解析偏好。

_PREFS_FILENAME = 'route_prefs.json'
_PREFS_LOCK = threading.RLock()
_PREFS_CACHE = {'key': None, 'data': None}


def _prefs_file() -> Path:
    """``route_prefs.json`` 路径（``OUT_ROOT/_config/route_prefs.json``）。"""
    try:
        from core.entity import route_prefs
        return Path(route_prefs.prefs_path())
    except Exception:
        try:
            out_root = Path(_settings().OUT_ROOT)
        except Exception:
            out_root = Path('.')
        return out_root / '_config' / _PREFS_FILENAME


def _prefs_default() -> dict:
    return {'version': 1, 'parts': {}}


def _prefs_read_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return _prefs_default()
    if not isinstance(data, dict):
        return _prefs_default()
    data.setdefault('version', 1)
    if not isinstance(data.get('parts'), dict):
        data['parts'] = {}
    return data


def _load_route_prefs() -> dict:
    """读路线偏好；缺失/损坏 → ``{"version":1,"parts":{}}``（不抛异常）。"""
    try:
        from core.entity import route_prefs
    except Exception:
        route_prefs = None
    if route_prefs is not None:
        try:
            data = route_prefs.load_prefs()
            if isinstance(data, dict):
                data.setdefault('version', 1)
                if not isinstance(data.get('parts'), dict):
                    data['parts'] = {}
                return data
        except Exception:
            pass
    path = _prefs_file()
    try:
        stat = path.stat()
    except OSError:
        return _prefs_default()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _PREFS_LOCK:
        if _PREFS_CACHE.get('key') == cache_key and _PREFS_CACHE.get('data') is not None:
            return _PREFS_CACHE['data']
    data = _prefs_read_file(path)
    with _PREFS_LOCK:
        _PREFS_CACHE['key'] = cache_key
        _PREFS_CACHE['data'] = data
    return data


def _save_route_prefs(prefs: dict) -> dict:
    """写路线偏好（优先走 ``route_prefs.save_prefs``，否则就地写 JSON）。"""
    prefs = dict(prefs or {})
    prefs.setdefault('version', 1)
    if not isinstance(prefs.get('parts'), dict):
        prefs['parts'] = {}
    try:
        from core.entity import route_prefs
    except Exception:
        route_prefs = None
    if route_prefs is not None and callable(getattr(route_prefs, 'save_prefs', None)):
        try:
            saved = route_prefs.save_prefs(prefs)
            if isinstance(saved, dict):
                return saved
        except Exception:
            pass
    path = _prefs_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)
    except OSError as exc:
        raise RuntimeError(f'无法写入路线偏好 {path}：{exc}') from exc
    with _PREFS_LOCK:
        _PREFS_CACHE['key'] = None
        _PREFS_CACHE['data'] = None
    return prefs


def _normalize_direction(value) -> str:
    """``auto`` / ``X`` / ``Y`` / ``Z`` / ``angle:<度>``（大小写不敏感）；非法返回空串。

    ``angle`` 是绕 +Z 轴、从 +X 逆时针为正的角度（页面「手动输入角度」用），
    例如 ``-27`` → ``angle:-27``。
    """
    text = str(value or '').strip()
    if not text:
        return ''
    if text.lower() == 'auto':
        return 'auto'
    upper = text.upper()
    if upper in ('X', 'Y', 'Z'):
        return upper
    match = _ANGLE_RE.match(text)
    if match:
        return 'angle:%g' % float(match.group(1))
    return ''


def _normalize_model_pref(raw) -> dict:
    if isinstance(raw, str):
        raw = {'impl': raw}
    raw = raw if isinstance(raw, dict) else {}
    impl = str(raw.get('impl') or 'default').strip().lower()
    if impl not in ('default',) + ROUTE_PREF_IMPLS:
        impl = 'default'
    return {
        'impl': impl,
        'direction': _normalize_direction(raw.get('direction')) or 'auto',
    }


def _normalize_part_pref(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    models = {}
    source = raw.get('models')
    if isinstance(source, dict):
        for key, value in source.items():
            models[str(key)] = _normalize_model_pref(value)
    return {
        'direction': _normalize_direction(raw.get('direction')) or 'auto',
        'models': models,
    }


def _part_pref(prefs, part) -> dict:
    """该件的偏好；无记录 → ``{"direction":"auto","models":{}}``。"""
    if not isinstance(prefs, dict):
        prefs = _prefs_default()
    try:
        from core.entity import route_prefs
        pref = route_prefs.get_part_pref(prefs, part)
        if isinstance(pref, dict):
            return _normalize_part_pref(pref)
    except Exception:
        pass
    return _normalize_part_pref((prefs.get('parts') or {}).get(part))


def _model_pref(prefs, part, key) -> dict:
    """该件该模型的偏好；无记录 → ``{"impl":"default","direction":"auto"}``。"""
    if not isinstance(prefs, dict):
        prefs = _prefs_default()
    try:
        from core.entity import route_prefs
        pref = route_prefs.get_model_pref(prefs, part, key)
        if isinstance(pref, dict):
            return _normalize_model_pref(pref)
    except Exception:
        pass
    models = (prefs.get('parts') or {}).get(part)
    raw = (models or {}).get('models') if isinstance(models, dict) else None
    return _normalize_model_pref((raw or {}).get(key))


def _resolve_impl(prefs, part, key) -> str:
    """该「件 + 模型」在 c1 侧的实现：``"c1"`` 或 ``"121"``。"""
    impl = _model_pref(prefs, part, key).get('impl') or 'default'
    if impl in ROUTE_PREF_IMPLS:
        return impl
    try:
        from core.entity import route_prefs
        value = str(route_prefs.resolve_impl(prefs, part, key) or '').strip().lower()
        if value in ROUTE_PREF_IMPLS:
            return value
    except Exception:
        pass
    contracts = _contracts()
    try:
        reference = bool(contracts.uses_reference_route(part, key, prefs))
    except TypeError:
        reference = bool(contracts.uses_reference_route(part, key))
    return '121' if reference else 'c1'


def _resolve_direction(prefs, part, key) -> str:
    """该「件 + 模型」的分型方向：``auto`` / ``X`` / ``Y`` / ``Z``。"""
    direction = _normalize_direction(_model_pref(prefs, part, key).get('direction'))
    if direction and direction != 'auto':
        return direction
    try:
        from core.entity import route_prefs
        value = _normalize_direction(route_prefs.resolve_direction(prefs, part, key))
        if value:
            return value
    except Exception:
        pass
    part_direction = _normalize_direction(_part_pref(prefs, part).get('direction'))
    if part_direction and part_direction != 'auto':
        return part_direction
    return 'auto'


def _end_treatment_for(part, key=None, prefs=None) -> str:
    """端部口径：``121`` 保形（含参考件）→ ``v1``，其余 ``c1``。"""
    contracts = _contracts()
    pref = _load_route_prefs() if prefs is None else prefs
    try:
        return str(contracts.end_treatment_for(part, key, pref))
    except TypeError:
        pass
    except Exception:
        pass
    try:
        if _resolve_impl(pref, part, key or ROUTE_PREF_MODEL) == '121':
            return 'v1'
    except Exception:
        pass
    try:
        return str(contracts.end_treatment_for(part, key))
    except TypeError:
        return str(contracts.end_treatment_for(part))


def _set_model_pref(prefs, part, key, *, impl=None, direction=None) -> dict:
    """就地写入一条模型偏好（``impl`` / ``direction`` 为 None 时保持原值）。"""
    prefs = dict(prefs or _prefs_default())
    prefs.setdefault('version', 1)
    parts = prefs.get('parts')
    if not isinstance(parts, dict):
        parts = prefs['parts'] = {}
    pref = parts.get(part)
    if not isinstance(pref, dict):
        pref = parts[part] = {'direction': 'auto', 'models': {}}
    pref.setdefault('direction', 'auto')
    models = pref.get('models')
    if not isinstance(models, dict):
        models = pref['models'] = {}
    current = _normalize_model_pref(models.get(key))
    if impl is not None:
        current['impl'] = impl
    if direction is not None:
        current['direction'] = direction
    models[key] = current
    return prefs


def _validate_route_prefs(prefs) -> str:
    """校验 route_prefs 结构；返回错误文案（空串 = 合法）。"""
    if prefs is None:
        return ''
    if not isinstance(prefs, dict):
        return 'route_prefs 必须是 JSON 对象'
    parts = prefs.get('parts')
    if parts is None:
        return ''
    if not isinstance(parts, dict):
        return 'route_prefs.parts 必须是对象'
    for part, pref in parts.items():
        if not isinstance(pref, dict):
            return f'route_prefs.parts[{part}] 必须是对象'
        direction = pref.get('direction')
        if direction not in (None, '') and not _normalize_direction(direction):
            return f'非法方向：{direction}（可选：auto / X / Y / Z / angle:<角度>）'
        models = pref.get('models')
        if models is None:
            continue
        if not isinstance(models, dict):
            return f'route_prefs.parts[{part}].models 必须是对象'
        for key, spec in models.items():
            if isinstance(spec, str):
                spec = {'impl': spec}
            if not isinstance(spec, dict):
                return f'route_prefs.parts[{part}].models[{key}] 必须是对象'
            impl = spec.get('impl')
            if impl not in (None, '', 'default'):
                if str(impl).strip().lower() not in ROUTE_PREF_IMPLS:
                    return f'非法实现：{impl}（可选：c1 / 121）'
            direction = spec.get('direction')
            if direction not in (None, ''):
                if not _normalize_direction(direction):
                    return f'非法方向：{direction}（可选：auto / X / Y / Z / angle:<角度>）'
    return ''


def _merge_route_prefs(base, override) -> dict:
    """磁盘偏好为底、请求体覆盖（按件、按模型逐层合并）。"""
    merged = {'version': 1, 'parts': {}}
    for source in (base, override):
        if not isinstance(source, dict):
            continue
        if source.get('version') and not override:
            merged['version'] = source['version']
        parts = source.get('parts')
        if not isinstance(parts, dict):
            continue
        for part, pref in parts.items():
            if not isinstance(pref, dict):
                continue
            target = merged['parts'].setdefault(str(part), {})
            direction = _normalize_direction(pref.get('direction'))
            if direction:
                target['direction'] = direction
            models = pref.get('models')
            if not isinstance(models, dict):
                continue
            slot = target.setdefault('models', {})
            for key, spec in models.items():
                if isinstance(spec, str):
                    spec = {'impl': spec}
                if not isinstance(spec, dict):
                    continue
                current = dict(slot.get(str(key)) or {})
                impl = str(spec.get('impl') or '').strip().lower()
                if impl in ROUTE_PREF_IMPLS:
                    current['impl'] = impl
                direction = _normalize_direction(spec.get('direction'))
                if direction:
                    current['direction'] = direction
                slot[str(key)] = current
    return merged


def _direction_vector(direction) -> list | None:
    """偏好里的方向字符串 → 单位向量；``auto`` / 非法值返回 None。"""
    try:
        from core.entity import directions
        vec = directions.parse_direction(direction)
        if vec is None:
            return None
        return [round(float(c), 6) for c in vec]
    except Exception:
        return None


def _direction_angle(vector) -> float | None:
    """XY 平面角度（度，绕 +Z、从 +X 逆时针）；竖直 / 无效返回 None。"""
    if not vector:
        return None
    try:
        from core.entity import directions
        return directions.angle_of_xy(vector)
    except Exception:
        return None


def _prefs_payload(part, prefs=None) -> dict:
    """GET/POST ``route-pref`` 的返回体（老字段不动，只新增）。"""
    pref = _load_route_prefs() if prefs is None else prefs
    models = {}
    try:
        contracts = _contracts()
        keys = list(contracts.MODEL_KEYS)
    except Exception:
        keys = [ROUTE_PREF_MODEL]
    for key in keys:
        impl = _resolve_impl(pref, part, key)
        models[key] = {
            'impl': impl,
            'direction': _resolve_direction(pref, part, key),
            'end_treatment': _end_treatment_for(part, key, pref),
        }
    focus = _model_pref(pref, part, ROUTE_PREF_MODEL)
    effective_impl = models.get(ROUTE_PREF_MODEL, {}).get('impl', 'c1')
    effective_direction = _resolve_direction(pref, part, ROUTE_PREF_MODEL)
    vector = _direction_vector(effective_direction)
    payload = {
        'success': True,
        'ok': True,
        'part': part,
        'model': ROUTE_PREF_MODEL,
        'impl': effective_impl,
        'direction': effective_direction,
        'direction_vector': vector,
        'direction_angle_deg': _direction_angle(vector),
        'impls': list(ROUTE_PREF_IMPLS),
        'directions': list(ROUTE_PREF_DIRECTIONS),
        'effective': {
            'impl': effective_impl,
            'end_treatment': _end_treatment_for(part, ROUTE_PREF_MODEL, pref),
        },
        'stored': focus,
        'route_pref': _part_pref(pref, part),
        'models': models,
    }
    return payload


# ---------------------------------------------------------------- 契约数据


def _measured_cache_info(part: str, end_treatment: str = 'c1'):
    """测量缓存是否就绪：返回 ``(ready, size_mb, path)``。

    只 ``stat`` 文件大小，不解析 60–154MB 的 JSON —— 管件列表逐件解析会把页面
    卡住（一屏 20 件就是几个 GB 的读盘）。
    """
    try:
        cache_path = Path(_settings().measured_path(part, end_treatment))
    except Exception:  # noqa: BLE001 - 配置异常不该让列表整页挂掉
        return False, None, None
    try:
        size = cache_path.stat().st_size
    except OSError:
        return False, None, cache_path
    if size <= 0:
        return False, None, cache_path
    return True, round(size / 1048576.0, 1), cache_path


def _part_source_files(src: Path):
    """源目录里的 STEP：先顶层（builtin），再 ``uploads/`` 递归（upload）。

    返回 ``[(path, source), ...]``；``source`` 为 ``"builtin"`` / ``"upload"``。
    跳过以 ``_`` / ``.`` 开头的文件与目录（``_cache``、`_tmp` 等）。
    """
    found = []
    try:
        top = sorted(src.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        top = []
    for file_path in top:
        try:
            if not file_path.is_file():
                continue
        except OSError:
            continue
        if file_path.name.startswith(('_', '.')):
            continue
        if file_path.suffix.lower() in _SOURCE_SUFFIXES:
            found.append((file_path, 'builtin'))

    uploads = src / _UPLOAD_SUBDIR
    if uploads.is_dir():
        try:
            uploaded = sorted(uploads.rglob('*'), key=lambda p: str(p).lower())
        except OSError:
            uploaded = []
        for file_path in uploaded:
            try:
                if not file_path.is_file():
                    continue
            except OSError:
                continue
            try:
                rel_parts = file_path.relative_to(uploads).parts
            except ValueError:
                rel_parts = (file_path.name,)
            if any(part.startswith(('_', '.')) for part in rel_parts):
                continue
            if file_path.suffix.lower() in _SOURCE_SUFFIXES:
                found.append((file_path, 'upload'))
    return found


def _scan_parts():
    """扫 SRC_PARTS_DIR（含 ``uploads/``），返回 (parts, src_dir)。

    同件号只留一份：**上传件优先于内置件**（用户重新传了同名 STEP，就该量新的）；
    ``.stp`` / ``.step`` 同 stem 也只留一个。
    """
    settings = _settings()
    src = Path(settings.SRC_PARTS_DIR)
    src_str = str(src)
    if not src.is_dir():
        return [], src_str

    contracts = _contracts()
    by_name = {}
    for file_path, source in _part_source_files(src):
        name = file_path.stem
        if not name:
            continue
        earlier = by_name.get(name)
        if earlier is not None and (earlier['source'] == 'upload' or source != 'upload'):
            continue
        end_treatment = _end_treatment_for(name, ROUTE_PREF_MODEL)
        measured, cache_mb, _cache_path = _measured_cache_info(name, end_treatment)
        by_name[name] = {
            'name': name,
            'file': file_path.name,
            'path': str(file_path),
            'size': _size_of(file_path),
            'mtime': _mtime_of(file_path),
            'end_treatment': end_treatment,
            'reference': name in contracts.REFERENCE_PARTS,
            'source': source,
            'builtin': source == 'builtin',
            'uploaded': source == 'upload',
            'measured': measured,
            'cache_mb': cache_mb,
        }
    parts = [by_name[name] for name in sorted(by_name, key=str.lower)]
    return parts, src_str


def _models_payload():
    contracts = _contracts()
    return [
        {
            'key': m.key,
            'label': f'{m.op_label}·{m.name}',
            'op': m.op,
            'op_label': m.op_label,
            'name': m.name,
            'contour': m.contour,
            'slug': m.slug,
            'c1_kind': m.c1_kind,
        }
        for m in contracts.MODELS
    ]


def _routes_payload():
    contracts = _contracts()
    return [
        {'key': route, 'label': contracts.ROUTE_LABELS.get(route, route)}
        for route in contracts.ROUTES
    ]


def _default_routes_payload():
    """模型键 → 默认路线（外径形腔 c1，其余 pipe）的外层映射。"""
    contracts = _contracts()
    return [
        {
            'key': m.key,
            'model': f'{m.op_label}·{m.name}',
            'route': contracts.default_route(m.key),
            'route_label': contracts.ROUTE_LABELS.get(contracts.default_route(m.key), ''),
        }
        for m in contracts.MODELS
    ]


def _build_items(parts, models, routes):
    contracts = _contracts()
    model_labels = {m.key: f'{m.op_label}·{m.name}' for m in contracts.MODELS}
    route_labels = dict(contracts.ROUTE_LABELS)
    items = []
    for part in parts:
        for key in models:
            for route in (routes or (contracts.default_route(key),)):
                items.append({
                    'part': part,
                    'key': key,
                    'model': model_labels.get(key, key),
                    'route': route,
                    'route_label': route_labels.get(route, route),
                    'status': 'pending',
                    'volume_mm3': None,
                    'volume_error_pct': None,
                    'faces': None,
                    'valid': None,
                    'seconds': None,
                    'error': None,
                    'step': None,
                    'step_rel': None,
                })
    return items


# ---------------------------------------------------------------- 任务对象


def _new_job(parts, models, routes, force, ensure_design=False):
    settings = _settings()
    job_id = uuid.uuid4().hex[:12]
    job = {
        'id': job_id,
        'status': 'pending',
        'created': _now_iso(),
        'started': None,
        'finished': None,
        'seconds': None,
        'force': bool(force),
        'ensure_design': bool(ensure_design),
        'designs': {},
        'parts': list(parts),
        'models': list(models),
        'routes': list(routes) if routes else None,
        'src_dir': str(settings.SRC_PARTS_DIR),
        'out_root': str(settings.OUT_ROOT),
        'error': None,
        'traceback': None,
        'current_part': None,
        'stop_requested': False,
        'stop_mode': None,
        'forced': False,
        'killed': [],
        'completed': False,
        'route_prefs': None,
        'logs': [],
        'items': _build_items(parts, models, routes),
        'counts': None,
        'progress_pct': 0.0,
        '_reports': {},
        '_index': {},
        '_stop_event': threading.Event(),
        '_thread': None,
        '_started_ts': None,
        '_finished_ts': None,
        '_final_collected': False,
    }
    job['_index'] = {
        (item['part'], item['key'], item['route']): item for item in job['items']
    }
    return job


def _job_running(job) -> bool:
    """任务还占着「当前任务」坑位：``pending`` / ``running`` / ``stopping``。

    ``stopping`` 是 graceful 停止的中间态：几何还在收尾，必须继续占坑，
    否则下一个任务会和正在收尾的 OCP 调用撞车。``force`` 会把状态直接改成
    ``stopped``，立刻释放坑位（旧线程的异常/收尾不再影响坑位）。
    """
    return job.get('status') in ('pending', 'running', 'stopping')


def _active_job_locked():
    for job in _JOBS.values():
        if _job_running(job):
            return job
    return None


def _prune_jobs_locked():
    if len(_JOBS) <= _MAX_JOBS:
        return
    ordered = sorted(_JOBS.values(), key=lambda j: j.get('created') or '')
    for job in ordered:
        if len(_JOBS) <= _MAX_JOBS:
            break
        if _job_running(job):
            continue
        _JOBS.pop(job['id'], None)


def _append_log(job, message):
    line = str(message)
    with _JOBS_LOCK:
        logs = job['logs']
        logs.append(line)
        if len(logs) > _MAX_LOG_LINES:
            del logs[:_MAX_LOG_LINES - 500]
    try:
        print(f'[entity:{job["id"]}] {line}')
    except Exception:
        pass


def _record_progress(job, event):
    """给 orchestrator 的可选进度回调（``ctx.options['progress']``）。"""
    if not isinstance(event, dict):
        _append_log(job, event)
        return
    if event.get('message'):
        _append_log(job, event['message'])
    part = event.get('part')
    key = event.get('key') or event.get('model')
    route = event.get('route')
    if not (part and key and route):
        return
    with _JOBS_LOCK:
        item = job['_index'].get((part, key, route))
        if not item:
            return
        for field in ('status', 'volume_mm3', 'volume_error_pct', 'faces',
                      'valid', 'seconds', 'error'):
            if field in event and event[field] is not None:
                item[field] = event[field]
        if event.get('step'):
            item['step'] = str(event['step'])
            item['step_rel'] = _rel_to(job['out_root'], event['step'])


# ---------------------------------------------------------------- 结果收集


def _load_report_results(job, part):
    """读 ``<part>/report.json`` 的 results，按 (key, route) 建索引（带 mtime 缓存）。"""
    report_path = Path(job['out_root']) / part / 'report.json'
    try:
        stat = report_path.stat()
    except OSError:
        return None
    cache = job['_reports'].get(part)
    if cache and cache[0] == stat.st_mtime_ns and cache[1] == stat.st_size:
        return cache[2]

    results = {}
    try:
        data = json.loads(report_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        data = None
    raw_results = data.get('results') if isinstance(data, dict) else None
    if isinstance(raw_results, list):
        for result in raw_results:
            if not isinstance(result, dict):
                continue
            key = result.get('key')
            route = result.get('route')
            if key and route:
                results[(key, route)] = result
    job['_reports'][part] = (stat.st_mtime_ns, stat.st_size, results)
    return results


def _scan_part_artifacts(job, part):
    """扫描该件已产出的 route STEP 与 5 类模型 STEP。"""
    base = Path(job['out_root']) / part
    produced = {}
    contracts = _contracts()
    explicit = job.get('routes') or None
    scan_routes = list(explicit) if explicit else list(contracts.ROUTES)
    for route in scan_routes:
        sub = '10_c1' if route == 'c1' else '20_pipe'
        directory = base / sub
        if not directory.is_dir():
            continue
        suffix = f'__{route}.step'
        try:
            for file_path in directory.iterdir():
                if file_path.is_file() and file_path.name.endswith(suffix):
                    produced[(file_path.name[: -len(suffix)], route)] = file_path
        except OSError:
            continue

    models = {}
    model_dir = base / '00_models'
    if model_dir.is_dir():
        try:
            for file_path in model_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in _SOURCE_SUFFIXES:
                    models[file_path.stem] = file_path
        except OSError:
            pass
    return produced, models


def _apply_result(item, result, out_root, part):
    status = str(result.get('status') or '').strip().lower()
    if status not in ('ok', 'failed', 'skipped', 'stopped', 'running', 'pending'):
        status = 'ok' if result.get('step') else 'failed'
    item['status'] = status
    step = result.get('step') or result.get('step_path')
    item['step'] = str(step) if step else None
    item['step_rel'] = _step_rel(out_root, part, step)
    item['volume_mm3'] = result.get('volume_mm3')
    item['volume_error_pct'] = result.get('volume_error_pct')
    item['faces'] = result.get('faces')
    item['valid'] = result.get('valid')
    item['seconds'] = result.get('seconds')
    item['error'] = result.get('error')


def _counts(job):
    counts = {
        'total': len(job['items']),
        'pending': 0,
        'running': 0,
        'produced': 0,
        'ok': 0,
        'failed': 0,
        'skipped': 0,
        'stopped': 0,
    }
    for item in job['items']:
        status = item.get('status') or 'pending'
        if status not in counts:
            status = 'pending'
        counts[status] += 1
    counts['done'] = (
        counts['ok'] + counts['failed'] + counts['skipped'] + counts['produced'] + counts['stopped']
    )
    return counts


def _collect_locked(job, *, final=False):
    """把磁盘上的 report.json / 已产出 STEP 合并进任务 items（须持锁调用）。"""
    out_root = job['out_root']
    running = _job_running(job)

    for part in job['parts']:
        results = _load_report_results(job, part)
        produced, _models = _scan_part_artifacts(job, part)

        for (key, route), file_path in produced.items():
            item = job['_index'].get((part, key, route))
            if not item or item['status'] in ('ok', 'failed', 'skipped', 'stopped'):
                continue
            if item['status'] in ('pending', 'running'):
                item['status'] = 'produced'
                item['step'] = str(file_path)
                item['step_rel'] = _rel_to(out_root, file_path)

        if results:
            for (key, route), result in results.items():
                item = job['_index'].get((part, key, route))
                if item:
                    _apply_result(item, result, part=part, out_root=out_root)

    if running and not job.get('stop_requested'):
        current = None
        for part in job['parts']:
            if not (Path(out_root) / part / 'report.json').is_file():
                current = part
                break
        job['current_part'] = current
    else:
        current = None
        job['current_part'] = None

    # 「运行中」是派生状态：只把当前件里还没结果的单元标成 running，
    # 换件/停止时先回退，避免上一件的 running 永久残留。
    for item in job['items']:
        if item.get('status') == 'running':
            item['status'] = 'pending'
    if current:
        for item in job['items']:
            if item['part'] == current and item['status'] == 'pending':
                item['status'] = 'running'

    counts = _counts(job)
    job['counts'] = counts
    total = counts['total'] or 0
    job['progress_pct'] = round((counts['done'] / total) * 100, 1) if total else 0.0
    if final:
        job['_final_collected'] = True


def _collect_if_needed_locked(job):
    if job.get('_final_collected') and not _job_running(job):
        return
    _collect_locked(job, final=not _job_running(job))


def _apply_report_object(job, report):
    """把 run_part 返回的 PartReport 合并进 items（兼容 dataclass / dict）。"""
    try:
        data = report.to_dict() if hasattr(report, 'to_dict') else report
    except Exception:
        return
    if not isinstance(data, dict):
        return
    part = data.get('part')
    results = data.get('results') or []
    with _JOBS_LOCK:
        for result in results:
            if not isinstance(result, dict):
                continue
            key = result.get('key')
            route = result.get('route')
            if not (part and key and route):
                continue
            item = job['_index'].get((part, key, route))
            if item:
                _apply_result(item, result, job['out_root'], part)


# ---------------------------------------------------------------- 任务运行


def _design_gen():
    """延迟导入补算模块（core.entity.design_gen）。"""
    from core.entity import design_gen

    return design_gen


def _ensure_designs_for_job(job, log):
    """运行前补算缺失的上游工艺设计：只补缺，不覆盖已有登记，失败不阻断。"""
    out_root = job.get('out_root')
    try:
        design_gen = _design_gen()
    except Exception as exc:  # noqa: BLE001
        log(f'[design] design_gen 不可用，跳过补算：{type(exc).__name__}: {exc}')
        return
    stop_event = job.get('_stop_event')
    for part in list(job.get('parts') or []):
        if stop_event is not None and stop_event.is_set():
            log('[design] 收到停止请求，跳过剩余补算')
            return
        try:
            existing = design_gen.existing_design(part)
        except Exception as exc:  # noqa: BLE001
            existing = None
            log(f'[design] 读取 {part} 的设计登记失败：{type(exc).__name__}: {exc}')
        if existing:
            job['designs'][part] = {
                'state': 'registered',
                'design_id': existing.get('id'),
                'route': existing.get('pp_method'),
            }
            log(f'[design] {part} 已登记设计 #{existing.get("id")}（{existing.get("pp_method")}）')
            continue
        log(f'[design] {part} 未登记工艺设计，按推荐路线尝试补算…')
        try:
            result = design_gen.create_default_design(part, out_root, log=log)
        except Exception as exc:  # noqa: BLE001
            result = {'state': 'error', 'message': f'{type(exc).__name__}: {exc}'}
        job['designs'][part] = {
            key: result.get(key)
            for key in ('state', 'design_id', 'route', 'fitness', 'message')
        }
        state = result.get('state')
        if state == 'created':
            log(f'[design] {part}：{result.get("message")}')
        elif state == 'infeasible':
            log(f'[design] {part}：{result.get("message")}（继续跑流水线，不阻断）')
        elif state == 'no_measured':
            log(f'[design] {part}：缺少测量缓存，先跳过补算（流水线会先测量）')
        elif state == 'error':
            log(f'[design] {part}：补算失败（继续跑流水线）：{result.get("message")}')


def _run_job(job_id: str):
    job = _JOBS.get(job_id)
    if not job:
        return
    with _JOBS_LOCK:
        job['started'] = _now_iso()
        job['_started_ts'] = time.time()
        if job.get('forced'):
            # 还没开跑就被 force 掉了：不再碰 OCP，直接收尾
            job['status'] = 'stopped'
            job['_thread_done'] = True
            job['finished'] = _now_iso()
            job['_finished_ts'] = time.time()
            return
        if job['_stop_event'].is_set():
            job['status'] = 'stopping'  # 已经收到 graceful 请求，别覆盖成 running
        else:
            job['status'] = 'running'

    def log(message):
        _append_log(job, message)

    try:
        contracts = _contracts()
        if job.get('ensure_design'):
            _ensure_designs_for_job(job, log)
        ctx = contracts.PipelineContext(
            src_dir=job['src_dir'],
            out_root=job['out_root'],
            parts=list(job['parts']),
            routes=tuple(job['routes']) if job.get('routes') else None,
            models=tuple(job['models']),
            force=job['force'],
            resume=not job['force'],
            options={},
            log=log,
        )
        stop_event = job['_stop_event']
        ctx.options.update({
            'stop_event': stop_event,
            'cancel_event': stop_event,
            'job_id': job_id,
            'stop_requested': stop_event.is_set,
            'progress': lambda event: _record_progress(job, event),
        })
        # 路线偏好：``/run`` 请求体覆盖磁盘配置，最终合并结果写在这里，
        # orchestrator 若支持会据此改 c1 / 121 与分型方向。
        route_prefs = job.get('route_prefs')
        if route_prefs is None:
            route_prefs = _load_route_prefs()
        ctx.options['route_prefs'] = route_prefs
        # 有些实现倾向把停止信号挂在 ctx 上；契约字段不变，仅附加属性。
        try:
            setattr(ctx, 'stop_event', stop_event)
        except Exception:
            pass

        orchestrator = _import_orchestrator()
        runner = getattr(orchestrator, 'run', None)
        if callable(runner):
            log('orchestrator.run(ctx) 开始执行')
            runner(ctx)
        else:
            run_part = getattr(orchestrator, 'run_part', None)
            if not callable(run_part):
                raise RuntimeError(
                    'core.entity.orchestrator 既没有 run(ctx) 也没有 run_part(ctx, part)'
                )
            log('orchestrator.run(ctx) 不存在，回退为逐件 run_part(ctx, part)')
            for part in list(job['parts']):
                if stop_event.is_set():
                    log(f'[stop] 收到停止请求，跳过剩余管件（下一个：{part}）')
                    break
                with _JOBS_LOCK:
                    job['current_part'] = part
                log(f'=== 开始管件 {part} ===')
                report = run_part(ctx, part)
                if report is not None:
                    _apply_report_object(job, report)
                with _JOBS_LOCK:
                    _collect_locked(job)

        stopped = stop_event.is_set()
        with _JOBS_LOCK:
            if job.get('forced'):
                job['status'] = 'stopped'
            else:
                job['status'] = 'stopped' if stopped else 'finished'
        if job.get('forced'):
            log('[stop] 任务已被强制停止（子进程已终止）')
        elif stopped:
            log('[stop] 任务已停止（graceful：已完成的单元保留，未开始的跳过）')
        else:
            log('流水线执行结束')
    except BaseException as exc:  # 后台线程：任何异常都不能让状态永久 running
        with _JOBS_LOCK:
            if job.get('forced'):
                # force 之后被杀掉的子进程/中断异常属于预期收尾，不当失败上报
                job['status'] = 'stopped'
                job.setdefault('stop_note', f'收尾异常已忽略：{type(exc).__name__}: {exc}')
            else:
                job['status'] = 'failed'
                job['error'] = f'{type(exc).__name__}: {exc}'
                job['traceback'] = traceback.format_exc(limit=20)
        log(f'[error] {type(exc).__name__}: {exc}')
    finally:
        with _JOBS_LOCK:
            job['_thread_done'] = True
            job['completed'] = True
            job['_finished_ts'] = time.time()
            job['finished'] = _now_iso()
            if job['_started_ts']:
                job['seconds'] = round(job['_finished_ts'] - job['_started_ts'], 1)
            job['current_part'] = None
            try:
                _collect_locked(job, final=True)
            except Exception:
                pass


# ---------------------------------------------------------------- 序列化


def _job_summary(job):
    counts = job.get('counts')
    if not counts:
        counts = _counts(job)
    return {
        'id': job['id'],
        'status': job['status'],
        'running': _job_running(job),
        'created': job['created'],
        'started': job['started'],
        'finished': job['finished'],
        'seconds': job['seconds'],
        'force': job['force'],
        'parts': list(job['parts']),
        'models': list(job['models']),
        'routes': list(job['routes']) if job.get('routes') else None,
        'default_routes': _default_routes_payload(),
        'current_part': job['current_part'],
        'stop_requested': job['stop_requested'],
        'stop_mode': job.get('stop_mode'),
        'forced': bool(job.get('forced')),
        'killed': list(job.get('killed') or []),
        'completed': bool(job.get('completed')),
        'route_prefs': dict(job.get('route_prefs') or {}),
        'counts': counts,
        'progress_pct': job.get('progress_pct') or 0.0,
        'error': job['error'],
        'ensure_design': bool(job.get('ensure_design')),
        'design_mode': 'auto' if job.get('ensure_design') else 'manual',
        'design_note': (
            '运行前自动补算缺失设计' if job.get('ensure_design')
            else '不自动补算设计：设计由人工判断后导入数据库，流水线只输出推荐路线'),
        'designs': dict(job.get('designs') or {}),
    }


def _job_detail(job):
    payload = _job_summary(job)
    payload.update({
        'src_dir': job['src_dir'],
        'out_root': job['out_root'],
        'items': job['items'],
        'logs': list(job['logs'][-_LOG_TAIL:]),
        'traceback': job['traceback'],
    })
    return payload


# ---------------------------------------------------------------- 路由：状态与数据


# ---------------------------------------------------------------- 设计输入与型线
#: 设计参数里按顺序展示的标量键（其余键是曲线/路径，不放进标量表）
_DESIGN_SCALAR_KEYS = (
    'D0', 'T0', 'L0', 'Dn', 'D1', 'D2', 'D3', 'T1', 'T2', 'T3', 'T4',
    'TL1', 'TL2', 'TL3', 'TL4', 'TL5', 'TLM1', 'TLM2', 'TLM3', 'TLM4',
    'LL', 'LL1', 'L1', 'L2', 'L3', 'L4', 'Ln', 'D20', 'BL', 'B_D',
)

#: 曲线抽样上限（前端 SVG 画图用，避免一次返回上万点）
_PROFILE_MAX_POINTS = 400
_CENTERLINE_MAX_POINTS = 200


def _design_db_path():
    return Path(__file__).resolve().parents[1] / 'database' / 'pp' / 'pp_design.db'


def _design_for(part):
    """从工艺设计库取出该管件的设计输入（标量参数 + 型线/壁厚曲线）。"""
    db = _design_db_path()
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.Error:
        return None
    try:
        row = con.execute(
            "select id, display_name, vehicle_spec, tube_number, pp_method, "
            "source_file_name, fitness, params_json, created_at "
            "from pp_design where source_file_name in (?, ?) "
            "order by id desc limit 1",
            (f'{part}.stp', f'{part}.step'),
        ).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        con.close()
    if row is None:
        return None

    try:
        params = json.loads(row['params_json'] or '{}')
    except ValueError:
        params = {}

    scalars, labels = [], []
    for key in _DESIGN_SCALAR_KEYS:
        if key not in params:
            continue
        value = params.get(key)
        if isinstance(value, (int, float)):
            scalars.append({'key': key, 'value': float(value)})
            labels.append(key)
    extra = {
        key: params[key] for key in ('抽管方式', '车种规格', '管件号', 'Taper段壁厚过渡')
        if key in params and not isinstance(params[key], (list, dict))
    }

    curves = params.get('壁厚数据') or []
    stations = len(curves) if isinstance(curves, list) else 0
    step = max(1, stations // _PROFILE_MAX_POINTS) if stations else 1
    picked = list(range(0, stations, step))
    if stations and picked and picked[-1] != stations - 1:
        picked.append(stations - 1)

    def _col(item, name):
        try:
            value = item.get(name)
        except AttributeError:
            return None
        return float(value) if isinstance(value, (int, float)) else None

    s, perimeter, wall, wall_mod = [], [], [], []
    for index in picked:
        item = curves[index]
        s.append(_col(item, '中心线长度(mm)'))
        perimeter.append(_col(item, '外径周长(mm)'))
        wall.append(_col(item, '壁厚(mm)'))
        wall_mod.append(_col(item, '修改后壁厚(mm)'))

    return {
        'id': row['id'],
        'display_name': row['display_name'],
        'vehicle_spec': row['vehicle_spec'],
        'tube_number': row['tube_number'],
        'pp_method': row['pp_method'],
        'source_file_name': row['source_file_name'],
        'fitness': row['fitness'],
        'created_at': row['created_at'],
        'scalars': scalars,
        'extra': extra,
        'segments': params.get('原始segments') or {},
        'stations': stations,
        'curves': {
            's_mm': s,
            'perimeter_mm': perimeter,
            'wall_mm': wall,
            'wall_modified_mm': wall_mod,
        },
    }


def _route_steps(part, meta, end_treatment, design):
    """上游工艺 + 实体化流水线的工序链（每步是否已有产物）。"""
    settings = _settings()
    out_root = Path(settings.OUT_ROOT)
    out_dir = out_root / part
    source = (meta or {}).get('path') or str(settings.SRC_PARTS_DIR / f'{part}.stp')
    measured = Path(settings.measured_path(part, end_treatment, out_root))

    def _exists(relative):
        return (out_dir / relative).is_file()

    def _first(exists_map, label):
        for key, value in exists_map.items():
            if value:
                return label.get(key, key)
        return None

    model_files = {
        model.slug: _exists(Path('00_models') / f'{model.slug}.stp')
        for model in _contracts().MODELS
    }
    entity_files = {
        route: _exists(Path('10_c1' if route == 'c1' else '20_pipe'))
        for route in ('c1', 'pipe')
    }
    any_entity = any(
        _exists(Path(sub) / f'{model.key}__{route}.step')
        for model in _contracts().MODELS
        for route, sub in (('c1', '10_c1'), ('pipe', '20_pipe'))
    )

    upstream = (design or {}).get('pp_method') or ''
    steps = [
        {
            'id': 'source', 'title': '原始管件',
            'subtitle': Path(source).name if source else f'{part}.stp',
            'done': Path(source).is_file() if source else False,
            'detail': '输入 STEP（gnt管件）',
        },
        {
            'id': 'measure', 'title': '测量 / 型线',
            'subtitle': '1 mm 站位 + 截面轮廓',
            'done': measured.is_file(),
            'detail': ('缓存 %.1f MB' % (measured.stat().st_size / 1048576.0)) if measured.is_file() else '尚未测量',
        },
        {
            'id': 'shaping', 'title': '成型',
            'subtitle': '外径形腔（放样）',
            'done': bool(model_files.get('shaping_cavity')) or entity_files.get('c1'),
            'detail': '上游工艺：' + (upstream or '未登记'),
        },
        {
            'id': 'belling', 'title': '扩口',
            'subtitle': '扩口腔体 / 扩口芯轴',
            'done': bool(model_files.get('belling_cavity') or model_files.get('belling_mandrel')),
            'detail': '按设计决定是否走扩口',
        },
        {
            'id': 'flattening', 'title': '压扁',
            'subtitle': '压扁模型',
            'done': bool(model_files.get('flattening_model')),
            'detail': '按设计决定是否走压扁',
        },
        {
            'id': 'bending', 'title': '压弯',
            'subtitle': '压弯模型',
            'done': bool(model_files.get('bending_model')),
            'detail': '按设计决定是否走压弯',
        },
        {
            'id': 'entity', 'title': '实体化',
            'subtitle': '4 面 Solid + 分型线',
            'done': any_entity,
            'detail': '默认路线：外径形腔 c1，其余 pipe',
        },
    ]
    return steps


def _profile_cache_path(out_root, part):
    return Path(out_root) / '_cache' / f'{_safe_name(part)}_profile.json'


def _profile_for(part, end_treatment, out_root, measured=None):
    """实测型线与壁厚（读测量缓存，结果按测量文件时间戳做小缓存）。"""
    settings = _settings()
    out_root = Path(out_root)
    measured = Path(measured or settings.measured_path(part, end_treatment, out_root))
    cache = _profile_cache_path(out_root, part)
    if cache.is_file() and cache.stat().st_mtime >= measured.stat().st_mtime:
        try:
            return json.loads(cache.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass

    data = json.loads(measured.read_text(encoding='utf-8'))
    sections = data.get('sections_full') or []
    centerline = data.get('center_line_points') or []

    stations = len(sections)
    step = max(1, stations // _PROFILE_MAX_POINTS) if stations else 1
    picked = list(range(0, stations, step))
    if stations and picked and picked[-1] != stations - 1:
        picked.append(stations - 1)

    series = {'s_mm': [], 'perimeter_mm': [], 'eq_diameter_mm': [],
              'wall_mm': [], 'wall_min_mm': [], 'wall_max_mm': [],
              'status': []}
    for index in picked:
        item = sections[index]
        series['s_mm'].append(_round(item.get('cumulative_length')))
        series['perimeter_mm'].append(_round(item.get('perimeter')))
        series['eq_diameter_mm'].append(_round(item.get('equivalent_diameter')))
        series['wall_mm'].append(_round(item.get('wall_thickness')))
        series['wall_min_mm'].append(_round(item.get('wall_thickness_min')))
        series['wall_max_mm'].append(_round(item.get('wall_thickness_max')))
        series['status'].append(str(item.get('measurement_status') or ''))

    step = max(1, len(centerline) // _CENTERLINE_MAX_POINTS) if centerline else 1
    picked_cl = list(range(0, len(centerline), step))
    if centerline and picked_cl and picked_cl[-1] != len(centerline) - 1:
        picked_cl.append(len(centerline) - 1)
    line = [
        [_round(centerline[i][0]), _round(centerline[i][1]), _round(centerline[i][2])]
        for i in picked_cl
    ]

    status_counts = {}
    for item in sections:
        key = str(item.get('measurement_status') or 'unknown')
        status_counts[key] = status_counts.get(key, 0) + 1

    walls = [float(item['wall_thickness']) for item in sections
             if isinstance(item.get('wall_thickness'), (int, float))]
    perimeters = [float(item['perimeter']) for item in sections
                  if isinstance(item.get('perimeter'), (int, float))]

    profile = {
        'success': True,
        'part': part,
        'measured': _file_entry(measured, out_root, f'{_safe_name(part)}_测量.json'),
        'station_count': stations,
        'length_mm': _round(data.get('center_line_length')),
        'bbox': [_round(v) for v in (data.get('bounding_box') or [])],
        'wall': {
            'min_mm': _round(min(walls)) if walls else None,
            'max_mm': _round(max(walls)) if walls else None,
            'median_mm': _round((data.get('wall_thickness_stats') or {}).get('median_mm')),
            'mean_mm': _round((data.get('wall_thickness_stats') or {}).get('mean_mm')),
        },
        'perimeter': {
            'min_mm': _round(min(perimeters)) if perimeters else None,
            'max_mm': _round(max(perimeters)) if perimeters else None,
        },
        'status_counts': status_counts,
        'centerline': line,
        'series': series,
    }
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass
    return profile


def _round(value, digits=4):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 单件流程

#: 工序中文名（下载文件名用）
_MODEL_OP_LABELS = {
    'shaping': '成型',
    'belling': '扩口',
    'flattening': '压扁',
    'bending': '压弯',
}


def _safe_name(text):
    """文件名安全化：去掉 Windows 非法字符与首尾空白/点。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', str(text or '')).strip(' .')
    return cleaned or 'part'


def _first_existing(*paths):
    """返回第一个存在的文件；都不存在时返回第一个（用于展示「未生成」）。"""
    for path in paths:
        if Path(path).is_file():
            return Path(path)
    return Path(paths[0]) if paths else Path('')


def _file_entry(path, out_root, download_name=None):
    path = Path(path)
    exists = path.is_file()
    return {
        'path': str(path),
        'rel_path': _rel_to(out_root, path) if exists else None,
        'name': path.name,
        'download_name': download_name or path.name,
        'exists': exists,
        'size': _size_of(path) if exists else None,
        'mtime': _mtime_of(path) if exists else None,
    }


def _model_naming(part, model):
    """`件号_工序_模型` 前缀的一套文件名（原模型 / c1 / pipe）。"""
    prefix = f"{_safe_name(part)}_{model.op_label}_{model.name}"
    return {
        'model': f'{prefix}.stp',
        'c1': f'{prefix}__c1.step',
        'pipe': f'{prefix}__pipe.step',
    }


def _model_params(model, contracts, settings):
    """该模型默认路线实际用到的参数（页面展示用）。"""
    route = contracts.default_route(model.key)
    if route == 'c1':
        return {
            'engine': 'OCP（c2 同一套 C1 外推 / 口径保形）',
            'half_samples': settings.HALF_SAMPLES,
            'smooth_passes': settings.SMOOTH_PASSES,
            'station_step_mm': settings.STATION_STEP_MM,
            'direction_rule': '逐截面 ±极点（对称轴）',
        }
    return {
        'engine': 'FreeCAD 1.1.4 + PipeSolidBridge（FreeCADCmd 子进程）',
        'half_points': settings.PIPE_OPTIONS.get('half_points'),
        'max_sections': settings.PIPE_OPTIONS.get('max_sections'),
        'direction_rule': '自动（对称轴 / 截面长轴 / 弯曲平面）',
        'export_stl': bool(settings.PIPE_OPTIONS.get('export_stl')),
    }


def _defaults_payload(part, end_treatment):
    settings = _settings()
    contracts = _contracts()
    return {
        'measurement': {
            'algorithm': 'A',
            'station_step_mm': 1.0,
            'sparse_step_mm': 25.0,
            'end_treatment': end_treatment,
            'source': str(settings.SRC_PARTS_DIR),
        },
        'c1': {
            'half_samples': settings.HALF_SAMPLES,
            'smooth_passes': settings.SMOOTH_PASSES,
            'station_step_mm': settings.STATION_STEP_MM,
            'contour': '外轮廓（实测截面）',
        },
        'pipe': {
            'direction': 'auto',
            'half_points': settings.PIPE_OPTIONS.get('half_points'),
            'max_sections': settings.PIPE_OPTIONS.get('max_sections'),
            'export_stl': bool(settings.PIPE_OPTIONS.get('export_stl')),
        },
        'routes': {m.key: contracts.default_route(m.key) for m in contracts.MODELS},
        'route_labels': dict(contracts.ROUTE_LABELS),
    }


def _part_results(out_dir):
    """读件级 report.json 的 results，返回 ({(model_key, route): item}, report_path)。"""
    report_path = Path(out_dir) / 'report.json'
    found = {}
    if not report_path.is_file():
        return found, report_path
    try:
        data = json.loads(report_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return found, report_path
    for item in data.get('results') or []:
        key = str(item.get('key') or '')
        route = str(item.get('route') or '')
        if key and route:
            found[(key, route)] = item
    return found, report_path


_ROUTE_GROUPS = (
    ('ga', 'GA 遗传算法', '适合复杂长管，搜索全局最优解',
     ('Taper+TP抽+扩口', '普通抽+TP抽+Taper+扩口', '加长Taper+TP抽+扩口')),
    ('enumerate', '暴力枚举', '适合简单短管，精确遍历求解',
     ('仅TP抽', '普通抽+TP抽', '仅扩口', '普通抽+扩口', 'TP抽+扩口',
      '普通抽+Taper+扩口', '普通抽+Taper')),
)


def _route_plan_for(part, design=None, end_treatment=None, out_root=None):
    """默认工艺路线目录（与「工艺参数优化」页同一套）+ 本件推荐值与已登记值。"""
    try:
        from core.pp.optimization import available_pp_methods, recommend_pp_method
    except Exception:  # noqa: BLE001
        return None

    length = 0.0
    max_d = 0.0
    min_d = 0.0
    try:
        settings = _settings()
        contracts = _contracts()
        end_treatment = end_treatment or _end_treatment_for(part, ROUTE_PREF_MODEL)
        out_root = Path(out_root or settings.OUT_ROOT)
        profile = _profile_for(part, end_treatment, out_root)
        length = float(profile.get('length_mm') or 0)
        per = [
            float(value)
            for value in (profile.get('series') or {}).get('perimeter_mm') or []
            if value
        ]
        if per:
            max_d = max(per) / math.pi
            min_d = min(per) / math.pi
    except Exception:  # noqa: BLE001
        pass

    l4 = 0.0
    for row in (design or {}).get('scalars') or []:
        if row.get('key') == 'L4':
            try:
                l4 = float(row.get('value') or 0)
            except (TypeError, ValueError):
                l4 = 0.0
    if l4 <= 0:
        # 没有已登记设计时从实测型线算直线段 L4（按 mtime 缓存）。
        # recommend_pp_method 内部按「形线总长 − L4」判断，漏掉 L4 会把
        # 631mm 的弯管误判成 ≥300mm 的长管。
        try:
            from core.entity import design_gen
            l4 = float(design_gen.straight_length_for(part, out_root))
        except Exception:  # noqa: BLE001
            l4 = 0.0
    line_length = max(0.0, length - l4)

    recommended_label = ''
    if length <= 0 or max_d <= 0:
        # 还没测量（或缓存缺失）时型线为空，算法只会把 0mm/0mm 算成「仅TP抽」。
        # 这种推荐没有意义，宁可不给，等测量完成后再刷新。
        rec = {}
        options = []
    else:
        rec = recommend_pp_method(length, max_d, min_d, l4) or {}
        options = rec.get('all_options') or []
    key_to_label = {item.get('method'): item.get('label') for item in options}
    feasibility = {item.get('label'): bool(item.get('feasible')) for item in options}
    recommended_label = (
        recommended_label
        or key_to_label.get(rec.get('recommended'))
        or rec.get('recommended')
        or ''
    )
    chosen = (design or {}).get('pp_method') or None
    methods = set(available_pp_methods())

    groups = []
    for group_id, title, subtitle, labels in _ROUTE_GROUPS:
        items = []
        for label in labels:
            if label not in methods:
                continue
            items.append({
                'label': label,
                'feasible': feasibility.get(label),
                'recommended': bool(recommended_label) and label == recommended_label,
                'chosen': bool(chosen) and label == chosen,
            })
        groups.append({'id': group_id, 'title': title, 'subtitle': subtitle, 'items': items})

    return {
        'line_length_mm': round(line_length, 3),
        'centerline_length_mm': round(length, 3),
        'l4_mm': round(l4, 3),
        'd_change_pct': round(float(rec.get('D_change') or 0), 3),
        'max_diameter_mm': round(max_d, 3),
        'min_diameter_mm': round(min_d, 3),
        'recommended_label': recommended_label,
        'reason': rec.get('reason') or (
            '' if recommended_label else '该件尚未测量（或测量缓存缺失），暂不推荐工艺路线'
        ),
        'chosen': chosen,
        'chosen_is_recommended': bool(chosen and recommended_label and chosen == recommended_label),
        'groups': groups,
        'free_combo': ['普通抽', 'TP抽', 'Taper', '扩口'],
        'source': '按本件实测型线计算默认推荐（与「工艺参数优化」页同规则）',
    }


def _c1_impl(part, key=ROUTE_PREF_MODEL, prefs=None):
    """该「件 + 模型」在 c1 侧的实现标签：``121保形`` / ``自参考保形`` / ``C1外推``。

    优先用 ``orchestrator.c1_impl``（A2 维护的判定，认识 ``prefs``）；不可用时按
    121 规则兜底（参考件 2052 的外径形腔 = 121 保形）。
    """
    pref = _load_route_prefs() if prefs is None else prefs
    try:
        orchestrator = _import_orchestrator()
        value = getattr(orchestrator, 'c1_impl', None)
        if callable(value):
            try:
                return str(value(part, key, pref) or 'C1外推')
            except TypeError:
                legacy = str(value(part, key) or '')
                if legacy:
                    # 旧签名不认 prefs：仍然让显式 121 覆盖标签
                    if _resolve_impl(pref, part, key) == '121':
                        return '121保形'
                    return legacy
    except Exception:
        pass
    try:
        if _resolve_impl(pref, part, key) == '121':
            return '121保形'
    except Exception:
        pass
    return 'C1外推'


def _process_summary_for(part, measured, design, route_plan, out_root):
    """``core.entity.process_summary.build_process_summary`` 的带缓存包装。

    缓存键 = ``(measured mtime, size, design id)``；模块未就绪或生成失败时返回
    ``(None, 错误文案)``，绝不把异常抛给接口。
    """
    try:
        from core.entity import process_summary
    except Exception as exc:  # noqa: BLE001 - A3 并行开发期可能还没落地
        return None, f'process_summary 不可用：{type(exc).__name__}: {exc}'
    try:
        stat = Path(measured).stat()
        measured_key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        measured_key = (None, None)
    design_id = design.get('id') if isinstance(design, dict) else None
    cache_key = (measured_key, design_id)
    with _SUMMARY_CACHE_LOCK:
        cached = _SUMMARY_CACHE.get(part)
        if cached and cached[0] == cache_key:
            return cached[1], None
    try:
        summary = process_summary.build_process_summary(
            part=part,
            measured_path=str(measured),
            design=design,
            route_plan=route_plan,
            out_root=str(out_root),
        )
    except Exception as exc:  # noqa: BLE001
        return None, f'process_summary 生成失败：{type(exc).__name__}: {exc}'
    if not isinstance(summary, dict):
        return None, 'process_summary 返回了非字典结果'
    if not design:
        # 没有登记设计时，摘要仍给推荐路线；「道次」明确留空并说明原因，
        # 不再显示 0 / 空表让用户误以为已自动生成。
        summary['design_missing'] = True
        drawing = summary.get('drawing')
        if isinstance(drawing, dict):
            drawing['missing_design'] = True
            drawing['note'] = _MISSING_DESIGN_NOTE
    with _SUMMARY_CACHE_LOCK:
        _SUMMARY_CACHE[part] = (cache_key, summary)
    return summary, None


def _part_flow(part, out_root=None):
    """单件全流程视图：输入、默认参数、5 类模型的默认路线与可下载产物。"""
    settings = _settings()
    contracts = _contracts()
    out_root = Path(out_root or settings.OUT_ROOT)
    parts, src_dir = _scan_parts()
    meta = next((p for p in parts if p['name'] == part), None)
    end_treatment = meta['end_treatment'] if meta else _end_treatment_for(part)
    out_dir = out_root / part
    if meta is None and not out_dir.is_dir():
        return None

    measured = Path(settings.measured_path(part, end_treatment, out_root))
    results, report_path = _part_results(out_dir)
    design = _design_for(part)
    try:
        route_plan = _route_plan_for(part, design, end_treatment, out_root)
    except Exception as exc:  # noqa: BLE001
        route_plan = {'error': str(exc)}

    prefs = _load_route_prefs()
    try:
        process_summary, process_summary_error = _process_summary_for(
            part, measured, design, route_plan, out_root)
    except Exception as exc:  # noqa: BLE001 - 汇总视图永不因摘要失败
        process_summary, process_summary_error = None, f'{type(exc).__name__}: {exc}'

    models = []
    for model in contracts.MODELS:
        names = _model_naming(part, model)
        default_route = contracts.default_route(model.key)
        outputs = []
        for route_key, sub in (('c1', '10_c1'), ('pipe', '20_pipe')):
            path = out_dir / sub / f'{model.key}__{route_key}.step'
            if not path.is_file():
                continue
            item = results.get((model.key, route_key)) or {}
            outputs.append(dict(
                _file_entry(path, out_root, names[route_key]),
                route=route_key,
                route_label=contracts.ROUTE_LABELS.get(route_key, route_key),
                default=route_key == default_route,
                status=item.get('status'),
                faces=item.get('faces'),
                solids=item.get('solids'),
                free_edges=item.get('free_edges'),
                parting_edges=item.get('parting_edges'),
                valid=item.get('valid'),
                volume_mm3=item.get('volume_mm3'),
                volume_error_pct=item.get('volume_error_pct'),
                seconds=item.get('seconds'),
            ))
        reference_route = _resolve_impl(prefs, part, model.key) == '121'
        route_label = contracts.ROUTE_LABELS.get(default_route, default_route)
        if reference_route:
            route_label = '口径保形（c1 路线，参考件）'
        params = _model_params(model, contracts, settings)
        if reference_route:
            params = dict(params, reference=True, reference_step='本件形腔 STEP（121 保形）')
        models.append({
            'key': model.key,
            'op': model.op,
            'op_label': model.op_label,
            'name': model.name,
            'slug': model.slug,
            'default_route': default_route,
            'route_label': route_label,
            'impl': _resolve_impl(prefs, part, model.key),
            'direction': _resolve_direction(prefs, part, model.key),
            'c1_impl': _c1_impl(part, model.key, prefs),
            'params': params,
            'original': _file_entry(
                _first_existing(
                    out_dir / '00_models' / f'{model.key}.stp',
                    out_dir / '00_models' / f'{model.slug}.stp',
                ),
                out_root, names['model'],
            ),
            'outputs': outputs,
        })

    return {
        'success': True,
        'part': part,
        'src_dir': src_dir,
        'out_root': str(out_root),
        'out_dir': str(out_dir),
        'source': meta or {'name': part, 'file': f'{part}.stp', 'path': None,
                           'source': 'unknown'},
        'end_treatment': end_treatment,
        'reference': part in contracts.REFERENCE_PARTS,
        'measured': _file_entry(measured, out_root, f'{_safe_name(part)}_测量.json'),
        'defaults': _defaults_payload(part, end_treatment),
        'design': design,
        'design_state': (
            {
                'state': 'registered',
                'design_id': design.get('id'),
                'route': design.get('pp_method'),
                'fitness': design.get('fitness'),
                'message': f"已登记设计 #{design.get('id')}（{design.get('pp_method')}）",
            }
            if design
            else {
                'state': 'missing',
                'message': ('未登记工艺设计；设计请人工判断后导入数据库'
                            '（流水线只给推荐路线，不再自动补算）'),
            }
        ),
        'route_plan': route_plan,
        'route_steps': _route_steps(part, meta, end_treatment, design),
        'route_pref': _part_pref(prefs, part),
        'route_pref_effective': {
            'model': ROUTE_PREF_MODEL,
            'impl': _resolve_impl(prefs, part, ROUTE_PREF_MODEL),
            'direction': _resolve_direction(prefs, part, ROUTE_PREF_MODEL),
            'end_treatment': _end_treatment_for(part, ROUTE_PREF_MODEL, prefs),
            'impls': list(ROUTE_PREF_IMPLS),
            'directions': list(ROUTE_PREF_DIRECTIONS),
        },
        'c1_impl': _c1_impl(part, ROUTE_PREF_MODEL, prefs),
        'process_summary': process_summary,
        'process_summary_error': process_summary_error,
        'models': models,
        'report': _file_entry(report_path, out_root, f'{_safe_name(part)}_报告.json'),
        'summary': _file_entry(
            out_root / '_report' / 'summary.md', out_root, '全部件汇总.md'),
    }


@entity_bp.route('/status', methods=['GET'])
def entity_status():
    try:
        settings = _settings()
        contracts = _contracts()
        parts, src_dir = _scan_parts()
    except Exception as exc:
        return _error(f'实体化模块不可用：{exc}', 500)

    with _JOBS_LOCK:
        active = _active_job_locked()
        running = active is not None
        job_id = active['id'] if active else None

    return jsonify({
        'success': True,
        'running': running,
        'job_id': job_id,
        'parts': [p['name'] for p in parts],
        'models': _models_payload(),
        'routes': _routes_payload(),
        'default_routes': _default_routes_payload(),
        'src_dir': src_dir,
        'out_root': str(settings.OUT_ROOT),
        'web_port': settings.WEB_PORT,
        'orchestrator_ready': _orchestrator_path().is_file(),
        'route_labels': dict(contracts.ROUTE_LABELS),
        'reference_parts': list(contracts.REFERENCE_PARTS),
        'upload_parallel': _upload_parallel(),
        'measurement': _measurement_settings_payload(),
    })


@entity_bp.route('/parts', methods=['GET'])
def entity_parts():
    try:
        settings = _settings()
        parts, src_dir = _scan_parts()
    except Exception as exc:
        return _error(f'扫描原始管件失败：{exc}', 500)
    return jsonify({
        'success': True,
        'parts': parts,
        'src_dir': src_dir,
        'out_root': str(settings.OUT_ROOT),
    })


@entity_bp.route('/parts/<part>', methods=['GET'])
@entity_bp.route('/parts/<part>/flow', methods=['GET'])
def entity_part_flow(part):
    """单件全流程：默认参数、5 类模型的默认路线、可下载产物。"""
    part = (part or '').strip()
    if not part:
        return _error('缺少管件名称', 404)
    try:
        settings = _settings()
    except Exception as exc:
        return _error(f'实体化模块不可用：{exc}', 500)
    try:
        payload = _part_flow(part, settings.OUT_ROOT)
    except Exception as exc:
        return _error(f'读取管件流程失败：{exc}', 500)
    if payload is None:
        return _error(f'管件不存在：{part}', 404)
    return jsonify(payload)


# ---------------------------------------------------------------- 路由：路线偏好


@entity_bp.route('/parts/<part>/route-pref', methods=['GET'])
def entity_part_route_pref(part):
    """该件外径形腔的 c1 / 121 与分型方向偏好（``model`` 查询参数可选）。"""
    part = (part or '').strip()
    if not part:
        return _error('缺少管件名称', 404)
    try:
        contracts = _contracts()
        prefs = _load_route_prefs()
    except Exception as exc:  # noqa: BLE001
        return _error(f'读取路线偏好失败：{type(exc).__name__}: {exc}', 500)
    model = str(request.args.get('model') or ROUTE_PREF_MODEL).strip()
    if model not in contracts.MODEL_KEYS:
        return _error(f'未知模型：{model}（可选：{", ".join(contracts.MODEL_KEYS)}）')
    payload = _prefs_payload(part, prefs)
    payload['model'] = model
    payload['impl'] = _resolve_impl(prefs, part, model)
    payload['direction'] = _resolve_direction(prefs, part, model)
    payload['effective'] = {
        'impl': payload['impl'],
        'end_treatment': _end_treatment_for(part, model, prefs),
    }
    payload['stored'] = _model_pref(prefs, part, model)
    return jsonify(payload)


@entity_bp.route('/parts/<part>/route-pref', methods=['POST'])
def entity_part_route_pref_set(part):
    """写一条偏好：``{"model": "成型_外径形腔", "impl": "121", "direction": "auto"}``。"""
    part = (part or '').strip()
    if not part:
        return _error('缺少管件名称', 404)
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _error('请求体必须是 JSON 对象：{"model": ..., "impl": ..., "direction": ...}')
    try:
        contracts = _contracts()
    except Exception as exc:  # noqa: BLE001
        return _error(f'实体化模块不可用：{exc}', 500)

    model = str(data.get('model') or ROUTE_PREF_MODEL).strip()
    if model not in contracts.MODEL_KEYS:
        return _error(f'未知模型：{model}（可选：{", ".join(contracts.MODEL_KEYS)}）')

    impl = None
    raw_impl = data.get('impl')
    if raw_impl is not None and str(raw_impl).strip():
        impl = str(raw_impl).strip().lower()
        if impl not in ROUTE_PREF_IMPLS:
            return _error(f'非法实现：{raw_impl}（可选：c1 / 121）')

    direction = None
    raw_direction = data.get('direction')
    if raw_direction is not None and str(raw_direction).strip():
        direction = _normalize_direction(raw_direction)
        if not direction:
            return _error(f'非法方向：{raw_direction}（可选：auto / X / Y / Z / angle:<角度>）')

    if impl is None and direction is None:
        return _error('至少需要 impl 或 direction 之一')

    try:
        prefs = _load_route_prefs()
        prefs = _set_model_pref(prefs, part, model, impl=impl, direction=direction)
        saved = _save_route_prefs(prefs)
    except Exception as exc:  # noqa: BLE001
        return _error(f'保存路线偏好失败：{type(exc).__name__}: {exc}', 500)
    if not isinstance(saved, dict) or not isinstance(saved.get('parts'), dict):
        saved = prefs

    payload = _prefs_payload(part, saved)
    payload['model'] = model
    payload['impl'] = _resolve_impl(saved, part, model)
    payload['direction'] = _resolve_direction(saved, part, model)
    payload['effective'] = {
        'impl': payload['impl'],
        'end_treatment': _end_treatment_for(part, model, saved),
    }
    payload['stored'] = _model_pref(saved, part, model)

    plan = None
    plan_error = None
    try:
        settings = _settings()
        design = _design_for(part)
        end_treatment = _end_treatment_for(part, model, saved)
        plan = _route_plan_for(part, design, end_treatment, Path(settings.OUT_ROOT))
    except Exception as exc:  # noqa: BLE001 - 计划只是附带信息
        plan_error = f'{type(exc).__name__}: {exc}'
    payload['plan'] = plan
    if plan_error:
        payload['plan_error'] = plan_error
    return jsonify(payload)


# ---------------------------------------------------------------- 路由：上传

_UPLOAD_ACTIVE_STATUSES = ('queued', 'measuring', 'saving', 'cancelling')


def _auto_upload_parallel() -> int:
    """按核数自动分配上传并行度：``物理核数 // 单件建议进程数``（至少 1）。

    单件建议进程数取 ``min(8, measurement_workers())``：
    站内并行的实测拐点是 8 个进程（12/16 进程反而更慢），所以核多的机器
    应该「多开几件」，而不是让单件无限吃核。于是本机（8 物理核）→ 1 件，
    16 核 → 2 件，32 核 → 4 件。
    """
    try:
        from core.pp.tube.section_pool import measurement_workers, physical_cores
        per_measure = min(_UPLOAD_PER_MEASURE_CAP, max(1, int(measurement_workers())))
        cores = max(1, int(physical_cores()))
    except Exception:  # noqa: BLE001 - 测不到核数就退回兜底
        return _UPLOAD_PARALLEL_FALLBACK
    return max(1, cores // per_measure)


def _upload_parallel() -> int:
    """上传测量并行数（同时测几件）。

    ``GTAPP_UPLOAD_PARALLEL`` 给了正整数就照它（夹在 1..8）；否则自动分配，
    见 :func:`_auto_upload_parallel`。注意这与「单件内切几个站位进程」
    （``GTAPP_MEASURE_WORKERS``）是两个旋钮，两者相乘应约等于物理核数。
    """
    raw = os.environ.get(_UPLOAD_PARALLEL_ENV, '').strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return max(1, min(value, _UPLOAD_PARALLEL_MAX))
    return max(1, min(_auto_upload_parallel(), _UPLOAD_PARALLEL_MAX))


def _measurement_settings_payload() -> dict:
    """当前测量口径（实测布点 / 归档抽稀），供页面与接口对比不同实例。

    ``GTAPP_STATION_MM`` 是实测布点间距（1 mm 为历史口径），
    ``GTAPP_STATION_GRID`` 是 1 mm 计算网格之上再抽稀的归档间距。
    两者都在进程启动时读取，改了要重启服务并重新测量。
    """
    try:
        from core.pp.tube.station_plan import (
            station_grid_mm, station_step_mm, coarse_step_mm, correction_rounds,
        )
    except Exception:  # noqa: BLE001 - 旧包没有该模块时不影响 /status
        return {}
    try:
        step = float(station_step_mm())
        grid = float(station_grid_mm())
        return {
            'station_step_mm': step,
            'station_grid_mm': grid,
            'coarse_step_mm': float(coarse_step_mm(step)),
            'correction_rounds': int(correction_rounds(step)),
            'signature': f's{step:g}g{grid:g}',
        }
    except Exception:  # noqa: BLE001
        return {}


def _upload_executor() -> ThreadPoolExecutor:
    """按当前并行度取（或创建）线程池；测量本身仍在子进程里跑。"""
    workers = _upload_parallel()
    with _UPLOAD_EXECUTOR_LOCK:
        executor = _UPLOAD_EXECUTORS.get(workers)
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix='entity-upload')
            _UPLOAD_EXECUTORS[workers] = executor
        return executor


def _uploads_busy():
    """有上传任务在排队/测量/入库时返回一句描述，否则 ``None``。

    上传测量跑在独立子进程里，流水线也跑在子进程里；两边同时进 OCP 会互相
    踩内存，所以 /run 在上传任务活跃期间直接拒绝。
    """
    with _UPLOAD_TASKS_LOCK:
        for task in _UPLOAD_TASKS.values():
            if task.get('status') in _UPLOAD_ACTIVE_STATUSES:
                return (f"上传任务 {task.get('part') or task.get('task_id')}"
                        f"（{task.get('status')}）正在进行")
    return None


_UPLOAD_PROGRESS_RE = re.compile(r'(\d{1,3})\s*%')


def _uploads_dir() -> Path:
    return Path(_settings().SRC_PARTS_DIR) / _UPLOAD_SUBDIR


def _unique_upload_path(directory: Path, filename: str) -> Path:
    """同名文件加 ``_2`` / ``_3`` … 后缀（不覆盖已有件）。"""
    stem = Path(filename).stem or 'upload'
    suffix = Path(filename).suffix or '.stp'
    candidate = directory / f'{stem}{suffix}'
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = directory / f'{stem}_{index}{suffix}'
        if not candidate.exists():
            return candidate
    raise RuntimeError('同名上传文件过多（已到 _999），请先清理 uploads/ 目录')


def _upload_task_summary(task) -> dict:
    status = task.get('status')
    return {
        'task_id': task.get('task_id'),
        'part': task.get('part'),
        'file': task.get('file'),
        'status': status,
        'cancellable': status in ('queued', 'measuring', 'saving'),
        'phase': task.get('phase'),
        'progress': task.get('progress') or 0.0,
        'message': task.get('message') or '',
        'created_at': task.get('created_at'),
        'updated_at': task.get('updated_at'),
        'error': task.get('error'),
        'warning': task.get('warning'),
        'tube_id': task.get('tube_id'),
        'end_treatment': task.get('end_treatment'),
        'seconds': task.get('seconds'),
    }


def _upload_set(task, **fields):
    """更新上传任务并留一条时间戳（消息进 logs，最多 200 行）。"""
    with _UPLOAD_TASKS_LOCK:
        message = fields.get('message')
        for key, value in fields.items():
            if value is not None:
                task[key] = value
        task['updated_at'] = _now_iso()
        if message:
            logs = task.setdefault('logs', [])
            logs.append(f'{task["updated_at"]} {message}')
            if len(logs) > 200:
                del logs[:-200]


def _prune_uploads_locked():
    if len(_UPLOAD_TASKS) <= _MAX_UPLOAD_TASKS:
        return
    ordered = sorted(_UPLOAD_TASKS.values(), key=lambda t: t.get('created_at') or '')
    for task in ordered:
        if len(_UPLOAD_TASKS) <= _MAX_UPLOAD_TASKS:
            break
        if task.get('status') in _UPLOAD_ACTIVE_STATUSES:
            continue
        _UPLOAD_TASKS.pop(task.get('task_id'), None)


def _upload_db_payload(analysis: dict) -> dict:
    """把测量 analysis 组装成 ``save_full_analysis`` 要的 payload。"""
    data = dict(analysis or {})
    sections = data.get('sections_full')
    data['sections_full'] = sections if isinstance(sections, list) else []
    try:
        centerline_length = float(data.get('center_line_length') or 0.0)
    except (TypeError, ValueError):
        centerline_length = 0.0
    data['center_line_length'] = centerline_length
    try:
        data['actual_z_length'] = float(data.get('actual_z_length') or centerline_length)
    except (TypeError, ValueError):
        data['actual_z_length'] = centerline_length
    try:
        data['volume'] = float(data.get('volume') or 0.0)
    except (TypeError, ValueError):
        data['volume'] = 0.0
    segments = data.get('segments')
    data['segments'] = segments if isinstance(segments, dict) else {}
    data['algorithm'] = str(data.get('algorithm') or 'A')
    data.setdefault('source_format', 'stp')
    # 补齐「老字段」：工艺规划 / 关键截面校验 / 形线 QA 读的是
    # wall_thickness_data 与 centerline_perimeter_data，而测量只产出
    # sections_full。缺这一步的话，这条记录在管件模型分析页「数据库加载」
    # 之后会因为形线为空直接判 QA_INVALID_INPUT（empty_profile）。
    try:
        from core.pp.section_records import ensure_results_records
        ensure_results_records(data)
    except Exception:  # noqa: BLE001 - 补字段失败不阻塞入库本身
        pass
    if not data.get('centerline_length'):
        data['centerline_length'] = data.get('center_line_length') or 0.0
    return data


def _archive_source_step(step_path) -> tuple:
    """把源 STEP 复制进 ``PP_TUBE_SAVED_DIR`` 并算 sha256。

    入库记录要指向一份「不会被下一次同名上传覆盖」的副本，所以入库时归档。
    返回 ``(saved_path, file_hash)``；复制失败就直接指向上传件本身。
    """
    source = Path(step_path)
    saved_path = str(source)
    try:
        import shutil

        from config import PP_TUBE_SAVED_DIR

        saved_dir = Path(PP_TUBE_SAVED_DIR)
        saved_dir.mkdir(parents=True, exist_ok=True)
        target = saved_dir / f'{uuid.uuid4().hex[:8]}_{_safe_name(source.name)}'
        shutil.copy2(source, target)
        saved_path = str(target)
    except Exception:  # noqa: BLE001 - 复制失败就直接指向上传文件
        saved_path = str(source)
    return saved_path, _file_sha256(saved_path)


def _db_save_measured(part: str, meta: dict, cache_path=None) -> dict:
    """把一件已测量的缓存写进 ``tube_analysis``，按件号幂等。

    规则（2026-11-11 与用户确认）：
      * 数据库里已有同名记录 → 不插入，状态 ``exists``；历史重复行顺手删掉旧的，
        只保留 id 最大（最新）那条；
      * 没有记录 → 读测量缓存重建老字段后插入，状态 ``imported``，返回新 ``tube_id``；
      * 缓存不可用 / 入库失败 → 状态 ``skipped`` 并带中文原因。
    """
    result = {'part': part, 'status': 'skipped', 'tube_id': None,
              'removed': [], 'reason': None}
    try:
        from config import PP_TUBE_DB
        from core.pp import thickness_db
    except Exception as exc:  # noqa: BLE001
        result['reason'] = f'入库模块不可用：{type(exc).__name__}: {exc}'
        return result

    existing = thickness_db.find_tubes_by_display_name(part, db_path=PP_TUBE_DB)
    if existing:
        keep = existing[0]
        removed = []
        for row in existing[1:]:
            try:
                if thickness_db.delete_tube_data(row['id'], db_path=PP_TUBE_DB):
                    removed.append(int(row['id']))
            except Exception:  # noqa: BLE001 - 清理历史重复失败不阻塞导入
                pass
        result.update({
            'status': 'exists',
            'tube_id': int(keep['id']),
            'removed': removed,
            'reason': f"数据库已存在（tube #{keep['id']}）",
        })
        return result

    cache = Path(cache_path) if cache_path else None
    if cache is None or not cache.is_file():
        result['reason'] = '测量缓存不存在，无法入库'
        return result
    try:
        analysis = json.loads(cache.read_text(encoding='utf-8'))
    except Exception as exc:  # noqa: BLE001
        result['reason'] = f'读取测量缓存失败：{type(exc).__name__}: {exc}'
        return result
    if not isinstance(analysis, dict):
        result['reason'] = '测量缓存格式不对（不是 JSON 对象）'
        return result
    if analysis.get('error'):
        result['reason'] = f"测量缓存里带错误：{analysis.get('error')}"
        return result

    payload = _upload_db_payload(analysis)
    saved_path, file_hash = _archive_source_step(meta.get('path'))
    try:
        tube_id, error = thickness_db.save_full_analysis(
            file_name=meta.get('file') or Path(str(meta.get('path') or '')).name,
            display_name=part,
            source_file_path=saved_path,
            source_file_type=(Path(str(meta.get('path') or '')).suffix.lstrip('.') or 'stp').lower(),
            file_hash=file_hash,
            payload=payload,
            db_path=PP_TUBE_DB,
            origin='entity',
        )
    except Exception as exc:  # noqa: BLE001
        result['reason'] = f'入库异常：{type(exc).__name__}: {exc}'
        return result
    if tube_id is None or int(tube_id) < 0:
        result['reason'] = f'入库失败：{error or "未知错误"}'
        return result
    result.update({'status': 'imported', 'tube_id': int(tube_id)})
    return result


def _upload_worker(task_id: str):
    """线程池任务：排队 → 子进程测量（measure_only）→ 解析 → 入库。

    测量走 ``prepare_proc.run_prepare(measure_only=True)``：每个任务一个子进程，
    线程池只控制并发数，OCP 不会在线程间共享。异常按「测量 / 结果读取 /
    入库」分别落到 ``task['error']``，前端可以直接展示。
    """
    task = _UPLOAD_TASKS.get(task_id)
    if not task:
        return
    started = time.time()

    def log(message):
        text = str(message or '').strip()
        if not text:
            return
        progress = None
        match = _UPLOAD_PROGRESS_RE.search(text)
        if match:
            pct = max(0, min(100, int(match.group(1))))
            progress = round(5 + pct * 0.83, 1)
        _upload_set(task, message=text[:500], progress=progress)

    try:
        settings = _settings()
        out_root = Path(settings.OUT_ROOT)
        end_treatment = task.get('end_treatment') or _end_treatment_for(task['part'])
        task['end_treatment'] = end_treatment
        cache_path = Path(settings.measured_path(task['part'], end_treatment, out_root))
        task['cache_path'] = str(cache_path)

        # 1) 排队：等当前流水线任务结束（流水线子进程同样吃 OCP）
        deadline = time.time() + _UPLOAD_WAIT_LIMIT
        announced = False
        while True:
            if task.get('cancelled'):
                raise RuntimeError('上传任务已取消')
            with _JOBS_LOCK:
                active = _active_job_locked()
                active_id = active['id'] if active else None
            if not active_id:
                break
            if time.time() > deadline:
                raise TimeoutError(
                    f'等待任务 {active_id} 结束超时（{_UPLOAD_WAIT_LIMIT:.0f} s）')
            if not announced:
                _upload_set(
                    task, status='queued', phase='queued', progress=2.0,
                    message=f'排队中：等待当前任务 {active_id} 结束…')
                announced = True
            time.sleep(_UPLOAD_POLL_SECONDS)
        if task.get('cancelled'):
            raise RuntimeError('上传任务已取消')

        # 1.5) 同名重现且缓存仍在 → 不重新测量
        if task.get('unchanged'):
            ready, _size_mb, _cache_path, _reason = _cache_ready(task['part'], end_treatment)
            if ready:
                seconds = round(time.time() - started, 1)
                _upload_set(
                    task, status='done', phase='done', progress=100.0, warning=None,
                    seconds=seconds, cached=True,
                    message=('完成：文件内容未变，复用已有测量缓存'
                             f'（{seconds} s）；点「导入主流程」写入分析数据库'))
                return

        # 2) 子进程测量（measure_only：只测量，不导出 5 类模型）
        _upload_set(task, status='measuring', phase='measuring', progress=5.0,
                    message=f'开始测量截面（端部口径 {end_treatment}）…')
        from core.entity import prepare_proc
        try:
            result = prepare_proc.run_prepare(
                part=task['part'],
                step=task['path'],
                end_treatment=end_treatment,
                out_root=str(out_root),
                measure_only=True,
                log=log,
                stop_event=task.get('stop_event'),
            )
        except prepare_proc.PrepareCancelled as exc:
            raise RuntimeError(f'测量已取消：{exc}') from None
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f'测量失败：{type(exc).__name__}: {exc}') from exc

        measured_path = Path((result or {}).get('measured_path') or cache_path)
        task['cache_path'] = str(measured_path)
        # 只做轻量体检：60–154MB 的 JSON 留到「导入主流程」时再读，别在测量线程里解析。
        if not measured_path.is_file():
            raise RuntimeError(f'测量结果不存在：{measured_path}')
        try:
            with open(measured_path, 'rb') as fh:
                head = fh.read(_CACHE_HEAD_BYTES)
            size_bytes = measured_path.stat().st_size
        except OSError as exc:
            raise RuntimeError(f'读取测量结果失败：{exc}') from exc
        if not size_bytes:
            raise RuntimeError('测量结果为空')
        size_mb = round(size_bytes / 1048576, 1)
        if _CACHE_MARKER not in head:
            raise RuntimeError('测量结果不完整（缺少 sections_full，可能测量中断）')
        cached = bool((result or {}).get('cached'))
        seconds = round(time.time() - started, 1)
        _upload_set(
            task, status='done', phase='done', progress=100.0, tube_id=None,
            warning=None, seconds=seconds, cached=cached,
            message=(f"完成：{'复用已有测量缓存' if cached else '已测量'}"
                     f'（{size_mb} MB，{seconds} s）；点「导入主流程」写入分析数据库'))
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给 Flask
        seconds = round(time.time() - started, 1)
        if task.get('cancelled'):
            _upload_set(
                task, status='cancelled', phase='cancelled', seconds=seconds,
                error=None, message='已取消：测量子进程已停止（重新测量会从头开始）')
            return
        _upload_set(
            task, status='error', phase='error', seconds=seconds,
            error=f'{type(exc).__name__}: {exc}',
            message=f'失败：{type(exc).__name__}: {exc}')


def _file_sha256(path) -> str:
    """流式计算 sha256（上传件判重、入库记录都用到）。"""
    try:
        import hashlib
        digest = hashlib.sha256()
        with open(path, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1 << 16), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ''


def _clear_measured_cache(part: str) -> list:
    """删掉某件的测量缓存（c1 / v1 / 型线），返回被删路径。

    上传同名但内容变了时调用：旧缓存对应的是旧几何，必须作废，否则会拿旧测量
    结果去算新件的模型。
    """
    removed = []
    try:
        settings = _settings()
        out_root = Path(settings.OUT_ROOT)
        for treatment in ('c1', 'v1'):
            try:
                path = Path(settings.measured_path(part, treatment, out_root))
            except Exception:  # noqa: BLE001
                continue
            if path.is_file():
                try:
                    path.unlink()
                    removed.append(str(path))
                except OSError:
                    pass
        profile = out_root / '_cache' / f'{part}_profile.json'
        if profile.is_file():
            try:
                profile.unlink()
                removed.append(str(profile))
            except OSError:
                pass
    except Exception:  # noqa: BLE001 - 清缓存失败不影响上传本身
        pass
    return removed


def _create_upload_task(file_storage, uploads: Path) -> dict:
    """保存单个上传文件并登记任务（不负责调度）。

    同名规则（2026-11-11 起）：**件名保持 STEP 原文件名**，同名上传覆盖旧文件而不是
    加 ``_2/_3``；内容与旧文件完全一致时复用原文件与测量缓存，内容变了则作废旧缓存。

    返回 ``{"task", "size", "unchanged", "overwritten", "cache_cleared"}``；
    后缀/空文件抛 ``ValueError``，保存失败抛 ``RuntimeError``，文案可直接展示给用户。
    """
    raw_name = Path(str(file_storage.filename or '').replace('\\', '/')).name
    suffix = Path(raw_name).suffix.lower()
    if suffix not in _SOURCE_SUFFIXES:
        raise ValueError(f'只支持 .stp / .step 文件（收到：{raw_name or "空文件名"}）')

    part = _safe_name(Path(raw_name).stem) or 'upload'
    try:
        uploads.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f'无法创建上传目录：{type(exc).__name__}: {exc}') from exc

    target = uploads / f'{part}{suffix}'
    staging = uploads / f'.{part}{suffix}.uploading'
    try:
        file_storage.save(str(staging))
    except Exception as exc:  # noqa: BLE001
        try:
            staging.unlink()
        except OSError:
            pass
        raise RuntimeError(f'保存上传文件失败：{type(exc).__name__}: {exc}') from exc
    try:
        size = staging.stat().st_size
    except OSError:
        size = 0
    if not size:
        try:
            staging.unlink()
        except OSError:
            pass
        raise ValueError('上传文件为空或保存失败')

    # 同名旧件：同后缀且内容一致 → 完全复用；否则覆盖并把旧几何的缓存作废
    previous = [p for p in (uploads / f'{part}.stp', uploads / f'{part}.step') if p.is_file()]
    unchanged = False
    overwritten = []
    if len(previous) == 1 and previous[0] == target and _file_sha256(previous[0]) == _file_sha256(staging):
        unchanged = True
        try:
            staging.unlink()
        except OSError:
            pass
    else:
        for old in previous:
            if old == target:
                continue
            try:
                old.unlink()
                overwritten.append(str(old))
            except OSError:
                pass
        os.replace(staging, target)
    cache_cleared = _clear_measured_cache(part) if not unchanged else []

    end_treatment = _end_treatment_for(part, ROUTE_PREF_MODEL)
    task_id = f'up_{uuid.uuid4().hex[:12]}'
    if unchanged:
        seed = '同名文件内容未变：复用原文件与测量缓存'
    elif previous:
        seed = '同名文件已覆盖，旧测量缓存已作废，重新测量…'
    else:
        seed = '排队中：等待测量线程…'
    task = {
        'task_id': task_id,
        'part': part,
        'file': target.name,
        'path': str(target),
        'status': 'queued',
        'phase': 'queued',
        'progress': 2.0,
        'message': seed,
        'created_at': _now_iso(),
        'updated_at': _now_iso(),
        'error': None,
        'warning': None,
        'tube_id': None,
        'end_treatment': end_treatment,
        'seconds': None,
        'cancelled': False,
        # 停止开关：POST /uploads/<task_id>/cancel 会 set 它，测量子进程树立刻被杀
        'stop_event': threading.Event(),
        'unchanged': unchanged,
        'overwritten': bool(overwritten or (previous and not unchanged)),
        'cache_cleared': cache_cleared,
        'out_root': str(_settings().OUT_ROOT),
        'cache_path': str(_settings().measured_path(part, end_treatment)),
        'logs': [],
    }
    with _UPLOAD_TASKS_LOCK:
        _UPLOAD_TASKS[task_id] = task
        _prune_uploads_locked()
    return {
        'task': task,
        'size': size,
        'unchanged': unchanged,
        'overwritten': task['overwritten'],
        'cache_cleared': cache_cleared,
    }


@entity_bp.route('/upload', methods=['POST'])
def entity_upload():
    """上传一个或多个 ``.stp`` / ``.step`` → 落 ``SRC_PARTS_DIR/uploads/`` → 并行测量。

    multipart 字段名 ``files``（可重复）；为兼容旧前端仍接受单文件字段 ``file``。
    每个文件独立 task，立即返回 task 列表；测量走子进程线程池（默认 2 路）。
    """
    candidates = []
    seen = set()
    for storage in list(request.files.getlist('files')) + list(request.files.getlist('file')):
        if storage is None or not str(storage.filename or '').strip():
            continue
        key = id(storage)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(storage)
    if not candidates:
        return _error('缺少上传文件（multipart 字段名 files，兼容 file）')

    uploads = _uploads_dir()
    entries, errors = [], []
    for storage in candidates:
        try:
            entries.append(_create_upload_task(storage, uploads))
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不拖累其它文件
            errors.append({
                'file': Path(str(storage.filename or '').replace('\\', '/')).name,
                'error': str(exc) or f'{type(exc).__name__}',
            })
    if not entries:
        detail = '；'.join(f"{item['file']}：{item['error']}" for item in errors)
        return _error(f'上传失败：{detail or "没有可用文件"}', 400)

    executor = _upload_executor()
    for entry in entries:
        task = entry['task']
        task['_future'] = executor.submit(_upload_worker, task['task_id'])

    parallel = _upload_parallel()
    tasks = [entry['task'] for entry in entries]
    first = tasks[0]
    response = {
        'success': True,
        'ok': True,
        'task_id': first['task_id'],
        'part': first['part'],
        'status': first['status'],
        'file': first['file'],
        'size': entries[0]['size'],
        'end_treatment': first['end_treatment'],
        'unchanged': bool(entries[0]['unchanged']),
        'overwritten': bool(entries[0]['overwritten']),
        'cache_cleared': entries[0]['cache_cleared'],
        'task': _upload_task_summary(first),
        'tasks': [_upload_task_summary(task) for task in tasks],
        'count': len(tasks),
        'errors': errors,
        'parallel': parallel,
        'message': (f'已接收 {len(tasks)} 个文件，后台并行测量'
                    f'（最多 {parallel} 路）'),
    }
    return jsonify(response)


@entity_bp.route('/uploads', methods=['GET'])
def entity_uploads():
    limit = request.args.get('limit', type=int) or _MAX_UPLOAD_TASKS
    limit = max(1, min(limit, _MAX_UPLOAD_TASKS))
    with _UPLOAD_TASKS_LOCK:
        tasks = sorted(
            _UPLOAD_TASKS.values(),
            key=lambda t: (t.get('created_at') or '', t.get('task_id') or ''),
            reverse=True,
        )
        active = sum(
            1 for task in tasks if task.get('status') in _UPLOAD_ACTIVE_STATUSES)
        items = [_upload_task_summary(task) for task in tasks[:limit]]
    return jsonify({
        'success': True,
        'tasks': items,
        'count': len(items),
        'active': active,
        'parallel': _upload_parallel(),
        'uploads_dir': str(_uploads_dir()),
    })


@entity_bp.route('/uploads/clear', methods=['POST'])
def entity_uploads_clear():
    """清空上传任务记录（只清内存表，不动磁盘缓存、队列与分析数据库）。

    请求体可给 ``{"keep_active": false}`` 连进行中的任务也一起清掉（默认保留）。
    """
    data = request.get_json(silent=True) or {}
    keep_active = data.get('keep_active', True)
    if isinstance(keep_active, str):
        keep_active = keep_active.strip().lower() not in ('0', 'false', 'no', 'off')
    with _UPLOAD_TASKS_LOCK:
        removed = []
        for task_id, task in list(_UPLOAD_TASKS.items()):
            if keep_active and task.get('status') in _UPLOAD_ACTIVE_STATUSES:
                continue
            removed.append(task_id)
            _UPLOAD_TASKS.pop(task_id, None)
        remaining = len(_UPLOAD_TASKS)
        active = sum(
            1 for task in _UPLOAD_TASKS.values()
            if task.get('status') in _UPLOAD_ACTIVE_STATUSES)
    message = f'已清空 {len(removed)} 条上传任务记录'
    if active:
        message += f'（保留 {active} 条进行中的任务）'
    return jsonify({
        'success': True,
        'removed': len(removed),
        'removed_ids': removed,
        'remaining': remaining,
        'active': active,
        'message': message,
    })


@entity_bp.route('/uploads/<task_id>/cancel', methods=['POST'])
def entity_upload_cancel(task_id):
    """停止一个上传测量任务（排队中或测量中都行）。

    测量跑在独立子进程树里（``core.entity.prepare_worker``），所以「停止」=
    set ``stop_event`` → 子进程里主循环 0.2 s 内发现 → ``taskkill /T /F``。
    排队中的任务只是把 ``cancelled`` 置位，轮到它时会直接跳过、不占进程。
    已经完成/失败的任务返回 409，避免误以为撤回了。
    """
    tid = str(task_id or '').strip()
    with _UPLOAD_TASKS_LOCK:
        task = _UPLOAD_TASKS.get(tid)
        if not task:
            return _error(f'上传任务不存在：{task_id}', 404)
        status = task.get('status')
        if status in ('done', 'imported', 'error', 'cancelled'):
            return _error(f'任务已结束（{status}），无需取消', 409)
        task['cancelled'] = True
        event = task.get('stop_event')
        if event is not None:
            try:
                event.set()
            except Exception:  # noqa: BLE001 - 事件已失效时只置标记
                pass
        future = task.get('_future')
    if future is not None:
        # 还在线程池排队、没开始跑：直接撤掉，状态立刻变成 cancelled
        try:
            future.cancel()
        except Exception:  # noqa: BLE001
            pass
    message = '已请求取消：正在停止测量子进程' if status in ('measuring', 'saving') else '已取消排队中的任务'
    _upload_set(task, status='cancelling' if status in ('measuring', 'saving') else 'cancelled',
                message=message, error=None)
    return jsonify({'success': True, 'task_id': tid, 'status': task.get('status'),
                    'message': message, 'task': _upload_task_summary(task)})


@entity_bp.route('/uploads/<task_id>', methods=['GET'])
def entity_upload_task(task_id):
    with _UPLOAD_TASKS_LOCK:
        task = _UPLOAD_TASKS.get(str(task_id or '').strip())
        if not task:
            return _error(f'上传任务不存在：{task_id}', 404)
        payload = _upload_task_summary(task)
        payload['logs'] = list(task.get('logs') or [])[-50:]
        payload['cache_path'] = task.get('cache_path')
    return jsonify({'success': True, **payload, 'task': payload})


# ------------------------------------------------------- 路由：导入主流程
# 上传区已经把多件并行测量完、写进 ``_cache/<件>_measured*.json``。这里只把
# 「已测量的件」登记成主流程的待跑队列，不碰几何、也不启动任务；之后点运行，
# orchestrator 命中缓存约 1.5s/件，主流程就不用自己再串行测量一遍。
#
# 注意：并行的只有测量。5 类模型导出与 c1/pipe 必须串行（OCP/FreeCAD 非线程
# 安全），所以队列里再多件也仍是一个任务逐件跑。

_IMPORT_FILENAME = 'import_queue.json'
_IMPORT_LOCK = threading.RLock()
#: 判缓存完整的头部特征：只读前 64KB，不解析 60–154MB 的 JSON。
_CACHE_HEAD_BYTES = 65536
_CACHE_MARKER = b'"sections_full"'

#: 导入主流程是否顺带写「管件模型分析」的 ``tube_analysis`` 库。
#:
#: 默认 ``True``（2026-11-21 与用户确认）：流水线测完的件要能在
#: 「管件模型分析 → 数据库加载」里打开，所以导入时写库；但行上盖 ``origin='entity'``，
#: 「工艺优化 → 从数据库加载分析结果」查询时带 ``origin=pp`` 把这一类过滤掉。
#: 不想写库时在 POST /import 的 body 里给 ``{"write_db": false}``。
_IMPORT_WRITE_DB = True


def _import_file() -> Path:
    try:
        out_root = Path(_settings().OUT_ROOT)
    except Exception:  # noqa: BLE001 - 配置坏了也要能读到空队列
        out_root = Path('.')
    return out_root / '_config' / _IMPORT_FILENAME


def _import_read() -> dict:
    try:
        data = json.loads(_import_file().read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return {'version': 1, 'parts': []}
    if not isinstance(data, dict) or not isinstance(data.get('parts'), list):
        return {'version': 1, 'parts': []}
    data.setdefault('version', 1)
    return data


def _import_write(payload: dict) -> None:
    path = _import_file()
    payload = dict(payload)
    payload['version'] = 1
    payload['updated_at'] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def _cache_ready(part: str, end_treatment: str = 'c1'):
    """测量缓存是否真的可用：``(ready, size_mb, path, reason)``。

    只看大小 + 头部标记 —— 解析整份 JSON 要么几十秒，要么直接把内存打满。
    """
    ready, size_mb, cache_path = _measured_cache_info(part, end_treatment)
    if not ready:
        return False, size_mb, cache_path, '尚未测量（没有测量缓存，先上传并测量）'
    try:
        with open(cache_path, 'rb') as fh:
            head = fh.read(_CACHE_HEAD_BYTES)
    except OSError as exc:
        return False, size_mb, cache_path, f'读取测量缓存失败：{exc}'
    if _CACHE_MARKER not in head:
        return False, size_mb, cache_path, '测量缓存不完整（缺少 sections_full，可能测量中断）'
    return True, size_mb, cache_path, None


def _db_records_by_name() -> dict:
    """分析数据库现有记录：``{显示名称: {"id": int, "file_name": str}}``。

    同名多条时取最新（id 最大）那条，和 ``_db_save_measured`` 的保留口径一致。
    """
    try:
        from config import PP_TUBE_DB
        from core.pp import thickness_db

        frame = thickness_db.list_database_tubes(db_path=PP_TUBE_DB)
    except Exception:  # noqa: BLE001 - 数据库读失败就当没有记录
        return {}
    out = {}
    try:
        if frame is None or getattr(frame, 'empty', True):
            return {}
        for _, row in frame.iterrows():
            name = str(row.get('显示名称') or '').strip()
            if name and name not in out:
                out[name] = {'id': int(row.get('ID') or 0), 'file_name': row.get('文件名')}
    except Exception:  # noqa: BLE001
        return {}
    return out


@entity_bp.route('/import', methods=['GET'])
def entity_import_get():
    """已导入主流程的队列 + 就绪状态 + 是否已在分析数据库里。"""
    try:
        parts, _src = _scan_parts()
    except Exception as exc:  # noqa: BLE001
        return _error(f'扫描管件失败：{exc}', 500)
    known = {item['name']: item for item in parts}
    db_by_name = _db_records_by_name()
    with _IMPORT_LOCK:
        queue = _import_read()
    items = []
    for entry in queue.get('parts') or []:
        if not isinstance(entry, dict):
            continue
        part = str(entry.get('part') or '').strip()
        if not part:
            continue
        item = dict(entry)
        meta = known.get(part) or {}
        end_treatment = str(item.get('end_treatment') or meta.get('end_treatment') or 'c1')
        ready, size_mb, cache_path, reason = _cache_ready(part, end_treatment)
        db_row = db_by_name.get(part)
        item['exists'] = bool(meta)
        item['step'] = meta.get('path') or item.get('step')
        item['ready'] = bool(ready and meta)
        item['cache_mb'] = size_mb if ready else item.get('cache_mb')
        item['cache_path'] = str(cache_path) if cache_path else item.get('cache_path')
        item['in_db'] = db_row is not None
        item['tube_id'] = int(db_row['id']) if db_row else None
        if not item['ready']:
            item['reason'] = reason or '源 STEP 已不在件库（可能被删除或改名）'
        elif item['in_db']:
            item['reason'] = f"分析数据库已有记录（tube #{item['tube_id']}）"
        else:
            item['reason'] = None
        items.append(item)
    return jsonify({
        'success': True,
        'parts': items,
        'count': len(items),
        'ready': [item['part'] for item in items if item['ready']],
        'in_db': [item['part'] for item in items if item.get('in_db')],
        'queue_file': str(_import_file()),
    })


@entity_bp.route('/import', methods=['POST'])
def entity_import_post():
    """把并行测量完的上传件登记成主流程的待跑队列，并写入「管件模型分析」数据库。

    body 三选一：``{"tasks": [task_id...]}``、``{"parts": [名...]}``、
    ``{"source": "upload"}``（件库里所有 upload 来源的件）。规则（2026-11-21 与用户确认）：

      * **写 ``tube_analysis``（``origin='entity'``）**：测完的件要能在
        「管件模型分析 → 数据库加载」里打开；按件号幂等（已有记录跳过、删了能补）；
      * **同时登记队列** ``import_queue.json``，点「运行」时批量复用测量缓存；
      * **「工艺优化」列表不显示**：那个页面查询带 ``origin=pp``，只列在「管件模型分析」
        页自己保存的分析（见 ``/api/pp/tubes``）；
      * 不想写库时在 body 里给 ``{"write_db": false}``；
      * 没测量完的件不会进队列，进 ``skipped``（带中文原因）。

    本接口不启动几何任务；5 类导出与 c1/pipe 仍由 ``/run`` 单任务串行执行。
    """
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _error('请求体必须是 JSON 对象')
    raw_write_db = data.get('write_db')
    write_db = bool(raw_write_db) if isinstance(raw_write_db, bool) else bool(_IMPORT_WRITE_DB)
    try:
        parts, _src = _scan_parts()
    except Exception as exc:  # noqa: BLE001
        return _error(f'扫描管件失败：{exc}', 500)
    known = {item['name']: item for item in parts}

    wanted = []
    seen = set()

    def _want(name: str) -> None:
        name = str(name or '').strip()
        if name and name not in seen:
            seen.add(name)
            wanted.append(name)

    raw_parts = data.get('parts')
    if isinstance(raw_parts, (list, tuple)):
        for item in raw_parts:
            _want(item)

    raw_tasks = data.get('tasks')
    task_ids = [str(item or '').strip() for item in raw_tasks] if isinstance(raw_tasks, (list, tuple)) else []
    source = str(data.get('source') or '').strip().lower()
    if source == 'upload' or task_ids:
        for name, meta in known.items():
            if source == 'upload' and meta.get('source') == 'upload':
                _want(name)
        if task_ids:
            with _UPLOAD_TASKS_LOCK:
                for task_id in task_ids:
                    task = _UPLOAD_TASKS.get(task_id)
                    if task and task.get('part'):
                        _want(task['part'])

    if not wanted:
        return _error('没有可导入的件：请给出 tasks / parts，或 source="upload"', 400)

    replace = bool(data.get('replace'))
    now = _now_iso()
    imported = []
    skipped = []
    with _IMPORT_LOCK:
        queue = _import_read()
        existing = queue.get('parts') if isinstance(queue.get('parts'), list) else []
        by_part = {}
        if not replace:
            for entry in existing:
                if isinstance(entry, dict) and entry.get('part'):
                    by_part[str(entry['part'])] = entry
        for name in wanted:
            meta = known.get(name)
            if not meta:
                skipped.append({'part': name, 'reason': '未知管件（件库里没有这个 STEP）'})
                continue
            end_treatment = meta.get('end_treatment') or 'c1'
            ready, size_mb, cache_path, reason = _cache_ready(name, end_treatment)
            if not ready:
                skipped.append({'part': name, 'reason': reason})
                continue
            if write_db:
                try:
                    with _UPLOAD_DB_LOCK:
                        outcome = _db_save_measured(name, meta, cache_path)
                except Exception as exc:  # noqa: BLE001
                    skipped.append({
                        'part': name,
                        'reason': f'入库异常：{type(exc).__name__}: {exc}',
                    })
                    continue
                if outcome.get('status') == 'skipped':
                    skipped.append({'part': name, 'reason': outcome.get('reason')})
                    continue
            else:
                # 只登记队列：不碰 tube_analysis，流水线的测量不进「工艺优化」数据源。
                outcome = {'status': 'queue_only'}
            entry = dict(by_part.get(name) or {})
            entry.update({
                'part': name,
                'step': meta.get('path'),
                'source': meta.get('source'),
                'end_treatment': end_treatment,
                'cache_path': str(cache_path),
                'cache_mb': size_mb,
                'tube_id': outcome.get('tube_id'),
                'db_status': outcome.get('status'),
                'imported_at': now,
            })
            by_part[name] = entry
            imported.append({
                'part': name,
                'step': entry.get('step'),
                'source': entry.get('source'),
                'end_treatment': end_treatment,
                'cache_path': entry.get('cache_path'),
                'cache_mb': size_mb,
                'tube_id': outcome.get('tube_id'),
                'db_status': outcome.get('status'),
                'duplicates_removed': outcome.get('removed') or [],
            })
            if outcome.get('status') == 'exists':
                skipped.append({
                    'part': name,
                    'reason': outcome.get('reason') or '数据库已存在',
                    'tube_id': outcome.get('tube_id'),
                })
        merged = list(by_part.values())
        _import_write({'version': 1, 'parts': merged})
    created = [item for item in imported if item.get('db_status') == 'imported']
    existing_items = [item for item in imported if item.get('db_status') == 'exists']
    existing_names = {item['part'] for item in existing_items}
    removed_ids = [tid for item in imported for tid in (item.get('duplicates_removed') or [])]
    other_skips = [item for item in skipped if item.get('part') not in existing_names]
    if write_db:
        message = f'已导入 {len(imported)} 件：新入库 {len(created)} 件（可在「管件模型分析」数据库加载）'
        if existing_items:
            message += f'，数据库已存在 {len(existing_items)} 件（未重复导入）'
        if removed_ids:
            message += f'，清理重复记录 {len(removed_ids)} 条'
    else:
        message = f'已导入 {len(imported)} 件到主流程队列（未写分析数据库）'
    if other_skips:
        message += f'，跳过 {len(other_skips)} 件'
    return jsonify({
        'success': True,
        'write_db': write_db,
        'imported': imported,
        'created': created,
        'existing': existing_items,
        'skipped': skipped,
        'imported_parts': [item['part'] for item in imported],
        'duplicates_removed': removed_ids,
        'queue': {
            'count': len(merged),
            'parts': [entry.get('part') for entry in merged],
            'file': str(_import_file()),
        },
        'message': message,
    })


@entity_bp.route('/parts/<part>/profile', methods=['GET'])
def entity_part_profile(part):
    """单件实测型线 / 壁厚曲线（供页面画图）。"""
    part = (part or '').strip()
    if not part:
        return _error('缺少管件名', 404)
    try:
        settings = _settings()
        contracts = _contracts()
        parts, _src = _scan_parts()
        meta = next((item for item in parts if item['name'] == part), None)
        end_treatment = meta['end_treatment'] if meta else contracts.end_treatment_for(part)
        out_root = Path(settings.OUT_ROOT)
        measured = Path(settings.measured_path(part, end_treatment, out_root))
        if not measured.is_file():
            return _error(f'未找到 {part} 的测量缓存（先跑一次测量）', 404)
        return jsonify(_profile_for(part, end_treatment, out_root, measured))
    except Exception as exc:  # noqa: BLE001
        return _error(f'读取型线失败：{exc}', 500)


@entity_bp.route('/parts/<part>/design', methods=['POST'])
def entity_part_design(part):
    """按推荐路线补算该件的上游工艺设计（幂等：已登记直接返回，只补缺）。"""
    part = (part or '').strip()
    if not part:
        return _error('缺少管件名', 404)
    try:
        parts, _src = _scan_parts()
    except Exception as exc:  # noqa: BLE001
        return _error(f'扫描管件失败：{exc}', 500)
    if part not in [item['name'] for item in parts]:
        return _error(f'未知管件：{part}', 404)
    data = request.get_json(silent=True) or {}
    try:
        design_gen = _design_gen()
    except Exception as exc:  # noqa: BLE001
        return _error(f'补算模块不可用：{type(exc).__name__}: {exc}', 500)
    try:
        result = design_gen.create_default_design(
            part,
            _settings().OUT_ROOT,
            method=(str(data.get('method')).strip() or None) if data.get('method') else None,
            force=bool(data.get('force')),
        )
    except Exception as exc:  # noqa: BLE001
        return _error(f'补算设计失败：{type(exc).__name__}: {exc}', 500)
    payload = {key: value for key, value in result.items() if key != 'best'}
    return jsonify({'success': True, 'part': part, **payload})


@entity_bp.route('/plan', methods=['GET'])
def entity_plan():
    try:
        contracts = _contracts()
        parts, _src_dir = _scan_parts()
    except Exception as exc:
        return _error(f'生成计划失败：{exc}', 500)
    part_names = [p['name'] for p in parts]
    models = list(contracts.MODEL_KEYS)
    raw_routes = (request.args.get('routes') or '').strip()
    try:
        requested = [item.strip() for item in raw_routes.replace('，', ',').split(',') if item.strip()]
        routes = contracts.normalize_routes(requested) if requested else None
    except ValueError as exc:
        return _error(str(exc), 400)
    units = [
        {'part': part, 'key': key, 'route': route}
        for part in part_names
        for key in models
        for route in (routes or (contracts.default_route(key),))
    ]
    return jsonify({
        'success': True,
        'parts': part_names,
        'models': _models_payload(),
        'routes': _routes_payload(),
        'default_routes': _default_routes_payload(),
        'explicit_routes': list(routes) if routes else None,
        'units': units,
        'count': len(units),
    })


# ---------------------------------------------------------------- 路由：运行


@entity_bp.route('/run', methods=['POST'])
def entity_run():
    data = request.get_json(silent=True)
    if data is None:
        return _error(
            '请求体必须是 JSON：{"parts": [...], "models": [...], '
            '"routes": [...], "force": false}')
    if not isinstance(data, dict):
        return _error('请求体必须是 JSON 对象')
    try:
        contracts = _contracts()
        scanned, _src_dir = _scan_parts()
    except Exception as exc:
        return _error(f'实体化模块不可用：{exc}', 500)

    available_parts = [p['name'] for p in scanned]
    raw_parts = data.get('parts')
    if raw_parts is None:
        selected_parts = list(available_parts)
    else:
        if not isinstance(raw_parts, (list, tuple)):
            return _error('parts 必须是管件名称数组')
        selected_parts = [str(p).strip() for p in raw_parts if str(p).strip()]
        if not selected_parts:
            # 空数组视同缺省：跑全部管件（与 CLI 语义一致）
            selected_parts = list(available_parts)
    if not selected_parts:
        return _error('没有可运行的管件：源目录为空或不存在')
    unknown_parts = [p for p in selected_parts if p not in available_parts]
    if unknown_parts:
        return _error(f'未知管件：{", ".join(unknown_parts)}（可选：{", ".join(available_parts)}）')

    all_models = list(contracts.MODEL_KEYS)
    raw_models = data.get('models')
    if raw_models is None:
        selected_models = list(all_models)
    else:
        if not isinstance(raw_models, (list, tuple)):
            return _error('models 必须是模型名称数组')
        selected_models = [str(m).strip() for m in raw_models if str(m).strip()]
        if not selected_models:
            # 空数组视同缺省：跑全部模型（与 CLI 语义一致）
            selected_models = list(all_models)
    unknown_models = [m for m in selected_models if m not in all_models]
    if unknown_models:
        return _error(f'未知模型：{", ".join(unknown_models)}（可选：{", ".join(all_models)}）')

    all_routes = list(contracts.ROUTES)
    raw_routes = data.get('routes')
    if raw_routes is None:
        selected_routes = None  # 按模型默认：外径形腔 c1、其余 pipe
    else:
        if not isinstance(raw_routes, (list, tuple)):
            return _error('routes 必须是路线名称数组（省略则按模型默认路线）')
        selected_routes = [str(r).strip() for r in raw_routes if str(r).strip()]
        if not selected_routes:
            selected_routes = None
    if selected_routes:
        unknown_routes = [r for r in selected_routes if r not in all_routes]
        if unknown_routes:
            return _error(f'未知路线：{", ".join(unknown_routes)}（可选：{", ".join(all_routes)}）')

    # 路线偏好：请求体（可选）覆盖磁盘配置，合并后透传给 orchestrator。
    raw_prefs = data.get('route_prefs')
    prefs_error = _validate_route_prefs(raw_prefs)
    if prefs_error:
        return _error(prefs_error, 400)
    try:
        merged_prefs = _merge_route_prefs(_load_route_prefs(), raw_prefs)
    except Exception as exc:  # noqa: BLE001 - 偏好文件坏了不该挡住跑批
        merged_prefs = _merge_route_prefs(_prefs_default(), raw_prefs)
        raw_prefs_error = f'{type(exc).__name__}: {exc}'
    else:
        raw_prefs_error = None

    if not _orchestrator_path().is_file():
        return _error(
            'orchestrator 尚未就绪：core/entity/orchestrator.py 不存在（S7 并行开发中）', 503)
    try:
        orchestrator = _import_orchestrator()
    except Exception as exc:
        return _error(f'orchestrator 导入失败：{type(exc).__name__}: {exc}', 503)
    if not (callable(getattr(orchestrator, 'run', None))
            or callable(getattr(orchestrator, 'run_part', None))):
        return _error('orchestrator 未提供 run(ctx) 或 run_part(ctx, part)', 503)

    force = bool(data.get('force'))
    # 默认不补算设计：设计由人工判断后导入数据库，流水线只输出推荐工艺路线。
    ensure_design = bool(data.get('ensure_design', False))
    busy_upload = _uploads_busy()
    if busy_upload:
        return _error(f'{busy_upload}，请等上传测量完成后再启动流水线（避免 OCP 并发）', 409)
    with _JOBS_LOCK:
        active = _active_job_locked()
        if active:
            return _error(f'已有流水线任务在运行（{active["id"]}），请先停止或等待完成', 409)
        job = _new_job(selected_parts, selected_models, selected_routes, force, ensure_design)
        job['route_prefs'] = merged_prefs
        _JOBS[job['id']] = job
        _prune_jobs_locked()

    thread = threading.Thread(
        target=_run_job, args=(job['id'],), name=f'entity-{job["id"]}', daemon=True)
    job['_thread'] = thread
    thread.start()
    cells = sum(
        1
        for _part in selected_parts
        for key in selected_models
        for _route in (selected_routes or (contracts.default_route(key),))
    )
    _append_log(job, f'任务已创建：{len(selected_parts)} 件 × '
                     f'{len(selected_models)} 模型 × '
                     f'{"默认路线（外径形腔 c1 / 其余 pipe）" if not selected_routes else ",".join(selected_routes)}'
                     f' = {cells} 格（force={force}，补算上游设计={ensure_design}）')
    if not ensure_design:
        _append_log(
            job, '设计：不自动补算缺失设计；设计请人工判断后导入数据库，'
                 '本任务只输出推荐工艺路线')
    if merged_prefs.get('parts'):
        selected_impls = [
            f'{part}:{_resolve_impl(merged_prefs, part, ROUTE_PREF_MODEL)}'
            for part in selected_parts
            if _resolve_impl(merged_prefs, part, ROUTE_PREF_MODEL) == '121'
        ]
        if selected_impls:
            _append_log(job, f'[prefs] 121 保形：{", ".join(selected_impls)}')
        else:
            _append_log(job, '[prefs] 无 121 保形覆盖（全部按默认 c1 / pipe）')
    if raw_prefs_error:
        _append_log(job, f'[prefs] 读取磁盘偏好失败，已只用请求体：{raw_prefs_error}')
    # 测量缓存体检：命中缓存约 1.5s/件；没命中的件会在本任务里串行测量（最慢 1–58min/件）。
    # 上传区的并行测量就是为了把这一步提前做掉，这里只如实告知，不擅自改 force。
    missing_measured = [
        item['name'] for item in scanned
        if item['name'] in selected_parts and not item.get('measured')
    ]
    if missing_measured:
        preview = '、'.join(missing_measured[:8])
        if len(missing_measured) > 8:
            preview += f' 等 {len(missing_measured)} 件'
        _append_log(job, f'测量缓存：{len(missing_measured)} 件没有缓存，将在本任务里串行测量（{preview}）；'
                         f'想并行测量请先在上传区上传并点「导入主流程」')
    else:
        _append_log(job, f'测量缓存：{len(selected_parts)} 件全部命中，直接导出（不再测量）')
    with _JOBS_LOCK:
        payload = _job_summary(job)
    return jsonify({
        'success': True,
        'job_id': job['id'],
        'job': payload,
        'missing_measured': missing_measured,
        'cached': len(selected_parts) - len(missing_measured),
    })


def _terminate_all_procs():
    """终止 ``proc_control`` 登记的子进程（A1 未就绪时返回空列表 + 提示）。

    返回 ``(killed_labels, note)``；``note`` 为 None 表示没有可报告的异常。
    """
    try:
        from core.entity import proc_control
    except Exception as exc:  # noqa: BLE001 - 并行开发期该模块可能还不存在
        return [], f'proc_control 不可用（{type(exc).__name__}），未终止子进程'
    try:
        killed = proc_control.terminate_all()
        # A1 的 terminate_all 只处理调用瞬间的快照；worker 可能恰好在快照之后
        # 才登记。再扫一次，避免强制停止后仍留一个测量子进程。
        time.sleep(0.3)
        second = proc_control.terminate_all()
        merged = list(killed or []) + [
            label for label in (second or []) if label not in (killed or [])
        ]
        killed = merged
    except Exception as exc:  # noqa: BLE001
        return [], f'终止子进程失败：{type(exc).__name__}: {exc}'
    if killed is None:
        return [], None
    return [str(label) for label in killed], None


def _notify_stop_hooks():
    """尽力通知 orchestrator 自己的停止钩子（有就调，没有/签名不符就忽略）。"""
    try:
        orchestrator = _import_orchestrator()
        for name in ('request_stop', 'request_cancel', 'cancel', 'stop'):
            hook = getattr(orchestrator, name, None)
            if callable(hook):
                try:
                    hook()
                except TypeError:
                    pass
                except Exception:
                    pass
    except Exception:
        pass


@entity_bp.route('/stop', methods=['POST'])
def entity_stop():
    """两档停止。

    * ``{"mode": "graceful"}``（默认）：置 ``stop_event``，状态 → ``stopping``，
      当前「件 × 模型 × 路线」单元跑完后的安全检查点退出；已完成的单元保留。
    * ``{"mode": "force"}``：再调 ``proc_control.terminate_all()`` 杀掉登记的
      OCP / FreeCAD 子进程，状态立即 → ``stopped``（立刻释放当前任务坑位），
      返回被终止的进程标签 ``killed``。
    """
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _error('请求体必须是 JSON 对象：{"mode": "graceful" | "force"}')
    mode = str(data.get('mode') or 'graceful').strip().lower()
    if mode not in ('graceful', 'force'):
        return _error(f'未知停止模式：{mode}（可选：graceful / force）')

    killed, note = [], None
    with _JOBS_LOCK:
        active = _active_job_locked()
        if not active:
            return _error('当前没有运行中的流水线任务', 409)
        job_id = active['id']
        active['stop_requested'] = True
        active['_stop_event'].set()
        active['stop_mode'] = mode
        if mode == 'force':
            # 立刻让出坑位：旧线程即使还在收尾，也不能让 /run 永久 409
            active['forced'] = True
            active['status'] = 'stopped'
            _append_log(active, '[stop] force：已置停止标志并释放任务坑位，正在终止子进程…')
        else:
            if active['status'] in ('pending', 'running'):
                active['status'] = 'stopping'
            _append_log(active, '[stop] graceful：已置停止标志，'
                                'orchestrator 会在当前单元结束后的安全检查点退出')

    if mode == 'force':
        killed, note = _terminate_all_procs()
        with _JOBS_LOCK:
            active['killed'] = list(killed)
            if note:
                active['kill_note'] = note
            _append_log(active, f'[stop] force：已终止 {len(killed)} 个子进程'
                                + (f'（{", ".join(killed)}）' if killed else '')
                                + (f'；{note}' if note else ''))

    _notify_stop_hooks()

    payload = {
        'success': True,
        'ok': True,
        'mode': mode,
        'job_id': job_id,
        'status': 'stopped' if mode == 'force' else 'stopping',
    }
    if mode == 'force':
        payload['killed'] = list(killed)
        if note:
            payload['note'] = note
        payload['message'] = (f'已强制停止：终止 {len(killed)} 个子进程'
                              f'{"" if killed else "（无可终止的登记进程）"}，任务状态已置 stopped。')
    else:
        payload['message'] = ('已请求停止（graceful）：当前单元跑完后的安全检查点退出，'
                             '已完成的单元保留；需要立即中断几何调用请用 mode=force。')
    return jsonify(payload)


# ---------------------------------------------------------------- 路由：任务查询


@entity_bp.route('/jobs', methods=['GET'])
def entity_jobs():
    limit = request.args.get('limit', type=int) or _MAX_JOBS
    limit = max(1, min(limit, _MAX_JOBS))
    with _JOBS_LOCK:
        summaries = [_job_summary(job) for job in _JOBS.values()]
        active = _active_job_locked()
        active_id = active['id'] if active else None
    summaries.sort(key=lambda j: j.get('created') or '', reverse=True)
    return jsonify({
        'success': True,
        'jobs': summaries[:limit],
        'active_job_id': active_id,
        'count': len(summaries),
    })


@entity_bp.route('/jobs/<job_id>', methods=['GET'])
def entity_job_detail(job_id):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return _error(f'任务不存在：{job_id}', 404)
        try:
            _collect_if_needed_locked(job)
        except Exception as exc:
            job['error'] = job.get('error') or f'读取进度失败：{exc}'
        payload = _job_detail(job)
    return jsonify({'success': True, 'job': payload})


@entity_bp.route('/jobs/<job_id>/files', methods=['GET'])
def entity_job_files(job_id):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return _error(f'任务不存在：{job_id}', 404)
        parts = list(job['parts'])
        out_root = job['out_root']

    root = Path(out_root)
    files = []
    for part in parts:
        base = root / part
        if not base.is_dir():
            continue
        try:
            candidates = sorted(base.rglob('*'))
        except OSError:
            continue
        for file_path in candidates:
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in _DOWNLOAD_SUFFIXES:
                continue
            rel_path = _rel_to(out_root, file_path)
            if not rel_path:
                continue
            try:
                relative_parts = file_path.relative_to(base).parts
            except ValueError:
                relative_parts = ()
            if relative_parts and relative_parts[0] == '00_models':
                kind = 'model'
            elif relative_parts and relative_parts[0] == '10_c1':
                kind = 'c1'
            elif relative_parts and relative_parts[0] == '20_pipe':
                kind = 'pipe'
            elif relative_parts and relative_parts[0] == 'logs':
                kind = 'log'
            else:
                kind = 'report'
            files.append({
                'part': part,
                'name': file_path.name,
                'rel_path': rel_path,
                'size': _size_of(file_path),
                'mtime': _mtime_of(file_path),
                'kind': kind,
            })
            if len(files) >= 2000:
                break
        if len(files) >= 2000:
            break
    return jsonify({'success': True, 'job_id': job_id, 'files': files, 'count': len(files)})


def _friendly_download_name(resolved, out_root):
    """按 `件号_工序_模型__路线.step` 规则生成下载名；不认识的保持原名。"""
    try:
        rel_parts = resolved.relative_to(Path(out_root)).parts
    except (OSError, ValueError):
        return resolved.name
    if not rel_parts:
        return resolved.name
    part = _safe_name(rel_parts[0])
    try:
        contracts = _contracts()
    except Exception:
        return resolved.name
    if len(rel_parts) >= 3:
        sub, filename = rel_parts[-2], rel_parts[-1]
        stem = Path(filename).stem
        if sub in ('10_c1', '20_pipe'):
            route = 'c1' if sub == '10_c1' else 'pipe'
            key = stem.rsplit('__', 1)[0]
            for model in contracts.MODELS:
                if model.key == key:
                    return f'{part}_{model.op_label}_{model.name}__{route}{resolved.suffix}'
        if sub == '00_models':
            for model in contracts.MODELS:
                if model.slug == stem:
                    return f'{part}_{model.op_label}_{model.name}{resolved.suffix}'
    if len(rel_parts) == 2 and rel_parts[-1].startswith('report'):
        return f'{part}_报告{resolved.suffix}'
    return resolved.name


# ---------------------------------------------------------------- 路由：下载


@entity_bp.route('/download', methods=['GET'])
def entity_download():
    rel_path = (request.args.get('path') or request.args.get('file') or '').strip()
    if not rel_path:
        return _error('缺少 path 参数（相对 out_root 的路径）')
    try:
        settings = _settings()
        out_root = Path(settings.OUT_ROOT).resolve()
    except Exception as exc:
        return _error(f'读取输出目录失败：{exc}', 500)

    candidate = Path(rel_path)
    if candidate.is_absolute() or candidate.drive:
        return _error('path 必须是相对 out_root 的路径')
    try:
        resolved = (out_root / candidate).resolve()
        resolved.relative_to(out_root)
    except (OSError, ValueError):
        return _error('path 越界：只允许下载 out_root 内的文件', 403)

    if not resolved.is_file():
        return _error(f'文件不存在：{rel_path}', 404)
    if resolved.suffix.lower() not in _DOWNLOAD_SUFFIXES:
        return _error(f'不允许下载该类型文件：{resolved.suffix or "(无后缀)"}', 403)

    download_name = (request.args.get('name') or '').strip()
    if not download_name:
        download_name = _friendly_download_name(resolved, out_root)
    return send_file(
        str(resolved),
        as_attachment=True,
        download_name=download_name,
        mimetype='application/octet-stream',
    )
