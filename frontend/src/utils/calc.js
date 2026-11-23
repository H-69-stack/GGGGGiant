export function getCalcAuth() {
  const user = JSON.parse(localStorage.getItem('user') || 'null')
  return {
    userName: user?.name || '/',
    license: user?.license || '',
  }
}

export function licenseRequestConfig(license) {
  return license ? { headers: { 'X-License': license } } : {}
}

export async function modifyMoldParam(apiPrefix, sessionId, moldName, row) {
  const { license } = getCalcAuth()
  const axios = (await import('axios')).default
  return axios.post(
    `${apiPrefix}/modify`,
    {
      session_id: sessionId,
      mold_name: moldName,
      key: row['参数'],
      value: row['值'],
      license,
    },
    licenseRequestConfig(license),
  )
}
