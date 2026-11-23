"""LicenseManager —— 客户端许可证加载与校验中枢。

职责：
  1. 读取磁盘上的 license.dat。
  2. 用内置公钥验签（防伪造 / 防篡改）。
  3. 校验机器指纹（防整体拷走盗用）。
  4. 校验有效期（防过期使用）。
  5. 对外暴露 allowed_workroles / expires_at 等声明，供注册与登录逻辑使用。

该模块做了进程内缓存并支持热加载（文件 mtime 变化时自动重新读取），
因此维护人员替换 license.dat 后无需重启服务即可生效。
"""
from __future__ import annotations

import datetime
import os
import re
import threading
from dataclasses import dataclass, field
from typing import List, Optional

from .crypto import deserialize_license, verify_payload, LicensePayload
from . import fingerprint as _fingerprint_module
from .fingerprint import format_machine_code, get_machine_fingerprint
from . import public_key as _pubkey
from .activation_code import (
    ActivationCodeError,
    looks_like_activation_code,
    verify_activation_code,
)


# 机器码展示与稳定性提示（对外文案，供 API / 前端直接使用）。
MACHINE_CODE_HINT = (
    '机器码已按本机系统标识锚定，重启或切换网络不会改变；'
    '更换主板/重装系统后需重新签发'
)
MACHINE_CODE_UNSTABLE_HINT = (
    '本次采集未拿到稳定系统标识，机器码可能变化，请联系供应商'
)

# 机器码/指纹只由十六进制字符组成，展示时才加分隔符。
_NON_HEX_RE = re.compile(r'[^0-9a-f]')


def _normalize_fp_value(value) -> str:
    """把机器码（可带 '-' / 空格分隔）或完整指纹归一化成纯小写 hex。"""
    if not value:
        return ''
    return _NON_HEX_RE.sub('', str(value).strip().lower())


def _detail_get(details, key: str, default=None):
    """兼容 dict / dataclass / 简单对象三种 details 返回形态。"""
    if isinstance(details, dict):
        return details.get(key, default)
    return getattr(details, key, default)


def fingerprint_meta(full_fp: Optional[str] = None) -> dict:
    """返回机器码及其稳定性扩展信息（供 API 展示，不泄露完整指纹）。

    兼容两代指纹模块：新模块提供 ``fingerprint_details()`` 时读取其中的
    source / stable / anchored；该函数缺失或抛异常时返回中性默认值
    （stable=None），保证旧版指纹模块下接口依然可用。
    """
    fp = (full_fp or get_machine_fingerprint() or '').strip().lower()
    details = {}
    details_fn = getattr(_fingerprint_module, 'fingerprint_details', None)
    if callable(details_fn):
        try:
            details = details_fn()
        except TypeError:
            # 兼容需要传入指纹参数的实现
            try:
                details = details_fn(fp)
            except Exception:
                details = {}
        except Exception:
            details = {}

    source = _detail_get(details, 'source', '') or ''
    stable = _detail_get(details, 'stable', None)
    anchored = _detail_get(details, 'anchored', None)
    if not isinstance(stable, bool):
        stable = None
    if not isinstance(anchored, bool):
        anchored = False

    # 优先采用指纹模块给出的机器码（锚点可能记录的是首次锚定值），
    # 模块未提供时再按完整指纹本地格式化。
    machine_code = str(_detail_get(details, 'machine_code', '') or '').strip()
    if not machine_code and fp:
        formatter = getattr(_fingerprint_module, 'format_machine_code', None) or format_machine_code
        try:
            machine_code = formatter(fp)
        except Exception:
            machine_code = ''

    return {
        'machine_code': machine_code,
        'fingerprint_stable': stable,
        'fingerprint_source': str(source).strip() or 'unknown',
        'fingerprint_anchored': anchored,
        'machine_code_hint': (
            MACHINE_CODE_UNSTABLE_HINT if stable is False else MACHINE_CODE_HINT
        ),
    }


@dataclass
class LicenseStatus:
    valid: bool
    reason: str = ''                       # 无效时的原因（面向用户）
    customer: str = ''
    license_id: str = ''
    issued_at: str = ''
    expires_at: str = ''
    allowed_workroles: List[str] = field(default_factory=list)
    max_users: int = 0
    edition: str = ''
    days_left: Optional[int] = None
    machine_match: bool = False
    # 下列为机器码扩展字段（只含机器码，不含完整指纹），旧前端可忽略。
    machine_code: str = ''
    fingerprint_stable: Optional[bool] = None
    fingerprint_source: str = ''
    fingerprint_anchored: bool = False
    machine_code_hint: str = ''

    def to_public_dict(self) -> dict:
        """对外（前端）暴露的安全字段，不含敏感信息。"""
        return {
            'valid': self.valid,
            'reason': self.reason,
            'customer': self.customer,
            'license_id': self.license_id,
            'issued_at': self.issued_at,
            'expires_at': self.expires_at,
            'allowed_workroles': self.allowed_workroles,
            'max_users': self.max_users,
            'edition': self.edition,
            'days_left': self.days_left,
            'machine_match': self.machine_match,
            'machine_code': self.machine_code,
            'fingerprint_stable': self.fingerprint_stable,
            'fingerprint_source': self.fingerprint_source,
            'fingerprint_anchored': self.fingerprint_anchored,
            'machine_code_hint': self.machine_code_hint,
        }


def fingerprint_matches(expected: str, actual_full: str) -> bool:
    """判断许可证绑定的指纹是否匹配本机。

    支持三种绑定写法：
      - 'ANY'：不绑定（仅开发/测试用）。
      - 完整 64 位指纹：与本机完整指纹全等。
      - 机器码（前 20 位 hex，带分隔符如 ``6ECF1-D9F4A-B3331-3F00A``）：
        去分隔符后作为本机完整指纹的前缀匹配。
    比较时统一去掉分隔符（含空格/冒号等非 hex 字符）并转小写。
    """
    if not expected or not actual_full:
        return False
    if str(expected).strip().upper() == 'ANY':
        return True
    norm_expected = _normalize_fp_value(expected)
    actual = _normalize_fp_value(actual_full)
    if not norm_expected or not actual:
        return False
    if norm_expected == actual:
        return True
    # 机器码前缀匹配（长度需足够，避免误判）
    if len(norm_expected) >= 16 and actual.startswith(norm_expected):
        return True
    return False


def _parse_date(date_str: str) -> Optional[datetime.date]:
    try:
        return datetime.datetime.strptime(date_str.strip(), '%Y-%m-%d').date()
    except (ValueError, AttributeError):
        return None


class LicenseManager:
    def __init__(self, license_path: str):
        self.license_path = license_path
        self._lock = threading.Lock()
        self._cached_status: Optional[LicenseStatus] = None
        self._cached_mtime: Optional[float] = None

    # ─── 加载与校验 ─────────────────────────────────────────────────────────

    def _current_mtime(self) -> Optional[float]:
        try:
            return os.path.getmtime(self.license_path)
        except OSError:
            return None

    def get_status(self, force_reload: bool = False) -> LicenseStatus:
        with self._lock:
            mtime = self._current_mtime()
            if (
                not force_reload
                and self._cached_status is not None
                and mtime == self._cached_mtime
            ):
                return self._cached_status
            status = self._evaluate()
            self._cached_status = status
            self._cached_mtime = mtime
            return status

    def _evaluate(self) -> LicenseStatus:
        if not _pubkey.is_configured():
            return LicenseStatus(valid=False, reason='客户端未配置发证公钥，请联系软件供应商')

        if not os.path.exists(self.license_path):
            return LicenseStatus(valid=False, reason='软件尚未激活，请输入供应商提供的授权码')

        try:
            with open(self.license_path, 'r', encoding='utf-8') as f:
                text = f.read().strip()
        except OSError:
            return LicenseStatus(valid=False, reason='无法读取许可证文件')

        if looks_like_activation_code(text):
            return self._evaluate_activation_code(text)

        try:
            payload_dict, signature = deserialize_license(text)
        except Exception:
            return LicenseStatus(valid=False, reason='许可证文件已损坏或格式不正确')

        # 1) 验签
        try:
            public_key = _load_public_key()
        except Exception:
            return LicenseStatus(valid=False, reason='客户端公钥无效，请联系软件供应商')

        if not verify_payload(payload_dict, signature, public_key):
            return LicenseStatus(valid=False, reason='许可证签名无效（可能被篡改或非本软件签发）')

        try:
            payload = LicensePayload.from_dict(payload_dict)
        except Exception:
            return LicenseStatus(valid=False, reason='许可证内容不完整')

        # 2) 机器指纹校验（托管部署可关闭，见 GTAPP_BIND_SERVER_MACHINE=0）
        try:
            from config import BIND_SERVER_MACHINE
            bind_server = BIND_SERVER_MACHINE
        except Exception:
            bind_server = True

        machine_fp = get_machine_fingerprint()
        machine_match = fingerprint_matches(payload.machine_fingerprint, machine_fp)
        meta = fingerprint_meta(machine_fp)

        # 3) 有效期校验
        expires = _parse_date(payload.expires_at)
        days_left = None
        if expires is not None:
            days_left = (expires - datetime.date.today()).days

        base = LicenseStatus(
            valid=False,
            customer=payload.customer,
            license_id=payload.license_id,
            issued_at=payload.issued_at,
            expires_at=payload.expires_at,
            allowed_workroles=payload.allowed_workroles,
            max_users=payload.max_users,
            edition=payload.edition,
            days_left=days_left,
            machine_match=machine_match,
            machine_code=meta['machine_code'],
            fingerprint_stable=meta['fingerprint_stable'],
            fingerprint_source=meta['fingerprint_source'],
            fingerprint_anchored=meta['fingerprint_anchored'],
            machine_code_hint=meta['machine_code_hint'],
        )

        if bind_server and not machine_match:
            base.reason = (
                '许可证未授权给本服务器（机器码不匹配）。'
                '若本机机器码近期发生变化，请复制当前机器码重新申请/签发许可证'
            )
            return base

        if expires is None:
            base.reason = '许可证到期日期无效'
            return base

        if days_left is not None and days_left < 0:
            base.reason = f'许可证已于 {payload.expires_at} 到期，请联系软件供应商续期'
            return base

        base.valid = True
        base.reason = ''
        return base

    def _evaluate_activation_code(self, code: str) -> LicenseStatus:
        try:
            public_key = _load_public_key()
            payload = verify_activation_code(code, public_key)
        except ActivationCodeError as exc:
            return LicenseStatus(valid=False, reason=str(exc))
        except Exception:
            return LicenseStatus(valid=False, reason='激活码无效，请核对后重试')

        try:
            from config import BIND_SERVER_MACHINE
            bind_server = BIND_SERVER_MACHINE
        except Exception:
            bind_server = True

        machine_fp = get_machine_fingerprint()
        machine_match = fingerprint_matches(payload.machine_fingerprint, machine_fp)
        meta = fingerprint_meta(machine_fp)

        expires = _parse_date(payload.expires_at)
        days_left = None
        if expires is not None:
            days_left = (expires - datetime.date.today()).days

        base = LicenseStatus(
            valid=False,
            customer=payload.customer,
            license_id=payload.license_id,
            issued_at=payload.issued_at,
            expires_at=payload.expires_at,
            allowed_workroles=payload.allowed_workroles,
            max_users=payload.max_users,
            edition=payload.edition,
            days_left=days_left,
            machine_match=machine_match,
            machine_code=meta['machine_code'],
            fingerprint_stable=meta['fingerprint_stable'],
            fingerprint_source=meta['fingerprint_source'],
            fingerprint_anchored=meta['fingerprint_anchored'],
            machine_code_hint=meta['machine_code_hint'],
        )

        if bind_server and not machine_match:
            base.reason = (
                '授权码未授权给本服务器（机器码不匹配）。'
                '若本机机器码近期发生变化，请复制当前机器码重新申请/签发授权码'
            )
            return base

        if expires is None:
            base.reason = '授权码到期日期无效'
            return base

        if days_left is not None and days_left < 0:
            base.reason = f'授权已于 {payload.expires_at} 到期，请联系软件供应商续期'
            return base

        base.valid = True
        base.reason = ''
        return base

    # ─── 便捷查询 ───────────────────────────────────────────────────────────

    @property
    def is_valid(self) -> bool:
        return self.get_status().valid

    def allowed_workroles(self) -> List[str]:
        return self.get_status().allowed_workroles

    def is_workrole_allowed(self, workrole: str) -> bool:
        from core.common.workrole_access import normalize_workrole, normalize_workroles

        status = self.get_status()
        if not status.valid:
            return False
        allowed = normalize_workroles(status.allowed_workroles)
        workrole = normalize_workrole(workrole)
        if 'ALL' in allowed:
            return True
        return workrole in allowed


def _load_public_key():
    from .crypto import load_public_key
    return load_public_key(_pubkey.PUBLIC_KEY_PEM)
