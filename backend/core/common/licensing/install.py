"""安装许可证（激活码或 license.dat）到本地存储。"""
from __future__ import annotations

import os

from config import LICENSE_DAT_PATH
from core.common.license import get_license_manager
from core.common.licensing.activation_code import (
    ActivationCodeError,
    activation_code_for_storage,
    looks_like_activation_code,
)
from core.common.licensing.manager import LicenseStatus


def install_license_content(content: str) -> LicenseStatus:
    content = (content or '').strip()
    if not content:
        raise ActivationCodeError('授权内容为空')

    os.makedirs(os.path.dirname(os.path.abspath(LICENSE_DAT_PATH)), exist_ok=True)
    if looks_like_activation_code(content):
        stored = activation_code_for_storage(content)
        with open(LICENSE_DAT_PATH, 'w', encoding='utf-8') as f:
            f.write(stored)
            f.write('\n')
    else:
        with open(LICENSE_DAT_PATH, 'w', encoding='utf-8') as f:
            f.write(content)
            if not content.endswith('\n'):
                f.write('\n')

    return get_license_manager().get_status(force_reload=True)
