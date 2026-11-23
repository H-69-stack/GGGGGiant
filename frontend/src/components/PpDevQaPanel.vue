<template>
  <el-card v-if="enabled" class="dev-qa-card" shadow="never">
    <template #header>
      <div class="dev-head">
        <span>DEV · Profile QA</span>
        <el-tag size="small" type="warning">development only</el-tag>
      </div>
    </template>
    <p class="muted">当前测试文件</p>
    <p class="file">{{ stats.filename || '（尚未上传 / 加载）' }}</p>
    <div class="grid">
      <div><span>Centerline</span><b>{{ fmt(stats.centerline_mm, 2) }} mm</b></div>
      <div><span>Samples</span><b>{{ stats.samples ?? '—' }}</b></div>
      <div><span>QA</span><b>{{ stats.qa_status || '—' }}</b></div>
      <div><span>Anomaly points</span><b>{{ pct(stats.anomaly_point_ratio) }}</b></div>
      <div><span>Anomaly length</span><b>{{ pct(stats.anomaly_length_ratio) }}</b></div>
      <div><span>Suspect segments</span><b>{{ stats.suspect_count ?? '—' }}</b></div>
      <div><span>Straight</span><b>{{ stats.straight_count ?? '—' }}</b></div>
      <div><span>QA 耗时</span><b>{{ fmt(stats.qa_ms, 0) }} ms</b></div>
      <div><span>STP 读取</span><b>{{ fmt(stats.stp_read_s, 2) }} s</b></div>
      <div><span>STP / QA 次数</span><b>{{ stats.stp_read_count || 0 }} / {{ stats.qa_count || 0 }}</b></div>
    </div>
    <p v-if="stats.present && !stats.cache_valid" class="warn">STP 文件指纹已变化，请「重新读取 STP + QA」。</p>
    <div class="btns">
      <el-button type="primary" :loading="busyQa" :disabled="!stats.present" @click="rerunQa">
        重新运行 QA
      </el-button>
      <el-button :loading="busyStp" :disabled="!stats.present" @click="rereadStp">
        重新读取 STP + QA
      </el-button>
    </div>
    <div v-if="fixtures.length" class="fixtures">
      <p class="muted">开发测试样本（仅 fixtures/stp）</p>
      <el-button
        v-for="name in fixtures"
        :key="name"
        size="small"
        :loading="busyFix === name"
        @click="loadFixture(name)"
      >{{ name.replace(/\.stp$/i, '') }}</el-button>
    </div>
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ppApi } from '../api/pp'

const emit = defineEmits(['restore', 'refreshed'])

const enabled = ref(false)
const busyQa = ref(false)
const busyStp = ref(false)
const busyFix = ref('')
const fixtures = ref([])
const stats = reactive({})

function fmt(v, n) {
  if (v == null || v === '') return '—'
  const x = Number(v)
  return Number.isFinite(x) ? x.toFixed(n) : '—'
}
function pct(v) {
  if (v == null) return '—'
  return `${(Number(v) * 100).toFixed(2)} %`
}
function applyStatus(d) {
  Object.keys(stats).forEach((k) => { delete stats[k] })
  Object.assign(stats, d || {})
}

async function refreshStatus() {
  try {
    const res = await ppApi.devStatus()
    if (!res.data?.success) {
      enabled.value = false
      return
    }
    enabled.value = true
    applyStatus(res.data)
    fixtures.value = res.data.fixtures || []
  } catch {
    enabled.value = false
  }
}

async function rerunQa() {
  busyQa.value = true
  try {
    const res = await ppApi.devRerunQa()
    if (!res.data?.success) {
      ElMessage.error(res.data?.message || '重新运行 QA 失败')
      return
    }
    applyStatus(res.data)
    emit('refreshed', res.data)
    ElMessage.success(`QA-only ${Number(res.data.elapsed_ms || 0).toFixed(0)} ms（未解析 STP）`)
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '重新运行 QA 失败')
  } finally {
    busyQa.value = false
  }
}

async function rereadStp() {
  busyStp.value = true
  try {
    const res = await ppApi.devRereadStp()
    if (!res.data?.success) {
      ElMessage.error(res.data?.message || '重新读取 STP 失败')
      return
    }
    applyStatus(res.data)
    emit('restore', res.data)
    ElMessage.success(`STP+QA ${Number(res.data.stp_read_s || 0).toFixed(1)} s`)
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '重新读取 STP 失败')
  } finally {
    busyStp.value = false
  }
}

async function loadFixture(name) {
  busyFix.value = name
  try {
    const res = await ppApi.devLoadFixture(name)
    if (!res.data?.success) {
      ElMessage.error(res.data?.message || '加载 fixture 失败')
      return
    }
    applyStatus(res.data)
    emit('restore', res.data)
    ElMessage.success(`已加载 ${name}`)
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '加载 fixture 失败')
  } finally {
    busyFix.value = ''
  }
}

onMounted(async () => {
  await refreshStatus()
  if (enabled.value && stats.session_id) {
    emit('restore', { session_id: stats.session_id, ...stats })
  }
})

defineExpose({ refreshStatus })
</script>

<style scoped>
.dev-qa-card { margin-bottom: 20px; border: 1px dashed #e6a23c; }
.dev-head { display: flex; justify-content: space-between; align-items: center; }
.muted { color: #909399; font-size: 12px; margin: 0 0 4px; }
.file { font-size: 16px; font-weight: 600; margin: 0 0 12px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; margin-bottom: 12px; font-size: 13px; }
.grid span { color: #909399; margin-right: 8px; }
.warn { color: #e6a23c; font-size: 12px; }
.btns { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.fixtures { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
</style>
