<template>
  <div class="login-container">
    <div class="login-card">
      <h1 class="login-title">{{ APP_NAME }}</h1>
      <p class="login-subtitle">版本：{{ buildVersion }}</p>

      <el-form :model="form" :rules="rules" ref="formRef" @submit.prevent="handleLogin">
        <el-form-item prop="id">
          <el-input
            v-model="form.id"
            prefix-icon="User"
            placeholder="请输入账号"
            size="large"
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            prefix-icon="Lock"
            type="password"
            placeholder="请输入密码"
            size="large"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <p class="password-hint">
            密码要求：至少8位，包含大小写字母、数字、特殊字符
          </p>
          <p class="password-hint device-hint">
            普通用户账户与浏览器一一绑定，首次登录将自动绑定本浏览器；请勿将账号借给他人使用。
          </p>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            @click="handleLogin"
            style="width: 100%"
          >
            登录
          </el-button>
        </el-form-item>
        <el-form-item>
          <div style="width: 100%; text-align: center;">
            <router-link to="/signup" class="signup-link">没有账号？注册新账号</router-link>
          </div>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { APP_NAME, APP_VERSION } from '../config/brand'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const router = useRouter()
const authStore = useAuthStore()
const formRef = ref(null)
const loading = ref(false)
const buildVersion = ref(APP_VERSION)

onMounted(async () => {
  // brand.js hardcodes a version that drifts from the real build; fetch the
  // running backend's build_info so the login page shows the true version.
  try {
    const res = await axios.get('/api/version')
    if (res.data?.version) buildVersion.value = res.data.version
  } catch { /* pre-login, best-effort — fall back to APP_VERSION */ }
})

const form = reactive({
  id: '',
  password: '',
})

const rules = {
  id: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码长度不能少于8位', trigger: 'blur' },
  ],
}

async function handleLogin() {
  if (!formRef.value) return
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    loading.value = true
    try {
      const result = await authStore.login(form.id, form.password)
      if (result.success) {
        ElMessage.success(result.message || '登录成功')
        router.push('/')
      } else {
        ElMessage.error(result.message || '登录失败')
      }
    } catch (err) {
      ElMessage.error(err.response?.data?.message || '网络错误')
    } finally {
      loading.value = false
    }
  })
}
</script>

<style scoped>
.password-hint {
  font-size: 12px;
  color: var(--text-light);
  margin: 0;
}
.device-hint {
  margin-top: 6px;
  color: var(--el-text-color-secondary, #909399);
}
.signup-link {
  font-size: 14px;
  color: var(--el-color-primary);
  text-decoration: none;
}
.signup-link:hover {
  text-decoration: underline;
}
</style>
