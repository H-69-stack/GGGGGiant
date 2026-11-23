<template>
  <div>
    <div v-if="softwareRendering" style="font-size: 12px; color: #64748b; margin-bottom: 6px">WebGL 不可用：矢量三维预览（可拖动旋转）</div>
    <div ref="containerRef" class="pp-3d-viewer" :style="{ height: height + 'px' }"></div>
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
  height: { type: Number, default: 500 },
  color: { type: String, default: '#409EFF' }
})

const emit = defineEmits(['section-hover', 'section-click', 'model-loaded'])

const containerRef = ref(null)
const softwareRendering = ref(false)
let scene, camera, renderer, controls, mesh
let animationFrame, resizeObserver
let centerlineObj = null, thicknessObj = null, planesObj = null, highlightObj = null, sectionsObj = null

// Color constants (matching legacy tooling palette)
const COLORS = {
  centerline: 0xe11d48,
  outerEdge: 0xff2222,
  sectionEdge: 0xff6644,
  thicknessCore: [0.16, 0.71, 0.35],    // #28b45a
  thicknessWarning: [0.96, 0.75, 0.18], // #f5be2d
  thicknessAnomaly: [0.86, 0.16, 0.16], // #dc2828
  highlight: 0xffdd00,
}

onMounted(() => {
  initScene()
  if (props.url) loadFromUrl(props.url)
  else if (props.data) loadFromData(props.data)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(animationFrame)
  resizeObserver?.disconnect()
  if (renderer) {
    renderer.dispose?.()
    renderer.forceContextLoss?.()
  }
  if (controls) controls.dispose()
})

watch(() => props.url, (val) => { if (val) loadFromUrl(val) })
watch(() => props.data, (val) => { if (val) loadFromData(val) })

function initScene() {
  const container = containerRef.value
  const width = container.clientWidth
  const height = container.clientHeight

  scene = new THREE.Scene()
  scene.background = new THREE.Color(0xffffff)

  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100000)
  camera.position.set(200, 150, 200)

  try {
    renderer = new THREE.WebGLRenderer({ antialias: true })
  } catch {
    softwareRendering.value = true
    renderer = new SVGRenderer()
  }
  renderer.setSize(width, height)
  renderer.setPixelRatio?.(window.devicePixelRatio)
  container.appendChild(renderer.domElement)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = !softwareRendering.value
  controls.dampingFactor = 0.08
  if (softwareRendering.value) controls.addEventListener('change', renderSoftware)

  // Lights
  scene.add(new THREE.AmbientLight(0xffffff, 0.8))
  const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.6)
  dirLight1.position.set(1, 2, 1.5)
  scene.add(dirLight1)
  const dirLight2 = new THREE.DirectionalLight(0xc0c0c0, 0.3)
  dirLight2.position.set(-1, 0.5, -1)
  scene.add(dirLight2)

  // Grid
  if (!softwareRendering.value) scene.add(new THREE.GridHelper(500, 50, 0xdddddd, 0xeeeeee))

  resizeObserver = new ResizeObserver(() => {
    const w = container.clientWidth, h = container.clientHeight
    if (!w || !h) return
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
    renderSoftware()
  })
  resizeObserver.observe(container)

  if (softwareRendering.value) renderSoftware()
  else animate()
}

function renderSoftware() {
  if (!softwareRendering.value) return
  cancelAnimationFrame(animationFrame)
  animationFrame = requestAnimationFrame(() => renderer.render(scene, camera))
}

function animate() {
  animationFrame = requestAnimationFrame(animate)
  controls.update()
  renderer.render(scene, camera)
}

function loadFromUrl(url) {
  const loader = new STLLoader()
  loader.load(url, (geometry) => {
    addGeometry(geometry)
  })
}

function loadFromData(data) {
  const loader = new STLLoader()
  const geometry = loader.parse(data)
  addGeometry(geometry)
}

function addGeometry(geometry) {
  if (mesh) scene.remove(mesh)

  const Material = softwareRendering.value ? THREE.MeshBasicMaterial : THREE.MeshStandardMaterial
  const material = new Material({
    color: new THREE.Color(props.color),
    ...(softwareRendering.value ? {} : { roughness: 0.72, metalness: 0.1 }),
    transparent: true,
    opacity: 0.28,
    side: THREE.DoubleSide
  })
  mesh = new THREE.Mesh(geometry, material)
  mesh.name = 'tubeMesh'

  // Compute bounding box for camera positioning only, no translation
  geometry.computeBoundingBox()
  const box = geometry.boundingBox
  const center = new THREE.Vector3()
  box.getCenter(center)

  const size = new THREE.Vector3()
  box.getSize(size)
  const maxDim = Math.max(size.x, size.y, size.z)
  const fitHeight = maxDim * 1.8
  camera.position.set(center.x + fitHeight * 0.5, center.y + fitHeight * 0.5, center.z + fitHeight)
  camera.lookAt(center)
  camera.near = 0.1
  camera.far = maxDim * 10
  camera.updateProjectionMatrix()

  controls.target.copy(center)
  controls.update()

  scene.add(mesh)
  console.log('[Pp3dViewer] model loaded, emitting event')
  emit('model-loaded')
  renderSoftware()
}

// ── Public methods ──

function displayCenterline(points) {
  console.log('[Pp3dViewer] displayCenterline called with', points?.length, 'points')
  if (!points || points.length < 2) {
    console.warn('[Pp3dViewer] not enough points for centerline')
    return
  }
  clearCenterline()

  const pos = points.flatMap(p => [p[0], p[1], p[2]])
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))

  centerlineObj = new THREE.Group()
  centerlineObj.name = 'centerLine'
  centerlineObj.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: COLORS.centerline, depthTest: false })))
  centerlineObj.add(new THREE.Points(g.clone(), new THREE.PointsMaterial({
    color: COLORS.centerline, size: 2, sizeAttenuation: false, depthTest: false,
  })))
  centerlineObj.children.forEach(child => { child.renderOrder = 100 })
  scene.add(centerlineObj)
  renderSoftware()
  console.log('[Pp3dViewer] centerline added to scene')
}

function displayThicknessPoints(samples) {
  if (!samples || samples.length === 0) return
  clearThicknessPoints()

  const pos = [], colors = []
  for (const s of samples) {
    pos.push(s.x, s.y, s.z)
    const c = COLORS[`thickness${s.color}`] || COLORS.thicknessCore
    colors.push(c[0], c[1], c[2])
  }

  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  g.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))

  thicknessObj = new THREE.Points(g, new THREE.PointsMaterial({
    size: 4, vertexColors: true, sizeAttenuation: true,
  }))
  thicknessObj.name = 'thicknessPoints'
  scene.add(thicknessObj)
}

function displayCuttingPlanes(planes) {
  if (!planes || planes.length === 0) return
  clearPlanes()

  planesObj = new THREE.Group()
  planesObj.name = 'cuttingPlanes'

  for (const plane of planes) {
    if (plane.outer_edge && plane.outer_edge.length > 2) {
      const edgePos = plane.outer_edge.flatMap(p => [p[0], p[1], p[2]])
      const edgeG = new THREE.BufferGeometry()
      edgeG.setAttribute('position', new THREE.Float32BufferAttribute(edgePos, 3))

      // Red outline
      const redMat = new THREE.LineBasicMaterial({ color: COLORS.outerEdge, linewidth: 3, transparent: true, opacity: 0.9 })
      planesObj.add(new THREE.LineLoop(edgeG, redMat))

      // Translucent fill
      const fillVerts = []
      const center = plane.position
      const pts = plane.outer_edge
      for (let i = 0; i < pts.length; i++) {
        const next = (i + 1) % pts.length
        fillVerts.push(center[0], center[1], center[2])
        fillVerts.push(pts[i][0], pts[i][1], pts[i][2])
        fillVerts.push(pts[next][0], pts[next][1], pts[next][2])
      }
      const fillG = new THREE.BufferGeometry()
      fillG.setAttribute('position', new THREE.Float32BufferAttribute(fillVerts, 3))
      const fillMat = new THREE.MeshBasicMaterial({
        color: COLORS.outerEdge, transparent: true, opacity: 0.06, side: THREE.DoubleSide,
      })
      planesObj.add(new THREE.Mesh(fillG, fillMat))
    }
  }
  scene.add(planesObj)
}

function displaySectionsFromData(sections) {
  console.log('[Pp3dViewer] displaySectionsFromData called with', sections?.length, 'sections')
  if (!sections || sections.length === 0) {
    console.warn('[Pp3dViewer] no sections to display')
    return
  }
  clearSections()

  sectionsObj = new THREE.Group()
  sectionsObj.name = 'crossSections'

  let addedCount = 0
  for (let i = 0; i < sections.length; i++) {
    const sec = sections[i]
    console.log(`[Pp3dViewer] section ${i}: outline points =`, sec.outline?.length)
    if (sec.outline && sec.outline.length > 2) {
      const edgePos = sec.outline.flatMap(p => [p[0], p[1], p[2]])
      const edgeG = new THREE.BufferGeometry()
      edgeG.setAttribute('position', new THREE.Float32BufferAttribute(edgePos, 3))

      const mat = new THREE.LineBasicMaterial({
        color: COLORS.sectionEdge,
        linewidth: 1,
        transparent: true,
        opacity: 0.7,
      })
      sectionsObj.add(new THREE.LineLoop(edgeG, mat))
      addedCount++
    }
  }

  scene.add(sectionsObj)
  console.log('[Pp3dViewer] sections added to scene:', addedCount)
}

function highlightSection(idx, sectionsFull) {
  hideHighlight()
  if (!sectionsFull || !sectionsFull[idx]) return

  const sec = sectionsFull[idx]
  const outline = sec.outline_3d || sec.outer_edge
  if (outline && outline.length > 2) {
    const pts = outline.flatMap(p => [p[0], p[1], p[2]])
    const geom = new THREE.BufferGeometry()
    geom.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))

    const mat = new THREE.LineBasicMaterial({ color: COLORS.highlight, linewidth: 2, depthTest: false })
    highlightObj = new THREE.LineLoop(geom, mat)
    highlightObj.renderOrder = 999
    scene.add(highlightObj)
    renderSoftware()
  }
}

function hideHighlight() {
  if (highlightObj) {
    highlightObj.geometry?.dispose()
    highlightObj.material?.dispose()
    scene.remove(highlightObj)
    highlightObj = null
    renderSoftware()
  }
}

function clearCenterline() {
  if (centerlineObj) {
    centerlineObj.traverse(c => { c.geometry?.dispose(); c.material?.dispose() })
    scene.remove(centerlineObj)
    centerlineObj = null
  }
}

function clearThicknessPoints() {
  if (thicknessObj) {
    thicknessObj.geometry?.dispose()
    thicknessObj.material?.dispose()
    scene.remove(thicknessObj)
    thicknessObj = null
  }
}

function clearPlanes() {
  if (planesObj) {
    planesObj.traverse(c => { c.geometry?.dispose(); c.material?.dispose() })
    scene.remove(planesObj)
    planesObj = null
  }
}

function clearSections() {
  if (sectionsObj) {
    sectionsObj.traverse(c => { c.geometry?.dispose(); c.material?.dispose() })
    scene.remove(sectionsObj)
    sectionsObj = null
  }
}

function clearOverlays() {
  clearCenterline()
  clearThicknessPoints()
  clearPlanes()
  clearSections()
  hideHighlight()
}

function setVisibility(options = {}) {
  if (mesh) mesh.visible = options.mesh !== false
  if (centerlineObj) centerlineObj.visible = !!options.centerline
  if (thicknessObj) thicknessObj.visible = !!options.thickness
  if (planesObj) planesObj.visible = !!options.planes
  if (sectionsObj) sectionsObj.visible = !!options.sections
  renderSoftware()
}

defineExpose({
  loadFromUrl,
  loadFromData,
  displayCenterline,
  displayThicknessPoints,
  displayCuttingPlanes,
  displaySectionsFromData,
  highlightSection,
  hideHighlight,
  clearOverlays,
  setVisibility,
})
</script>

<style scoped>
.pp-3d-viewer {
  width: 100%;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
  background: #ffffff;
}
</style>
