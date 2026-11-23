import { defineStore } from 'pinia'
import api from '../api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: JSON.parse(localStorage.getItem('user') || 'null'),
    token: localStorage.getItem('authToken') || '',
    licenseValid: JSON.parse(localStorage.getItem('licenseValid') || 'false'),
    licenseInfo: JSON.parse(localStorage.getItem('licenseInfo') || 'null'),
  }),

  getters: {
    isLoggedIn: (state) => !!state.user,
    userName: (state) => state.user?.name || '',
    workrole: (state) => state.user?.workrole || '',
    isAdmin: (state) => state.user?.authority === 'admin',
  },

  actions: {
    async login(id, password) {
      const res = await api.post('/api/auth/login', { id, password })
      if (res.data.success) {
        this.user = res.data.user
        this.token = res.data.token || ''
        this.licenseValid = res.data.license_valid
        this.licenseInfo = res.data.license_info
        localStorage.setItem('user', JSON.stringify(this.user))
        localStorage.setItem('authToken', this.token)
        localStorage.setItem('licenseValid', JSON.stringify(this.licenseValid))
        localStorage.setItem('licenseInfo', JSON.stringify(this.licenseInfo))
        return {
          success: true,
          message: res.data.message,
          deviceNewlyBound: res.data.device_newly_bound,
        }
      }
      return { success: false, message: res.data.message }
    },

    async logout() {
      try {
        await api.post('/api/auth/logout')
      } catch {
        // 会话已失效时忽略
      }
      this.user = null
      this.token = ''
      this.licenseValid = false
      this.licenseInfo = null
      localStorage.removeItem('user')
      localStorage.removeItem('authToken')
      localStorage.removeItem('licenseValid')
      localStorage.removeItem('licenseInfo')
    },

    async register(data) {
      const res = await api.post('/api/auth/register', data)
      return res.data
    },

    setUser(userData) {
      this.user = userData
      localStorage.setItem('user', JSON.stringify(userData))
    },

    setLicenseState(valid, info) {
      this.licenseValid = valid
      this.licenseInfo = info
      localStorage.setItem('licenseValid', JSON.stringify(valid))
      localStorage.setItem('licenseInfo', JSON.stringify(info))
    },
  },
})
