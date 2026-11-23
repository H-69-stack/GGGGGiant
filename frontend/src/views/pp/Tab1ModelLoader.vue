<template>
  <div>
    <!-- Load mode selector -->
    <el-radio-group v-model="loadMode" size="large" style="margin-bottom: 20px">
      <el-radio-button value="upload">上传新模型文件</el-radio-button>
      <el-radio-button value="db">从数据库加载已分析模型</el-radio-button>
    </el-radio-group>

    <!-- ═══════════ UPLOAD MODE ═══════════ -->
    <template v-if="loadMode === 'upload'">
      <!-- Algorithm selection -->
      <el-form :inline="true" style="margin-bottom: 12px">
        <el-form-item label="分析算法">
          <el-radio-group v-model="algorithm">
            <el-radio-button value="A">A：轴向判定 + 中心线法向截面（推荐）</el-radio-button>
            <el-radio-button value="B">B：Z向切片质心中心线</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <el-alert v-if="algorithm === 'B'" type="info" :closable="false" style="margin-bottom: 12px">
        B算法不旋转模型、不使用PCA；要求模型本身已对齐（主轴基本沿Z轴）。
      </el-alert>

      <!-- File upload -->
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept=".stl,.STL,.stp,.step,.STEP"
        :on-change="handleFileChange"
        :on-remove="handleFileRemove"
        style="margin-bottom: 12px"
      >
        <el-icon size="48" color="#1890ff"><UploadFilled /></el-icon>
        <div class="el-upload__text">将STL/STP文件拖拽到此处，或<em>点击上传</em></div>
      </el-upload>
      <div v-if="selectedFile" style="margin: 8px 0; color: #666">
        已选文件: {{ selectedFile.name }} ({{ (selectedFile.size / 1024 / 1024).toFixed(2) }} MB)
      </div>

      <!-- Advanced options -->
      <el-collapse style="margin-bottom: 12px">
        <el-collapse-item title="高级选项" name="advanced">
          <el-form :inline="true">
            <el-form-item label="起点舍弃(mm)"><el-input-number v-model="endTrimStart" :min="0" placeholder="自动识别" /></el-form-item>
            <el-form-item label="终点舍弃(mm)"><el-input-number v-model="endTrimEnd" :min="0" placeholder="自动识别" /></el-form-item>
          </el-form>
          <p style="font-size:12px;color:#666">STP 两端舍弃后按实测形状梯度 C1 外推轮廓，同时线性延拓中心点、壁厚，保留完整长度坐标。留空自动识别；起点位于大周长端。修改后重新分析。</p>
          <el-button size="small" @click="handleClearCache">清除网格缓存</el-button>
        </el-collapse-item>
      </el-collapse>

      <!-- Analyze button -->
      <el-button
        type="primary"
        :loading="analyzing"
        :disabled="!selectedFile"
        style="width: 100%; margin-bottom: 16px"
        @click="handleAnalyze"
      >
        开始高级分析
      </el-button>

      <!-- Progress -->
      <div v-if="analyzing || progressMsg" style="margin-bottom: 16px">
        <el-progress :percentage="progressPct" :status="progressDone ? 'success' : undefined" />
        <div style="color: #666; font-size: 12px; margin-top: 4px">{{ progressMsg }}</div>
        <div v-if="elapsedSec > 0" style="color: #999; font-size: 12px">已用时: {{ elapsedSec }}秒</div>
      </div>
    </template>

    <!-- ═══════════ DATABASE MODE ═══════════ -->
    <template v-if="loadMode === 'db'">
      <div style="margin-bottom: 12px; display: flex; gap: 8px; align-items: center">
        <el-input v-model="dbSearch" placeholder="搜索文件名" clearable style="width: 240px" @input="loadTubes" />
        <el-switch v-model="dbDeleteMode" active-text="启用批量删除" />
      </div>

      <div v-if="dbDeleteMode" style="margin-bottom: 8px">
        <el-button size="small" @click="selectAllTubes">全选</el-button>
        <el-button size="small" @click="deselectAllTubes">取消全选</el-button>
        <el-popconfirm
          v-if="selectedForDelete.length"
          :title="`确定删除 ${selectedForDelete.length} 条记录？此操作不可撤销。`"
          @confirm="handleDeleteTubes"
        >
          <template #reference>
            <el-button size="small" type="danger" :disabled="!selectedForDelete.length">
              删除选中 ({{ selectedForDelete.length }})
            </el-button>
          </template>
        </el-popconfirm>
      </div>

      <el-table :data="dbTubes" border stripe max-height="300" style="margin-bottom: 12px"
                @selection-change="handleDbSelectionChange"
                v-if="dbDeleteMode">
        <el-table-column type="selection" width="55" />
        <el-table-column prop="ID" label="ID" width="80" />
        <el-table-column prop="文件名" label="文件名" />
        <el-table-column prop="创建时间" label="创建时间" width="180" />
      </el-table>
      <el-table v-else :data="dbTubes" border stripe max-height="300" style="margin-bottom: 12px">
        <el-table-column prop="ID" label="ID" width="80" />
        <el-table-column prop="文件名" label="文件名" />
        <el-table-column prop="创建时间" label="创建时间" width="180" />
      </el-table>

      <el-select v-model="selectedDbId" placeholder="选择要加载的模型" style="width: 100%; margin-bottom: 12px"
                 v-if="dbTubes.length">
        <el-option
          v-for="tube in dbTubes"
          :key="tube.ID"
          :label="`ID: ${tube.ID} - ${tube.文件名}`"
          :value="tube.ID"
        />
      </el-select>

      <el-button type="primary" :disabled="!selectedDbId" @click="handleLoadTube" style="width: 100%">
        加载选中的模型数据
      </el-button>
    </template>

    <PpDevQaPanel
      v-if="isDevBuild"
      @restore="onDevRestore"
      @refreshed="onDevRefreshed"
    />

    <!-- ═══════════ ANALYSIS RESULTS (shown after successful analysis or DB load) ═══════════ -->
    <template v-if="sessionId && infoReady">
      <el-divider />

      <el-alert v-if="analysisQuality" :type="analysisQuality.requires_review ? 'warning' : 'success'"
                :closable="false" style="margin-bottom: 16px" title="STP 原生截面测量">
        可靠区间采用法向截面外轮廓的面积质心与截面内壁厚中位数；端部按实测形状梯度 C1 外推并单独标记。
        非圆截面的周长等效直径为周长 ÷ π。
        <div v-for="message in analysisQuality.warnings" :key="message">{{ message }}</div>
      </el-alert>

      <!-- 4 stat cards -->
      <el-row :gutter="16" style="margin-bottom: 20px">
        <el-col :span="6">
          <el-card shadow="hover">
            <div style="color: #606266; font-size: 13px">文件名</div>
            <div style="margin-top: 8px; overflow-wrap: anywhere">{{ fileName }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover">
            <el-statistic :title="analysisQuality?.end_treatment ? '完整长度坐标 (mm)' : analysisQuality ? '有效测量段长度（旧数据）(mm)' : '中心线长度 (mm)'" :value="centerlineLength" :precision="2" />
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover">
            <el-statistic title="体积 (cm³)" :value="volumeCm3" :precision="1" />
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover">
            <el-statistic :title="bendLabel" :value="bendAngleVal" :precision="2">
              <template #suffix>°</template>
            </el-statistic>
          </el-card>
        </el-col>
      </el-row>

      <!-- Main content area: 3D viewer + side panel -->
      <el-row :gutter="16" style="margin-bottom: 20px">
        <!-- Left: 3D model viewer (full height) -->
        <el-col :xs="24" :sm="16">
          <el-card>
            <template #header><span>3D 模型预览</span></template>
            <Pp3dViewer
              v-if="sessionId && infoReady"
              ref="viewer3dRef"
              :height="500"
              color="#409EFF"
              @section-hover="onSectionHover"
              @section-click="onSectionClick"
            />
            <el-empty v-else description="加载3D模型中..." :image-size="60" />
          </el-card>
        </el-col>

        <!-- Right: Side panel (bend detection only) -->
        <el-col :xs="24" :sm="8">
          <el-card>
            <template #header><span>弯曲检测结果</span></template>
            <el-alert :type="isBended ? 'warning' : 'success'" :closable="false" style="margin-bottom: 12px">
              {{ isBended ? '该管件需要压弯处理' : '该管件无需压弯处理' }}
              <br>最大弯曲角度: {{ bendAngleVal.toFixed(2) }}°
            </el-alert>
            <el-divider style="margin: 12px 0">工程师判断</el-divider>
            <el-radio-group v-model="engineerDecision" @change="handleEngineerBending" style="width: 100%">
              <el-radio :value="null" style="margin-bottom: 8px">暂不判断</el-radio>
              <el-radio :value="true" style="margin-bottom: 8px">弯管</el-radio>
              <el-radio :value="false">直管</el-radio>
            </el-radio-group>
            <div v-if="engineerDecision !== null" style="margin-top: 8px">
              <el-tag :type="engineerDecision ? 'warning' : 'success'">
                最终判断: {{ engineerDecision ? '弯管' : '直管' }} (工程师决定)
              </el-tag>
            </div>
            <div v-else>
              <el-tag type="info">当前状态: {{ isBended ? '弯管' : '直管' }} (程序判断)</el-tag>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <PpQaPanel
        v-if="sessionId && infoReady"
        ref="qaPanelRef"
        :session-id="sessionId"
        @update="onQaUpdate"
        @display="onQaDisplay"
      />

      <!-- Data tabs (perimeter / thickness / formline) -->
      <el-card style="margin-bottom: 20px">
        <el-tabs v-model="dataTab" type="border-card">
          <el-tab-pane label="周长数据" name="perimeter">
            <PpChart
              v-if="sectionsFull.length"
              type="perimeter"
              :sections="chartSections"
              :suspect-segments="['QA_LOCAL_ANOMALY', 'QA_MANUAL_REQUIRED', 'QA_RECHECK_FAILED'].includes(qa?.status) ? (qa?.suspect_segments || []) : []"
              :straight-segments="chartStraightSegments"
              :raw-diameters="qa?.raw_profile?.outer_diameter_mm || []"
              :preview-diameters="qa?.processed_profile?.outer_diameter_mm || []"
              :show-raw="qaShowRaw"
              :show-preview="qaShowPreview"
              :anomaly-indices="qa?.anomaly_indices || []"
              :profile-source="qa?.profile_source || ''"
              :qa-status="qa?.status || ''"
              @hover-section="onSectionHover"
              @click-section="onSectionClick"
            />
            <el-empty v-else description="暂无周长数据" :image-size="60" />
          </el-tab-pane>

          <el-tab-pane label="壁厚数据" name="thickness">
            <PpChart
              v-if="sectionsFull.length && hasThicknessData"
              type="thickness"
              :sections="sectionsFull"
              :thickness-stats="thicknessStats"
              @hover-section="onSectionHover"
              @click-section="onSectionClick"
            />
            <el-empty v-else description="暂无壁厚数据" :image-size="60" />
            <el-row :gutter="8" v-if="thicknessStats">
              <el-col :span="6">
                <el-statistic title="中位数 (mm)" :value="thicknessStats.median_mm" :formatter="v => Number(v).toFixed(3)" />
              </el-col>
              <el-col :span="6">
                <el-statistic title="平均值 (mm)" :value="thicknessStats.mean_mm" :formatter="v => Number(v).toFixed(3)" />
              </el-col>
              <el-col :span="6">
                <el-statistic title="沿程最低中位数 (mm)" :value="thicknessStats.min_mm" :formatter="v => Number(v).toFixed(3)" />
              </el-col>
              <el-col :span="6">
                <el-statistic title="沿程最高中位数 (mm)" :value="thicknessStats.max_mm" :formatter="v => Number(v).toFixed(3)" />
              </el-col>
            </el-row>
          </el-tab-pane>

          <el-tab-pane label="直线段识别" name="formline">
            <div v-if="formlineSegments.length">
              <el-tag type="success" style="margin-bottom: 12px">
                识别到 {{ formlineSegments.length }} 个直线段，总长度: {{ formlineTotal.toFixed(2) }} mm
              </el-tag>
              <el-table :data="formlineSegments" border stripe size="small" max-height="300">
                <el-table-column label="段" width="60">
                  <template #default="{ $index }">{{ $index + 1 }}</template>
                </el-table-column>
                <el-table-column label="位置 (mm)" width="200">
                  <template #default="{ row }">{{ row.start_length.toFixed(1) }} - {{ row.end_length.toFixed(1) }}</template>
                </el-table-column>
                <el-table-column label="长度 (mm)" width="120">
                  <template #default="{ row }">{{ row.length.toFixed(1) }}</template>
                </el-table-column>
                <el-table-column label="周长 (mm)">
                  <template #default="{ row }">{{ row.perimeter.toFixed(2) }}</template>
                </el-table-column>
                <el-table-column label="极差 (mm)">
                  <template #default="{ row }">{{ row.perimeter_range?.toFixed(3) ?? '-' }}</template>
                </el-table-column>
              </el-table>
            </div>
            <el-empty v-else description="未识别到直线段" :image-size="60" />
          </el-tab-pane>
        </el-tabs>
      </el-card>

      <!-- Cross-section detail dialog -->
      <el-dialog v-model="csDialogVisible" :title="`截面详情 #${csIdx}`" width="420px" align-center>
        <canvas ref="csCanvasRef" width="380" height="380" class="cs-canvas" />
        <div class="cs-info">
          <p><strong>外轮廓周长:</strong> {{ csData.perimeter.toFixed(3) }} mm</p>
          <p><strong>周长等效直径:</strong> {{ (csData.perimeter / Math.PI).toFixed(3) }} mm</p>
          <p v-if="csData.wallThickness != null"><strong>壁厚中位数:</strong> {{ csData.wallThickness.toFixed(3) }} mm</p>
          <p v-if="csData.wallMin != null"><strong>周向壁厚范围:</strong> {{ csData.wallMin.toFixed(3) }} – {{ csData.wallMax.toFixed(3) }} mm</p>
          <p><strong>累计长度:</strong> {{ csData.cumulativeLength.toFixed(1) }} mm</p>
        </div>
      </el-dialog>

      <!-- Bottom actions -->
      <div style="display: flex; gap: 12px; margin-bottom: 20px">
        <el-button @click="handleReanalyze">重新分析</el-button>
        <el-button type="primary" @click="handleSaveToDb">保存到数据库</el-button>
        <el-button type="primary" :disabled="!qa?.profile_choice" @click="handleApplyToOptimization">
          应用数据到工艺优化
        </el-button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onUnmounted, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { ppApi } from '../../api/pp'
import Pp3dViewer from '../../components/Pp3dViewer.vue'
import PpChart from '../../components/PpChart.vue'
import PpDevQaPanel from '../../components/PpDevQaPanel.vue'
import PpQaPanel from '../../components/PpQaPanel.vue'

const emit = defineEmits(['ready'])
const isDevBuild = import.meta.env.DEV
const qaPanelRef = ref(null)

// ── Load mode ──
const loadMode = ref('upload')

// ── Upload mode state ──
const algorithm = ref('A')
const endTrimStart = ref(undefined)
const endTrimEnd = ref(undefined)
const selectedFile = ref(null)
const analyzing = ref(false)
const sessionId = ref('')
const progressPct = ref(0)
const progressMsg = ref('')
const progressDone = ref(false)
const elapsedSec = ref(0)

// ── DB mode state ──
const dbSearch = ref('')
const dbTubes = ref([])
const dbDeleteMode = ref(false)
const selectedDbId = ref(null)
const selectedForDelete = ref([])

// ── Results state ──
const infoReady = ref(false)
const fileName = ref('')
const centerlineLength = ref(0)
const volumeCm3 = ref(0)
const isBended = ref(false)
const bendAngleVal = ref(0)
const engineerDecision = ref(null)
const bendLabel = computed(() =>
  engineerDecision.value !== null
    ? (engineerDecision.value ? '弯管 (工程师决定)' : '直管 (工程师决定)')
    : (isBended.value ? '是否弯管' : '是否弯管')
)
const dataTab = ref('perimeter')
const sectionsFull = ref([])
const centerlinePoints = ref([])
const sparseSections = ref([])
const thicknessSamples = ref([])
const thicknessStats = ref(null)
const analysisQuality = ref(null)
const hoveredSectionIdx = ref(-1)
const viewer3dRef = ref(null)

// Visibility controls
const showCenterline = ref(true)
const showThickness = ref(false)
const showPlanes = ref(false)
const showSections = ref(true)
const meshOpacity = ref(0.28)
const pointSize = ref(4)

// Cross-section dialog state
const csDialogVisible = ref(false)
const csIdx = ref(-1)
const csData = reactive({ perimeter: 0, wallThickness: null, wallMin: null, wallMax: null, cumulativeLength: 0 })
const csCanvasRef = ref(null)

const formlineSegments = ref([])
const formlineTotal = ref(0)
const qa = ref(null)
const qaShowRaw = ref(true)
const qaShowPreview = ref(false)
const xlsxErrors = ref([])

// Computed: check if any section has wall_thickness data
const hasThicknessData = computed(() =>
  sectionsFull.value.some(s => s.wall_thickness != null && s.wall_thickness !== undefined)
)
const chartSections = computed(() => {
  const base = sectionsFull.value || []
  const qaObj = qa.value
  let profile = qaObj?.working_profile
  if (qaObj?.profile_choice === 'manual') profile = qaObj.manual_profile
  else if (qaObj?.profile_choice === 'auto' || qaObj?.status === 'QA_PASS') {
    profile = qaObj.final_profile || qaObj.auto_profile || qaObj.working_profile
  }
  const circ = profile?.outer_circumference_mm
  const s = profile?.s_mm
  if (!circ?.length || !base.length) return base
  if (circ.length === base.length) {
    return base.map((sec, i) => ({
      ...sec,
      perimeter: circ[i] != null ? circ[i] : sec.perimeter,
    }))
  }
  if (!s?.length || s.length !== circ.length) return base
  const xs = s.map(Number)
  const ys = circ.map(Number)
  const lerp = (x) => {
    if (x <= xs[0]) return ys[0]
    if (x >= xs[xs.length - 1]) return ys[ys.length - 1]
    let lo = 0
    let hi = xs.length - 1
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1
      if (xs[mid] <= x) lo = mid
      else hi = mid
    }
    const t = (x - xs[lo]) / Math.max(1e-12, xs[hi] - xs[lo])
    return ys[lo] + t * (ys[hi] - ys[lo])
  }
  return base.map((sec) => ({
    ...sec,
    perimeter: lerp(Number(sec.cumulative_length || 0)),
  }))
})
const chartStraightSegments = computed(() => {
  const segs = qa.value?.straight_segments || []
  if (qa.value?.status === 'QA_PASS') return segs.filter((s) => s.status === 'confirmed')
  return segs.filter((s) => s.status !== 'cancelled')
})

// Polling timer
let pollTimer = null
let elapsedTimer = null

// ── File handling ──
function handleFileChange(file) {
  selectedFile.value = file.raw
}
function handleFileRemove() {
  selectedFile.value = null
}

// ── Analyze ──
async function handleAnalyze() {
  if (!selectedFile.value) return
  analyzing.value = true
  progressPct.value = 0
  progressMsg.value = '开始分析...'
  progressDone.value = false
  elapsedSec.value = 0
  infoReady.value = false

  const formData = new FormData()
  formData.append('file', selectedFile.value)
  formData.append('algorithm', algorithm.value)
  formData.append('end_treatment', JSON.stringify({ start_trim_mm: endTrimStart.value ?? null, end_trim_mm: endTrimEnd.value ?? null }))

  try {
    const res = await ppApi.upload(formData)
    if (!res.data.success) {
      ElMessage.error(res.data.message || '上传失败')
      analyzing.value = false
      return
    }
    sessionId.value = res.data.session_id
    startPolling()
    startElapsedTimer()
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '上传失败')
    analyzing.value = false
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const res = await ppApi.uploadStatus(sessionId.value)
      const p = res.data.progress
      if (!p) return
      progressPct.value = Math.round(p.pct || 0)
      progressMsg.value = p.message || ''
      if (p.done) {
        clearInterval(pollTimer)
        pollTimer = null
        analyzing.value = false
        progressDone.value = true
        if (p.error) {
          ElMessage.error(p.error)
        } else {
          await loadSessionInfo()
        }
      }
    } catch (e) {
      // ignore poll errors
    }
  }, 500)
}

function startElapsedTimer() {
  if (elapsedTimer) clearInterval(elapsedTimer)
  elapsedTimer = setInterval(() => { elapsedSec.value++ }, 1000)
}

async function loadSessionInfo() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
  try {
    const res = await ppApi.sessionInfo(sessionId.value)
    if (!res.data.success) return
    const d = res.data
    fileName.value = d.filename || ''
    centerlineLength.value = d.centerline_length || d.actual_z_length || 0
    volumeCm3.value = (d.volume || 0) / 1000
    isBended.value = d.is_bended || false
    bendAngleVal.value = d.bend_angle || 0
    engineerDecision.value = d.engineer_bending_decision ?? null

    infoReady.value = true

    // Load sections_full from session-info response
    sectionsFull.value = d.sections_full || []
    centerlinePoints.value = d.center_line?.points || []
    sparseSections.value = d.sections || []
    analysisQuality.value = d.analysis_quality || null

    // Load thickness data
    const thickRes = await ppApi.thicknessData(sessionId.value)
    if (thickRes.data.success) {
      thicknessSamples.value = thickRes.data.samples || []
      thicknessStats.value = thickRes.data.stats || null
    }

    await nextTick()
    display3dOverlays()

    // Load formline
    await loadFormline()
  } catch (e) {
    ElMessage.error('加载会话信息失败')
  }
}

function display3dOverlays() {
  const viewer = viewer3dRef.value
  if (!viewer) return
  viewer.clearOverlays()
  viewer.displayCenterline(centerlinePoints.value)
  viewer.displaySectionsFromData(sparseSections.value)
  updateVisibility()
}

function drawCrossSection(idx) {
  const sec = sectionsFull.value[idx]
  if (!sec) return

  const canvas = csCanvasRef.value
  if (!canvas) return

  const ctx = canvas.getContext('2d')
  const W = canvas.width
  const H = canvas.height

  // White background
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, W, H)

  // These coordinates are already in the actual section plane.
  const outerPts = sec.outer_2d || []
  const innerPts = sec.inner_2d || []

  if (!outerPts.length && !innerPts.length) {
    ctx.fillStyle = '#999'
    ctx.font = '14px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('无截面轮廓数据', W / 2, H / 2)
    return
  }

  // Combine all points to compute bounding box
  const allPts = [...outerPts, ...innerPts]
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const p of allPts) {
    if (p.length >= 2) {
      if (p[0] < minX) minX = p[0]
      if (p[1] < minY) minY = p[1]
      if (p[0] > maxX) maxX = p[0]
      if (p[1] > maxY) maxY = p[1]
    }
  }

  // Add padding and compute scale
  const pad = 40
  const scaleX = (maxX - minX) > 0 ? (W - 2 * pad) / (maxX - minX) : 1
  const scaleY = (maxY - minY) > 0 ? (H - 2 * pad) / (maxY - minY) : 1
  const scale = Math.min(scaleX, scaleY)

  function toCanvas(pt) {
    return [
      (pt[0] - minX) * scale + pad,
      H - ((pt[1] - minY) * scale + pad), // Flip Y
    ]
  }

  // Draw outer edge (red)
  if (outerPts.length > 2) {
    ctx.strokeStyle = '#ff4444'
    ctx.lineWidth = 2
    ctx.beginPath()
    const [sx, sy] = toCanvas(outerPts[0])
    ctx.moveTo(sx, sy)
    for (let i = 1; i < outerPts.length; i++) {
      const [x, y] = toCanvas(outerPts[i])
      ctx.lineTo(x, y)
    }
    ctx.closePath()
    ctx.stroke()
  }

  // Draw inner edge (cyan)
  if (innerPts.length > 2) {
    ctx.strokeStyle = '#00ccff'
    ctx.lineWidth = 2
    ctx.beginPath()
    const [sx, sy] = toCanvas(innerPts[0])
    ctx.moveTo(sx, sy)
    for (let i = 1; i < innerPts.length; i++) {
      const [x, y] = toCanvas(innerPts[i])
      ctx.lineTo(x, y)
    }
    ctx.closePath()
    ctx.stroke()
  }

  // Draw center point (yellow circle with black border)
  const centerPt = sec.position
  if (centerPt && centerPt.length >= 2) {
    const [cx, cy] = toCanvas([centerPt[0], centerPt[1]])
    ctx.fillStyle = '#ffdd00'
    ctx.strokeStyle = '#333'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    ctx.arc(cx, cy, 6, 0, Math.PI * 2)
    ctx.fill()
    ctx.stroke()
  }
}

// ── Formline ──
async function loadFormline() {
  try {
    const res = await ppApi.formline(sessionId.value, {
      tolerance: 1.0, min_segment_length: 50.0, trend_tolerance: 0.01, min_point_count: 30
    })
    if (res.data.success && res.data.has_segments) {
      formlineSegments.value = res.data.segments || []
      formlineTotal.value = res.data.statistics?.total_length || 0
    } else {
      formlineSegments.value = []
      formlineTotal.value = 0
    }
  } catch (e) { /* ignore */ }
}

function onQaUpdate(next) {
  qa.value = next || null
  xlsxErrors.value = qa.value?.details || qa.value?.xlsx_errors || []
}

function onQaDisplay({ showRaw, showPreview } = {}) {
  if (showRaw != null) qaShowRaw.value = showRaw
  if (showPreview != null) qaShowPreview.value = showPreview
}

// ── 3D-Chart Linkage ──
function onSectionHover(idx) {
  hoveredSectionIdx.value = idx
  if (viewer3dRef.value && idx >= 0) {
    viewer3dRef.value.highlightSection(idx, sectionsFull.value)
  } else if (viewer3dRef.value) {
    viewer3dRef.value.hideHighlight()
  }
}

function onSectionClick(idx) {
  if (idx < 0 || !sectionsFull.value[idx]) return
  const sec = sectionsFull.value[idx]
  csIdx.value = idx
  csData.perimeter = sec.perimeter || 0
  csData.wallThickness = sec.wall_thickness ?? null
  csData.wallMin = sec.wall_thickness_min ?? null
  csData.wallMax = sec.wall_thickness_max ?? null
  csData.cumulativeLength = sec.cumulative_length || 0
  csDialogVisible.value = true
  // Draw after dialog opens (next tick)
  setTimeout(() => drawCrossSection(idx), 100)
}

// ── Visibility ──
function updateVisibility() {
  if (viewer3dRef.value) {
    viewer3dRef.value.setVisibility({
      mesh: true,
      centerline: showCenterline.value,
      thickness: showThickness.value,
      planes: showPlanes.value,
      sections: showSections.value,
    })
  }
}

watch([showCenterline, showThickness, showPlanes, showSections], updateVisibility)

// ── Engineer bending ──
async function handleEngineerBending(val) {
  try {
    const res = await ppApi.engineerBending(sessionId.value, val)
    if (res.data.success) {
      isBended.value = res.data.is_bended
    }
  } catch (e) { /* ignore */ }
}

// ── Reanalyze ──
async function handleReanalyze() {
  try {
    await ppApi.clearSession(sessionId.value)
  } catch (e) { /* ignore */ }
  sessionId.value = ''
  infoReady.value = false
  formlineSegments.value = []
  qa.value = null
  xlsxErrors.value = []
  progressDone.value = false
  progressPct.value = 0
  progressMsg.value = ''
  elapsedSec.value = 0
}

// ── Save to DB ──
async function handleSaveToDb() {
  try {
    const choice = qa.value?.profile_choice
    if (!choice) {
      ElMessage.warning('请先选择采用自动读取形线或人工测量形线')
      return
    }
    const res = await ppApi.saveTube(sessionId.value, '', null, choice)
    if (res.data.success) ElMessage.success('保存成功')
    else ElMessage.error(res.data.message || '保存失败')
  } catch (e) {
    const serverMsg = e?.response?.data?.message
    console.error('[Tab1 saveTube] failed', e?.response?.status, e?.response?.data, e)
    ElMessage.error(serverMsg ? `保存失败：${serverMsg}` : `保存失败：${e?.message || '网络错误'}`)
  }
}

// ── Apply to optimization ──
async function handleApplyToOptimization() {
  try {
    const choice = qa.value?.profile_choice
    if (!choice) {
      ElMessage.warning('请先选择采用自动读取形线或人工测量形线')
      return
    }
    const res = await ppApi.applyToOptimization(sessionId.value, choice)
    if (res.data.success) {
      ElMessage.success(`已解锁工艺优化（L4=${res.data.L4 ?? qa.value?.L4} mm，形线段=${res.data.formline_length ?? qa.value?.formline_length} mm）`)
      emit('ready', {
        sessionId: sessionId.value,
        modelLength: centerlineLength.value || 0,
      })
    } else {
      ElMessage.error(res.data.message || '尚未通过形线质量确认')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '操作失败')
  }
}

// ── Clear mesh cache ──
async function handleClearCache() {
  try {
    const res = await ppApi.clearMeshCache()
    if (res.data.success) ElMessage.success('网格缓存已清除')
  } catch (e) {
    ElMessage.error('清除缓存失败')
  }
}

// ── DB operations ──
async function loadTubes() {
  try {
    const res = await ppApi.listTubes(dbSearch.value)
    if (res.data.success) dbTubes.value = res.data.tubes || []
  } catch (e) { /* ignore */ }
}
function handleDbSelectionChange(rows) {
  selectedForDelete.value = rows.map(r => r.ID)
}
function selectAllTubes() {
  selectedForDelete.value = dbTubes.value.map(r => r.ID)
}
function deselectAllTubes() {
  selectedForDelete.value = []
}
async function handleDeleteTubes() {
  if (!selectedForDelete.value.length) return
  try {
    const res = await ppApi.deleteTubes(selectedForDelete.value)
    if (res.data.success) {
      ElMessage.success(`已删除 ${res.data.deleted} 条记录`)
      selectedForDelete.value = []
      await loadTubes()
    }
  } catch (e) {
    ElMessage.error('删除失败')
  }
}
async function handleLoadTube() {
  if (!selectedDbId.value) return
  try {
    const res = await ppApi.loadTube(selectedDbId.value)
    if (res.data.success) {
      sessionId.value = res.data.session_id
      await loadSessionInfo()

      ElMessage.success('加载成功')
      emit('ready', {
        sessionId: sessionId.value,
        // 优先 centerline_length（折线弧长，几何上正确），actual_z_length 是中心线两端点直线距离（兜底）
        modelLength: res.data.centerline_length || res.data.actual_z_length || 0,
      })
    } else {
      ElMessage.error(res.data.message || '加载失败')
    }
  } catch (e) {
    ElMessage.error('加载失败')
  }
}

async function onDevRestore(payload) {
  if (!payload?.session_id) return
  sessionId.value = payload.session_id
  await loadSessionInfo()
}

async function onDevRefreshed(payload) {
  if (payload?.qa) {
    qaPanelRef.value?.applyQa(payload.qa)
  } else if (sessionId.value) {
    await qaPanelRef.value?.loadQa()
  }
}

// Load tubes on mount when in DB mode
loadTubes()

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (elapsedTimer) clearInterval(elapsedTimer)
})
</script>

<style scoped>
.el-upload__text { font-size: 14px; }

/* Cross-section dialog styles */
.cs-canvas {
  display: block;
  width: 100%;
  height: 380px;
  background: #ffffff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  margin-bottom: 12px;
}

.cs-info {
  font-size: 14px;
  color: #303133;
  line-height: 1.8;
}

.cs-info p {
  margin: 4px 0;
}

.cs-info strong {
  color: #606266;
}
</style>
