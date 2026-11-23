"""一体化流水线（entity）HTTP 接口自动化测试。

覆盖本轮新增/修复的能力：两档停止、上传、路线偏好、工艺摘要无缓存行为、
任务查询。全部走 Flask ``test_client``，不启真实服务、不跑 OCP / FreeCAD、
不真实测量（``prepare_proc.run_prepare`` 与入库函数均被 monkeypatch）。
"""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

MODEL = '成型_外径形腔'
PART = 'H0A3Z2-DT'
RUN_PART = '24TK_1324-1'


def _wait_until(predicate, timeout=5.0, interval=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------- GET /parts


def test_parts_lists_all_builtin_parts(client, auth_headers, tmp_src):
    """内置件扫描：源目录顶层的 STEP 全部列出，且标成 builtin。

    这里用临时源目录（不读真实 gnt管件/），避免开发机上 ``uploads/`` 里
    同名上传件把内置件遮住（上传件优先是预期行为）。
    """
    builtin_names = [
        PART, RUN_PART, '24TK_1324-2', '24TK_1324-31',
        '24TK_1442-1', '24TK_2052-2', 'H0A2Z3-DT',
    ]
    for index, name in enumerate(builtin_names):
        (tmp_src / f'{name}.stp').write_text(
            f'ISO-10303-21;\n/* {index} */\n', encoding='utf-8')

    response = client.get('/api/entity/parts', headers=auth_headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload['success'] is True
    parts = payload['parts']
    builtin = [item for item in parts if item.get('builtin')]
    names = {item['name'] for item in builtin}

    assert len(builtin) >= 6
    assert PART in names
    assert RUN_PART in names

    src = Path(payload['src_dir'])
    expected = {
        path.stem
        for path in src.iterdir()
        if path.is_file()
        and not path.name.startswith(('_', '.'))
        and path.suffix.lower() in ('.stp', '.step')
    }
    assert names == expected

    for item in builtin:
        assert item['source'] == 'builtin'
        assert item['uploaded'] is False
        assert item['file'].lower().endswith(('.stp', '.step'))
    for item in parts:
        # 每个条目都必须带来源标记（老字段保持不动，只新增）。
        assert 'source' in item and 'uploaded' in item


# ---------------------------------------------------------------- 工艺摘要 / 路线计划


def test_flow_without_cache_reports_error_and_no_fake_route(client, auth_headers, tmp_out):
    response = client.get(f'/api/entity/parts/{PART}/flow', headers=auth_headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()

    assert payload['part'] == PART
    summary = payload['process_summary']
    assert isinstance(summary, dict)
    assert summary.get('error'), '无测量缓存时工艺摘要必须给出 error'
    assert '测量缓存' in summary['error']
    assert payload.get('process_summary_error') is None

    plan = payload['route_plan']
    assert isinstance(plan, dict)
    # 回归：没测量时不能给出 0 mm 假推荐。
    assert plan['recommended_label'] == ''
    assert '暂不推荐' in (plan.get('reason') or '')

    # 单件详情的别名端点复用同一份数据。
    alias = client.get(f'/api/entity/parts/{PART}', headers=auth_headers)
    assert alias.status_code == 200
    assert alias.get_json()['part'] == PART


# ---------------------------------------------------------------- 路线偏好


def test_route_pref_defaults_and_roundtrip(client, auth_headers, tmp_out):
    url = f'/api/entity/parts/{PART}/route-pref'
    real_prefs = Path(__file__).resolve().parents[3] / 'entity_out' / '_config' / 'route_prefs.json'
    before = real_prefs.read_bytes() if real_prefs.is_file() else None

    first = client.get(url, headers=auth_headers)
    assert first.status_code == 200, first.get_data(as_text=True)
    body = first.get_json()
    assert body['impl'] == 'c1'
    assert body['direction'] == 'auto'
    assert body['effective']['impl'] == 'c1'
    assert body['effective']['end_treatment'] == 'c1'

    saved = client.post(
        url, json={'model': MODEL, 'impl': '121', 'direction': 'Y'},
        headers=auth_headers)
    assert saved.status_code == 200, saved.get_data(as_text=True)
    saved_body = saved.get_json()
    assert saved_body['impl'] == '121'
    assert saved_body['direction'] == 'Y'
    assert saved_body['effective']['impl'] == '121'
    assert saved_body['effective']['end_treatment'] == 'v1'
    assert isinstance(saved_body['plan'], dict)

    prefs_path = tmp_out / '_config' / 'route_prefs.json'
    assert prefs_path.is_file()
    stored = json.loads(prefs_path.read_text(encoding='utf-8'))
    assert stored['parts'][PART]['models'][MODEL] == {'impl': '121', 'direction': 'Y'}

    again = client.get(url, headers=auth_headers).get_json()
    assert again['impl'] == '121' and again['direction'] == 'Y'
    assert again['effective']['end_treatment'] == 'v1'
    assert again['stored'] == {'impl': '121', 'direction': 'Y'}

    flow = client.get(f'/api/entity/parts/{PART}/flow', headers=auth_headers).get_json()
    effective = flow['route_pref_effective']
    assert effective['impl'] == '121'
    assert effective['direction'] == 'Y'
    assert effective['end_treatment'] == 'v1'
    assert flow['c1_impl'] == '121保形'

    restored = client.post(
        url, json={'model': MODEL, 'impl': 'c1', 'direction': 'auto'},
        headers=auth_headers)
    assert restored.status_code == 200
    restored_body = restored.get_json()
    assert restored_body['impl'] == 'c1'
    assert restored_body['direction'] == 'auto'
    assert restored_body['effective']['end_treatment'] == 'c1'

    # 临时 out_root 生效：真实配置文件没有被本次测试写过。
    after = real_prefs.read_bytes() if real_prefs.is_file() else None
    assert after == before


def test_route_pref_rejects_invalid_values(client, auth_headers, tmp_out):
    url = f'/api/entity/parts/{PART}/route-pref'
    cases = [
        {'model': MODEL, 'impl': 'c2', 'direction': 'auto'},
        {'model': MODEL, 'impl': 'c1', 'direction': 'W'},
        {'model': MODEL},
        {'model': '不存在的模型', 'impl': 'c1', 'direction': 'auto'},
    ]
    for payload in cases:
        response = client.post(url, json=payload, headers=auth_headers)
        assert response.status_code == 400, (payload, response.get_data(as_text=True))
        assert response.get_json()['success'] is False

    # 小写方向可接受，落盘统一成大写。
    lowered = client.post(
        url, json={'model': MODEL, 'impl': '121', 'direction': 'x'},
        headers=auth_headers)
    assert lowered.status_code == 200, lowered.get_data(as_text=True)
    assert lowered.get_json()['direction'] == 'X'
    client.post(url, json={'model': MODEL, 'impl': 'c1', 'direction': 'auto'},
                headers=auth_headers)


# ---------------------------------------------------------------- 两档停止


def test_stop_without_job_409_and_invalid_mode_400(client, auth_headers, clean_entity_state):
    response = client.post('/api/entity/stop', json={'mode': 'graceful'}, headers=auth_headers)
    assert response.status_code == 409
    assert response.get_json()['success'] is False

    response = client.post('/api/entity/stop', json={'mode': 'turbo'}, headers=auth_headers)
    assert response.status_code == 400
    assert 'graceful' in response.get_json()['message']
    assert 'force' in response.get_json()['message']


def test_graceful_then_force_stop(client, auth_headers, tmp_out, clean_entity_state, monkeypatch):
    import api.entity as entity
    from core.entity import orchestrator, proc_control

    entered = threading.Event()
    release = threading.Event()
    observed = {}
    terminate_calls = []

    def fake_run(ctx):
        observed['job_id'] = ctx.options.get('job_id')
        observed['stop_event'] = ctx.options.get('stop_event')
        entered.set()
        # 模拟「当前单元还在收尾」：不理会 stop_event，等测试放行。
        release.wait(timeout=20)
        return {}

    def fake_terminate_all():
        terminate_calls.append(1)
        return ['ocp:test'] if len(terminate_calls) == 1 else []

    monkeypatch.setattr(orchestrator, 'run', fake_run)
    monkeypatch.setattr(proc_control, 'terminate_all', fake_terminate_all)

    run_payload = {
        'parts': [RUN_PART],
        'models': [MODEL],
        'routes': ['c1'],
        'ensure_design': False,
    }
    new_id = None
    response = client.post('/api/entity/run', json=run_payload, headers=auth_headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    job_id = response.get_json()['job_id']
    try:
        assert entered.wait(5), 'orchestrator.run 未被调用'
        assert observed['job_id'] == job_id
        assert _wait_until(lambda: entity._JOBS[job_id]['status'] == 'running')

        graceful = client.post(
            '/api/entity/stop', json={'mode': 'graceful'}, headers=auth_headers)
        assert graceful.status_code == 200, graceful.get_data(as_text=True)
        body = graceful.get_json()
        assert body['ok'] is True
        assert body['mode'] == 'graceful'
        assert body['job_id'] == job_id
        assert body['status'] == 'stopping'
        assert _wait_until(lambda: entity._JOBS[job_id]['status'] == 'stopping')
        assert entity._JOBS[job_id]['_stop_event'].is_set()
        assert entity._JOBS[job_id]['stop_mode'] == 'graceful'
        # graceful 保留坑位：新任务仍被 409 拒绝。
        blocked = client.post('/api/entity/run', json=run_payload, headers=auth_headers)
        assert blocked.status_code == 409, blocked.get_data(as_text=True)
        assert terminate_calls == []

        forced = client.post(
            '/api/entity/stop', json={'mode': 'force'}, headers=auth_headers)
        assert forced.status_code == 200, forced.get_data(as_text=True)
        forced_body = forced.get_json()
        assert forced_body['mode'] == 'force'
        assert forced_body['status'] == 'stopped'
        assert forced_body['killed'] == ['ocp:test']
        assert len(terminate_calls) == 2
        assert _wait_until(lambda: entity._JOBS[job_id]['status'] == 'stopped')

        # force 立即释放坑位：新任务可以启动。
        rerun = client.post('/api/entity/run', json=run_payload, headers=auth_headers)
        assert rerun.status_code == 200, rerun.get_data(as_text=True)
        new_id = rerun.get_json()['job_id']
        assert new_id != job_id
    finally:
        release.set()
        for jid in (job_id, new_id):
            job = entity._JOBS.get(jid) if jid else None
            if job is not None:
                job['_stop_event'].set()
                thread = job.get('_thread')
                if thread is not None:
                    thread.join(timeout=5)


# ---------------------------------------------------------------- 只输出推荐路线


def test_run_defaults_to_manual_design(
        client, auth_headers, tmp_out, clean_entity_state, monkeypatch):
    """``/run`` 默认不再自动补算设计，只跑推荐路线。"""
    import api.entity as entity
    from core.entity import design_gen, orchestrator

    design_calls = []

    def fake_create_default_design(*args, **kwargs):
        design_calls.append((args, kwargs))
        return {'state': 'created'}

    monkeypatch.setattr(design_gen, 'create_default_design', fake_create_default_design)
    monkeypatch.setattr(design_gen, 'existing_design', lambda part: None)
    monkeypatch.setattr(orchestrator, 'run', lambda ctx: {})

    response = client.post(
        '/api/entity/run',
        json={'parts': [RUN_PART], 'models': [MODEL], 'routes': ['c1']},
        headers=auth_headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    job = body['job']
    assert job['ensure_design'] is False
    assert job['design_mode'] == 'manual'
    assert '不自动补算' in job['design_note']

    job_id = body['job_id']
    assert _wait_until(
        lambda: entity._JOBS[job_id]['status'] in ('finished', 'failed', 'stopped'),
        timeout=10)
    assert design_calls == [], '默认 ensure_design=False 时不应调用 create_default_design'
    logs = '\n'.join(entity._JOBS[job_id]['logs'])
    assert '不自动补算' in logs

    # 显式 ensure_design=true 仍走补算（保留手工/兼容入口）。
    monkeypatch.setattr(orchestrator, 'run', lambda ctx: {})
    forced = client.post(
        '/api/entity/run',
        json={'parts': [RUN_PART], 'models': [MODEL], 'routes': ['c1'],
              'ensure_design': True},
        headers=auth_headers)
    assert forced.status_code == 200, forced.get_data(as_text=True)
    forced_job_id = forced.get_json()['job_id']
    assert _wait_until(
        lambda: entity._JOBS[forced_job_id]['status'] in ('finished', 'failed', 'stopped'),
        timeout=10)
    assert design_calls, '显式 ensure_design=true 应调用 create_default_design'


def test_process_summary_without_design_marks_missing(tmp_out, monkeypatch):
    """没有登记设计时：道次留空并给出「人工导入」提示。"""
    import api.entity as entity
    from core.entity import process_summary

    monkeypatch.setattr(
        process_summary, 'build_process_summary',
        lambda **kwargs: {
            'drawing': {'normal': {'label': '普通抽', 'passes': []},
                        'note': '旧提示'},
            'route': {'recommended': '仅TP抽'},
        })
    measured = tmp_out / '_cache' / 'NoDesign_measured.json'
    measured.parent.mkdir(parents=True, exist_ok=True)
    measured.write_text('{}', encoding='utf-8')
    entity._SUMMARY_CACHE.clear()

    summary, error = entity._process_summary_for(
        'NoDesign', measured, None, {'recommended_label': '仅TP抽'}, tmp_out)
    assert error is None
    assert summary['design_missing'] is True
    assert summary['drawing']['missing_design'] is True
    assert '未导入工艺设计' in summary['drawing']['note']
    assert '只给推荐路线' in summary['drawing']['note']


# ---------------------------------------------------------------- 上传


def test_upload_rejects_non_step_file(client, auth_headers, tmp_src, clean_entity_state):
    response = client.post(
        '/api/entity/upload',
        data={'file': (io.BytesIO(b'not a step file'), 'notes.txt')},
        headers=auth_headers,
        content_type='multipart/form-data',
    )
    assert response.status_code == 400
    assert '只支持' in response.get_json()['message']

    missing = client.post(
        '/api/entity/upload', data={}, headers=auth_headers,
        content_type='multipart/form-data')
    assert missing.status_code == 400
    assert '缺少上传文件' in missing.get_json()['message']
    assert not (tmp_src / 'uploads').exists()


def _fake_prepare(calls, *, with_progress=True):
    """构造 ``prepare_proc.run_prepare`` 的假实现：写测量缓存，不导出模型。"""

    def fake_run_prepare(*, part, step, end_treatment, out_root, models=None,
                         force=False, measure_only=False, log=None,
                         stop_event=None, timeout=7200.0, command=None):
        calls.setdefault('runs', []).append({
            'part': part,
            'step': Path(step),
            'end_treatment': end_treatment,
            'out_root': Path(out_root),
            'measure_only': measure_only,
            'force': force,
        })
        measured = Path(out_root) / '_cache' / f'{part}_measured.json'
        measured.parent.mkdir(parents=True, exist_ok=True)
        measured.write_text(
            json.dumps({'sections_full': [], 'center_line_length': 12.5}),
            encoding='utf-8')
        if with_progress and callable(log):
            log(f'[{part}] 截面测量 42%')
        return {
            'models': {},
            'measured_path': str(measured),
            'seconds': 0.0,
            'cached': False,
            'measure_only': True,
        }

    return fake_run_prepare


def test_upload_uppercase_step_measures_in_background(
        client, auth_headers, tmp_src, tmp_out, clean_entity_state, monkeypatch):
    import api.entity as entity
    from core.entity import prepare_proc

    # 缺省并行度按核数自动分配（本机可能是 1），这里显式钉成 2 以便断言线程池口径。
    monkeypatch.setenv('GTAPP_UPLOAD_PARALLEL', '2')

    calls = {}

    monkeypatch.setattr(prepare_proc, 'run_prepare', _fake_prepare(calls))

    content = b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n'
    response = client.post(
        '/api/entity/upload',
        data={'file': (io.BytesIO(content), 'DemoPart.STP')},
        headers=auth_headers,
        content_type='multipart/form-data',
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body['success'] is True
    assert body['ok'] is True
    assert body['part'] == 'DemoPart'
    assert body['file'] == 'DemoPart.stp'
    assert body['size'] == len(content)
    assert body['status'] in ('queued', 'measuring')
    assert body['parallel'] == 2
    task_id = body['task_id']
    assert [item['task_id'] for item in body['tasks']] == [task_id]

    task = entity._UPLOAD_TASKS[task_id]
    task['_future'].result(timeout=15)
    assert task['status'] == 'done', task.get('error')
    # 2026-11-11 起上传只测量、不写分析数据库：入库由「导入主流程」负责。
    assert task['tube_id'] is None
    assert '导入主流程' in (task['message'] or '')
    assert task['warning'] is None
    assert task['error'] is None

    target = tmp_src / 'uploads' / 'DemoPart.stp'
    assert target.is_file()
    assert target.read_bytes() == content
    run = calls['runs'][0]
    assert run['step'] == target
    assert run['part'] == 'DemoPart'
    assert run['end_treatment'] == 'c1'
    assert run['measure_only'] is True
    assert run['out_root'] == tmp_out

    listing = client.get('/api/entity/uploads', headers=auth_headers)
    assert listing.status_code == 200
    listing_body = listing.get_json()
    assert listing_body['success'] is True
    assert listing_body['uploads_dir'] == str(tmp_src / 'uploads')
    assert listing_body['parallel'] == 2
    assert 'active' in listing_body
    assert task_id in {item['task_id'] for item in listing_body['tasks']}
    assert listing_body['count'] >= 1

    detail = client.get(f'/api/entity/uploads/{task_id}', headers=auth_headers)
    assert detail.status_code == 200
    detail_body = detail.get_json()
    assert detail_body['status'] == 'done'
    assert detail_body['part'] == 'DemoPart'
    assert detail_body['tube_id'] is None
    assert detail_body['cache_path'] == str(tmp_out / '_cache' / 'DemoPart_measured.json')
    assert isinstance(detail_body['logs'], list)
    assert detail_body['task']['task_id'] == task_id

    parts = client.get('/api/entity/parts', headers=auth_headers).get_json()
    uploaded = next(item for item in parts['parts'] if item['name'] == 'DemoPart')
    assert uploaded['uploaded'] is True
    assert uploaded['builtin'] is False
    assert uploaded['source'] == 'upload'
    assert uploaded['file'] == 'DemoPart.stp'
    assert parts['src_dir'] == str(tmp_src)
    assert parts['out_root'] == str(tmp_out)

    entity._UPLOAD_TASKS.pop(task_id, None)


def test_upload_multiple_files_creates_parallel_tasks(
        client, auth_headers, tmp_src, tmp_out, clean_entity_state, monkeypatch):
    import api.entity as entity
    from core.entity import prepare_proc

    monkeypatch.setenv('GTAPP_UPLOAD_PARALLEL', '2')

    calls = {}

    monkeypatch.setattr(prepare_proc, 'run_prepare', _fake_prepare(calls))

    content = b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n'
    response = client.post(
        '/api/entity/upload',
        data={'files': [
            (io.BytesIO(content), 'MultiA.stp'),
            (io.BytesIO(content), 'MultiB.step'),
        ]},
        headers=auth_headers,
        content_type='multipart/form-data',
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body['success'] is True
    assert body['count'] == 2
    assert len(body['tasks']) == 2
    assert {item['part'] for item in body['tasks']} == {'MultiA', 'MultiB'}
    assert body['parallel'] == 2
    assert body['message'].startswith('已接收 2 个文件')
    task_ids = [item['task_id'] for item in body['tasks']]

    for task_id in task_ids:
        task = entity._UPLOAD_TASKS[task_id]
        task['_future'].result(timeout=15)
        assert task['status'] == 'done', task.get('error')

    assert sorted(run['part'] for run in calls['runs']) == ['MultiA', 'MultiB']
    assert all(run['measure_only'] is True for run in calls['runs'])
    assert (tmp_src / 'uploads' / 'MultiA.stp').is_file()
    assert (tmp_src / 'uploads' / 'MultiB.step').is_file()

    parts = client.get('/api/entity/parts', headers=auth_headers).get_json()
    names = {item['name'] for item in parts['parts'] if item.get('uploaded')}
    assert {'MultiA', 'MultiB'} <= names

    # 同名、同内容再传一次：覆盖同一件名（不再产生 _2），并直接复用已有测量缓存。
    again = client.post(
        '/api/entity/upload',
        data={'files': [(io.BytesIO(content), 'MultiA.stp')]},
        headers=auth_headers,
        content_type='multipart/form-data',
    )
    assert again.status_code == 200
    again_body = again.get_json()
    assert again_body['part'] == 'MultiA'
    assert again_body['unchanged'] is True
    again_task_id = again_body['task_id']
    entity._UPLOAD_TASKS[again_task_id]['_future'].result(timeout=15)
    again_task = entity._UPLOAD_TASKS[again_task_id]
    assert again_task['status'] == 'done', again_task.get('error')
    assert again_task['unchanged'] is True
    assert '复用' in (again_task['message'] or '')
    assert not (tmp_src / 'uploads' / 'MultiA_2.stp').exists()
    assert len(calls['runs']) == 2  # 未内容变化 → 不重测

    for task_id in task_ids + [again_task_id]:
        entity._UPLOAD_TASKS.pop(task_id, None)


def test_upload_cancel_stops_measuring_task(
        client, auth_headers, tmp_src, tmp_out, clean_entity_state, monkeypatch):
    import time as _time

    import api.entity as entity
    from core.entity import prepare_proc

    monkeypatch.setenv('GTAPP_UPLOAD_PARALLEL', '1')
    entered = {}

    def blocking_prepare(*, part, step, end_treatment, out_root, models=None,
                         force=False, measure_only=False, log=None,
                         stop_event=None, timeout=7200.0, command=None):
        entered['event'] = stop_event
        assert stop_event is not None, '取消必须把 stop_event 交给测量子进程'
        if callable(log):
            log(f'[{part}] 截面测量 30%')
        for _ in range(600):
            if stop_event.is_set():
                raise prepare_proc.PrepareCancelled('测试取消')
            _time.sleep(0.01)
        raise AssertionError('取消信号没有传到测量子进程')

    monkeypatch.setattr(prepare_proc, 'run_prepare', blocking_prepare)

    content = b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n'
    response = client.post(
        '/api/entity/upload',
        data={'file': (io.BytesIO(content), 'CancelMe.stp')},
        headers=auth_headers,
        content_type='multipart/form-data',
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    task_id = response.get_json()['task_id']
    task = entity._UPLOAD_TASKS[task_id]

    deadline = _time.time() + 10
    while not entered.get('event') and _time.time() < deadline:
        _time.sleep(0.02)
    assert entered.get('event') is not None, '测量线程没有启动'

    cancel = client.post(f'/api/entity/uploads/{task_id}/cancel', headers=auth_headers)
    assert cancel.status_code == 200, cancel.get_data(as_text=True)
    assert cancel.get_json()['success'] is True
    assert cancel.get_json()['status'] == 'cancelling'
    assert entered['event'].is_set() is True

    task['_future'].result(timeout=15)
    assert task['status'] == 'cancelled', task.get('error')
    assert task['error'] is None
    assert '已取消' in (task['message'] or '')

    # 已经结束的任务再取消 → 409，避免误以为撤回了
    again = client.post(f'/api/entity/uploads/{task_id}/cancel', headers=auth_headers)
    assert again.status_code == 409
    entity._UPLOAD_TASKS.pop(task_id, None)


def test_unknown_upload_task_404(client, auth_headers, clean_entity_state):
    response = client.get('/api/entity/uploads/up_doesnotexist', headers=auth_headers)
    assert response.status_code == 404
    assert '上传任务不存在' in response.get_json()['message']


# ---------------------------------------------------------------- 任务查询


def test_jobs_list_and_unknown_job_404(client, auth_headers, clean_entity_state):
    response = client.get('/api/entity/jobs', headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body['success'] is True
    assert isinstance(body['jobs'], list)
    assert body['count'] == len(body['jobs'])
    assert 'active_job_id' in body

    missing = 'deadbeef1234'
    detail = client.get(f'/api/entity/jobs/{missing}', headers=auth_headers)
    assert detail.status_code == 404
    assert '任务不存在' in detail.get_json()['message']

    files = client.get(f'/api/entity/jobs/{missing}/files', headers=auth_headers)
    assert files.status_code == 404
    assert '任务不存在' in files.get_json()['message']


# ---------------------------------------------------------------- 上传并行度


def test_upload_parallel_defaults_to_core_budget(monkeypatch):
    """缺省并行度 = 物理核数 // 单件测量进程数（夹在 1..8）；环境变量优先。"""
    import api.entity as entity
    from core.pp.tube import section_pool

    monkeypatch.delenv('GTAPP_UPLOAD_PARALLEL', raising=False)
    cores = max(1, int(section_pool.physical_cores()))
    per_measure = min(8, max(1, int(section_pool.measurement_workers())))
    expected = max(1, min(8, cores // per_measure))

    assert entity._auto_upload_parallel() == expected
    assert entity._upload_parallel() == expected
    assert 1 <= entity._upload_parallel() <= 8

    monkeypatch.setenv('GTAPP_UPLOAD_PARALLEL', '3')
    assert entity._upload_parallel() == 3

    monkeypatch.setenv('GTAPP_UPLOAD_PARALLEL', '99')
    assert entity._upload_parallel() == 8

    for invalid in ('0', '-1', 'abc', ''):
        monkeypatch.setenv('GTAPP_UPLOAD_PARALLEL', invalid)
        assert entity._upload_parallel() == expected


# ------------------------------------------------- 导入主流程（入库幂等）/ 同名覆盖


def _write_measured_cache(out_root, part, *, length=5.0, sections=6):
    """造一份最小可用的测量缓存（``sections_full`` + 中心线长度）。"""
    cache = Path(out_root) / '_cache' / f'{part}_measured.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    rows = [{
        'cumulative_length': float(index),
        'perimeter': 200.0 + index,
        'wall_thickness': 2.0,
        'position': [0.0, 0.0, float(index)],
        'x_length': 60.0,
        'y_length': 60.0,
        'measurement_status': 'measured',
    } for index in range(sections)]
    cache.write_text(json.dumps({
        'sections_full': rows,
        'center_line_length': length,
        'algorithm': 'A',
        'volume': 1234.5,
    }), encoding='utf-8')
    return cache


def _point_db_at_tmp(monkeypatch, tmp_path):
    import config

    db_path = tmp_path / 'tube_analysis.db'
    monkeypatch.setattr(config, 'PP_TUBE_DB', str(db_path))
    monkeypatch.setattr(config, 'PP_TUBE_SAVED_DIR', str(tmp_path / 'saved_sources'))
    return db_path


def test_import_writes_db_once_and_restores_after_delete(
        client, auth_headers, tmp_src, tmp_out, clean_entity_state, monkeypatch, tmp_path):
    """导入=写分析数据库；重复导入不重复；删了记录再导入能补回一份。"""
    from core.pp import thickness_db

    db_path = _point_db_at_tmp(monkeypatch, tmp_path)
    uploads = tmp_src / 'uploads'
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / 'DupImport.stp').write_text('ISO-10303-21;\n', encoding='utf-8')
    _write_measured_cache(tmp_out, 'DupImport')

    first = client.post('/api/entity/import', json={'parts': ['DupImport']},
                        headers=auth_headers)
    assert first.status_code == 200, first.get_data(as_text=True)
    body = first.get_json()
    assert body['success'] is True
    assert [item['db_status'] for item in body['imported']] == ['imported']
    tube_id = body['imported'][0]['tube_id']
    assert tube_id > 0
    assert body['queue']['count'] == 1
    rows = thickness_db.find_tubes_by_display_name('DupImport', db_path=str(db_path))
    assert [row['id'] for row in rows] == [tube_id]

    second = client.post('/api/entity/import', json={'parts': ['DupImport']},
                         headers=auth_headers)
    second_body = second.get_json()
    assert [item['db_status'] for item in second_body['imported']] == ['exists']
    assert second_body['imported'][0]['tube_id'] == tube_id
    assert '数据库已存在' in second_body['message']
    assert len(thickness_db.find_tubes_by_display_name('DupImport', db_path=str(db_path))) == 1

    assert thickness_db.delete_tube_data(tube_id, db_path=str(db_path)) is True
    third = client.post('/api/entity/import', json={'parts': ['DupImport']},
                        headers=auth_headers)
    third_body = third.get_json()
    assert [item['db_status'] for item in third_body['imported']] == ['imported']
    new_id = third_body['imported'][0]['tube_id']
    assert new_id != tube_id
    rows = thickness_db.find_tubes_by_display_name('DupImport', db_path=str(db_path))
    assert len(rows) == 1 and rows[0]['id'] == new_id


def test_import_cleans_duplicate_rows_keeping_latest(
        client, auth_headers, tmp_src, tmp_out, clean_entity_state, monkeypatch, tmp_path):
    """库里已有同名多条时，导入只保留最新一条，并在响应里报出来。"""
    from core.pp import thickness_db

    db_path = _point_db_at_tmp(monkeypatch, tmp_path)
    uploads = tmp_src / 'uploads'
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / 'DupClean.stp').write_text('ISO-10303-21;\n', encoding='utf-8')
    _write_measured_cache(tmp_out, 'DupClean')

    payload = {'sections_full': [], 'centerline_length': 3.0, 'algorithm': 'A'}
    first_id, err1 = thickness_db.save_full_analysis(
        'a.stp', 'DupClean', 'a.stp', 'stp', 'h1', payload, db_path=str(db_path))
    second_id, err2 = thickness_db.save_full_analysis(
        'b.stp', 'DupClean', 'b.stp', 'stp', 'h2', payload, db_path=str(db_path))
    assert err1 is None and err2 is None and second_id > first_id

    body = client.post('/api/entity/import', json={'parts': ['DupClean']},
                       headers=auth_headers).get_json()
    assert [item['db_status'] for item in body['imported']] == ['exists']
    assert body['imported'][0]['tube_id'] == second_id
    assert body['duplicates_removed'] == [first_id]
    rows = thickness_db.find_tubes_by_display_name('DupClean', db_path=str(db_path))
    assert [row['id'] for row in rows] == [second_id]


def test_upload_overwrites_same_name_when_content_changes(
        client, auth_headers, tmp_src, tmp_out, clean_entity_state, monkeypatch):
    """同名但内容不同 → 覆盖同一件名、清缓存后重测，不产生 _2。"""
    import api.entity as entity
    from core.entity import prepare_proc

    monkeypatch.setenv('GTAPP_UPLOAD_PARALLEL', '1')
    calls = {}
    monkeypatch.setattr(prepare_proc, 'run_prepare', _fake_prepare(calls))

    first_content = b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nA;\nENDSEC;\nEND-ISO-10303-21;\n'
    second_content = b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nB;\nENDSEC;\nEND-ISO-10303-21;\n'

    first = client.post(
        '/api/entity/upload',
        data={'file': (io.BytesIO(first_content), 'Over.stp')},
        headers=auth_headers,
        content_type='multipart/form-data',
    ).get_json()
    entity._UPLOAD_TASKS[first['task_id']]['_future'].result(timeout=15)
    cache = tmp_out / '_cache' / 'Over_measured.json'
    assert cache.is_file()

    second = client.post(
        '/api/entity/upload',
        data={'file': (io.BytesIO(second_content), 'Over.stp')},
        headers=auth_headers,
        content_type='multipart/form-data',
    ).get_json()
    assert second['part'] == 'Over'
    assert second['unchanged'] is False
    assert second['overwritten'] is True
    assert second['cache_cleared']
    task = entity._UPLOAD_TASKS[second['task_id']]
    task['_future'].result(timeout=15)
    assert task['status'] == 'done', task.get('error')
    assert len(calls['runs']) == 2

    target = tmp_src / 'uploads' / 'Over.stp'
    assert target.read_bytes() == second_content
    assert not (tmp_src / 'uploads' / 'Over_2.stp').exists()
    for task_id in (first['task_id'], second['task_id']):
        entity._UPLOAD_TASKS.pop(task_id, None)


def test_upload_part_shadows_builtin_part(client, auth_headers, tmp_src, clean_entity_state):
    """上传件与内置件同名时，件表只留上传件（上传优先）。"""
    uploads = tmp_src / 'uploads'
    uploads.mkdir(parents=True, exist_ok=True)
    (tmp_src / 'Shadow.stp').write_text('ISO-10303-21;\n', encoding='utf-8')
    (uploads / 'Shadow.stp').write_text('ISO-10303-21;\n', encoding='utf-8')

    payload = client.get('/api/entity/parts', headers=auth_headers).get_json()
    matches = [item for item in payload['parts'] if item['name'] == 'Shadow']
    assert len(matches) == 1
    assert matches[0]['source'] == 'upload'
    assert matches[0]['uploaded'] is True
    assert matches[0]['builtin'] is False


def test_uploads_clear_only_removes_finished(client, auth_headers, clean_entity_state):
    """上传任务表只在内存里；clear 清掉已结束的，默认保留进行中的。"""
    import api.entity as entity

    with entity._UPLOAD_TASKS_LOCK:
        entity._UPLOAD_TASKS['up_done'] = {
            'task_id': 'up_done', 'part': 'Alpha', 'status': 'done',
            'created_at': '2026-01-01T00:00:00', 'updated_at': '2026-01-01T00:00:00',
        }
        entity._UPLOAD_TASKS['up_live'] = {
            'task_id': 'up_live', 'part': 'Beta', 'status': 'measuring',
            'created_at': '2026-01-01T00:00:01', 'updated_at': '2026-01-01T00:00:01',
        }
    try:
        payload = client.post('/api/entity/uploads/clear', json={}, headers=auth_headers).get_json()
        assert payload['success'] is True
        assert payload['removed'] == 1
        assert payload['active'] == 1
        assert 'up_done' not in entity._UPLOAD_TASKS
        assert 'up_live' in entity._UPLOAD_TASKS

        again = client.post(
            '/api/entity/uploads/clear', json={'keep_active': False}, headers=auth_headers).get_json()
        assert again['removed'] == 1
        assert not entity._UPLOAD_TASKS
    finally:
        with entity._UPLOAD_TASKS_LOCK:
            entity._UPLOAD_TASKS.pop('up_done', None)
            entity._UPLOAD_TASKS.pop('up_live', None)

