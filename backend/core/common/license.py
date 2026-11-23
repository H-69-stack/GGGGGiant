"""服务器级许可证校验入口（基于签名许可证 license.dat）。

新版许可证模型：
  - 一份由开发侧私钥签发的 license.dat，绑定本服务器硬件指纹，
    内含"允许注册的账户类型"与"到期日期"。
  - 客户端只用内置公钥验签，无法伪造。
  - 这里保留了旧的 license_check() 函数签名以兼容现有调用，但其实际
    校验的是"服务器级许可证"而非旧的每用户 key。

开发态（dev 通道 + GTAPP_LICENSE_GATE=0）：
  - 不做任何签名校验，直接视为"有效"，让前端跳过激活页、注册时允许全部账户类型。
  - release 通道下 config.LICENSE_GATE_ENABLED 恒为 True，此处不会生效。
"""
from core.common.licensing import LicenseManager, LicenseStatus
from config import LICENSE_DAT_PATH, LICENSE_GATE_ENABLED

# 进程内单例。LicenseManager 内部按文件 mtime 做热加载，
# 维护人员替换 license.dat 后无需重启即可生效。
_manager = LicenseManager(LICENSE_DAT_PATH)


def get_license_manager() -> LicenseManager:
    return _manager


def dev_mode() -> bool:
    """是否处于"许可证门禁关闭"的开发态。"""
    try:
        return not LICENSE_GATE_ENABLED
    except Exception:
        return False


def dev_status() -> LicenseStatus:
    """开发态下合成的"有效"许可证状态（不绑定机器码、无到期日）。"""
    return LicenseStatus(
        valid=True,
        reason='',
        customer='开发态（未启用许可证门禁）',
        license_id='DEV-MODE',
        issued_at='',
        expires_at='',
        allowed_workroles=['ALL'],
        max_users=0,
        edition='dev',
        days_left=None,
        machine_match=True,
    )


def license_check(license_key=None):
    """校验服务器级许可证是否有效。

    为兼容旧调用保留 license_key 参数，但新模型下该参数被忽略，
    始终以服务器签名许可证为准；开发态直接放行。

    返回 (is_valid, info_or_message)。
    """
    if dev_mode():
        return True, {'start': '', 'end': '开发态（无期限）', 'dev': True}
    status = _manager.get_status()
    if status.valid:
        return True, {'start': status.issued_at, 'end': status.expires_at}
    return False, status.reason
