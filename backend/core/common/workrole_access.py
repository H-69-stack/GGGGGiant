"""工作角色与 API 模块访问控制（与前端侧栏 canAccess 规则一致）。"""
from __future__ import annotations

from typing import FrozenSet, Optional, Set

WORKROLE_PP = '工艺规划'
WORKROLE_MOLD = '模具设计'
WORKROLE_RFM = '硬模成型'
WORKROLE_ALL = 'ALL'

# 历史错别字「硬膜成型」，兼容旧数据库与许可证
_WORKROLE_ALIASES = {
    '硬膜成型': WORKROLE_RFM,
}


def normalize_workrole(workrole: str) -> str:
    if not workrole:
        return workrole
    return _WORKROLE_ALIASES.get(workrole, workrole)


def normalize_workroles(roles) -> list[str]:
    return [normalize_workrole(r) for r in roles]

# API 前缀 → 所需工作角色（admin / workrole=ALL 始终放行）
API_PREFIX_WORKROLES: tuple[tuple[str, FrozenSet[str]], ...] = (
    ('/api/pp/', frozenset({WORKROLE_PP})),
    ('/api/normal/', frozenset({WORKROLE_MOLD})),
    ('/api/tp/', frozenset({WORKROLE_MOLD})),
    ('/api/taper/', frozenset({WORKROLE_MOLD})),
    ('/api/rfm/', frozenset({WORKROLE_RFM})),
)

# 仅需登录、不限制 workrole 的 API
API_LOGIN_ONLY_PREFIXES: tuple[str, ...] = (
    '/api/rotation/',
    # 实体化流水线：任何登录用户可用，但必须登录
    '/api/entity/',
)

# 业务 API 前缀合集（需登录）
BUSINESS_API_PREFIXES: tuple[str, ...] = tuple(
    prefix for prefix, _ in API_PREFIX_WORKROLES
) + API_LOGIN_ONLY_PREFIXES


def required_workroles_for_api(path: str) -> Optional[Set[str]]:
    """返回访问该 API 路径所需的 workrole 集合；None 表示仅需登录。"""
    for prefix, roles in API_PREFIX_WORKROLES:
        if path.startswith(prefix):
            return set(roles)
    for prefix in API_LOGIN_ONLY_PREFIXES:
        if path.startswith(prefix):
            return None
    return None


def user_can_access_workroles(user: dict, required_roles: Set[str]) -> bool:
    if user.get('authority') == 'admin':
        return True
    workrole = normalize_workrole(user.get('workrole', ''))
    if workrole == WORKROLE_ALL:
        return True
    normalized_required = {normalize_workrole(r) for r in required_roles}
    return workrole in normalized_required


def route_workrole_for_path(path: str) -> Optional[str]:
    """前端路由路径 → 所需单一 workrole（与侧栏模块一致）。"""
    if not path:
        return None
    if path.startswith('/upload') or path.startswith('/pp'):
        return WORKROLE_PP
    if path.startswith('/normal') or path.startswith('/tp') or path.startswith('/taper'):
        return WORKROLE_MOLD
    if path.startswith('/rfm'):
        return WORKROLE_RFM
    return None
