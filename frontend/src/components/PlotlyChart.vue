<template>
  <div ref="el" class="plotly-chart" :style="{ height: height + 'px' }" />
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import Plotly from 'plotly.js-dist-min'

const props = defineProps({
  figure: { type: Object, default: null },
  height: { type: Number, default: 400 },
})

const el = ref(null)
let resizeHandler = null

function render() {
  if (!props.figure || !el.value) return
  const layout = { ...props.figure.layout, height: props.height, margin: { l: 50, r: 30, t: 40, b: 50 } }
  Plotly.newPlot(el.value, props.figure.data, layout, { responsive: true, displayModeBar: false })
}

function handleResize() {
  if (el.value) Plotly.Plots.resize(el.value)
}

onMounted(() => {
  render()
  resizeHandler = handleResize
  window.addEventListener('resize', resizeHandler)
})

onBeforeUnmount(() => {
  if (el.value) Plotly.purge(el.value)
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
})

watch(() => props.figure, (newVal, oldVal) => {
  // Only re-render if the figure reference actually changed (avoid deep re-renders)
  if (newVal !== oldVal) {
    render()
  }
})
</script>

<style scoped>
.plotly-chart { width: 100%; }
</style>
