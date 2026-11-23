<template>
  <el-dialog
    v-model="visible"
    title="登录已失效"
    width="430px"
    append-to-body
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
  >
    <el-alert
      type="warning"
      :closable="false"
      :title="message"
      description="为避免刷新页面丢失当前填写内容，请在这里直接重新登录；登录后页面保持原样，可继续操作。"
      show-icon
    />
    <el-form class="reauth-form" label-width="64px" @submit.prevent>
      <el-form-item label="账号">
        <el-input v-model="form.id" placeholder="登录账号" autocomplete="username" />
      </el-form-item>
      <el-form-item label="密码">
        <el-input
          v-model="form.password"
          type="password"
          show-password
          placeholder="登录密码"
          autocomplete="current-password"
          @keyup.enter="submit"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="goLoginPage">转到登录页</el-button>
      <el-button type="primary" :loading="busy" @click="submit">重新登录</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import { useRouter } from 'vue-router'
import { resetAuthExpiredFlag } from '@/api'

const router = useRouter()
const visible = ref(false)
const busy = ref(false)
const message = ref('登录状态已失效')
const form = reactive({ id: '', password: '' })

function rememberAccount() {
  try {
    const user = JSON.parse(localStorage.getItem('user') || 'null')
    if (user?.id && !form.id) form.id = String(user.id)
  } catch (_) { /* ignore */ }
}

function onExpired(event) {
  rememberAccount()
  message.value = event?.detail?.message || '登录状态已失效'
  form.password = ''
  visible.value = true
}

function onRestored() {
  visible.value = false
  busy.value = false
}

async function submit() {
  if (busy.value) return
  if (!form.id || !form.password) {
    ElMessage.warning('请填写账号和密码')
    return
  }
  busy.value = true
  try {
    const res = await axios.post('/api/auth/login', { id: form.id, password: form.password })
    const d = res.data || {}
    if (!d.success || !d.token) {
      ElMessage.error(d.message || '登录失败')
      return
    }
    localStorage.setItem('user', JSON.stringify(d.user))
    localStorage.setItem('authToken', d.token)
    localStorage.setItem('licenseValid', JSON.stringify(d.license_valid))
    localStorage.setItem('licenseInfo', JSON.stringify(d.license_info ?? null))
    resetAuthExpiredFlag()
    ElMessage.success('已重新登录，当前页面内容未丢失，可继续操作')
    visible.value = false
    window.dispatchEvent(new CustomEvent('gtapp:auth-restored', { detail: { user: d.user } }))
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '登录失败，请检查账号密码或网络')
  } finally {
    busy.value = false
  }
}

function goLoginPage() {
  visible.value = false
  resetAuthExpiredFlag()
  router.push('/login').catch(() => { window.location.href = '/login' })
}

onMounted(() => {
  window.addEventListener('gtapp:auth-expired', onExpired)
  window.addEventListener('gtapp:auth-restored', onRestored)
})

onUnmounted(() => {
  window.removeEventListener('gtapp:auth-expired', onExpired)
  window.removeEventListener('gtapp:auth-restored', onRestored)
})
</script>

<style scoped>
.reauth-form {
  margin-top: 16px;
}
</style>
