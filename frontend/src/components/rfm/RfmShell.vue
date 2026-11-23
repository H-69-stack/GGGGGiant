<template>
  <div class="rfm-shell">
    <el-alert
      v-if="!rfmStore.hasSession"
      title="请先在「加载数据」页面选择并加载工艺设计"
      type="warning"
      :closable="false"
      style="margin-bottom: 12px"
    />

    <!-- Data summary -->
    <el-card v-if="rfmStore.hasSession" class="section-card">
      <template #header><span>已加载工艺设计</span></template>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="工艺名称">{{ rfmStore.displayName }}</el-descriptions-item>
        <el-descriptions-item label="车种规格">{{ rfmStore.vehicleSpec }}</el-descriptions-item>
        <el-descriptions-item label="抽管方式">{{ rfmStore.ppMethod }}</el-descriptions-item>
        <el-descriptions-item label="管件号">{{ rfmStore.tubeNumber }}</el-descriptions-item>
        <el-descriptions-item label="中心线长度">{{ rfmStore.centerlineLength?.toFixed(1) }} mm</el-descriptions-item>
        <el-descriptions-item label="壁厚数据">{{ rfmStore.wallThicknessData.length }} 条</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- Process design params display -->
    <el-card v-if="rfmStore.hasSession && showParams" class="section-card">
      <template #header><span>工艺设计参数</span></template>
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="L1 (扩口段长度)">{{ rfmStore.L1?.toFixed(1) }} mm</el-descriptions-item>
        <el-descriptions-item label="T3 (壁厚)">{{ rfmStore.T3?.toFixed(2) }} mm</el-descriptions-item>
        <el-descriptions-item label="D1 (扩口段大径)">{{ rfmStore.D1?.toFixed(2) }} mm</el-descriptions-item>
        <el-descriptions-item label="D2 (TP抽段小径)">{{ rfmStore.D2?.toFixed(2) }} mm</el-descriptions-item>
        <el-descriptions-item label="D3 (直线段直径)">{{ rfmStore.D3?.toFixed(2) }} mm</el-descriptions-item>
        <el-descriptions-item label="L4 (直线段长度)">{{ rfmStore.L4?.toFixed(1) }} mm</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- Operation config (slot) -->
    <el-card
      v-if="rfmStore.hasSession"
      class="section-card"
      v-loading="generating"
      :element-loading-text="generatingStage"
      element-loading-background="rgba(255,255,255,0.85)"
    >
      <template #header><span>操作参数</span></template>
      <el-alert v-if="analysisWarnings" :title="analysisWarnings" type="warning" :closable="false" style="margin-bottom:12px" />
      <div class="entity-route-row">
        <span class="entity-route-label">实体化路线</span>
        <el-select v-model="entityRoute" size="small" style="width: 230px">
          <el-option v-for="opt in routeOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
        </el-select>
        <span class="entity-route-hint">{{ routeHint }}</span>
        <el-button
          v-if="props.operation === 'shaping' && entityRoute === '121'"
          size="small"
          text
          @click="remeasureV1"
        >重新测量 v1</el-button>
      </div>
      <div v-if="v1Task && v1Task.status !== 'cached'" class="v1-status">
        <el-progress
          :percentage="v1Task.progress || 0"
          :stroke-width="6"
          :status="v1Task.status === 'error' ? 'exception' : undefined"
          style="width: 220px"
        />
        <span class="v1-status-text">{{ v1Task.message || '准备 v1 口径…' }}</span>
        <el-button
          v-if="v1Task.status === 'running'"
          size="small"
          text
          type="danger"
          @click="cancelV1"
        >取消</el-button>
      </div>
      <slot :config="config" />
      <MoldOutputOptions :config="config" />
      <div style="margin-top:12px; display:flex; gap:8px">
        <el-button type="primary" @click="handleGenerate" :loading="generating">
          {{ props.operation === 'belling' ? '生成芯轴与型腔' : '生成模型' }}
        </el-button>
        <el-button v-if="sessionReady" @click="regenerate" :loading="generating">
          重新生成
        </el-button>
      </div>
    </el-card>

    <!-- Result -->
    <el-card v-if="sessionReady" class="section-card">
      <template #header><span>生成结果<span v-if="rfmStore.tubeNumber"> · {{ rfmStore.tubeNumber }}</span></span></template>
      <el-alert v-if="configChanged" title="参数已修改，请重新生成后再保存或下载" type="warning" :closable="false" />
      <el-alert
        v-if="entityInfo && entityInfo.applied"
        type="success"
        :closable="false"
        style="margin-bottom:10px"
        :title="`已实体化：路线 ${entityInfo.route}；${entityInfo.faces} 面 / ${entityInfo.solids} 实体 / 分型边 ${entityInfo.parting_edges} 条 / ${entityInfo.valid ? '实体有效' : '实体无效'}；体积 ${fmtVolume(entityInfo.volume_after)} mm³（原 ${fmtVolume(entityInfo.volume_before)} mm³）`"
        :description="entityDetail(entityInfo)"
      />
      <el-alert
        v-else-if="entityInfo && entityInfo.error"
        type="warning"
        :closable="false"
        style="margin-bottom:10px"
        :title="`实体化未生效（保留原模型）：${entityInfo.error}`"
      />
      <p v-if="resultInfo.mold_info && (props.operation !== 'belling' || activePreviewTab === 'mold')">模体 XYZ：{{ resultInfo.mold_info.size_xyz_mm.map(v => v.toFixed(1)).join(' × ') }} mm；最小单侧余量：{{ resultInfo.mold_info.minimum_margin_mm.toFixed(2) }} mm。</p>
      <p v-if="resultInfo.section_summary">截面来源：{{ resultInfo.section_summary.source }}；原始站位：{{ resultInfo.section_summary.stations }} 个。</p>
      <p v-if="resultInfo.section_summary?.full_axes_min_mm">生成截面 X/Y 全轴长范围：{{ resultInfo.section_summary.full_axes_min_mm.map(v => v.toFixed(3)).join(' / ') }} 至 {{ resultInfo.section_summary.full_axes_max_mm.map(v => v.toFixed(3)).join(' / ') }} mm。</p>
      <p v-if="resultInfo.quality">曲面自检：侧面接缝最大 {{ resultInfo.quality.max_lateral_seam_deg.toFixed(2) }}°、最小面面积 {{ resultInfo.quality.min_face_area_mm2.toFixed(2) }} mm²（{{ resultInfo.quality.status === 'ok' ? '通过' : '有提示' }}）。</p>
      <el-descriptions v-if="props.operation !== 'belling'" :column="2" border size="small" style="margin-bottom:12px">
        <el-descriptions-item label="顶点数">{{ resultInfo.vertices?.toLocaleString() }}</el-descriptions-item>
        <el-descriptions-item label="面数">{{ resultInfo.faces?.toLocaleString() }}</el-descriptions-item>
      </el-descriptions>

      <!-- 3D Preview — operations with paired models use two viewers -->
      <div v-if="resultInfo.model_kind === 'mold' && props.operation !== 'belling'" class="viewer-wrap">
        <el-switch v-model="cutaway" active-text="剖开查看内部（仅影响预览）" />
        <StlViewer :key="previewKey" :url="previewUrl" :cutaway="cutaway" height="440" />
      </div>
      <div v-else-if="props.operation === 'shaping'">
        <div class="viewer-wrap">
          <StlViewer v-if="sessionReady" ref="outerViewerRef" :key="outerPreviewKey" :url="outerPreviewUrl" height="380" />
        </div>
      </div>
      <div v-else-if="props.operation === 'belling'">
        <p v-if="mandrelInfo && activePreviewTab === 'mandrel'">芯轴：型线区间 0～{{ mandrelInfo.profile_interval_mm[1].toFixed(3) }} mm；实际长度 {{ mandrelInfo.model_length_mm.toFixed(3) }} mm；首端直径 {{ mandrelInfo.diameters_mm[0].toFixed(3) }} mm；末端直径 {{ mandrelInfo.diameters_mm.at(-1).toFixed(3) }} mm。</p>
        <el-tag v-if="entityMandrelInfo?.applied && activePreviewTab === 'mandrel'" type="success" size="small" style="margin-bottom:6px">
          芯轴已实体化（{{ entityMandrelInfo.route }}）：{{ entityMandrelInfo.faces }} 面 / {{ entityMandrelInfo.solids }} 实体 / 分型边 {{ entityMandrelInfo.parting_edges }} 条 / 体积 {{ fmtVolume(entityMandrelInfo.volume_after) }} mm³
        </el-tag>
        <p v-if="cavityInfo && activePreviewTab === 'cavity'">型腔：全长 {{ cavityInfo.model_length_mm.toFixed(3) }} mm；首端直径 {{ cavityInfo.diameters_mm[0].toFixed(3) }} mm；末端直径 {{ cavityInfo.diameters_mm.at(-1).toFixed(3) }} mm。</p>
        <el-tag v-if="entityCavityInfo?.applied && activePreviewTab === 'cavity'" type="success" size="small" style="margin-bottom:6px">
          型腔已实体化（{{ entityCavityInfo.route }}）：{{ entityCavityInfo.faces }} 面 / {{ entityCavityInfo.solids }} 实体 / 分型边 {{ entityCavityInfo.parting_edges }} 条 / 体积 {{ fmtVolume(entityCavityInfo.volume_after) }} mm³
        </el-tag>
        <el-tabs v-model="activePreviewTab" style="margin-top:8px">
          <el-tab-pane label="扩口芯轴" name="mandrel" :disabled="!hasMandrel">
            <div class="viewer-wrap">
              <StlViewer v-if="activePreviewTab === 'mandrel' && hasMandrel" :key="mandrelPreviewKey" :url="mandrelPreviewUrl" color="#e79b35" height="380" />
            </div>
          </el-tab-pane>
          <el-tab-pane label="扩口型腔（全长）" name="cavity" :disabled="!hasCavityModel">
            <div class="viewer-wrap">
              <StlViewer v-if="activePreviewTab === 'cavity' && hasCavityModel" ref="cavityViewerRef" :key="cavityPreviewKey" :url="cavityPreviewUrl" height="380" />
            </div>
          </el-tab-pane>
          <el-tab-pane v-if="resultInfo.model_kind === 'mold'" label="外部反切模具" name="mold">
            <el-switch v-model="cutaway" active-text="剖开查看内部（仅影响预览）" />
            <StlViewer v-if="activePreviewTab === 'mold'" :key="previewKey" :url="previewUrl" :cutaway="cutaway" height="380" />
          </el-tab-pane>
        </el-tabs>
      </div>
      <div v-else class="viewer-wrap">
        <StlViewer ref="viewerRef" :key="previewKey" :url="previewUrl" height="380" />
      </div>

      <div v-if="!configChanged" style="display:flex; gap:8px; flex-wrap:wrap; margin-top:12px">
        <el-button type="primary" @click="handleSave" :loading="saving">保存</el-button>
        <el-button type="warning" @click="handleDownload">{{ primaryDownloadText }}</el-button>
        <el-button v-if="hasMandrel && resultInfo.model_kind === 'mold'" type="warning" @click="handleDownloadMandrel">下载扩口芯轴STEP</el-button>
        <el-button v-if="hasCavityModel" type="info" @click="handleDownloadCavity">下载扩口腔体STEP</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRfmStore } from '@/stores/rfm'
import { rfmApi } from '@/api/rfm'
import StlViewer from '@/components/StlViewer.vue'
import MoldOutputOptions from '@/components/rfm/MoldOutputOptions.vue'
import { saveBlob } from '@/utils/download'

const props = defineProps({
  operation: { type: String, required: true },
  showParams: { type: Boolean, default: true },
})

const emit = defineEmits(['generated'])

const rfmStore = useRfmStore()
const generating = ref(false)
const generatingStage = ref('正在生成模型，请稍候…')
const saving = ref(false)
const sessionReady = ref(false)
const hasCavityModel = ref(false)
const hasMandrel = ref(false)
const mandrelInfo = ref(null)
const cavityInfo = ref(null)
const mandrelPreviewUrl = ref('')
const mandrelPreviewKey = ref(0)
const resultInfo = ref({ vertices: 0, faces: 0 })
const analysisWarnings = ref('')
const previewKey = ref(0)
const outerPreviewKey = ref(0)
const cavityPreviewKey = ref(0)
const previewUrl = ref('')
const outerPreviewUrl = ref('')
const cavityPreviewUrl = ref('')
const activePreviewTab = ref('outer')
const rfmSessionId = ref('')
const viewerRef = ref(null)
const outerViewerRef = ref(null)
const cavityViewerRef = ref(null)
const config = reactive({ mold_enabled: false, mold_auto_size: true, mold_margin: 15 })
const v1Task = ref(null)
let v1Timer = null
const cutaway = ref(true)
const generatedConfig = ref('')
const routeOptions = computed(() => (props.operation === 'shaping'
  ? [
    { value: 'c1', label: 'c1 外推（默认）' },
    { value: '121', label: '121 保形（v1 口径）' },
    { value: 'pipe', label: 'pipe 重蒙皮' },
  ]
  : [
    { value: 'pipe', label: 'pipe 重蒙皮（默认）' },
    { value: 'c1', label: 'c1 外推（4 面）' },
    { value: '121', label: '121 保形（当前口径）' },
  ]))
const routeHint = computed(() => (props.operation === 'shaping'
  ? '121 = v1 测量口径 + 121 保形：有 v1 缓存直接复用，没有会后台测量（显示进度，可取消）。'
  : 'pipe 为默认；c1/121 都基于当前工序外形，121 用参考截面保形。'))
const defaultEntityRoute = () => (props.operation === 'shaping' ? 'c1' : 'pipe')
const entityRoute = ref(defaultEntityRoute())
const generatedRoute = ref(defaultEntityRoute())
const entityInfo = ref(null)
const entityMandrelInfo = ref(null)
const entityCavityInfo = ref(null)
const configChanged = computed(() => generatedConfig.value !== JSON.stringify(config)
  || generatedRoute.value !== entityRoute.value)

function fmtVolume(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 3 })
}

function entityDetail(info) {
  if (!info) return ''
  const bits = []
  if (info.direction) bits.push(`分型方向 ${Array.isArray(info.direction) ? info.direction.map(v => Number(v).toFixed(2)).join(', ') : info.direction}`)
  if (info.sections_used) bits.push(`参与重建 ${info.sections_used}/${info.sections_total ?? '?'} 个截面`)
  if (info.fallback) bits.push(`回退原因：${info.fallback}`)
  if (info.problems && info.problems.length) bits.push(`提示：${info.problems.join('；')}`)
  if (info.seconds) bits.push(`耗时 ${Number(info.seconds).toFixed(1)} s`)
  return bits.join('　')
}

const primaryDownloadText = computed(() => {
  if (resultInfo.value.model_kind === 'mold') return '下载反切模具 STEP'
  if (props.operation === 'belling') return '下载扩口芯轴STEP'
  if (props.operation === 'shaping') return '下载外径形腔STEP'
  return '下载STEP'
})

watch(
  () => rfmStore.designRecord?.id,
  () => {
    if (!rfmStore.hasSession) return
    sessionReady.value = false
    entityRoute.value = defaultEntityRoute()
    generatedRoute.value = defaultEntityRoute()
    analysisWarnings.value = ''
    for (const key of Object.keys(config)) delete config[key]
    Object.assign(config, { mold_enabled: false, mold_auto_size: true, mold_margin: 15 })
    if (props.operation === 'belling') {
      config.belling_length = Math.min(rfmStore.L1 > 0 ? rfmStore.L1 : 200, rfmStore.centerlineLength || 200)
      config.length_allowance = 0
      config.wall_thickness = rfmStore.T3 > 0 ? rfmStore.T3 : 2.0
      config.wall_thickness_source = rfmStore.T3 > 0 ? 'constant' : 'data'
    } else if (props.operation === 'flattening') {
      config.flattening_length = rfmStore.L1 > 0 ? rfmStore.L1 : 200
      config.flattening_allowance = 0
      config.total_length = rfmStore.centerlineLength > 0 ? rfmStore.centerlineLength : undefined
      config.transition_length = 150.0
      config.num_sections = 200
    } else if (props.operation === 'bending') {
      config.num_sections = 152
      config.flattening_executed = false
      config.flattening_length = rfmStore.L1 > 0 ? rfmStore.L1 : 200
      config.transition_length = 150
      config.flattening_allowance = 0
      const previous = rfmStore.processConfigs.flattening
      if (previous) {
        for (const key of ['ellipse_x', 'ellipse_y', 'flattening_length', 'transition_length', 'flattening_allowance', 'total_length', 'num_sections']) {
          if (previous[key] !== undefined) config[key] = previous[key]
        }
        config.flattening_executed = true
      }
    }
    Object.assign(config, rfmStore.processConfigs[props.operation] || {})
  },
  { immediate: true }
)

function stopV1Polling() {
  if (v1Timer) {
    clearInterval(v1Timer)
    v1Timer = null
  }
}

function pollV1(taskId) {
  return new Promise((resolve, reject) => {
    const started = Date.now()
    stopV1Polling()
    v1Timer = setInterval(async () => {
      try {
        const res = await rfmApi.v1Status(taskId)
        const task = res.data || {}
        v1Task.value = { ...v1Task.value, ...task }
        if (task.status === 'done') {
          stopV1Polling()
          resolve(task)
        } else if (task.status === 'error') {
          stopV1Polling()
          reject(new Error(task.message || task.error || 'v1 测量失败'))
        } else if (task.status === 'cancelled') {
          stopV1Polling()
          reject(new Error('已取消 v1 测量'))
        } else if (Date.now() - started > 2 * 3600 * 1000) {
          stopV1Polling()
          reject(new Error('v1 测量超时'))
        }
      } catch (e) {
        stopV1Polling()
        reject(e)
      }
    }, 2000)
  })
}

async function ensureV1(sid, force = false) {
  if (props.operation !== 'shaping' || entityRoute.value !== '121') return
  stopV1Polling()
  v1Task.value = null
  generatingStage.value = '正在检查 v1 测量缓存…'
  const res = await rfmApi.prepareV1(sid, force)
  const task = res.data || {}
  v1Task.value = task
  if (task.status === 'cached') {
    ElMessage.info('已复用该件的 v1 测量缓存')
    return
  }
  if (task.status !== 'running' && task.status !== 'cancelling') {
    throw new Error(task.message || 'v1 测量启动失败')
  }
  // 测量期间放开 loading 遮罩，进度条和「取消」才点得到。
  generating.value = false
  generatingStage.value = '正在测量 v1 口径…'
  try {
    await pollV1(task.task_id)
    ElMessage.success('v1 口径已就绪，继续生成 121 实体')
  } finally {
    generating.value = true
  }
}

async function cancelV1() {
  const task = v1Task.value
  if (!task?.task_id) return
  try {
    await rfmApi.v1Cancel(task.task_id)
    v1Task.value = { ...task, status: 'cancelling', message: '正在取消 v1 测量…' }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || e.message || '取消失败')
  }
}

async function remeasureV1() {
  const sid = rfmSessionId.value
  if (!sid) {
    ElMessage.warning('请先生成一次模型，再重新测量 v1 口径')
    return
  }
  if (entityRoute.value !== '121') {
    ElMessage.warning('请先把实体化路线切到 121')
    return
  }
  try {
    generating.value = false
    await ensureV1(sid, true)
    ElMessage.success('v1 口径已更新，请点击「生成模型」')
  } catch (e) {
    ElMessage.error(e.response?.data?.message || e.message || 'v1 测量失败')
  } finally {
    generating.value = false
    generatingStage.value = '正在生成模型，请稍候…'
  }
}

onBeforeUnmount(stopV1Polling)

async function handleGenerate() {
  if (!rfmStore.hasSession) {
    ElMessage.warning('请先加载数据')
    return
  }
  generating.value = true
  generatingStage.value = '正在初始化会话…'
  sessionReady.value = false
  hasCavityModel.value = false
  hasMandrel.value = false
  mandrelInfo.value = null
  cavityInfo.value = null
  entityInfo.value = null
  entityMandrelInfo.value = null
  entityCavityInfo.value = null
  try {
    const designId = rfmStore.designRecord?.id
    const initRes = await rfmApi.initFromDesign(designId, props.operation)
    if (!initRes.data.success) {
      ElMessage.error(initRes.data.message || '初始化失败')
      return
    }
    const sid = initRes.data.session_id
    analysisWarnings.value = (initRes.data.analysis_quality?.warnings || []).join('；')
    generatingStage.value = '正在应用操作参数…'
    const effectiveConfig = { ...initRes.data.config, ...config }
    if (!effectiveConfig.total_length) effectiveConfig.total_length = initRes.data.config.total_length
    const processRes = await rfmApi.process(sid, effectiveConfig)
    if (!processRes.data.success) throw new Error(processRes.data.message || '参数保存失败')

    await ensureV1(sid)
    generatingStage.value = '正在生成模型并实体化（复杂管件可能需要数分钟，请勿关闭页面）…'
    const genRes = await rfmApi.generate(sid, { entity_route: entityRoute.value })
    if (!genRes.data.success) {
      ElMessage.error(genRes.data.message || '生成失败')
      return
    }
    entityInfo.value = genRes.data.entity || null

    resultInfo.value = {
      vertices: genRes.data.vertices,
      faces: genRes.data.faces,
      model_kind: genRes.data.model_kind,
      mold_info: genRes.data.mold_info,
      section_summary: genRes.data.section_summary,
      quality: genRes.data.quality,
    }
    rfmSessionId.value = sid
    // Surface-check findings share the existing warning strip. A rejected part
    // never reaches this point: the API answers 400 and the message is toasted.
    const qualityWarnings = genRes.data.quality?.warnings || []
    if (qualityWarnings.length) {
      analysisWarnings.value = [analysisWarnings.value, ...qualityWarnings].filter(Boolean).join('；')
    }

    if (genRes.data.model_kind === 'mold') {
      previewUrl.value = rfmApi.previewUrl(sid, Date.now())
      previewKey.value++
      const sizes = genRes.data.mold_info.size_xyz_mm
      for (const [i, axis] of ['x', 'y', 'z'].entries()) config['mold_size_' + axis] = sizes[i]
    }
    if (props.operation === 'belling') {
      generatingStage.value = '正在生成扩口芯轴…'
      const mandrelRes = genRes.data.model_kind === 'mandrel' ? genRes : await rfmApi.generateMandrel(sid, { entity_route: entityRoute.value })
      if (!mandrelRes.data.success) throw new Error(mandrelRes.data.message || '芯轴生成失败')
      hasMandrel.value = true
      mandrelInfo.value = mandrelRes.data.belling_profile
      entityMandrelInfo.value = mandrelRes.data.entity || null
      entityInfo.value = mandrelRes.data.entity || entityInfo.value
      mandrelPreviewUrl.value = rfmApi.previewMandrelUrl(sid, Date.now())
      mandrelPreviewKey.value++
      activePreviewTab.value = 'mandrel'
      generatingStage.value = '芯轴已生成，正在生成扩口型腔…'
      const cavityRes = await rfmApi.generateCavity(sid, { entity_route: entityRoute.value })
      if (!cavityRes.data.success) throw new Error(cavityRes.data.message || '扩口型腔生成失败')
      hasCavityModel.value = true
      cavityInfo.value = cavityRes.data.belling_profile
      entityCavityInfo.value = cavityRes.data.entity || null
      cavityPreviewUrl.value = rfmApi.previewCavityUrl(sid, Date.now())
      cavityPreviewKey.value++
    } else if (genRes.data.model_kind === 'mold') {
      // Primary mold preview was set above.
    } else if (props.operation === 'shaping') {
      activePreviewTab.value = 'outer'
      const outerCacheKey = Date.now()
      outerPreviewUrl.value = rfmApi.previewUrl(sid, outerCacheKey)
      outerPreviewKey.value++
      sessionReady.value = true
      rfmSessionId.value = sid
    } else {
      previewUrl.value = rfmApi.previewUrl(sid, Date.now())
      previewKey.value++
      rfmSessionId.value = sid
    }

    sessionReady.value = true
    rfmStore.rememberProcessConfig(props.operation, effectiveConfig)
    generatedConfig.value = JSON.stringify(config)
    generatedRoute.value = entityRoute.value
    emit('generated')
    ElMessage.success(props.operation === 'belling' ? '扩口芯轴与型腔已生成' : genRes.data.model_kind === 'mold' ? '反切模具生成成功' : '模型生成成功')
  } catch (e) {
    ElMessage.error(e.response?.data?.message || e.message || '生成失败')
  } finally {
    generating.value = false
    generatingStage.value = '正在生成模型，请稍候…'
  }
}

async function regenerate() {
  sessionReady.value = false
  hasCavityModel.value = false
  await handleGenerate()
}

async function handleSave() {
  if (!rfmSessionId.value) return
  saving.value = true
  try {
    const res = await rfmApi.save(rfmSessionId.value)
    if (res.data.success) ElMessage.success('保存成功')
    else ElMessage.error(res.data.message)
  } catch { ElMessage.error('保存失败') }
  finally { saving.value = false }
}

/** 服务端 Content-Disposition 里的文件名（RFC 5987 优先） */
function serverFileName(res, fallback) {
  const cd = (res && res.headers && (res.headers['content-disposition'] || res.headers['Content-Disposition'])) || ''
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd)
  if (star) { try { return decodeURIComponent(star[1]) } catch { return star[1] } }
  const plain = /filename="?([^";]+)"?/i.exec(cd)
  if (plain) { try { return decodeURIComponent(plain[1]) } catch { return plain[1] } }
  return fallback
}

function preferredName(info, fallback) {
  const named = info && info.download_name
  return named || fallback
}

async function handleDownload() {
  if (!rfmSessionId.value) return
  try {
    const res = await rfmApi.download(rfmSessionId.value)
    const fallback = resultInfo.value.model_kind === 'mold'
      ? '反切模具.step'
      : props.operation === 'belling'
        ? '扩口芯轴.step'
        : props.operation === 'shaping'
          ? '外径形腔.step'
          : 'rfm_model.step'
    saveBlob(res.data, serverFileName(res, preferredName(entityInfo.value, fallback)))
    ElMessage.success('下载成功')
  } catch {
    ElMessage.error('下载失败，请确认已登录')
  }
}

async function handleDownloadMandrel() {
  if (!rfmSessionId.value) return
  try {
    const res = await rfmApi.downloadMandrel(rfmSessionId.value)
    saveBlob(res.data, serverFileName(res, preferredName(entityMandrelInfo.value, '扩口芯轴.step')))
    ElMessage.success('下载成功')
  } catch { ElMessage.error('芯轴下载失败，请重新生成') }
}

async function handleDownloadCavity() {
  if (!rfmSessionId.value) return
  try {
    const res = await rfmApi.downloadCavity(rfmSessionId.value)
    saveBlob(res.data, serverFileName(res, preferredName(entityCavityInfo.value, '扩口腔体.step')))
    ElMessage.success('下载成功')
  } catch {
    ElMessage.error('下载失败，请确认已登录')
  }
}
</script>

<style scoped>
.rfm-shell { display: flex; flex-direction: column; gap: 16px; }
.entity-route-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.entity-route-label { font-size: 13px; color: #606266; }
.entity-route-hint { font-size: 12px; color: #909399; }
.v1-status { display: flex; align-items: center; gap: 10px; margin: 0 0 12px; }
.v1-status-text { font-size: 12px; color: #606266; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.section-card { max-width: 1000px; }
.viewer-wrap { margin-bottom: 12px; }
</style>
