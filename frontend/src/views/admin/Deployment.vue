<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <span>部署与授权</span>
      </template>

      <el-alert
        title="本页用于内网部署配置与授权管理。日常激活请在软件启动时的激活页完成；此处可用于更换授权码或上传 license.dat 备份。"
        type="info"
        :closable="false"
        style="margin-bottom: 20px;"
      />

      <!-- 机器码 -->
      <h3>本机机器码</h3>
      <el-descriptions :column="1" border style="margin-bottom: 20px;">
        <el-descriptions-item label="机器码">
          <span class="machine-code">{{ info.machine_code || '—' }}</span>
          <el-button
            v-if="info.machine_code"
            type="primary"
            link
            style="margin-left: 12px;"
            @click="copyText(info.machine_code)"
          >
            复制
          </el-button>
        </el-descriptions-item>
        <el-descriptions-item label="机器码稳定状态">
          <el-tag v-if="info.fingerprint_stable === true" type="success" size="small">
            已锚定（重启/换网络不变）
          </el-tag>
          <el-tag v-else-if="info.fingerprint_stable === false" type="warning" size="small">
            不稳定（机器码可能变化）
          </el-tag>
          <el-tag v-else type="info" size="small">未知</el-tag>
          <span class="fingerprint-source">来源：{{ fingerprintSourceLabel }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="硬件绑定">
          {{ info.bind_server_machine ? '已开启（许可证须绑定本机）' : '未开启（托管模式）' }}
        </el-descriptions-item>
      </el-descriptions>
      <p v-if="info.machine_code_hint" class="hint machine-hint">{{ info.machine_code_hint }}</p>

      <!-- 许可证状态 -->
      <h3>软件授权</h3>
      <el-alert
        v-if="info.license"
        :title="licenseAlertTitle"
        :type="info.license.valid ? 'success' : 'error'"
        :closable="false"
        style="margin-bottom: 16px;"
      />

      <div class="license-upload">
        <p class="hint">粘贴供应商提供的短授权码（GT- 开头），或上传 license.dat 备份文件。</p>
        <el-input
          v-model="activationCodeText"
          type="textarea"
          :rows="3"
          placeholder="GT-XXXXX-XXXXX-…"
          style="margin-bottom: 12px;"
        />
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!activationCodeText.trim()"
          @click="submitActivationCode"
        >
          安装授权码
        </el-button>

        <el-divider>或上传 license.dat</el-divider>

        <el-upload
          drag
          :auto-upload="false"
          :limit="1"
          accept=".dat,.txt"
          :on-change="onFileChange"
          :on-remove="() => { licenseFile = null }"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">将 license.dat 拖到此处，或<em>点击选择</em></div>
        </el-upload>
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!licenseFile"
          style="margin-top: 12px;"
          @click="uploadLicense"
        >
          安装许可证
        </el-button>
      </div>

      <el-divider />

      <!-- 网络访问 -->
      <h3>内网访问配置</h3>
      <p class="hint">
        当前访问端口：<strong>{{ access.port || currentPort }}</strong>（修改端口：编辑安装目录下
        <code>.env</code> 中 <code>APP_PORT</code>，保存后从开始菜单重新「启动软件」）
      </p>

      <div class="access-panel">
        <div class="access-row">
          <span class="access-label">本机访问</span>
          <code class="access-url">{{ access.localhost_url || localFallbackUrl }}</code>
          <el-button type="primary" link @click="copyText(access.localhost_url || localFallbackUrl)">
            复制
          </el-button>
        </div>

        <div v-if="access.lan_urls?.length" class="lan-block">
          <p class="access-label">局域网访问（其他电脑浏览器打开下列地址）</p>
          <div
            v-for="(url, idx) in access.lan_urls"
            :key="url"
            class="access-row"
          >
            <span class="access-ip">{{ access.lan_ips[idx] }}</span>
            <code class="access-url">{{ url }}</code>
            <el-button type="primary" link @click="copyText(url)">复制</el-button>
          </div>
        </div>
        <el-alert
          v-else
          type="warning"
          :closable="false"
          show-icon
          title="未能自动检测到局域网 IP"
          description="请在本机运行 ipconfig 查看 IPv4 地址，其他电脑访问：http://&lt;该IP&gt;:端口"
          style="margin-top: 12px;"
        />
      </div>

      <el-form :model="networkForm" label-width="160px" style="max-width: 640px; margin-top: 20px;">
        <el-form-item label="启用 IP 白名单">
          <el-switch v-model="networkForm.ip_allowlist_enabled" />
          <span class="form-hint">关闭后任何 IP 均可访问（仅建议排障时临时关闭）</span>
        </el-form-item>
        <el-form-item label="允许访问的网段">
          <el-input
            v-model="networkForm.allowed_cidrs"
            type="textarea"
            :rows="3"
            placeholder="例如：192.168.0.0/16,10.0.0.0/8"
            :disabled="!networkForm.ip_allowlist_enabled"
          />
          <p class="form-hint">CIDR 格式，多个网段用英文逗号分隔。默认放行常见私有局域网网段。</p>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="savingNetwork" @click="saveNetwork">
            保存网络配置
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import axios from 'axios'
import { useAuthStore } from '../../stores/auth'

const authStore = useAuthStore()

const info = reactive({
  machine_code: '',
  machine_code_hint: '',
  fingerprint_stable: null,
  fingerprint_source: '',
  fingerprint_anchored: false,
  bind_server_machine: false,
  license: null,
  network: null,
})

const access = reactive({
  port: null,
  localhost_url: '',
  lan_ips: [],
  lan_urls: [],
})

const licenseFile = ref(null)
const activationCodeText = ref('')
const uploading = ref(false)
const savingNetwork = ref(false)

const networkForm = reactive({
  allowed_cidrs: '',
  ip_allowlist_enabled: true,
})

const currentPort = computed(() => access.port || window.location.port || '5880')

const localFallbackUrl = computed(() => `http://127.0.0.1:${currentPort.value}`)

const licenseAlertTitle = computed(() => {
  const lic = info.license
  if (!lic) return '无法读取许可证状态'
  if (lic.valid) {
    return `已授权（客户：${lic.customer}｜到期：${lic.expires_at}，剩余 ${lic.days_left ?? '—'} 天）`
  }
  return lic.reason || '未授权'
})

// 指纹来源的中文展示（后端 source 取值见 licensing/fingerprint.py 的 KIND_*）。
const fingerprintSourceLabel = computed(() => {
  const labels = {
    'machine-guid': '系统安装标识（MachineGuid）',
    anchor: '已锚定指纹',
    'cim-uuid': '整机 UUID',
    'nic-mac': '物理网卡 MAC',
    platform: '系统平台标识',
    hostname: '主机名（兜底）',
    env: '环境变量注入（宿主机）',
    unknown: '未知',
  }
  const key = info.fingerprint_source || 'unknown'
  return labels[key] || key
})

async function copyText(text) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch (e) {
    ElMessage.error('复制失败，请手动选择复制')
  }
}

function onFileChange(uploadFile) {
  licenseFile.value = uploadFile?.raw || null
}

async function loadInfo() {
  try {
    const res = await axios.get('/api/auth/deployment-info')
    if (res.data.success) {
      info.machine_code = res.data.machine_code
      info.machine_code_hint = res.data.machine_code_hint || ''
      info.fingerprint_stable = res.data.fingerprint_stable ?? null
      info.fingerprint_source = res.data.fingerprint_source || ''
      info.fingerprint_anchored = res.data.fingerprint_anchored === true
      info.bind_server_machine = res.data.bind_server_machine
      info.license = res.data.license
      info.network = res.data.network
      if (res.data.access) {
        access.port = res.data.access.port
        access.localhost_url = res.data.access.localhost_url || ''
        access.lan_ips = res.data.access.lan_ips || []
        access.lan_urls = res.data.access.lan_urls || []
      }
      if (res.data.network) {
        networkForm.allowed_cidrs = res.data.network.allowed_cidrs || ''
        networkForm.ip_allowlist_enabled = res.data.network.ip_allowlist_enabled !== false
      }
      if (res.data.license?.valid) {
        authStore.setLicenseState(true, {
          start: res.data.license.issued_at,
          end: res.data.license.expires_at,
        })
      }
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '加载部署信息失败')
  }
}

async function submitActivationCode() {
  const code = activationCodeText.value.trim()
  if (!code) return
  uploading.value = true
  try {
    const res = await axios.post('/api/auth/upload-license', { activation_code: code })
    if (res.data.success) {
      ElMessage.success(res.data.message || '授权码安装成功')
      info.license = res.data.license
      authStore.setLicenseState(true, {
        start: res.data.license.issued_at,
        end: res.data.license.expires_at,
      })
      activationCodeText.value = ''
    } else {
      ElMessage.error(res.data.message || '授权码无效')
      info.license = res.data.license
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '安装失败')
    if (e.response?.data?.license) {
      info.license = e.response.data.license
    }
  } finally {
    uploading.value = false
  }
}

async function uploadLicense() {
  if (!licenseFile.value) return
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', licenseFile.value)
    const res = await axios.post('/api/auth/upload-license', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    if (res.data.success) {
      ElMessage.success(res.data.message || '许可证安装成功')
      info.license = res.data.license
      authStore.setLicenseState(true, {
        start: res.data.license.issued_at,
        end: res.data.license.expires_at,
      })
      licenseFile.value = null
    } else {
      ElMessage.error(res.data.message || '许可证无效')
      info.license = res.data.license
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '上传失败')
    if (e.response?.data?.license) {
      info.license = e.response.data.license
    }
  } finally {
    uploading.value = false
  }
}

async function saveNetwork() {
  savingNetwork.value = true
  try {
    const res = await axios.post('/api/auth/network-config', {
      allowed_cidrs: networkForm.allowed_cidrs,
      ip_allowlist_enabled: networkForm.ip_allowlist_enabled,
    })
    if (res.data.success) {
      ElMessage.success(res.data.message || '已保存')
      info.network = res.data.network
    } else {
      ElMessage.error(res.data.message || '保存失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '保存失败')
  } finally {
    savingNetwork.value = false
  }
}

onMounted(loadInfo)
</script>

<style scoped>
h3 {
  margin: 0 0 12px;
  color: var(--el-color-primary);
  font-size: 15px;
}
.machine-code {
  font-family: ui-monospace, monospace;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.05em;
}
.fingerprint-source {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.hint, .form-hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin: 0 0 8px;
  line-height: 1.6;
}
.access-panel {
  margin: 12px 0 0;
  padding: 16px;
  background: var(--el-fill-color-light, #f5f7fa);
  border-radius: 8px;
  max-width: 720px;
}
.access-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-bottom: 10px;
}
.access-row:last-child {
  margin-bottom: 0;
}
.access-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  min-width: 72px;
}
.lan-block {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--el-border-color);
}
.lan-block .access-label {
  display: block;
  margin-bottom: 10px;
  min-width: unset;
}
.access-ip {
  font-family: ui-monospace, monospace;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  min-width: 110px;
}
.access-url {
  flex: 1;
  min-width: 200px;
  font-family: ui-monospace, monospace;
  font-size: 14px;
  background: #fff;
  padding: 6px 10px;
  border-radius: 4px;
  border: 1px solid var(--el-border-color-lighter);
}
.access-list {
  margin: 8px 0 0;
  padding-left: 20px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.access-list code {
  background: var(--el-fill-color-light);
  padding: 2px 6px;
  border-radius: 4px;
}
.license-upload {
  max-width: 480px;
}
</style>
