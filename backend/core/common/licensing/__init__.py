"""巨管软件 - 许可证与授权核心包。

本包同时被两侧使用：
  - 客户端后端（仅用公钥*验证*许可证，无签发能力）。
  - 开发/维护侧发证工具（持有私钥，可*签发/续期*许可证）。

模块说明：
  - crypto:       Ed25519 签名 / 验签 + 许可证文件的序列化。
  - fingerprint:  采集服务器硬件指纹（客户自装场景绑定 license.dat 时使用）。
  - public_key:   内置在客户端中的公钥（PEM）。
  - manager:      LicenseManager，加载并校验 license.dat。
"""

from .crypto import (
    LicensePayload,
    sign_payload,
    verify_payload,
    serialize_license,
    deserialize_license,
)
from .fingerprint import get_machine_fingerprint, format_machine_code
from .manager import LicenseManager, LicenseStatus

__all__ = [
    'LicensePayload',
    'sign_payload',
    'verify_payload',
    'serialize_license',
    'deserialize_license',
    'get_machine_fingerprint',
    'format_machine_code',
    'LicenseManager',
    'LicenseStatus',
]
