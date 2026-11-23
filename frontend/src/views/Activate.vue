<template>
  <div class="activate-container">
    <div class="activate-card">
      <h1 class="title">{{ APP_NAME }}</h1>
      <p class="subtitle">版本 {{ APP_VERSION }} · 软件激活</p>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="首次使用请先激活"
        description="请将下方机器码发送给软件供应商，获取授权码后粘贴到下方输入框完成激活。"
        class="intro-alert"
      />

      <div class="machine-section">
        <div class="section-label">本机机器码</div>
        <div ref="machineCodeEl" class="machine-code">{{ machineCode || '读取中…' }}</div>
        <el-button type="primary" link :disabled="!machineCode" @click="copyMachineCode">
          复制机器码
        </el-button>
        <p v-if="machineCodeHint" class="machine-hint">{{ machineCodeHint }}</p>
      </div>

      <el-alert
        v-if="fingerprintStable === false"
        type="warning"
        :closable="false"
        show-icon
        title="机器码可能不稳定"
        description="本次采集未拿到稳定系统标识，机器码可能变化，请联系供应商"
        class="unstable-alert"
      />

      <el-alert
        v-if="machineMismatchMessage"
        type="warning"
        :closable="false"
        show-icon
        title="许可证与本机机器码不匹配，请按本机机器码重新申请许可证"
        :description="machineMismatchMessage"
        class="mismatch-alert"
      />

      <el-form @submit.prevent="handleActivate">
        <el-form-item label="授权码">
          <el-input
            v-model="activationCode"
            type="textarea"
            :rows="4"
            placeholder="粘贴供应商提供的授权码（GT-XXXX-XXXX-…）"
            size="large"
          />
        </el-form-item>
        <el-button
          type="primary"
          size="large"
          :loading="loading"
          :disabled="!activationCode.trim()"
          style="width: 100%"
          @click="handleActivate"
        >
          激活并进入软件
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import { APP_NAME, APP_VERSION } from '../config/brand'

const router = useRouter()
const authStore = useAuthStore()

const machineCode = ref('')
const machineCodeHint = ref('')
const fingerprintStable = ref(null)
const machineMismatchMessage = ref('')
const machineCodeEl = ref(null)
const activationCode = ref('')
const loading = ref(false)

onMounted(async () => {
  try {
    const [mcRes, licRes] = await Promise.all([
      axios.get('/api/auth/machine-code'),
      axios.get('/api/auth/license-status'),
    ])
    machineCode.value = mcRes.data.machine_code || ''
    machineCodeHint.value = mcRes.data.machine_code_hint || ''
    fingerprintStable.value = mcRes.data.fingerprint_stable ?? null
    const lic = licRes.data.license || {}
    if (lic.valid) {
      authStore.setLicenseState(true, { start: lic.issued_at, end: lic.expires_at })
      router.replace('/login')
    }
  } catch (e) {
    ElMessage.error('无法连接服务，请确认软件已启动')
  }
})

// 复制失败时退化为「选中机器码」，让用户按 Ctrl+C 手动复制。
function selectMachineCode() {
  const el = machineCodeEl.value
  if (!el || !window.getSelection) return false
  const range = document.createRange()
  range.selectNodeContents(el)
  const selection = window.getSelection()
  selection.removeAllRanges()
  selection.addRange(range)
  return true
}

async function copyMachineCode() {
  if (!machineCode.value) return
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(machineCode.value)
    ElMessage.success('机器码已复制')
  } catch (e) {
    const selected = selectMachineCode()
    ElMessage.warning(selected ? '已选中机器码，请按 Ctrl+C 复制' : '复制失败，请手动选择复制')
  }
}

// 激活失败时把「机器码不匹配」单独突出展示，提示需要重新签发。
function showActivateError(message) {
  const msg = message || '激活失败'
  ElMessage.error(msg)
  machineMismatchMessage.value = msg.includes('机器码') ? msg : ''
}

async function handleActivate() {
  const code = activationCode.value.trim()
  if (!code) return
  loading.value = true
  machineMismatchMessage.value = ''
  try {
    const res = await axios.post('/api/auth/activate', { activation_code: code })
    if (res.data.success) {
      const lic = res.data.license || {}
      authStore.setLicenseState(true, { start: lic.issued_at, end: lic.expires_at })
      ElMessage.success(res.data.message || '激活成功')
      router.push('/login')
    } else {
      showActivateError(res.data.message)
    }
  } catch (e) {
    showActivateError(e.response?.data?.message)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.activate-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--el-bg-color-page, #f0f2f5);
  padding: 24px;
}
.activate-card {
  width: 100%;
  max-width: 520px;
  background: var(--el-bg-color, #fff);
  border-radius: 12px;
  padding: 32px 28px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.08);
}
.title {
  margin: 0;
  text-align: center;
  font-size: 24px;
}
.subtitle {
  margin: 8px 0 20px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}
.intro-alert {
  margin-bottom: 20px;
}
.machine-section {
  text-align: center;
  margin-bottom: 24px;
  padding: 16px;
  background: var(--el-fill-color-light, #f5f7fa);
  border-radius: 8px;
}
.section-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
}
.machine-code {
  font-family: ui-monospace, monospace;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--el-color-primary);
  word-break: break-all;
  margin-bottom: 4px;
}
.machine-hint {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}
.unstable-alert,
.mismatch-alert {
  margin-bottom: 20px;
}
</style>
