<template>
  <el-card v-if="qa" class="qa-card" shadow="never">
    <template #header>
      <div class="qa-head">
        <span>形线质量检查</span>
        <el-tag :type="qaStatusTag">{{ qa.status }}</el-tag>
      </div>
    </template>
    <el-alert
      v-if="qa.status === 'QA_LOCAL_ANOMALY'"
      type="warning"
      :closable="false"
      style="margin-bottom: 12px"
    >
      检测到形线局部异常
      <template v-if="qa.suspect_segments?.length">
        <div v-for="seg in qa.suspect_segments" :key="seg.start_index">
          可疑范围：{{ Number(seg.start_s).toFixed(1) }}–{{ Number(seg.end_s).toFixed(1) }} mm
        </div>
      </template>
      该区域与管件总体单调递减趋势不一致，可能由 STP 几何识别或采样误差导致。是否对该区间进行平滑修正？
    </el-alert>
    <el-alert
      v-else-if="qa.status === 'QA_MANUAL_REQUIRED' || qa.status === 'QA_RECHECK_FAILED'"
      type="error"
      :closable="false"
      style="margin-bottom: 12px"
    >
      STP 自动读取的形线数据存在较大异常，自动修正可能导致错误几何结果。请上传人工测量的形线数据 XLSX 文件。
      <template v-if="qa.suspect_segments?.length">
        <div v-for="seg in qa.suspect_segments" :key="`m-${seg.start_index}`">
          识别范围：{{ Number(seg.start_s).toFixed(1) }}–{{ Number(seg.end_s).toFixed(1) }} mm
          <template v-if="Number(seg.max_positive_deviation) > 0">
            ，凸起约 {{ Number(seg.max_positive_deviation).toFixed(2) }} mm
          </template>
        </div>
      </template>
      <div v-if="qa.status === 'QA_RECHECK_FAILED'">自动平滑后形线仍未通过质量检查，建议上传手工测量数据。</div>
    </el-alert>
    <el-alert
      v-else-if="qa.status === 'QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION'"
      type="info"
      :closable="false"
      style="margin-bottom: 12px"
    >
      形线已通过单调性检查。请确认候选直管段（算法结果仅为 candidate，确认后才成为工程定义）。
    </el-alert>
    <el-alert
      v-else-if="qa.status === 'QA_PASS'"
      type="success"
      :closable="false"
      style="margin-bottom: 12px"
    >
      最终 QA PASS。形线来源：{{ qa.profile_source }}
    </el-alert>
    <el-alert
      v-else-if="qa.status === 'QA_INVALID_INPUT'"
      type="error"
      :closable="false"
      style="margin-bottom: 12px"
    >
      基础数据不合法（{{ qa.reason || 'QA_INVALID_INPUT' }}），请检查 STP 读取结果或改用手工 XLSX。
    </el-alert>
    <el-alert
      v-else-if="qa.status === 'QA_MANUAL_DATA_INVALID'"
      type="error"
      :closable="false"
      style="margin-bottom: 12px"
    >
      人工形线数据格式错误，请按行修正后重新上传。
    </el-alert>

    <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom: 12px">
      <el-switch v-model="qaShowRaw" active-text="查看原始数据" />
      <el-switch v-if="canSmoothAnomaly" v-model="qaShowPreview" active-text="建议平滑" />
    </div>

    <div v-if="canSmoothAnomaly" style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom: 12px">
      <el-button @click="handleQaPreviewSmooth">查看原始数据 / 预览建议平滑</el-button>
      <el-button type="primary" @click="handleQaConfirmSmooth">应用建议平滑</el-button>
      <el-button @click="handleQaRejectSmooth">保留原始数据</el-button>
    </div>

    <div v-if="qa.status === 'QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION'">
      <el-empty v-if="!qa.straight_segments?.length" description="未检测到候选直管段" :image-size="48" />
      <el-table
        v-else
        :data="qa.straight_segments"
        border
        stripe
        max-height="360"
        style="margin-bottom: 16px"
        :row-class-name="straightRowClass"
      >
        <el-table-column prop="segment_id" label="段" width="70" />
        <el-table-column label="位置 (mm)" min-width="160">
          <template #default="{ row }">{{ Number(row.start_s).toFixed(2) }}–{{ Number(row.end_s).toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="长度 (mm)" min-width="110">
          <template #default="{ row }">{{ Number(row.length).toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="代表外径 (mm)" min-width="130">
          <template #default="{ row }">{{ Number(row.representative_diameter).toFixed(3) }}</template>
        </el-table-column>
        <el-table-column label="最大波动 (mm)" min-width="130">
          <template #default="{ row }">{{ Number(row.max_deviation).toFixed(3) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag :type="straightStatusType(row.status)" effect="dark" size="small">
              {{ straightStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" @click="handleConfirmStraight(row.segment_id)">确认直管段</el-button>
            <el-button @click="handleRejectStraight(row.segment_id)">取消直管段</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-button type="primary" @click="handleFinalizeStraight">完成本步确认</el-button>
    </div>

    <div class="length-box">
      <div>中心线总长：{{ fmtMm(qa.centerline_length_mm) }} mm</div>
      <div>已确认直管段 L4：{{ fmtMm(qa.L4) }} mm</div>
      <div>形线段（总长 − L4）：{{ fmtMm(qa.formline_length) }} mm</div>
      <div v-if="qa.length_validation">
        长度校验：{{ qa.length_validation.status }}
        （图纸 {{ fmtMm(qa.length_validation.drawing_length) }} / STP {{ fmtMm(qa.length_validation.stp_length) }} mm）
      </div>
    </div>

    <div class="choice-box">
      <div class="choice-title">确认保存前请选择采用哪一种形线</div>
      <p class="hint">自动读取与人工测量可同时保留；保存或进入工艺规划前必须点选其一，且该形线不能为空。</p>
      <div class="choice-meta">
        自动读取：{{ autoPoints }} 点{{ autoReady ? '' : '（为空，不可选）' }}
        ｜ 人工测量：{{ manualPoints }} 点{{ manualReady ? '' : '（未上传或为空）' }}
        ｜ 当前选择：{{ choiceLabel }}
      </div>
      <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:8px">
        <el-button :type="qa.profile_choice === 'auto' ? 'primary' : 'default'" :disabled="!autoReady" @click="handleSelectProfile('auto')">
          采用自动读取形线
        </el-button>
        <el-button :type="qa.profile_choice === 'manual' ? 'primary' : 'default'" :disabled="!manualReady" @click="handleSelectProfile('manual')">
          采用人工测量形线
        </el-button>
      </div>
    </div>

    <div style="margin-top: 12px">
      <p class="hint">
        模板列：<code>centerline_mm</code>（中心线累计长度 mm）与
        <code>outer_circumference_mm</code>（外径周长 mm）。不要同时填写外径。
        控制点不必等距。上传后由独立人工形线模块生成：直管段常值，渐变段 PCHIP，方向统一为大径→小径单调不增。
      </p>
      <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center">
        <el-button @click="downloadQaTemplate">下载 XLSX 模板</el-button>
        <el-upload :auto-upload="false" :show-file-list="false" accept=".xlsx" :on-change="handleManualXlsx">
          <el-button type="primary">上传人工测量 XLSX</el-button>
        </el-upload>
      </div>
      <el-table v-if="xlsxErrors.length" :data="xlsxErrors" border size="small" style="margin-top:12px">
        <el-table-column prop="row" label="行" width="70" />
        <el-table-column prop="column" label="字段" width="200" />
        <el-table-column prop="reason" label="问题" />
        <el-table-column prop="how_to_fix" label="如何修改" />
      </el-table>
    </div>
  </el-card>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ppApi } from '../api/pp'

const props = defineProps({
  sessionId: { type: String, default: '' },
})
const emit = defineEmits(['update', 'display'])

const qa = ref(null)
const qaShowRaw = ref(true)
const qaShowPreview = ref(false)
const xlsxErrors = ref([])

const qaStatusTag = computed(() => {
  const s = qa.value?.status
  if (s === 'QA_PASS') return 'success'
  if (s === 'QA_LOCAL_ANOMALY' || s === 'QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION') return 'warning'
  if (!s) return 'info'
  return 'danger'
})
const canSmoothAnomaly = computed(() => {
  const s = qa.value?.status
  if (s === 'QA_LOCAL_ANOMALY') return true
  return s === 'QA_MANUAL_REQUIRED' && (qa.value?.suspect_segments || []).length > 0
})
const autoPoints = computed(() => {
  const p = qa.value?.auto_profile || qa.value?.working_profile || qa.value?.raw_profile
  return p?.s_mm?.length || 0
})
const manualPoints = computed(() => qa.value?.manual_profile?.s_mm?.length || 0)
const autoReady = computed(() => autoPoints.value >= 2)
const manualReady = computed(() => manualPoints.value >= 2)
const choiceLabel = computed(() => {
  if (qa.value?.profile_choice === 'auto') return '自动读取'
  if (qa.value?.profile_choice === 'manual') return '人工测量'
  return '未选择'
})

function fmtMm(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(1) : '—'
}

function straightStatusType(status) {
  if (status === 'confirmed') return 'success'
  if (status === 'cancelled') return 'info'
  return 'danger'
}

function straightStatusLabel(status) {
  if (status === 'confirmed') return '已确认'
  if (status === 'cancelled') return '已取消'
  return '未确认'
}

function straightRowClass({ row }) {
  if (row.status === 'confirmed') return 'straight-row-confirmed'
  if (row.status === 'cancelled') return 'straight-row-cancelled'
  return 'straight-row-pending'
}

function publish() {
  emit('update', qa.value)
  emit('display', { showRaw: qaShowRaw.value, showPreview: qaShowPreview.value })
}

async function loadQa() {
  if (!props.sessionId) {
    qa.value = null
    publish()
    return
  }
  try {
    const res = await ppApi.qa(props.sessionId)
    if (res.data.success) {
      qa.value = res.data.qa || null
      xlsxErrors.value = qa.value?.details || qa.value?.xlsx_errors || []
      if (qa.value?.processed_profile) qaShowPreview.value = true
    }
  } catch {
    qa.value = null
  }
  publish()
}

function applyQa(next) {
  qa.value = next || null
  xlsxErrors.value = qa.value?.details || qa.value?.xlsx_errors || []
  if (qa.value?.processed_profile) qaShowPreview.value = true
  publish()
}

async function handleQaPreviewSmooth() {
  try {
    const res = await ppApi.qaPreviewSmooth(props.sessionId)
    if (res.data.success) {
      qa.value = res.data.qa
      qaShowRaw.value = true
      qaShowPreview.value = true
      publish()
      ElMessage.success('已加载原始数据与建议平滑预览，请确认后再应用')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '预览失败')
  }
}

async function handleQaConfirmSmooth() {
  try {
    const res = await ppApi.qaConfirmSmooth(props.sessionId)
    if (res.data.success) {
      qa.value = res.data.qa
      if (qa.value?.status === 'QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION') {
        qaShowPreview.value = false
      }
      publish()
      ElMessage.success('已应用建议平滑并复检')
    } else {
      ElMessage.error(res.data.message || '平滑失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '平滑失败')
  }
}

async function handleQaRejectSmooth() {
  try {
    const res = await ppApi.qaRejectSmooth(props.sessionId)
    if (res.data.success) {
      qa.value = res.data.qa
      qaShowPreview.value = false
      publish()
      ElMessage.warning('已保留原始数据。原始形线仍未通过单调性检查，不能作为最终合格形线。')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '操作失败')
  }
}

async function handleConfirmStraight(segmentId) {
  try {
    const res = await ppApi.qaConfirmStraight(props.sessionId, segmentId)
    if (res.data.success) {
      qa.value = res.data.qa
      publish()
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '确认失败')
  }
}

async function handleRejectStraight(segmentId) {
  try {
    const res = await ppApi.qaRejectStraight(props.sessionId, segmentId)
    if (res.data.success) {
      qa.value = res.data.qa
      publish()
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '取消失败')
  }
}

async function handleFinalizeStraight() {
  try {
    const confirmed = (qa.value?.straight_segments || [])
      .filter(s => s.status === 'confirmed')
      .map(s => s.segment_id)
    const res = await ppApi.qaFinalizeStraight(props.sessionId, confirmed)
    if (res.data.success) {
      qa.value = res.data.qa
      qaShowPreview.value = false
      qaShowRaw.value = true
      publish()
      ElMessage.success('形线 QA 已通过')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '确认失败')
  }
}

async function handleSelectProfile(choice) {
  try {
    const res = await ppApi.qaSelectProfile(props.sessionId, choice)
    if (res.data.success) {
      qa.value = res.data.qa
      publish()
      ElMessage.success(choice === 'manual' ? '已选择人工测量形线' : '已选择自动读取形线')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '选择形线失败')
  }
}

async function downloadQaTemplate() {
  try {
    const res = await ppApi.qaDownloadTemplate()
    const blob = new Blob([res.data], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'manual_profile_template.xlsx'
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('模板下载失败')
  }
}

async function handleManualXlsx(file) {
  const raw = file.raw
  if (!raw) return
  const formData = new FormData()
  formData.append('file', raw)
  formData.append('session_id', props.sessionId)
  try {
    const res = await ppApi.qaUploadXlsx(formData)
    if (res.data.success) {
      qa.value = res.data.qa
      xlsxErrors.value = []
      publish()
      ElMessage.success('人工形线已上传，正在进行质量检查')
    } else {
      qa.value = res.data.qa || qa.value
      xlsxErrors.value = res.data.details || []
      publish()
      ElMessage.error(res.data.message || '人工数据格式错误')
    }
  } catch (e) {
    const d = e.response?.data
    qa.value = d?.qa || qa.value
    xlsxErrors.value = d?.details || []
    publish()
    ElMessage.error(d?.message || '上传失败')
  }
}

watch(() => props.sessionId, (id) => {
  if (id) loadQa()
  else {
    qa.value = null
    publish()
  }
}, { immediate: true })

watch([qaShowRaw, qaShowPreview], publish)

defineExpose({ loadQa, applyQa, qa, qaShowRaw, qaShowPreview })
</script>

<style scoped>
.qa-card { margin-bottom: 12px; height: 100%; }
.qa-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 16px; font-weight: 700; }
.hint { color: #606266; font-size: 13px; margin-bottom: 8px; }
.length-box, .choice-box {
  margin: 14px 0;
  padding: 14px 16px;
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  font-size: 14px;
  color: #303133;
  line-height: 1.8;
}
.choice-title { font-weight: 700; font-size: 15px; margin-bottom: 6px; }
.choice-meta { color: #606266; }
.qa-card :deep(.straight-row-pending td) {
  background: rgba(245, 108, 108, 0.08);
}
.qa-card :deep(.straight-row-confirmed td) {
  background: rgba(103, 194, 58, 0.1);
}
.qa-card :deep(.straight-row-cancelled td) {
  background: #f5f7fa;
}
</style>
