<template>
  <div class="pp-chart-container">
    <canvas ref="canvasRef"></canvas>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { Chart, registerables } from 'chart.js'

Chart.register(...registerables)

const props = defineProps({
  type: { type: String, default: 'perimeter' }, // 'perimeter' | 'thickness'
  sections: { type: Array, default: () => [] },
  thicknessStats: { type: Object, default: null },
  suspectSegments: { type: Array, default: () => [] },
  straightSegments: { type: Array, default: () => [] },
  rawDiameters: { type: Array, default: () => [] },
  previewDiameters: { type: Array, default: () => [] },
  showRaw: { type: Boolean, default: false },
  showPreview: { type: Boolean, default: false },
  anomalyIndices: { type: Array, default: () => [] },
  profileSource: { type: String, default: '' },
  qaStatus: { type: String, default: '' },
})

const emit = defineEmits(['hover-section', 'click-section'])

const canvasRef = ref(null)
let chart = null

const chartColors = {
  perimeter: '#1478ff',
  perimeterBg: 'rgba(20,120,255,0.1)',
  thickness: '#28b45a',
  thicknessBg: 'rgba(40,180,90,0.1)',
  median: '#f5be2d',
  hover: '#ffdd00',
  grid: '#32445d',
  text: '#9fb0c7',
  tooltipBg: 'rgba(16,23,34,0.9)',
  tooltipText: '#e7edf7',
  tooltipBody: '#9fb0c7',
  tooltipBorder: '#32445d',
  raw: '#9fb0c7',
  preview: '#e6a23c',
  suspect: 'rgba(245, 108, 108, 0.22)',
  straight: 'rgba(103, 194, 58, 0.18)',
}

const chartDefaults = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 400 },
  plugins: {
    legend: { display: false },
    tooltip: {
      enabled: true,
      backgroundColor: chartColors.tooltipBg,
      titleColor: chartColors.tooltipText,
      bodyColor: chartColors.tooltipBody,
      borderColor: chartColors.tooltipBorder,
      borderWidth: 1,
      cornerRadius: 4,
    },
  },
  scales: {
    x: {
      grid: { color: chartColors.grid },
      ticks: { color: chartColors.text, font: { size: 10 } },
    },
    y: {
      grid: { color: chartColors.grid },
      ticks: { color: chartColors.text, font: { size: 10 } },
    },
  },
}

onMounted(() => {
  renderChart()
})

onBeforeUnmount(() => {
  if (chart) chart.destroy()
})

watch(() => [
  props.sections, props.suspectSegments, props.straightSegments,
  props.rawDiameters, props.previewDiameters, props.showRaw, props.showPreview,
  props.qaStatus, props.profileSource, props.thicknessStats,
], () => {
  renderChart()
}, { deep: true })

function renderChart() {
  if (!canvasRef.value || !props.sections.length) return
  if (chart) chart.destroy()

  const ctx = canvasRef.value.getContext('2d')

  if (props.type === 'perimeter') {
    renderPerimeterChart(ctx)
  } else {
    renderThicknessChart(ctx)
  }
}

function sectionIndexForS(sVal) {
  const arr = props.sections || []
  if (!arr.length) return 0
  let lo = 0
  let hi = arr.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (Number(arr[mid].cumulative_length || 0) < sVal) lo = mid + 1
    else hi = mid
  }
  if (lo > 0) {
    const a = Number(arr[lo - 1].cumulative_length || 0)
    const b = Number(arr[lo].cumulative_length || 0)
    return Math.abs(sVal - a) <= Math.abs(sVal - b) ? lo - 1 : lo
  }
  return lo
}

function bandIndexRange(seg) {
  if (seg.start_s != null && seg.end_s != null) {
    return [sectionIndexForS(Number(seg.start_s)), sectionIndexForS(Number(seg.end_s))]
  }
  return [seg.start_index ?? 0, seg.end_index ?? 0]
}

function visibleStraightSegments() {
  const segs = props.straightSegments || []
  if (props.qaStatus === 'QA_PASS') return segs.filter((s) => s.status === 'confirmed')
  return segs.filter((s) => s.status !== 'cancelled')
}

function qaBandPlugin() {
  return {
    id: 'qaBands',
    beforeDraw(chart) {
      if (props.type !== 'perimeter') return
      const { ctx, chartArea, scales } = chart
      if (!chartArea || !scales?.x) return
      const n = props.sections.length
      if (!n) return
      const xOf = (idx) => {
        const v = scales.x.getPixelForValue(idx)
        return Math.max(chartArea.left, Math.min(chartArea.right, v))
      }
      const paint = (startIdx, endIdx, fill) => {
        const x0 = xOf(startIdx)
        const x1 = xOf(endIdx)
        ctx.save()
        ctx.fillStyle = fill
        ctx.fillRect(Math.min(x0, x1), chartArea.top, Math.abs(x1 - x0) || 2, chartArea.bottom - chartArea.top)
        ctx.restore()
      }
      for (const seg of visibleStraightSegments()) {
        const [i0, i1] = bandIndexRange(seg)
        paint(i0, i1, chartColors.straight)
      }
      if (props.qaStatus === 'QA_LOCAL_ANOMALY' || props.qaStatus === 'QA_MANUAL_REQUIRED' || props.qaStatus === 'QA_RECHECK_FAILED') {
        for (const seg of props.suspectSegments || []) {
          const [i0, i1] = bandIndexRange(seg)
          paint(i0, i1, chartColors.suspect)
        }
      }
    },
  }
}

function renderPerimeterChart(ctx) {
  const labels = props.sections.map(s => s.cumulative_length?.toFixed(0) || '0')
  const data = props.sections.map(s => s.perimeter || 0)
  const passed = props.qaStatus === 'QA_PASS'
  const mainLabel = passed
    ? '确认后形线'
    : (props.profileSource === 'stp_smoothed' ? '平滑后形线' : '当前形线（周长）')
  const datasets = [{
    label: mainLabel,
    data,
    borderColor: chartColors.perimeter,
    backgroundColor: chartColors.perimeterBg,
    borderWidth: passed ? 2.4 : 1.5,
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHoverBackgroundColor: chartColors.hover,
    fill: true,
    tension: passed ? 0 : 0.2,
  }]
  if (props.showRaw && props.rawDiameters?.length) {
    datasets.push({
      label: '原始 STP 外径',
      data: props.rawDiameters.map(d => (d || 0) * Math.PI),
      borderColor: chartColors.raw,
      borderWidth: 1,
      borderDash: [4, 3],
      pointRadius: 0,
      fill: false,
      tension: 0,
    })
  }
  if (props.showPreview && props.previewDiameters?.length && (props.qaStatus === 'QA_LOCAL_ANOMALY' || props.qaStatus === 'QA_MANUAL_REQUIRED')) {
    datasets.push({
      label: '建议平滑外径',
      data: props.previewDiameters.map(d => (d || 0) * Math.PI),
      borderColor: chartColors.preview,
      borderWidth: 1.5,
      borderDash: [6, 3],
      pointRadius: 0,
      fill: false,
      tension: 0,
    })
  }

  const anomalySet = new Set(props.anomalyIndices || [])

  chart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    plugins: [qaBandPlugin()],
    options: {
      ...chartDefaults,
      interaction: { mode: 'index', intersect: false },
      onHover: (event, elements) => {
        if (elements.length > 0) {
          emit('hover-section', elements[0].index)
        } else {
          emit('hover-section', -1)
        }
      },
      onClick: (event, elements) => {
        if (elements.length > 0) {
          emit('click-section', elements[0].index)
        }
      },
      scales: {
        ...chartDefaults.scales,
        x: {
          ...chartDefaults.scales.x,
          title: { display: true, text: '累计长度 (mm)', color: chartColors.text, font: { size: 10 } }
        },
        y: {
          ...chartDefaults.scales.y,
          title: { display: true, text: '周长 (mm)', color: chartColors.text, font: { size: 10 } }
        },
      },
      plugins: {
        ...chartDefaults.plugins,
        legend: {
          display: datasets.length > 1 || passed,
          labels: { color: chartColors.text, font: { size: 10 }, boxWidth: 12 },
        },
        tooltip: {
          ...chartDefaults.plugins.tooltip,
          callbacks: {
            title(items) {
              const idx = items[0]?.dataIndex ?? 0
              const sec = props.sections[idx] || {}
              return `中心线 ${Number(sec.cumulative_length || 0).toFixed(1)} mm`
            },
            label(item) {
              return `${item.dataset.label}: ${Number(item.raw || 0).toFixed(2)} mm`
            },
            afterBody(items) {
              const idx = items[0]?.dataIndex ?? 0
              const sec = props.sections[idx] || {}
              const od = (sec.perimeter || 0) / Math.PI
              const anom = anomalySet.has(idx) ? '是' : '否'
              const sVal = Number(sec.cumulative_length || 0)
              const straight = (props.straightSegments || []).find((s) => {
                if (s.start_s != null && s.end_s != null) {
                  return sVal >= Number(s.start_s) && sVal <= Number(s.end_s)
                }
                return idx >= (s.start_index || 0) && idx <= (s.end_index || 0)
              })
              const lines = [
                `外径: ${od.toFixed(3)} mm`,
                `数据来源: ${props.profileSource || 'stp'}`,
                `是否异常: ${anom}`,
              ]
              if (straight) lines.push(`所属 segment: ${straight.segment_id} (${straight.status})`)
              return lines
            },
          },
        },
      },
    },
  })
}

function renderThicknessChart(ctx) {
  const valid = props.sections.filter(s => s.wall_thickness != null)
  if (!valid.length) return

  const labels = valid.map(s => s.cumulative_length?.toFixed(0) || '0')
  const data = valid.map(s => s.wall_thickness || 0)
  const medianVal = props.thicknessStats?.median_mm

  const datasets = [{
    data,
    borderColor: chartColors.thickness,
    backgroundColor: chartColors.thicknessBg,
    borderWidth: 1.5,
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHoverBackgroundColor: chartColors.hover,
    fill: true,
    tension: 0.2,
  }]

  if (medianVal != null) {
    datasets.push({
      data: Array(labels.length).fill(medianVal),
      borderColor: chartColors.median,
      borderWidth: 1.5,
      borderDash: [6, 3],
      pointRadius: 0,
      fill: false,
    })
  }

  chart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      ...chartDefaults,
      interaction: { mode: 'index', intersect: false },
      onHover: (event, elements) => {
        if (elements.length > 0) {
          // Map back to original index
          const chartIdx = elements[0].index
          const sec = valid[chartIdx]
          const realIdx = props.sections.indexOf(sec)
          emit('hover-section', realIdx >= 0 ? realIdx : chartIdx)
        } else {
          emit('hover-section', -1)
        }
      },
      onClick: (event, elements) => {
        const chartIdx = elements.length > 0 ? elements[0].index : -1
        if (chartIdx >= 0) {
          const sec = valid[chartIdx]
          const realIdx = props.sections.indexOf(sec)
          emit('click-section', realIdx >= 0 ? realIdx : chartIdx)
        }
      },
      scales: {
        ...chartDefaults.scales,
        x: {
          ...chartDefaults.scales.x,
          title: { display: true, text: '累计长度 (mm)', color: chartColors.text, font: { size: 10 } }
        },
        y: {
          ...chartDefaults.scales.y,
          title: { display: true, text: '壁厚 (mm)', color: chartColors.text, font: { size: 10 } }
        },
      },
      plugins: {
        ...chartDefaults.plugins,
        legend: {
          display: medianVal != null,
          labels: {
            color: chartColors.text,
            font: { size: 10 },
            usePointStyle: true,
            generateLabels: () => [{
              text: `中位数 ${medianVal?.toFixed(3)} mm`,
              fillStyle: chartColors.median,
              strokeStyle: chartColors.median,
              lineWidth: 1.5,
              lineDash: [6, 3]
            }]
          }
        },
      },
    },
  })
}
</script>

<style scoped>
.pp-chart-container {
  position: relative;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 250px;
  overflow: hidden;
  background: rgba(16, 23, 34, 0.5);
  border-radius: 8px;
}
.pp-chart-container canvas {
  position: absolute;
  inset: 8px;
  display: block;
  max-width: 100%;
  max-height: 100%;
}
</style>
