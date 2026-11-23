<template>
  <div class="stl-viewer" :style="{ height: height + 'px' }">
    <div ref="containerRef" class="surface" />
    <div v-if="error" class="message">{{ error }}</div>
    <div v-else-if="loading" class="message">加载模型…</div>
    <div class="legend">拖动旋转 · 滚轮缩放<span v-if="cutaway"> · Y 中面剖视（不改变导出文件）</span></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { SVGRenderer } from 'three/examples/jsm/renderers/SVGRenderer.js'

const props = defineProps({
  url: { type: String, default: '' },
  data: { type: [ArrayBuffer, null], default: null },
  height: { type: [Number, String], default: 400 },
  color: { type: String, default: '#1890ff' },
  cutaway: { type: Boolean, default: false },
})
const containerRef = ref(null)
const loading = ref(false)
const error = ref('')
let scene, camera, renderer, controls, mesh, source, resizeObserver, requestController
let disposed = false
let renderFrame = 0

function render() {
  if (disposed || !renderer || renderFrame) return
  renderFrame = requestAnimationFrame(() => {
    renderFrame = 0
    renderer.render(scene, camera)
  })
}

function initScene() {
  if (renderer || disposed || !containerRef.value) return
  scene = new THREE.Scene()
  scene.background = new THREE.Color('#eef2f7')
  camera = new THREE.PerspectiveCamera(40, 1, .1, 10000)
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  } catch {
    renderer = new SVGRenderer()
    renderer.setQuality('high')
  }
  containerRef.value.appendChild(renderer.domElement)
  controls = new OrbitControls(camera, renderer.domElement)
  controls.addEventListener('change', render)
  scene.add(new THREE.AmbientLight(0xffffff, .65))
  const light = new THREE.DirectionalLight(0xffffff, .8)
  light.position.set(1, 2, 3)
  scene.add(light)
  resizeObserver = new ResizeObserver(() => {
    const width = containerRef.value?.clientWidth || 1
    const height = containerRef.value?.clientHeight || 1
    renderer.setSize(width, height)
    camera.aspect = width / height
    camera.updateProjectionMatrix()
    render()
  })
  resizeObserver.observe(containerRef.value)
}

// Clip triangles geometrically, so the same cutaway works in WebGL and SVG.
function displayedGeometry() {
  if (!props.cutaway) return source.clone()
  const pos = source.getAttribute('position')
  const result = []
  for (let i = 0; i < pos.count; i += 3) {
    const triangle = [0, 1, 2].map(k => new THREE.Vector3().fromBufferAttribute(pos, i + k))
    const clipped = []
    for (let j = 0; j < 3; j++) {
      const a = triangle[j], b = triangle[(j + 1) % 3]
      if (a.y <= 0) clipped.push(a)
      if ((a.y <= 0) !== (b.y <= 0)) clipped.push(a.clone().lerp(b, -a.y / (b.y - a.y)))
    }
    for (let j = 1; j + 1 < clipped.length; j++) {
      for (const p of [clipped[0], clipped[j], clipped[j + 1]]) result.push(p.x, p.y, p.z)
    }
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(result, 3))
  geometry.computeVertexNormals()
  return geometry
}

function showGeometry(fit = false) {
  if (!source || disposed) return
  if (mesh) {
    scene.remove(mesh)
    mesh.geometry.dispose()
    mesh.material.dispose()
  }
  mesh = new THREE.Mesh(displayedGeometry(), new THREE.MeshLambertMaterial({
    color: props.color, side: THREE.DoubleSide,
  }))
  scene.add(mesh)
  if (fit) {
    source.computeBoundingBox()
    const size = source.boundingBox.getSize(new THREE.Vector3())
    const radius = size.length() / 2
    const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2))
    const direction = size.x >= size.y && size.x >= size.z
      ? new THREE.Vector3(.25, 1, .8)
      : size.z >= size.y ? new THREE.Vector3(1, .8, .25) : new THREE.Vector3(1, .25, .8)
    camera.position.copy(direction.normalize().multiplyScalar(dist * 1.05))
    camera.near = Math.max(.01, radius / 1000)
    camera.far = Math.max(1000, dist * 10)
    camera.updateProjectionMatrix()
    controls.target.set(0, 0, 0)
    controls.update()
  }
  render()
}

function loadFromData(buffer) {
  if (disposed || !buffer?.byteLength) return
  initScene()
  const geometry = new STLLoader().parse(buffer)
  if (!geometry.getAttribute('position')?.count) throw new Error('模型没有可显示的三角面')
  geometry.center()
  source?.dispose()
  source = geometry.index ? geometry.toNonIndexed() : geometry
  showGeometry(true)
}

async function loadFromUrl(url) {
  requestController?.abort()
  if (!url) return
  const controller = new AbortController()
  requestController = controller
  loading.value = true
  error.value = ''
  try {
    const token = localStorage.getItem('authToken')
    const response = await fetch(url, { credentials: 'include', signal: controller.signal,
      headers: token ? { 'X-Auth-Token': token } : {} })
    if (!response.ok) throw new Error(`模型加载失败 (${response.status})`)
    const buffer = await response.arrayBuffer()
    if (!controller.signal.aborted && !disposed) loadFromData(buffer)
  } catch (e) {
    if (e.name !== 'AbortError') error.value = e.message
  } finally {
    if (requestController === controller) loading.value = false
  }
}

onMounted(() => {
  initScene()
  if (props.url) loadFromUrl(props.url)
  else if (props.data) loadFromData(props.data)
})
watch(() => props.url, loadFromUrl)
watch(() => props.data, loadFromData)
watch(() => props.cutaway, () => showGeometry())
onBeforeUnmount(() => {
  disposed = true
  requestController?.abort()
  resizeObserver?.disconnect()
  cancelAnimationFrame(renderFrame)
  controls?.dispose()
  source?.dispose()
  mesh?.geometry.dispose()
  mesh?.material.dispose()
  renderer?.dispose?.()
  renderer?.forceContextLoss?.()
})
defineExpose({ loadFromUrl, loadFromData, initScene })
</script>

<style scoped>
.stl-viewer { width: 100%; position: relative; border: 1px solid #d8e1ec; border-radius: 6px; overflow: hidden; }
.surface { width: 100%; height: 100%; }
.message { position: absolute; left: 16px; top: 16px; color: #a33; background: white; padding: 8px; }
.legend { position: absolute; bottom: 8px; left: 12px; font-size: 12px; color: #52647a; pointer-events: none; }
</style>
