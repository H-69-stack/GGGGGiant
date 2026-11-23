import api from './index'

export const ppApi = {
  upload(formData) {
    return api.post('/api/pp/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  uploadStatus(id) {
    return api.get(`/api/pp/upload-status?id=${encodeURIComponent(id)}`)
  },
  clearSession(id) {
    return api.post('/api/pp/clear-session', { session_id: id })
  },
  sessionInfo(id) {
    return api.get(`/api/pp/session-info?id=${encodeURIComponent(id)}`)
  },
  previewData(id) {
    return api.get(`/api/pp/stl-file/${encodeURIComponent(id)}`, { responseType: 'arraybuffer' })
  },
  thicknessData(id) {
    return api.get(`/api/pp/thickness-data/${encodeURIComponent(id)}`)
  },
  sectionAt(id, pos) {
    return api.post('/api/pp/section-at', { session_id: id, position_mm: pos })
  },
  regeneratePerimeter(id, step) {
    return api.post('/api/pp/regenerate-perimeter', { session_id: id, sample_interval_mm: step })
  },
  formline(id, opts = {}) {
    return api.post('/api/pp/formline', { session_id: id, ...opts })
  },
  engineerBending(id, decision) {
    return api.post('/api/pp/engineer-bending', { session_id: id, decision })
  },
  viz3d(id) {
    return api.get(`/api/pp/viz/3d?id=${encodeURIComponent(id)}`)
  },
  applyToOptimization(id, profileChoice = null) {
    const body = { session_id: id }
    if (profileChoice) body.profile_choice = profileChoice
    return api.post('/api/pp/apply-to-optimization', body)
  },
  qa(id) {
    return api.get(`/api/pp/qa?id=${encodeURIComponent(id)}`)
  },
  qaRerun(id) {
    return api.post('/api/pp/qa/rerun', { session_id: id })
  },
  qaPreviewSmooth(id) {
    return api.post('/api/pp/qa/preview-smooth', { session_id: id })
  },
  qaConfirmSmooth(id, confirmedBy = '') {
    return api.post('/api/pp/qa/confirm-smooth', { session_id: id, confirmed_by: confirmedBy })
  },
  qaRejectSmooth(id, confirmedBy = '') {
    return api.post('/api/pp/qa/reject-smooth', { session_id: id, confirmed_by: confirmedBy })
  },
  qaConfirmStraight(id, segmentId, confirmedBy = '') {
    return api.post('/api/pp/qa/confirm-straight', { session_id: id, segment_id: segmentId, confirmed_by: confirmedBy })
  },
  qaRejectStraight(id, segmentId, confirmedBy = '') {
    return api.post('/api/pp/qa/reject-straight', { session_id: id, segment_id: segmentId, confirmed_by: confirmedBy })
  },
  qaFinalizeStraight(id, confirmedIds = [], confirmedBy = '') {
    return api.post('/api/pp/qa/finalize-straight', {
      session_id: id,
      confirmed_ids: confirmedIds,
      confirmed_by: confirmedBy,
    })
  },
  qaXlsxTemplateUrl() {
    return '/api/pp/qa/xlsx-template'
  },
  qaDownloadTemplate() {
    return api.get('/api/pp/qa/xlsx-template', { responseType: 'blob' })
  },
  qaUploadXlsx(formData) {
    return api.post('/api/pp/qa/upload-xlsx', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  qaSelectProfile(id, choice) {
    return api.post('/api/pp/qa/select-profile', { session_id: id, choice })
  },
  qaConfirmWallLength(id, values, correctionSide = null, confirmedBy = '', extra = {}) {
    const body = { session_id: id, values, confirmed_by: confirmedBy }
    if (correctionSide) body.correction_side = correctionSide
    if (extra.small_end_correction_mm != null && extra.small_end_correction_mm !== '') {
      body.small_end_correction_mm = Number(extra.small_end_correction_mm)
    }
    return api.post('/api/pp/qa/confirm-wall-length', body)
  },
  qaSearchKeySection(id, length, width, expectedS = null) {
    const body = { session_id: id, length, width }
    if (expectedS != null && expectedS !== '') body.expected_s_mm = Number(expectedS)
    return api.post('/api/pp/qa/search-key-section', body)
  },
  qaSaveKeySection(id, payload = {}) {
    return api.post('/api/pp/qa/save-key-section', { session_id: id, ...payload })
  },
  qaDeleteKeySection(id, recordId) {
    return api.post('/api/pp/qa/delete-key-section', { session_id: id, record_id: recordId })
  },
  qaFinalProfile(id) {
    return api.get(`/api/pp/qa/final-profile?id=${encodeURIComponent(id)}`)
  },
  devStatus() {
    return api.get('/api/pp/dev/status')
  },
  devRerunQa() {
    return api.post('/api/pp/dev/rerun-qa', {})
  },
  devRereadStp() {
    return api.post('/api/pp/dev/reread-stp', {})
  },
  devFixtures() {
    return api.get('/api/pp/dev/fixtures')
  },
  devLoadFixture(filename, algorithm = 'A') {
    return api.post('/api/pp/dev/load-fixture', { filename, algorithm })
  },
  clearMeshCache() {
    return api.post('/api/pp/clear-mesh-cache')
  },
  // DB
  listTubes(search = '', origin = '') {
    const params = new URLSearchParams()
    if (search) params.set('search', search)
    if (origin) params.set('origin', origin)
    const qs = params.toString()
    return api.get(`/api/pp/tubes${qs ? `?${qs}` : ''}`)
  },
  loadTube(id) {
    return api.post('/api/pp/tubes/load', { tube_id: id })
  },
  deleteTubes(ids) {
    return api.post('/api/pp/tubes/delete', { ids })
  },
  saveTube(id, displayName = '', manualSegments = null, profileChoice = null, grindAllowanceMm = null) {
    const body = {
      session_id: id,
      display_name: displayName,
      manual_segments: manualSegments,
    }
    if (profileChoice) body.profile_choice = profileChoice
    if (grindAllowanceMm != null && Number(grindAllowanceMm) > 0) {
      body.grind_allowance_mm = Number(grindAllowanceMm)
    }
    return api.post('/api/pp/tubes/save', body)
  },
  renameTube(id, name) {
    return api.post('/api/pp/tubes/rename', { tube_id: id, name })
  },
  // TAB2: Manual Input
  thicknessSegments(sessionId) {
    return api.get(`/api/pp/thickness-segments?id=${encodeURIComponent(sessionId)}`)
  },
  saveManualParams(sessionId, params) {
    return api.post('/api/pp/save-manual-params', { session_id: sessionId, ...params })
  },
  uploadManualFile(formData) {
    return api.post('/api/pp/upload-manual-file', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  applyToTab3(sessionId) {
    return api.post('/api/pp/apply-to-tab3', { session_id: sessionId })
  },
  // TAB3: Optimization
  optimize(data) {
    return api.post('/api/pp/optimize', data)
  },
  optimizeMethods() {
    return api.get('/api/pp/optimize-methods')
  },
  optimizeRecalc(data) {
    return api.post('/api/pp/optimize-recalc', data)
  },
  optimizeStatus(taskId) {
    return api.get(`/api/pp/optimize-status?task_id=${encodeURIComponent(taskId)}`)
  },
  optimizeResult(taskId) {
    return api.get(`/api/pp/optimize-result?task_id=${encodeURIComponent(taskId)}`)
  },
  optimizeRecommend(sessionId) {
    return api.get(`/api/pp/optimize/recommend?session_id=${encodeURIComponent(sessionId)}`)
  },
  // 工艺设计数据库
  designParamMeta() {
    return api.get('/api/pp/design/param-meta')
  },
  designFeatureFlagsGet() {
    return api.get('/api/pp/design/feature-flags')
  },
  designFeatureFlagsSet(payload) {
    return api.post('/api/pp/design/feature-flags', payload)
  },
  designPreview(payload) {
    return api.post('/api/pp/design/preview', payload)
  },
  saveDesign(payload) {
    return api.post('/api/pp/design/save', payload)
  },
  listDesigns() {
    return api.get('/api/pp/design/list')
  },
  getDesign(id) {
    return api.get(`/api/pp/design/${encodeURIComponent(id)}`)
  },
  renameDesign(id, name) {
    return api.post('/api/pp/design/rename', { design_id: id, name })
  },
  deleteDesigns(ids) {
    return api.post('/api/pp/design/delete', { ids })
  },
}

export default ppApi
