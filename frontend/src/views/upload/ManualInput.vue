<template>
  <div class="manual-input-page">
    <el-alert
      title="基于自动识别的壁厚曲线交互式校正：拖拽分段边界 / 拖拽平台壁厚 / 直接输入。"
      type="info"
      :closable="false"
      class="mb-3"
    />

    <!-- Session check -->
    <el-alert
      v-if="!sessionId"
      title="未检测到分析会话"
      type="warning"
      :closable="false"
      class="mb-3"
    >
      请先在「模型文件上传」页面上传并分析模型文件。
      <template #default>
        <el-button type="primary" size="small" @click="goToUpload" style="margin-top: 8px">
          前往模型文件上传
        </el-button>
      </template>
    </el-alert>

    <el-radio-group v-model="inputMode" class="mb-3" :disabled="!sessionId">
      <el-radio-button label="curve">壁厚曲线校正</el-radio-button>
      <el-radio-button label="file">文件上传</el-radio-button>
    </el-radio-group>

    <!-- ═══════════ Curve editor ═══════════ -->
    <div v-if="inputMode === 'curve' && sessionId">
      <div class="toolbar">
        <el-button :loading="loading" @click="loadSegments" size="small" type="primary" plain>
          <span v-if="!curveData">自动识别壁厚分段</span>
          <span v-else>重新自动识别</span>
        </el-button>
        <el-button v-if="curveData" @click="resetToAuto" size="small">还原到自动结果</el-button>
        <span class="info-text" v-if="modelLength > 0">
          模型总长度: <b>{{ modelLength.toFixed(1) }} mm</b>
        </span>
        <span class="info-text" v-if="totalInputLength > 0">
          输入总长: <b>{{ totalInputLength.toFixed(1) }} mm</b>
          <span :class="lengthOk ? 'ok' : 'warn'">
            (差 {{ lengthDiff.toFixed(1) }} mm)
          </span>
        </span>
      </div>

      <!-- Curve canvas with interactive boundary draggers -->
      <div class="curve-card" v-if="curveData">
        <div class="canvas-wrap" ref="canvasWrapRef">
          <canvas
            ref="canvasRef"
            @pointerdown="onPointerDown"
            @pointermove="onPointerMove"
            @pointerup="onPointerUp"
            @pointerleave="onPointerUp"
          />
        </div>
        <div class="canvas-hint">
          - 拖拽 <b style="color:#1478ff">蓝色竖线</b> 调整分段边界<br>
          - 拖拽 <b style="color:#28b45a">绿色横线</b> 调整该平台段的壁厚（仅平台段可拖）<br>
          - 灰色细线 = 原始读数，蓝色粗线 = 自动平滑，橙色 = 用户校正后的曲线
        </div>
      </div>
      <div v-else class="curve-card placeholder">
        <el-empty description="点击 “自动识别壁厚分段” 开始" :image-size="60" />
      </div>

      <!-- Segments table -->
      <div class="segments-card" v-if="curveData">
        <table class="seg-table">
          <thead>
            <tr>
              <th>段</th>
              <th>类型</th>
              <th>起点(mm)</th>
              <th>长度(mm)</th>
              <th>终点(mm)</th>
              <th>壁厚(mm)</th>
              <th>统计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(seg, i) in segments" :key="i" :class="{ 'seg-flat': seg.label === 'flat', 'seg-trans': seg.label === 'transition' }">
              <td>{{ segLabel(i) }}</td>
              <td>
                <el-tag size="small" :type="seg.label === 'flat' ? 'success' : 'warning'" effect="plain">
                  {{ seg.label === 'flat' ? '平台' : '过渡' }}
                </el-tag>
              </td>
              <td>{{ cumulativeAt(i).toFixed(1) }}</td>
              <td>
                <el-input-number
                  v-model="seg.length_mm"
                  :min="0" :step="1" :precision="1"
                  controls-position="right"
                  size="small"
                  style="width: 100px"
                  @change="onSegmentEdit"
                />
              </td>
              <td>{{ cumulativeAt(i + 1).toFixed(1) }}</td>
              <td>
                <el-input-number
                  v-if="seg.label === 'flat'"
                  v-model="seg.mean_thickness"
                  :min="0" :step="0.1" :precision="2"
                  controls-position="right"
                  size="small"
                  style="width: 100px"
                  @change="onSegmentEdit"
                />
                <span v-else class="muted">—</span>
              </td>
              <td v-if="seg.label === 'flat'" class="muted small">
                平均 {{ seg.mean_thickness.toFixed(2) }} ± {{ seg.std_thickness.toFixed(2) }}<br>
                范围 [{{ seg.min_thickness.toFixed(2) }}, {{ seg.max_thickness.toFixed(2) }}]
              </td>
              <td v-else class="muted small">—</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Derived TL/T summary -->
      <div class="tl-summary" v-if="curveData">
        <div class="tl-row">
          <div class="tl-pill" v-for="key in ['TL1','TLM1','TL2','TLM2','TL3','TLM3','TL4']" :key="key">
            <span class="tl-key">{{ key }}</span>
            <span class="tl-val">{{ tlTValues[key]?.toFixed(1) ?? '0.0' }}</span>
            <span class="tl-unit">mm</span>
          </div>
        </div>
        <div class="tl-row">
          <div class="tl-pill thickness" v-for="key in ['T1','T2','T3','T4']" :key="key">
            <span class="tl-key">{{ key }}</span>
            <span class="tl-val">{{ tlTValues[key]?.toFixed(2) ?? '0.00' }}</span>
            <span class="tl-unit">mm</span>
          </div>
        </div>
      </div>

      <!-- Errors -->
      <el-alert
        v-if="errors.length > 0"
        title="输入验证失败"
        type="error"
        :closable="false"
        class="mt-3"
      >
        <template #default>
          <ul style="margin: 8px 0; padding-left: 20px;">
            <li v-for="(err, idx) in errors" :key="idx">{{ err }}</li>
          </ul>
        </template>
      </el-alert>

      <!-- Save -->
      <div class="actions mt-3" v-if="curveData">
        <el-button type="primary" :loading="saving" :disabled="!sessionId" @click="handleSave">
          保存壁厚校正结果
        </el-button>
        <el-text class="ml-2" :type="saveResult ? 'success' : 'info'" v-if="saveResult">
          已保存 {{ saveResult.data_points }} 个数据点 (总长 {{ saveResult.total_length?.toFixed(1) }} mm)
        </el-text>
      </div>
    </div>

    <!-- ═══════════ File upload mode ═══════════ -->
    <div v-if="inputMode === 'file' && sessionId">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept=".csv,.xlsx,.xls"
        :on-change="handleFileChange"
      >
        <el-icon class="el-icon--upload"><Upload /></el-icon>
        <div class="el-upload__text">拖拽文件到此处或 <em>点击上传</em></div>
        <template #tip>
          <div class="el-upload__tip">支持 CSV / XLSX / XLS，需含「中心线长度(mm)」和「外径周长(mm)」列</div>
        </template>
      </el-upload>
      <div v-if="selectedFile" class="mt-3">
        <p>已选择: <strong>{{ selectedFile.name }}</strong> ({{ (selectedFile.size / 1024).toFixed(1) }} KB)</p>
        <el-button type="primary" :loading="uploading" @click="handleFileUpload">上传并解析</el-button>
      </div>
      <el-alert v-if="fileUploadResult" :title="fileUploadResult.message" :type="fileUploadResult.success ? 'success' : 'error'" :closable="false" class="mt-2" />
    </div>

    <el-divider />
    <div class="mt-3">
      <el-button type="primary" size="large" :loading="applying" :disabled="!canApply" @click="handleApplyToOptimization">
        应用数据到工艺优化
      </el-button>
      <el-text type="info" class="ml-2" v-if="!canApply">请先保存数据后再应用</el-text>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import { ppApi } from '../../api/pp'

const router = useRouter()

// ── Session ──
const sessionId = ref('')
const modelLength = ref(0)
onMounted(() => {
  sessionId.value = sessionStorage.getItem('pp_session_id') || ''
  modelLength.value = parseFloat(sessionStorage.getItem('pp_model_length') || '0')
  if (sessionId.value) loadSegments()
})

const inputMode = ref('curve')
const loading = ref(false)
const saving = ref(false)
const uploading = ref(false)
const applying = ref(false)
const errors = ref([])
const saveResult = ref(null)
const fileUploadResult = ref(null)
const selectedFile = ref(null)
const dataSaved = ref(false)

// ── Curve data ──
// curveData: { curve: [{length_mm, thickness_raw, thickness_smoothed, perimeter_mm}], segments: [...], stats }
const curveData = ref(null)
const segments = ref([])         // editable segment list
const initialSegments = ref([])  // immutable copy from server (for reset)

// ── Canvas ──
const canvasRef = ref(null)
const canvasWrapRef = ref(null)
let resizeObserver = null
const dragState = reactive({ kind: null, idx: -1 })  // kind: 'boundary' | 'thickness'

// ── Computed ──
const tlTValues = computed(() => {
  const out = { TL1: 0, TLM1: 0, TL2: 0, TLM2: 0, TL3: 0, TLM3: 0, TL4: 0, T1: 0, T2: 0, T3: 0, T4: 0 }
  const tlKeys = ['TL1', 'TLM1', 'TL2', 'TLM2', 'TL3', 'TLM3', 'TL4']
  const tKeys = ['T1', 'T2', 'T3', 'T4']
  let flatIdx = 0
  for (let i = 0; i < segments.value.length && i < tlKeys.length; i++) {
    out[tlKeys[i]] = +segments.value[i].length_mm || 0
    if (segments.value[i].label === 'flat' && flatIdx < tKeys.length) {
      out[tKeys[flatIdx]] = +segments.value[i].mean_thickness || 0
      flatIdx++
    }
  }
  return out
})

const totalInputLength = computed(() =>
  segments.value.reduce((sum, s) => sum + (+s.length_mm || 0), 0)
)
const lengthDiff = computed(() =>
  modelLength.value > 0 ? Math.abs(totalInputLength.value - modelLength.value) : 0
)
const lengthOk = computed(() => modelLength.value <= 0 || lengthDiff.value <= 5)
const canApply = computed(() => dataSaved.value && sessionId.value)

function cumulativeAt(i) {
  let s = 0
  for (let j = 0; j < Math.min(i, segments.value.length); j++) s += +segments.value[j].length_mm || 0
  return s
}

function segLabel(i) {
  const keys = ['TL1', 'TLM1', 'TL2', 'TLM2', 'TL3', 'TLM3', 'TL4']
  return keys[i] || `S${i}`
}

// ── Load auto-detected segments ──
async function loadSegments() {
  if (!sessionId.value) return
  loading.value = true
  try {
    const res = await ppApi.thicknessSegments(sessionId.value)
    if (!res.data.success) {
      ElMessage.error(res.data.message || '识别失败')
      return
    }
    curveData.value = {
      curve: res.data.curve,
      stats: res.data.stats,
    }
    segments.value = res.data.segments.map(s => ({ ...s }))
    initialSegments.value = res.data.segments.map(s => ({ ...s }))
    ElMessage.success(`自动识别完成: ${segments.value.length} 段`)
    await nextTick()
    setupCanvas()
    drawCurve()
  } catch (e) {
    ElMessage.error('识别失败: ' + (e.message || ''))
  } finally {
    loading.value = false
  }
}

function resetToAuto() {
  segments.value = initialSegments.value.map(s => ({ ...s }))
  drawCurve()
}

function onSegmentEdit() {
  drawCurve()
}

// ── Canvas rendering ──
function setupCanvas() {
  if (resizeObserver) resizeObserver.disconnect()
  if (!canvasWrapRef.value) return
  resizeObserver = new ResizeObserver(() => drawCurve())
  resizeObserver.observe(canvasWrapRef.value)
}

// Coordinate helpers — defined as functions so they always read the latest state.
let viewState = { padL: 50, padR: 12, padT: 18, padB: 32, xMin: 0, xMax: 1, yMin: 0, yMax: 1, W: 0, H: 0 }

function recomputeView() {
  if (!canvasRef.value) return
  const canvas = canvasRef.value
  const dpr = window.devicePixelRatio || 1
  const cssW = canvas.parentElement?.clientWidth || 800
  const cssH = 280
  canvas.width = cssW * dpr
  canvas.height = cssH * dpr
  canvas.style.width = cssW + 'px'
  canvas.style.height = cssH + 'px'
  viewState.W = cssW
  viewState.H = cssH

  const xMin = 0
  const xMax = Math.max(totalInputLength.value, curveData.value?.curve?.[curveData.value.curve.length - 1]?.length_mm || 1)
  let yMin = Infinity, yMax = -Infinity
  for (const p of curveData.value?.curve || []) {
    if (p.thickness_raw < yMin) yMin = p.thickness_raw
    if (p.thickness_raw > yMax) yMax = p.thickness_raw
  }
  for (const seg of segments.value) {
    if (seg.label === 'flat') {
      if (seg.mean_thickness < yMin) yMin = seg.mean_thickness
      if (seg.mean_thickness > yMax) yMax = seg.mean_thickness
    }
  }
  if (!isFinite(yMin)) { yMin = 0; yMax = 1 }
  const margin = (yMax - yMin) * 0.25 || 0.5
  viewState.xMin = xMin
  viewState.xMax = xMax
  viewState.yMin = Math.max(0, yMin - margin)
  viewState.yMax = yMax + margin
}

function xToPx(x) {
  const { padL, padR, W, xMin, xMax } = viewState
  return padL + ((x - xMin) / (xMax - xMin)) * (W - padL - padR)
}
function pxToX(px) {
  const { padL, padR, W, xMin, xMax } = viewState
  return xMin + ((px - padL) / (W - padL - padR)) * (xMax - xMin)
}
function yToPx(y) {
  const { padT, padB, H, yMin, yMax } = viewState
  return padT + (1 - (y - yMin) / (yMax - yMin)) * (H - padT - padB)
}
function pxToY(px) {
  const { padT, padB, H, yMin, yMax } = viewState
  return yMin + (1 - (px - padT) / (H - padT - padB)) * (yMax - yMin)
}

function drawCurve() {
  if (!canvasRef.value || !curveData.value) return
  recomputeView()
  const canvas = canvasRef.value
  const ctx = canvas.getContext('2d')
  const dpr = window.devicePixelRatio || 1
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.scale(dpr, dpr)
  const { W, H, padL, padR, padT, padB } = viewState
  ctx.clearRect(0, 0, W, H)
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, W, H)

  // axes
  ctx.strokeStyle = '#dcdfe6'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(padL, padT)
  ctx.lineTo(padL, H - padB)
  ctx.lineTo(W - padR, H - padB)
  ctx.stroke()

  // y ticks
  ctx.fillStyle = '#909399'
  ctx.font = '10px sans-serif'
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  const yTicks = 5
  for (let t = 0; t <= yTicks; t++) {
    const yv = viewState.yMin + ((viewState.yMax - viewState.yMin) * t) / yTicks
    const py = yToPx(yv)
    ctx.fillText(yv.toFixed(2), padL - 6, py)
    ctx.strokeStyle = '#f0f2f5'
    ctx.beginPath(); ctx.moveTo(padL, py); ctx.lineTo(W - padR, py); ctx.stroke()
  }
  // x ticks
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  const xTicks = 8
  for (let t = 0; t <= xTicks; t++) {
    const xv = viewState.xMin + ((viewState.xMax - viewState.xMin) * t) / xTicks
    const px = xToPx(xv)
    ctx.fillStyle = '#909399'
    ctx.fillText(xv.toFixed(0), px, H - padB + 4)
  }
  ctx.save()
  ctx.translate(14, (padT + H - padB) / 2)
  ctx.rotate(-Math.PI / 2)
  ctx.fillStyle = '#606266'
  ctx.fillText('壁厚 (mm)', 0, 0)
  ctx.restore()
  ctx.fillText('中心线长度 (mm)', (padL + W - padR) / 2, H - 4)

  // Raw curve (grey)
  const curve = curveData.value.curve
  if (curve && curve.length) {
    ctx.strokeStyle = '#c0c4cc'
    ctx.lineWidth = 1
    ctx.beginPath()
    let first = true
    for (const p of curve) {
      const px = xToPx(p.length_mm), py = yToPx(p.thickness_raw)
      if (first) { ctx.moveTo(px, py); first = false } else { ctx.lineTo(px, py) }
    }
    ctx.stroke()
    // Smoothed (blue)
    ctx.strokeStyle = '#1478ff'
    ctx.lineWidth = 1.5
    ctx.beginPath()
    first = true
    for (const p of curve) {
      const px = xToPx(p.length_mm), py = yToPx(p.thickness_smoothed)
      if (first) { ctx.moveTo(px, py); first = false } else { ctx.lineTo(px, py) }
    }
    ctx.stroke()
  }

  // User-corrected piecewise curve (orange)
  const userCurve = buildUserCurve()
  if (userCurve.length > 1) {
    ctx.strokeStyle = '#ff9d2d'
    ctx.lineWidth = 2.5
    ctx.beginPath()
    for (let i = 0; i < userCurve.length; i++) {
      const px = xToPx(userCurve[i][0]), py = yToPx(userCurve[i][1])
      if (i === 0) ctx.moveTo(px, py)
      else ctx.lineTo(px, py)
    }
    ctx.stroke()
  }

  // Segment fills & flat plateau drag bars
  let xCursor = 0
  for (let i = 0; i < segments.value.length; i++) {
    const seg = segments.value[i]
    const x0 = xCursor
    const x1 = xCursor + (+seg.length_mm || 0)
    xCursor = x1
    if (x1 <= x0) continue
    const px0 = xToPx(x0), px1 = xToPx(x1)
    if (seg.label === 'flat') {
      ctx.fillStyle = 'rgba(40, 180, 90, 0.05)'
      ctx.fillRect(px0, padT, px1 - px0, H - padT - padB)
      // green draggable line at the mean thickness
      const py = yToPx(seg.mean_thickness)
      ctx.strokeStyle = '#28b45a'
      ctx.lineWidth = 3
      ctx.setLineDash([8, 4])
      ctx.beginPath(); ctx.moveTo(px0, py); ctx.lineTo(px1, py); ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = '#28b45a'
      ctx.font = 'bold 11px sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(`T${flatIndex(i)}=${seg.mean_thickness.toFixed(2)}`, (px0 + px1) / 2, py - 6)
    } else {
      ctx.fillStyle = 'rgba(245, 190, 45, 0.06)'
      ctx.fillRect(px0, padT, px1 - px0, H - padT - padB)
    }
  }

  // Boundary draggers (blue vertical lines)
  let cum = 0
  for (let i = 0; i <= segments.value.length; i++) {
    const isEnd = (i === 0 || i === segments.value.length)
    const px = xToPx(cum)
    ctx.strokeStyle = isEnd ? '#909399' : '#1478ff'
    ctx.lineWidth = isEnd ? 1 : 2
    ctx.beginPath()
    ctx.moveTo(px, padT - 2)
    ctx.lineTo(px, H - padB + 2)
    ctx.stroke()
    if (!isEnd) {
      ctx.fillStyle = '#1478ff'
      ctx.beginPath(); ctx.arc(px, padT + 6, 4, 0, Math.PI * 2); ctx.fill()
      ctx.beginPath(); ctx.arc(px, H - padB - 6, 4, 0, Math.PI * 2); ctx.fill()
    }
    if (i < segments.value.length) cum += +segments.value[i].length_mm || 0
  }
}

function flatIndex(segIdx) {
  let cnt = 0
  for (let i = 0; i <= segIdx; i++) {
    if (segments.value[i]?.label === 'flat') cnt++
  }
  return cnt
}

function buildUserCurve() {
  // Piecewise: flat = constant, transition = linear between previous and next flat plateaus
  const pts = []
  let x = 0
  let prevThickness = null
  for (let i = 0; i < segments.value.length; i++) {
    const seg = segments.value[i]
    const len = +seg.length_mm || 0
    if (seg.label === 'flat') {
      pts.push([x, seg.mean_thickness])
      pts.push([x + len, seg.mean_thickness])
      prevThickness = seg.mean_thickness
    } else {
      // Use neighbouring flat thicknesses
      const left = prevThickness ?? seg.mean_thickness
      // find next flat
      let right = left
      for (let j = i + 1; j < segments.value.length; j++) {
        if (segments.value[j].label === 'flat') { right = segments.value[j].mean_thickness; break }
      }
      pts.push([x, left])
      pts.push([x + len, right])
    }
    x += len
  }
  return pts
}

// ── Drag handlers ──
function getCanvasPoint(e) {
  const rect = canvasRef.value.getBoundingClientRect()
  return { x: e.clientX - rect.left, y: e.clientY - rect.top }
}

function hitTest(px, py) {
  // Check boundary handles first (top/bottom dots)
  let cum = 0
  for (let i = 0; i <= segments.value.length; i++) {
    const x = xToPx(cum)
    if (i > 0 && i < segments.value.length) {
      // Hit any point along the vertical line, with some tolerance
      if (Math.abs(px - x) <= 6) {
        return { kind: 'boundary', idx: i }
      }
    }
    if (i < segments.value.length) cum += +segments.value[i].length_mm || 0
  }
  // Check thickness plateau lines (within their x-span, ±4 px y-distance)
  let xCursor = 0
  for (let i = 0; i < segments.value.length; i++) {
    const seg = segments.value[i]
    const len = +seg.length_mm || 0
    if (seg.label === 'flat' && len > 0) {
      const x0 = xToPx(xCursor), x1 = xToPx(xCursor + len)
      const y = yToPx(seg.mean_thickness)
      if (px >= x0 - 4 && px <= x1 + 4 && Math.abs(py - y) <= 6) {
        return { kind: 'thickness', idx: i }
      }
    }
    xCursor += len
  }
  return null
}

function onPointerDown(e) {
  const { x, y } = getCanvasPoint(e)
  const hit = hitTest(x, y)
  if (!hit) return
  dragState.kind = hit.kind
  dragState.idx = hit.idx
  canvasRef.value.setPointerCapture(e.pointerId)
}

function onPointerMove(e) {
  const { x, y } = getCanvasPoint(e)
  if (dragState.kind == null) {
    canvasRef.value.style.cursor = hitTest(x, y) ? 'grab' : 'default'
    return
  }
  if (dragState.kind === 'boundary') {
    // Boundary `idx` separates segment idx-1 and segment idx.
    const newCum = Math.max(0, Math.min(viewState.xMax, pxToX(x)))
    // Compute desired left segment length
    let leftStart = 0
    for (let j = 0; j < dragState.idx - 1; j++) leftStart += +segments.value[j].length_mm || 0
    const oldLeftLen = +segments.value[dragState.idx - 1].length_mm || 0
    const oldRightLen = +segments.value[dragState.idx].length_mm || 0
    const total = oldLeftLen + oldRightLen
    let newLeft = newCum - leftStart
    newLeft = Math.max(0, Math.min(total, newLeft))
    segments.value[dragState.idx - 1].length_mm = +newLeft.toFixed(2)
    segments.value[dragState.idx].length_mm = +(total - newLeft).toFixed(2)
    drawCurve()
  } else if (dragState.kind === 'thickness') {
    const newT = Math.max(0, pxToY(y))
    segments.value[dragState.idx].mean_thickness = +newT.toFixed(2)
    drawCurve()
  }
  canvasRef.value.style.cursor = 'grabbing'
}

function onPointerUp(e) {
  if (dragState.kind != null && canvasRef.value) {
    try { canvasRef.value.releasePointerCapture(e.pointerId) } catch (_) {}
  }
  dragState.kind = null
  dragState.idx = -1
  if (canvasRef.value) canvasRef.value.style.cursor = 'default'
}

watch([segments, totalInputLength], () => drawCurve(), { deep: true })

// ── Save ──
async function handleSave() {
  if (!sessionId.value) {
    ElMessage.error('会话不存在')
    return
  }
  errors.value = []
  saveResult.value = null

  // Validate
  const v = tlTValues.value
  const errs = []
  if (v.T1 > 0 && !(v.TL1 > 0)) errs.push('请填写壁厚 T1 对应的长度 TL1。')
  if (v.T1 <= 0) errs.push('T1 壁厚必须大于 0')
  if (v.TLM1 > 0 && v.T2 <= 0) errs.push('TLM1 > 0 时 T2 必须 > 0')
  if (v.TLM2 > 0 && v.T3 <= 0) errs.push('TLM2 > 0 时 T3 必须 > 0')
  if (v.TLM3 > 0 && v.T4 <= 0) errs.push('TLM3 > 0 时 T4 必须 > 0')
  if (modelLength.value > 0 && Math.abs(totalInputLength.value - modelLength.value) > 5) {
    errs.push(`总长度与模型相差 ${lengthDiff.value.toFixed(1)} mm > 5mm`)
  }
  if (errs.length) {
    errors.value = errs
    return
  }

  saving.value = true
  try {
    const res = await ppApi.saveManualParams(sessionId.value, v)
    if (res.data.success) {
      saveResult.value = res.data
      dataSaved.value = true
      ElMessage.success(res.data.message || '保存成功')
    } else {
      errors.value = res.data.errors || [res.data.message || '保存失败']
    }
  } catch (e) {
    errors.value = [e.response?.data?.message || e.message || '保存异常']
  } finally {
    saving.value = false
  }
}

// ── File upload ──
function handleFileChange(file) {
  selectedFile.value = file.raw
  fileUploadResult.value = null
}
async function handleFileUpload() {
  if (!selectedFile.value || !sessionId.value) return
  uploading.value = true
  const formData = new FormData()
  formData.append('file', selectedFile.value)
  formData.append('session_id', sessionId.value)
  try {
    const res = await ppApi.uploadManualFile(formData)
    fileUploadResult.value = { success: res.data.success, message: res.data.message }
    if (res.data.success) {
      dataSaved.value = true
      ElMessage.success(res.data.message)
    } else {
      ElMessage.error(res.data.message)
    }
  } catch (e) {
    fileUploadResult.value = { success: false, message: e.message }
  } finally {
    uploading.value = false
  }
}

async function handleApplyToOptimization() {
  if (!dataSaved.value || !sessionId.value) return
  applying.value = true
  try {
    const res = await ppApi.applyToTab3(sessionId.value)
    if (res.data.success) {
      ElMessage.success('已解锁工艺参数优化')
      router.push('/pp/optimization')
    } else {
      ElMessage.error(res.data.message)
    }
  } catch (e) {
    ElMessage.error('应用失败')
  } finally {
    applying.value = false
  }
}

function goToUpload() {
  router.push('/upload/model')
}

onUnmounted(() => {
  if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null }
})
</script>

<style scoped>
.manual-input-page {
  padding: 16px;
}
.mb-3 { margin-bottom: 12px; }
.mt-3 { margin-top: 12px; }
.mt-2 { margin-top: 8px; }
.ml-2 { margin-left: 8px; }

.toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.info-text { font-size: 13px; color: #606266; }
.info-text .ok { color: #28b45a; margin-left: 4px; }
.info-text .warn { color: #f56c6c; margin-left: 4px; }

.curve-card {
  background: #ffffff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 8px;
}
.curve-card.placeholder { display: flex; justify-content: center; padding: 24px; }
.canvas-wrap { position: relative; width: 100%; }
.canvas-wrap canvas { display: block; width: 100%; height: 280px; cursor: default; touch-action: none; }
.canvas-hint { font-size: 11px; color: #909399; padding: 6px 8px 2px; line-height: 1.6; }

.segments-card {
  margin-top: 12px;
  background: #ffffff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow-x: auto;
}
.seg-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.seg-table th, .seg-table td {
  border-bottom: 1px solid #f0f2f5;
  padding: 6px 8px;
  text-align: center;
  vertical-align: middle;
}
.seg-table th { background: #fafafa; color: #909399; font-weight: 500; }
.seg-table tr.seg-flat { background: rgba(40, 180, 90, 0.03); }
.seg-table tr.seg-trans { background: rgba(245, 190, 45, 0.04); }
.muted { color: #c0c4cc; }
.small { font-size: 11px; }

.tl-summary {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tl-row { display: flex; gap: 6px; flex-wrap: wrap; }
.tl-pill {
  display: inline-flex; align-items: baseline; gap: 4px;
  padding: 4px 10px;
  border-radius: 14px;
  background: #eef4ff; color: #1478ff;
  font-size: 12px;
}
.tl-pill.thickness { background: #ecf9f1; color: #28b45a; }
.tl-key { font-weight: 600; }
.tl-val { font-weight: 600; }
.tl-unit { opacity: 0.6; font-size: 10px; }

.actions { display: flex; align-items: center; gap: 8px; }
</style>
