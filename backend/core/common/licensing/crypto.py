"""许可证签名 / 验签与序列化。

设计要点：
  - 采用 Ed25519 非对称签名。私钥只在开发侧的发证工具中存在，
    客户端只内置公钥，因此客户即便拿到全部文件也无法伪造许可证。
  - 许可证文件 = 一段明文 JSON（payload）+ 对其规范化字节的签名（base64）。
    payload 任何字段被篡改都会导致验签失败。
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, asdict, field
from typing import List, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# 许可证数据结构的版本号；将来字段变化时用于兼容判断。
LICENSE_SCHEMA_VERSION = 1

# 允许出现在许可证中的账户（工作角色）类型。
VALID_WORKROLES = ['ALL', '工艺规划', '模具设计', '硬模成型']


@dataclass
class LicensePayload:
    """一份许可证承载的全部声明（claims）。

    machine_fingerprint 为 "ANY" 时表示不绑定机器（仅用于开发/测试，
    切勿用于正式交付）。
    """

    customer: str                       # 客户名称
    license_id: str                     # 许可证编号
    issued_at: str                      # 签发日期 YYYY-MM-DD
    expires_at: str                     # 到期日期 YYYY-MM-DD
    machine_fingerprint: str            # 绑定的服务器硬件指纹
    allowed_workroles: List[str]        # 允许注册的账户类型
    max_users: int = 0                  # 最大账户数，0 表示不限制
    edition: str = 'standard'           # 版本/套餐标识
    note: str = ''                      # 备注
    schema_version: int = LICENSE_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> 'LicensePayload':
        from core.common.workrole_access import normalize_workroles

        return LicensePayload(
            customer=data['customer'],
            license_id=data['license_id'],
            issued_at=data['issued_at'],
            expires_at=data['expires_at'],
            machine_fingerprint=data['machine_fingerprint'],
            allowed_workroles=normalize_workroles(list(data.get('allowed_workroles', []))),
            max_users=int(data.get('max_users', 0)),
            edition=data.get('edition', 'standard'),
            note=data.get('note', ''),
            schema_version=int(data.get('schema_version', LICENSE_SCHEMA_VERSION)),
        )


def _canonical_bytes(payload: dict) -> bytes:
    """对 payload 做确定性序列化，保证签名/验签字节一致。"""
    return json.dumps(
        payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    ).encode('utf-8')


# ─── 密钥加载 ──────────────────────────────────────────────────────────────

def load_private_key(pem_data: bytes, password: Optional[bytes] = None) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem_data, password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('提供的私钥不是 Ed25519 私钥')
    return key


def load_public_key(pem_data: bytes) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem_data)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError('提供的公钥不是 Ed25519 公钥')
    return key


def generate_keypair(password: Optional[bytes] = None) -> tuple[bytes, bytes]:
    """生成一对新的 Ed25519 密钥，返回 (private_pem, public_pem)。"""
    private_key = Ed25519PrivateKey.generate()
    enc = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=enc,
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


# ─── 签名 / 验签 ───────────────────────────────────────────────────────────

def sign_payload(payload: LicensePayload, private_key: Ed25519PrivateKey) -> str:
    """对 payload 签名，返回 base64 编码的签名字符串。"""
    signature = private_key.sign(_canonical_bytes(payload.to_dict()))
    return base64.b64encode(signature).decode('ascii')


def verify_payload(payload: dict, signature_b64: str, public_key: Ed25519PublicKey) -> bool:
    """验证签名是否匹配 payload。"""
    try:
        signature = base64.b64decode(signature_b64)
        public_key.verify(signature, _canonical_bytes(payload))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ─── 许可证文件序列化 ───────────────────────────────────────────────────────

def serialize_license(payload: LicensePayload, private_key: Ed25519PrivateKey) -> str:
    """生成可写入磁盘的许可证文件内容（JSON 文本）。"""
    signature = sign_payload(payload, private_key)
    document = {
        'payload': payload.to_dict(),
        'signature': signature,
        'algorithm': 'Ed25519',
    }
    return json.dumps(document, ensure_ascii=False, indent=2)


def deserialize_license(text: str) -> tuple[dict, str]:
    """解析许可证文件，返回 (payload_dict, signature_b64)。"""
    document = json.loads(text)
    return document['payload'], document['signature']
