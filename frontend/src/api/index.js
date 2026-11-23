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

function attachResponseHandler(instance) {
  instance.interceptors.response.use(
    (response) => response,
    (error) => {
      const requestUrl = error.config?.url || ''
      const isLoginRequest = requestUrl.includes('/api/auth/login')
      const isOnLoginPage = window.location.pathname === '/login'

      if (error.response?.status === 401 && !isLoginRequest && !isOnLoginPage) {
        const hadToken = localStorage.getItem('authToken')
        if (hadToken) {
          sessionStorage.setItem(
            'loginRedirectMessage',
            '登录已失效：可能已在其他浏览器登录，或会话已过期'
          )
        }
        localStorage.removeItem('user')
        localStorage.removeItem('authToken')
        localStorage.removeItem('licenseValid')
        localStorage.removeItem('licenseInfo')
        window.location.href = '/login'
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
