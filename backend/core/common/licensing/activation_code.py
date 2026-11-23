"""短激活码编解码（离线人工传递，外观不可读）。

结构：签名载荷 + Ed25519 签名 → XOR 混淆 → Base32 分组展示。
验签使用内置公钥；私钥仅存在于发证工具。
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import struct
from typing import List, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .crypto import LicensePayload, VALID_WORKROLES
from core.common.workrole_access import normalize_workrole

MAGIC = b'GT'
VERSION = 2
CUSTOMER_FIELD_LEN = 16
PAYLOAD_SIZE = 42
SIGNATURE_SIZE = 64
BLOB_SIZE = PAYLOAD_SIZE + SIGNATURE_SIZE

# 客户端内置混淆因子（仅隐藏明文结构，安全性依赖 Ed25519 签名）
_OBF_PEPPER = b'GTAPP.v2.activation.obfuscation.pepper'

_ROLE_BIT = {
    'ALL': 0x01,
    '工艺规划': 0x02,
    '模具设计': 0x04,
    '硬模成型': 0x08,
}


class ActivationCodeError(ValueError):
    pass


def _obfuscate(data: bytes) -> bytes:
    key = hashlib.sha256(_OBF_PEPPER).digest()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _date_to_int(date_str: str) -> int:
    parts = date_str.strip().split('-')
    if len(parts) != 3:
        raise ActivationCodeError(f'日期格式无效：{date_str!r}')
    y, m, d = (int(p) for p in parts)
    return y * 10000 + m * 100 + d


def _int_to_date(value: int) -> str:
    y = value // 10000
    m = (value // 100) % 100
    d = value % 100
    return f'{y:04d}-{m:02d}-{d:02d}'


def _roles_to_flags(roles: List[str]) -> int:
    if 'ALL' in roles:
        return _ROLE_BIT['ALL']
    flags = 0
    for role in roles:
        bit = _ROLE_BIT.get(normalize_workrole(role))
        if bit:
            flags |= bit
    return flags


def _flags_to_roles(flags: int) -> List[str]:
    if flags & _ROLE_BIT['ALL']:
        return ['ALL']
    roles = []
    for role, bit in _ROLE_BIT.items():
        if role != 'ALL' and flags & bit:
            roles.append(role)
    return roles or ['ALL']


def _machine_to_bytes(machine_fingerprint: str) -> bytes:
    raw = machine_fingerprint.replace('-', '').strip().lower()
    if len(raw) < 20:
        raise ActivationCodeError('机器码长度不足')
    try:
        return bytes.fromhex(raw[:20])
    except ValueError as exc:
        raise ActivationCodeError('机器码格式无效') from exc


def _bytes_to_machine(raw: bytes) -> str:
    return raw.hex()


def _pack_customer(name: str) -> bytes:
    encoded = (name or '').encode('utf-8')[:CUSTOMER_FIELD_LEN]
    return encoded.ljust(CUSTOMER_FIELD_LEN, b'\x00')


def _unpack_customer(raw: bytes) -> str:
    return raw.rstrip(b'\x00').decode('utf-8', errors='ignore').strip()


def pack_payload_bytes(payload: LicensePayload, *, nonce: Optional[bytes] = None) -> bytes:
    if len(nonce or b'') not in (0, 4):
        raise ActivationCodeError('nonce 长度必须为 4 字节')
    nonce = nonce or secrets.token_bytes(4)
    body = struct.pack(
        '>2sBBII10s4s16s',
        MAGIC,
        VERSION,
        _roles_to_flags(payload.allowed_workroles),
        _date_to_int(payload.expires_at),
        _date_to_int(payload.issued_at),
        _machine_to_bytes(payload.machine_fingerprint),
        nonce,
        _pack_customer(payload.customer),
    )
    if len(body) != PAYLOAD_SIZE:
        raise ActivationCodeError('激活码载荷长度异常')
    return body


def payload_from_bytes(body: bytes, *, license_id: str = '') -> LicensePayload:
    if len(body) != PAYLOAD_SIZE:
        raise ActivationCodeError('激活码载荷损坏')
    magic, version, flags, expires_i, issued_i, machine_raw, _nonce, customer_raw = struct.unpack(
        '>2sBBII10s4s16s', body
    )
    if magic != MAGIC or version != VERSION:
        raise ActivationCodeError('激活码版本不受支持')
    customer = _unpack_customer(customer_raw)
    if not license_id:
        stamp = secrets.token_hex(4).upper()
        prefix = ''.join(ch for ch in customer if ch.isalnum())[:6].upper() or 'CUST'
        license_id = f'LIC-{prefix}-{stamp}'
    return LicensePayload(
        customer=customer or 'Licensed',
        license_id=license_id,
        issued_at=_int_to_date(issued_i),
        expires_at=_int_to_date(expires_i),
        machine_fingerprint=_bytes_to_machine(machine_raw),
        allowed_workroles=_flags_to_roles(flags),
        max_users=0,
        edition='standard',
        note='activation-code',
    )


def encode_activation_code(payload: LicensePayload, private_key: Ed25519PrivateKey) -> str:
    body = pack_payload_bytes(payload)
    signature = private_key.sign(body)
    if len(signature) != SIGNATURE_SIZE:
        raise ActivationCodeError('签名长度异常')
    blob = _obfuscate(body + signature)
    encoded = base64.b32encode(blob).decode('ascii').rstrip('=')
    groups = [encoded[i:i + 5] for i in range(0, len(encoded), 5)]
    return 'GT-' + '-'.join(groups)


def normalize_activation_code(text: str) -> str:
    s = (text or '').strip().upper()
    s = re.sub(r'[\s\r\n]', '', s)
    if s.startswith('GT-'):
        core = s[3:].replace('-', '')
    elif s.startswith('GT'):
        core = s[2:].replace('-', '')
    else:
        core = s.replace('-', '')
    if not core:
        raise ActivationCodeError('激活码为空')
    pad = (-len(core)) % 8
    try:
        blob = base64.b32decode(core + ('=' * pad), casefold=True)
    except Exception as exc:
        raise ActivationCodeError('激活码格式无效') from exc
    if len(blob) != BLOB_SIZE:
        raise ActivationCodeError('激活码长度无效')
    encoded = base64.b32encode(blob).decode('ascii').rstrip('=')
    groups = [encoded[i:i + 5] for i in range(0, len(encoded), 5)]
    return 'GT-' + '-'.join(groups)


def _decode_blob(text: str) -> Tuple[bytes, bytes]:
    normalized = normalize_activation_code(text)
    core = normalized[3:].replace('-', '')
    pad = (-len(core)) % 8
    blob = base64.b32decode(core + ('=' * pad), casefold=True)
    clear = _obfuscate(blob)
    return clear[:PAYLOAD_SIZE], clear[PAYLOAD_SIZE:]


def looks_like_activation_code(text: str) -> bool:
    s = (text or '').strip()
    if not s or s.lstrip().startswith('{'):
        return False
    cleaned = re.sub(r'[\s\-]', '', s.upper())
    return cleaned.startswith('GT') and len(cleaned) >= 20


def verify_activation_code(text: str, public_key: Ed25519PublicKey) -> LicensePayload:
    try:
        body, signature = _decode_blob(text)
        public_key.verify(signature, body)
    except ActivationCodeError:
        raise
    except Exception as exc:
        raise ActivationCodeError('激活码无效或已被篡改') from exc
    return payload_from_bytes(body)


def activation_code_for_storage(text: str) -> str:
    """规范化后写入 license.dat（单行激活码）。"""
    return normalize_activation_code(text)
