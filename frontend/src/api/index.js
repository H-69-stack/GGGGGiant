import axios from 'axios'

const api = axios.create({
  baseURL: '',
  timeout: 120000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
})

axios.defaults.withCredentials = true

function attachRequestAuth(instance) {
  instance.interceptors.request.use((config) => {
    const token = localStorage.getItem('authToken')
    if (token) {
      config.headers = config.headers || {}
      config.headers['X-Auth-Token'] = token
    }
    config.withCredentials = true
    return config
  })
}

// 401 不再把整页跳到 /login（那等于刷新，正在填写的壁厚/图纸长度等会全部丢失）。
// 改为抛一个全局事件，由常驻的 <SessionExpiredDialog> 原地弹窗重新登录。
let authExpiredNotified = false

export function resetAuthExpiredFlag() {
  authExpiredNotified = false
}

function notifyAuthExpired(message) {
  if (authExpiredNotified) return
  authExpiredNotified = true
  window.dispatchEvent(new CustomEvent('gtapp:auth-expired', { detail: { message } }))
}

function attachResponseHandler(instance) {
  instance.interceptors.response.use(
    (response) => response,
    (error) => {
      const requestUrl = error.config?.url || ''
      const isLoginRequest = requestUrl.includes('/api/auth/login')
      const isOnLoginPage = window.location.pathname === '/login'

      if (error.response?.status === 401 && !isLoginRequest && !isOnLoginPage) {
        const hadToken = localStorage.getItem('authToken')
        localStorage.removeItem('user')
        localStorage.removeItem('authToken')
        localStorage.removeItem('licenseValid')
        localStorage.removeItem('licenseInfo')
        notifyAuthExpired(
          hadToken
            ? '登录已失效：可能已在其他浏览器登录，或服务重启导致会话失效'
            : '当前未登录或会话已失效，请重新登录'
        )
      }

      if (error.response?.status === 403) {
        const data = error.response.data || {}
        if (data.license_invalid) {
          localStorage.setItem('licenseValid', JSON.stringify(false))
          localStorage.setItem('licenseInfo', JSON.stringify(data.message || '许可证无效'))
        }
      }

      return Promise.reject(error)
    }
  )
}

attachRequestAuth(api)
attachRequestAuth(axios)
attachResponseHandler(api)
attachResponseHandler(axios)

export default api
