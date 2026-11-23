"""entity API 测试公共夹具。

只起 Flask ``test_client``：不启真实服务、不跑 OCP / FreeCAD、不真实测量。
环境变量必须在导入 ``app`` / ``config`` 之前设置，所以放在本文件顶层。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# backend/tests/entity/conftest.py -> parents[2] == backend
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 许可门禁关闭；开发工具开启（登录 / user.db 走开发模式）。
os.environ['GTAPP_LICENSE_GATE'] = '0'
os.environ['GTAPP_DEV_TOOLS'] = '1'


@pytest.fixture(scope='session')
def app():
    from app import create_app

    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture(scope='session')
def client(app):
    return app.test_client()


@pytest.fixture(scope='session')
def auth_headers(client):
    """真实登录拿 token（字段是 id，不是 username）。"""
    response = client.post(
        '/api/auth/login', json={'id': 'admin', 'password': 'Admin@2027'})
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    token = payload.get('token')
    assert token, payload
    return {'X-Auth-Token': token}


@pytest.fixture
def tmp_out(tmp_path, monkeypatch):
    """把 ``settings.OUT_ROOT`` 指到临时目录，避免污染真实 entity_out。"""
    from core.entity import settings

    import api.entity as entity

    out_root = tmp_path / 'entity_out'
    out_root.mkdir()
    monkeypatch.setattr(settings, 'OUT_ROOT', out_root)
    # 工艺摘要按「测量缓存 mtime/size」缓存，换目录后清掉更直观。
    monkeypatch.setattr(entity, '_SUMMARY_CACHE', {})
    return out_root


@pytest.fixture
def tmp_src(tmp_path, monkeypatch):
    """把 ``settings.SRC_PARTS_DIR`` 指到临时目录（上传测试用）。"""
    from core.entity import settings

    src_dir = tmp_path / 'src_parts'
    src_dir.mkdir()
    monkeypatch.setattr(settings, 'SRC_PARTS_DIR', src_dir)
    return src_dir


@pytest.fixture
def clean_entity_state():
    """清理 ``_JOBS`` / ``_UPLOAD_TASKS``，保证 stop / jobs 用例互不影响。"""
    import api.entity as entity

    def _drain():
        for job in list(entity._JOBS.values()):
            event = job.get('_stop_event')
            if event is not None:
                event.set()
            thread = job.get('_thread')
            if thread is not None and thread.is_alive():
                thread.join(timeout=5)
        with entity._JOBS_LOCK:
            entity._JOBS.clear()

        for task in list(entity._UPLOAD_TASKS.values()):
            task['cancelled'] = True
            future = task.get('_future')
            if future is not None:
                try:
                    future.result(timeout=5)
                except Exception:  # noqa: BLE001 - 用例结束后台任务失败即可
                    pass
        with entity._UPLOAD_TASKS_LOCK:
            entity._UPLOAD_TASKS.clear()

    _drain()
    yield
    _drain()
