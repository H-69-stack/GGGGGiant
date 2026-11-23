"""机器码 / 许可证展示相关回归测试（不启真实服务、不跑测量）。

覆盖：
  1. ``fingerprint_matches`` 同时支持带分隔符机器码与完整指纹两种绑定写法。
  2. 机器码不匹配时 reason 给出可操作的「重新申请/签发」提示，且不泄露完整指纹。
  3. ``/api/auth/machine-code`` 与 ``/api/auth/license-status`` 返回
     fingerprint_stable / fingerprint_source / machine_code_hint 等扩展字段；
     新指纹模块的 ``fingerprint_details()`` 未落地时也能返回安全默认值。
"""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# 许可证门禁关闭，避免依赖现场 license.dat。
os.environ.setdefault('GTAPP_LICENSE_GATE', '0')

# 固定一份 64 位指纹与它的机器码展示形式，测试不触碰真实硬件采集。
FINGERPRINT = '6ecf1d9f4ab33313f00a' + 'b' * 44
MACHINE_CODE = '6ECF1-D9F4A-B3331-3F00A'


# ─── fingerprint_matches ────────────────────────────────────────────────────

def test_fingerprint_matches_full_fingerprint_and_machine_code():
    from core.common.licensing.manager import fingerprint_matches

    # 完整指纹：大小写不敏感
    assert fingerprint_matches(FINGERPRINT, FINGERPRINT)
    assert fingerprint_matches(FINGERPRINT.upper(), FINGERPRINT)
    # 机器码（带分隔符）：作为完整指纹前缀匹配
    assert fingerprint_matches(MACHINE_CODE, FINGERPRINT)
    assert fingerprint_matches(MACHINE_CODE.lower(), FINGERPRINT)
    # 分隔符宽松一点（空格分隔）也应匹配
    assert fingerprint_matches('6ECF1 D9F4A B3331 3F00A', FINGERPRINT)
    # 不绑定
    assert fingerprint_matches('ANY', FINGERPRINT)


def test_fingerprint_matches_rejects_wrong_or_empty_values():
    from core.common.licensing.manager import fingerprint_matches

    assert not fingerprint_matches('AAAAA-BBBBB-CCCCC-DDDDD', FINGERPRINT)
    assert not fingerprint_matches('', FINGERPRINT)
    assert not fingerprint_matches(None, FINGERPRINT)
    assert not fingerprint_matches(MACHINE_CODE, '')
    assert not fingerprint_matches(None, None)


# ─── 机器码不匹配时的可操作 reason ──────────────────────────────────────────

def _fake_payload(machine_fingerprint: str) -> dict:
    return {
        'customer': '测试客户',
        'license_id': 'L-TEST-1',
        'issued_at': '2026-01-01',
        'expires_at': '2030-12-31',
        'machine_fingerprint': machine_fingerprint,
        'allowed_workroles': ['ALL'],
        'max_users': 0,
        'edition': 'standard',
        'note': '',
        'schema_version': 1,
    }


def _manager_with_license(monkeypatch, tmp_path, machine_fingerprint: str):
    """构造一台「指纹固定」的 LicenseManager，并伪造一份已验签的许可证。"""
    from core.common.licensing import fingerprint as fp_mod
    from core.common.licensing import manager as manager_mod

    license_path = tmp_path / 'license.dat'
    license_path.write_text('fake-license-content', encoding='utf-8')

    monkeypatch.setattr(manager_mod._pubkey, 'is_configured', lambda: True)
    monkeypatch.setattr(manager_mod, '_load_public_key', lambda: object())
    monkeypatch.setattr(
        manager_mod,
        'deserialize_license',
        lambda text: (_fake_payload(machine_fingerprint), 'fake-signature'),
    )
    monkeypatch.setattr(manager_mod, 'verify_payload', lambda *args, **kwargs: True)
    monkeypatch.setattr(manager_mod, 'get_machine_fingerprint', lambda: FINGERPRINT)
    # 避免测试触碰真实硬件采集 / 写锚点文件。
    monkeypatch.setattr(
        fp_mod,
        'fingerprint_details',
        lambda: {'source': 'machine-guid', 'stable': True, 'anchored': True},
        raising=False,
    )
    monkeypatch.setattr('config.BIND_SERVER_MACHINE', True)
    return manager_mod.LicenseManager(str(license_path))


def test_machine_mismatch_reason_asks_for_reissue(monkeypatch, tmp_path):
    manager = _manager_with_license(monkeypatch, tmp_path, 'AAAAA-BBBBB-CCCCC-DDDDD')
    status = manager.get_status(force_reload=True)

    assert status.valid is False
    assert status.machine_match is False
    assert '机器码' in status.reason
    # 必须给出可操作的下一步：重新申请/签发
    assert '重新申请/签发' in status.reason
    # 状态里带机器码扩展字段，但 reason 不泄露完整指纹
    public = status.to_public_dict()
    assert public['machine_code'] == MACHINE_CODE
    assert FINGERPRINT not in status.reason


def test_matching_machine_code_license_is_valid(monkeypatch, tmp_path):
    manager = _manager_with_license(monkeypatch, tmp_path, MACHINE_CODE)
    status = manager.get_status(force_reload=True)

    assert status.valid is True
    assert status.machine_match is True
    assert status.machine_code == MACHINE_CODE
    # 扩展字段透传：与 fingerprint_details() 返回的稳定性/来源一致
    assert status.fingerprint_stable is True
    assert status.fingerprint_source == 'machine-guid'
    assert status.fingerprint_anchored is True


# ─── /api/auth/machine-code 与 /api/auth/license-status ─────────────────────

@pytest.fixture
def auth_client(monkeypatch):
    """最小 Flask 应用 + auth 蓝图；指纹固定，不触发真实硬件采集。"""
    from flask import Flask

    import api.auth as auth_mod

    monkeypatch.setattr(auth_mod, 'get_machine_fingerprint', lambda: FINGERPRINT)
    app = Flask(__name__)
    app.register_blueprint(auth_mod.auth_bp, url_prefix='/api/auth')
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_machine_code_endpoint_reports_stable_fingerprint(monkeypatch, auth_client):
    from core.common.licensing import fingerprint as fp_mod

    monkeypatch.setattr(
        fp_mod,
        'fingerprint_details',
        lambda: {
            'source': 'machine-guid',
            'stable': True,
            'anchored': True,
            'components': ['machine-guid:test-guid'],
        },
        raising=False,
    )

    res = auth_client.get('/api/auth/machine-code')
    body = res.get_json()

    assert res.status_code == 200
    assert body['success'] is True
    assert body['machine_code'] == MACHINE_CODE
    assert body['fingerprint'] == FINGERPRINT
    assert body['fingerprint_stable'] is True
    assert body['fingerprint_source'] == 'machine-guid'
    assert body['fingerprint_anchored'] is True
    assert '锚定' in body['machine_code_hint']


def test_machine_code_endpoint_reports_unstable_fingerprint(monkeypatch, auth_client):
    from core.common.licensing import fingerprint as fp_mod

    monkeypatch.setattr(
        fp_mod,
        'fingerprint_details',
        lambda: {'source': 'hostname', 'stable': False, 'anchored': False},
        raising=False,
    )

    body = auth_client.get('/api/auth/machine-code').get_json()

    assert body['fingerprint_stable'] is False
    assert body['fingerprint_source'] == 'hostname'
    assert body['fingerprint_anchored'] is False
    assert '机器码可能变化' in body['machine_code_hint']


def test_machine_code_endpoint_defaults_when_details_missing(monkeypatch, auth_client):
    """M1（fingerprint_details）未落地时，接口仍返回安全默认值。"""
    from core.common.licensing import fingerprint as fp_mod

    monkeypatch.delattr(fp_mod, 'fingerprint_details', raising=False)

    body = auth_client.get('/api/auth/machine-code').get_json()

    assert body['machine_code'] == MACHINE_CODE
    assert body['fingerprint_stable'] is None
    assert body['fingerprint_source'] == 'unknown'
    assert body['fingerprint_anchored'] is False
    assert body['machine_code_hint']


def test_license_status_endpoint_includes_machine_code_fields(monkeypatch, auth_client):
    from core.common.licensing import fingerprint as fp_mod

    monkeypatch.setattr(
        fp_mod,
        'fingerprint_details',
        lambda: {'source': 'machine-guid', 'stable': True, 'anchored': True},
        raising=False,
    )

    res = auth_client.get('/api/auth/license-status')
    body = res.get_json()

    assert res.status_code == 200
    assert body['success'] is True
    assert body['machine_code'] == MACHINE_CODE
    assert body['fingerprint_stable'] is True
    assert body['fingerprint_source'] == 'machine-guid'
    assert body['fingerprint_anchored'] is True
    assert body['machine_code_hint']
    # license-status 是公开接口，不下发完整指纹
    assert 'fingerprint' not in body
    assert isinstance(body.get('license'), dict)
