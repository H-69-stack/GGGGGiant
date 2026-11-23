<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <span>个人信息</span>
      </template>

      <div v-if="user" class="user-info">
        <h3>欢迎！</h3>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="账号">{{ user.id }}</el-descriptions-item>
          <el-descriptions-item label="姓名">{{ user.name }}</el-descriptions-item>
          <el-descriptions-item label="权限">{{ user.authority }}</el-descriptions-item>
          <el-descriptions-item label="工作角色">{{ user.workrole }}</el-descriptions-item>
          <el-descriptions-item label="审批状态">
            {{ approvalLabel(user.approval_status) }}
          </el-descriptions-item>
          <el-descriptions-item v-if="user.authority !== 'admin'" label="浏览器绑定">
            {{ user.device_bound ? '已绑定本浏览器' : '尚未绑定（首次登录后自动绑定）' }}
          </el-descriptions-item>
          <el-descriptions-item label="账户有效期">
            {{ accountExpiresLabel }}
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="serverLicenseInfo"
          :title="serverLicenseInfo"
          :type="licenseValid ? 'success' : 'error'"
          :closable="false"
          style="margin-top: 16px;"
        />

        <el-button @click="loadUser" style="margin-top: 16px;">刷新</el-button>
      </div>

      <el-divider />

      <h3>个人信息维护</h3>

      <el-collapse v-model="activePanels">
        <el-collapse-item title="修改密码" name="password">
          <el-alert
            title="新密码要求：至少8位，包含大小写字母、数字、特殊字符（!@#$%^&*等）"
            type="info"
            :closable="false"
            style="margin-bottom: 12px;"
          />
          <el-form :model="passwordForm" label-width="120px" style="max-width: 400px;">
            <el-form-item label="旧密码">
              <el-input v-model="passwordForm.oldPassword" type="password" show-password />
            </el-form-item>
            <el-form-item label="新密码">
              <el-input v-model="passwordForm.newPassword" type="password" show-password />
            </el-form-item>
            <el-form-item label="确认新密码">
              <el-input v-model="passwordForm.confirmPassword" type="password" show-password />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="handleChangePassword" :loading="loadingPassword">
                确认修改密码
              </el-button>
            </el-form-item>
          </el-form>
        </el-collapse-item>
      </el-collapse>

      <el-divider />

      <el-button type="danger" @click="handleLogout" style="width: 100%;">
        退出登录
      </el-button>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const user = ref(null)
const licenseValid = ref(false)
const serverLicenseInfo = ref('')
const activePanels = ref(['password'])

const passwordForm = reactive({
  oldPassword: '',
  newPassword: '',
  confirmPassword: ''
})

const loadingPassword = ref(false)

const accountExpiresLabel = computed(() => {
  if (!user.value) return '—'
  if (user.value.authority === 'admin' || !user.value.expires_at) return '永久'
  return user.value.expires_at
})

function approvalLabel(status) {
  const map = { pending: '待审批', approved: '已通过', rejected: '已拒绝' }
  if (user.value?.authority === 'admin') return '—'
  return map[status] || '已通过'
}

async function loadServerLicense() {
  try {
    const res = await axios.get('/api/auth/license-status')
    const lic = res.data.license || {}
    licenseValid.value = !!lic.valid
    if (lic.valid) {
      serverLicenseInfo.value = `服务器已授权（客户：${lic.customer}｜到期：${lic.expires_at}）`
      authStore.setLicenseState(true, { start: lic.issued_at, end: lic.expires_at })
    } else {
      serverLicenseInfo.value = lic.reason || '服务器许可证无效'
      authStore.setLicenseState(false, lic.reason)
    }
  } catch (e) {
    serverLicenseInfo.value = '无法获取服务器许可证状态'
  }
}

async function loadUser() {
  try {
    const res = await axios.get('/api/auth/me', {
      params: { id: authStore.user?.id }
    })
    if (res.data.success) {
      user.value = res.data.user
      authStore.setUser(res.data.user)
    }
  } catch (e) {
    ElMessage.error('加载用户信息失败')
  }
}

async function handleChangePassword() {
  if (!passwordForm.newPassword) {
    ElMessage.error('新密码不能为空')
    return
  }
  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    ElMessage.error('两次输入的新密码不一致')
    return
  }

  loadingPassword.value = true
  try {
    const res = await axios.put(`/api/auth/users/${authStore.user.id}`, {
      old_password: passwordForm.oldPassword,
      password: passwordForm.newPassword
    })
    if (res.data.success) {
      ElMessage.success('密码修改成功')
      passwordForm.oldPassword = ''
      passwordForm.newPassword = ''
      passwordForm.confirmPassword = ''
    } else {
      ElMessage.error(res.data.message || '密码修改失败')
    }
  } catch (e) {
    ElMessage.error('密码修改失败')
  } finally {
    loadingPassword.value = false
  }
}

function handleLogout() {
  authStore.logout()
  router.push('/login')
}

onMounted(() => {
  loadUser()
  loadServerLicense()
})
</script>

<style scoped>
.user-info h3 {
  margin-bottom: 16px;
  color: var(--el-color-primary);
}
h3 {
  margin-bottom: 12px;
  color: var(--el-color-primary);
}
</style>
