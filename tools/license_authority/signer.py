"""发证核心：加载私钥，按输入参数生成签名许可证文件。

仅供开发 / 维护侧使用。依赖 keys/private_key.pem（绝密）。
"""
from __future__ import annotations

import datetime
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def _tool_base_dir() -> str:
    """源码运行时用 tools/license_authority；PyInstaller 打包后用 exe 同目录。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _THIS_DIR
_BACKEND_DIR = os.path.abspath(os.path.join(_THIS_DIR, '..', '..', 'backend'))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from core.common.licensing.crypto import (  # noqa: E402
    LicensePayload,
    serialize_license,
    deserialize_license,
    load_private_key,
    verify_payload,
    load_public_key,
    VALID_WORKROLES,
)

DEFAULT_KEYS_DIR = os.path.join(_tool_base_dir(), 'keys')
DEFAULT_PRIVATE_KEY = os.path.join(DEFAULT_KEYS_DIR, 'private_key.pem')
DEFAULT_PUBLIC_KEY = os.path.join(DEFAULT_KEYS_DIR, 'public_key.pem')
DEFAULT_ISSUED_DIR = os.path.join(_tool_base_dir(), 'issued')


def load_signing_key(private_key_path: str = DEFAULT_PRIVATE_KEY, password: str | None = None):
    if not os.path.exists(private_key_path):
        raise FileNotFoundError(
            f'未找到私钥：{private_key_path}\n请先运行 generate_keys.py 生成密钥对。'
        )
    with open(private_key_path, 'rb') as f:
        pem = f.read()
    pwd = password.encode('utf-8') if password else None
    return load_private_key(pem, password=pwd)


def make_license_id(customer: str) -> str:
    stamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    prefix = ''.join(ch for ch in customer if ch.isalnum())[:6].upper() or 'CUST'
    return f'LIC-{prefix}-{stamp}'


def build_payload(
    customer: str,
    machine_fingerprint: str,
    allowed_workroles: list[str],
    expires_at: str,
    *,
    issued_at: str | None = None,
    license_id: str | None = None,
    max_users: int = 0,
    edition: str = 'standard',
    note: str = '',
) -> LicensePayload:
    issued_at = issued_at or datetime.date.today().strftime('%Y-%m-%d')
    license_id = license_id or make_license_id(customer)

    # 基本校验
    _validate_date(issued_at, '签发日期')
    _validate_date(expires_at, '到期日期')
    if not allowed_workroles:
        raise ValueError('至少需要选择一种允许的账户类型')
    for role in allowed_workroles:
        if role not in VALID_WORKROLES:
            raise ValueError(f'非法账户类型：{role}（合法值：{VALID_WORKROLES}）')
    if not machine_fingerprint.strip():
        raise ValueError('机器指纹/机器码不能为空')

    return LicensePayload(
        customer=customer,
        license_id=license_id,
        issued_at=issued_at,
        expires_at=expires_at,
        machine_fingerprint=machine_fingerprint.strip(),
        allowed_workroles=allowed_workroles,
        max_users=int(max_users),
        edition=edition,
        note=note,
    )


def issue_license_text(payload: LicensePayload, private_key_path: str = DEFAULT_PRIVATE_KEY,
                       password: str | None = None) -> str:
    private_key = load_signing_key(private_key_path, password)
    return serialize_license(payload, private_key)


def issue_license_file(payload: LicensePayload, out_path: str,
                       private_key_path: str = DEFAULT_PRIVATE_KEY,
                       password: str | None = None) -> str:
    text = issue_license_text(payload, private_key_path, password)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text)
    return out_path


def issue_activation_code(payload: LicensePayload,
                          private_key_path: str = DEFAULT_PRIVATE_KEY,
                          password: str | None = None) -> str:
    """生成短激活码（供人工传递，外观不可读）。"""
    from core.common.licensing.activation_code import encode_activation_code

    private_key = load_signing_key(private_key_path, password)
    return encode_activation_code(payload, private_key)


def verify_license_file(path: str, public_key_path: str = DEFAULT_PUBLIC_KEY) -> tuple[bool, dict]:
    """用公钥自检已签发的许可证（仅验签，不校验机器/有效期）。"""
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    payload, signature = deserialize_license(text)
    with open(public_key_path, 'rb') as f:
        pub = load_public_key(f.read())
    return verify_payload(payload, signature, pub), payload


def _validate_date(date_str: str, label: str):
    try:
        datetime.datetime.strptime(date_str.strip(), '%Y-%m-%d')
    except ValueError:
        raise ValueError(f'{label}格式应为 YYYY-MM-DD，当前：{date_str!r}')
