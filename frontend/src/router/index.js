import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { userCanAccessRoute } from '../utils/workrole'
import axios from 'axios'

const routes = [
  {
    path: '/activate',
    name: 'Activate',
    component: () => import('../views/Activate.vue'),
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: { guest: true }
  },
  {
    path: '/signup',
    name: 'Signup',
    component: () => import('../views/Signup.vue'),
    meta: { guest: true }
  },
  {
    path: '/',
    component: () => import('../components/layout/AppLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'Dashboard',
        component: () => import('../views/Dashboard.vue')
      },
      // Account
      {
        path: 'account',
        name: 'Account',
        component: () => import('../views/Account.vue')
      },
      // Upload / 上传分析
      {
        path: 'upload/introduction',
        name: 'UploadIntroduction',
        component: () => import('../views/upload/Introduction.vue')
      },
      {
        path: 'upload/model',
        name: 'ModelUpload',
        component: () => import('../views/upload/ModelUpload.vue')
      },
      // Process Planning / 工艺优化
      {
        path: 'pp/introduction',
        name: 'PPIntroduction',
        component: () => import('../views/pp/Introduction.vue')
      },
      {
        path: 'pp/optimization',
        name: 'ProcessOptimization',
        component: () => import('../views/pp/Optimization.vue')
      },
      // Normal
      {
        path: 'normal/introduction',
        name: 'NormalIntroduction',
        component: () => import('../views/normal/Introduction.vue')
      },
      {
        path: 'normal/calculate',
        name: 'NormalCalculate',
        component: () => import('../views/normal/Calculate.vue')
      },
      {
        path: 'normal/search',
        name: 'NormalSearch',
        component: () => import('../views/normal/Search.vue')
      },
      // TP
      {
        path: 'tp/introduction',
        name: 'TPIntroduction',
        component: () => import('../views/tp/Introduction.vue')
      },
      {
        path: 'tp/calculate',
        name: 'TPCalculate',
        component: () => import('../views/tp/Calculate.vue')
      },
      {
        path: 'tp/search',
        name: 'TPSearch',
        component: () => import('../views/tp/Search.vue')
      },
      // Taper
      {
        path: 'taper/introduction',
        name: 'TaperIntroduction',
        component: () => import('../views/taper/Introduction.vue')
      },
      {
        path: 'taper/calculate',
        name: 'TaperCalculate',
        component: () => import('../views/taper/Calculate.vue')
      },
      {
        path: 'taper/search',
        name: 'TaperSearch',
        component: () => import('../views/taper/Search.vue')
      },
      // RFM
      {
        path: 'rfm/introduction',
        name: 'RFMIntroduction',
        component: () => import('../views/rfm/Introduction.vue')
      },
      {
        path: 'rfm/load-data',
        name: 'RFMLoadData',
        component: () => import('../views/rfm/LoadData.vue')
      },
      {
        path: 'rfm/belling',
        name: 'RFMBelling',
        component: () => import('../views/rfm/Belling.vue')
      },
      {
        path: 'rfm/bending',
        name: 'RFMBending',
        component: () => import('../views/rfm/Bending.vue')
      },
      {
        path: 'rfm/flattening',
        name: 'RFMFlattening',
        component: () => import('../views/rfm/Flattening.vue')
      },
      {
        path: 'rfm/shaping',
        name: 'RFMShaping',
        component: () => import('../views/rfm/Shaping.vue')
      },
      // Admin
      {
        path: 'admin/users',
        name: 'AdminUsers',
        component: () => import('../views/admin/Users.vue'),
        meta: { requiresAdmin: true }
      },
      {
        path: 'admin/register',
        name: 'AdminRegister',
        component: () => import('../views/admin/Register.vue'),
        meta: { requiresAdmin: true }
      },
      {
        path: 'admin/deployment',
        name: 'AdminDeployment',
        component: () => import('../views/admin/Deployment.vue'),
        meta: { requiresAdmin: true }
      },
      // Entity pipeline / 一体化流水线
      {
        path: 'entity',
        name: 'EntityPipeline',
        component: () => import('../views/EntityPipeline.vue')
      },
      // Tools
      {
        path: 'tools/rotation',
        name: 'RotationTool',
        component: () => import('../views/tools/Rotation.vue')
      },
    ]
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Navigation guards
router.beforeEach(async (to, from, next) => {
  const authStore = useAuthStore()

  let licenseValid = false
  try {
    const res = await axios.get('/api/auth/license-status')
    const lic = res.data.license || {}
    licenseValid = !!lic.valid
    authStore.setLicenseState(
      licenseValid,
      licenseValid ? { start: lic.issued_at, end: lic.expires_at } : lic.reason,
    )
  } catch (e) {
    licenseValid = authStore.licenseValid
  }

  if (!licenseValid && to.path !== '/activate') {
    next('/activate')
    return
  }
  if (licenseValid && to.path === '/activate') {
    next('/login')
    return
  }

  if (to.meta.requiresAuth && !authStore.isLoggedIn) {
    next('/login')
    return
  }
  if (to.meta.guest && authStore.isLoggedIn) {
    next('/')
    return
  }
  if (to.meta.requiresAdmin && authStore.user?.authority !== 'admin') {
    next('/')
    return
  }

  if (to.meta.requiresAuth && authStore.isLoggedIn && !userCanAccessRoute(authStore.user, to.path)) {
    next('/')
    return
  }

  next()
})

export default router
