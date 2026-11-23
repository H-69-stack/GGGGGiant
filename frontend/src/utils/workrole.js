/** 工作角色访问控制（与后端 workrole_access.py、侧栏 AppSidebar 一致） */

export const WORKROLES = {
  PP: '工艺规划',
  MOLD: '模具设计',
  RFM: '硬模成型',
  ALL: 'ALL',
}

/** 历史错别字「硬膜成型」，兼容旧账户数据 */
const WORKROLE_ALIASES = {
  '硬膜成型': WORKROLES.RFM,
}

export function normalizeWorkrole(role) {
  if (!role) return role
  return WORKROLE_ALIASES[role] || role
}

export function canAccessWorkrole(user, role) {
  if (!user) return false
  if (user.authority === 'admin') return true
  const wr = normalizeWorkrole(user.workrole || '')
  return wr === WORKROLES.ALL || wr === normalizeWorkrole(role)
}

/** 根据路由 path 返回所需 workrole；null 表示不限制 */
export function routeRequiredWorkrole(path) {
  if (!path) return null
  if (path.startsWith('/upload') || path.startsWith('/pp')) return WORKROLES.PP
  if (path.startsWith('/normal') || path.startsWith('/tp') || path.startsWith('/taper')) {
    return WORKROLES.MOLD
  }
  if (path.startsWith('/rfm')) return WORKROLES.RFM
  return null
}

export function userCanAccessRoute(user, path) {
  const required = routeRequiredWorkrole(path)
  if (!required) return true
  return canAccessWorkrole(user, required)
}
