import api from './index'

export const rfmApi = {
  /** Initialize from a pp_design.db record (loads design params + tube geometry) */
  initFromDesign(designId, operation) {
    return api.post('/api/rfm/init-from-design', { design_id: designId, operation })
  },

  /** Initialize from an active in-memory PP session (e.g. after upload) */
  initFromPP(ppSessionId, operation) {
    return api.post('/api/rfm/init-from-pp', { pp_session_id: ppSessionId, operation })
  },

  /** Set operation config */
  process(sessionId, config) {
    return api.post('/api/rfm/process', { session_id: sessionId, config })
  },

  /** Generate 3D model */
  generate(sessionId, extra = {}) {
    return api.post('/api/rfm/generate', { session_id: sessionId, ...extra }, { timeout: 300000 })
  },

  /** Generate belling cavity model (belling only) */
  generateCavity(sessionId, extra = {}) {
    return api.post('/api/rfm/generate-cavity', { session_id: sessionId, ...extra }, { timeout: 300000 })
  },

  generateMandrel(sessionId, extra = {}) {
    return api.post('/api/rfm/generate-mandrel', { session_id: sessionId, ...extra }, { timeout: 300000 })
  },

  /** 成型 121：准备 v1 测量缓存（缺失时后台测量；已有缓存直接复用） */
  prepareV1(sessionId, force = false) {
    return api.post('/api/rfm/v1/prepare', { session_id: sessionId, force })
  },

  v1Status(taskId) {
    return api.get(`/api/rfm/v1/status/${taskId}`)
  },

  v1Cancel(taskId) {
    return api.post(`/api/rfm/v1/cancel/${taskId}`)
  },

  downloadMandrel(sessionId) {
    return api.get(`/api/rfm/download-mandrel/${sessionId}`, { responseType: 'blob' })
  },

  previewMandrelUrl(sessionId, cacheKey = '') {
    return `/api/rfm/preview-mandrel/${sessionId}${cacheKey ? `?v=${encodeURIComponent(cacheKey)}` : ''}`
  },

  /** Save to database */
  save(sessionId) {
    return api.post('/api/rfm/save', { session_id: sessionId })
  },

  /** Download generated model as STEP */
  download(sessionId) {
    return api.get(`/api/rfm/download/${sessionId}`, { responseType: 'blob' })
  },

  /** Download belling cavity as STEP */
  downloadCavity(sessionId) {
    return api.get(`/api/rfm/download-cavity/${sessionId}`, { responseType: 'blob' })
  },

  /** @deprecated use download() — kept for any legacy callers */
  downloadUrl(sessionId) {
    return `/api/rfm/download/${sessionId}`
  },

  /** Download belling cavity as STEP */
  downloadCavityUrl(sessionId) {
    return `/api/rfm/download-cavity/${sessionId}`
  },

  /** Preview generated STL (for iframe / quick view) */
  previewUrl(sessionId, cacheKey = '') {
    const suffix = cacheKey ? `?v=${encodeURIComponent(cacheKey)}` : ''
    return `/api/rfm/preview/${sessionId}${suffix}`
  },

  /** Preview belling cavity STL */
  previewCavityUrl(sessionId, cacheKey = '') {
    const suffix = cacheKey ? `?v=${encodeURIComponent(cacheKey)}` : ''
    return `/api/rfm/preview-cavity/${sessionId}${suffix}`
  },

  /** Legacy: list tubes from tube_analysis.db */
  listDbTubes() {
    return api.get('/api/rfm/db-tubes')
  },

  /** Legacy: load tube from tube_analysis.db */
  loadDbTube(tubeId, operation) {
    return api.post('/api/rfm/db-tubes/load', { tube_id: tubeId, operation })
  },
}
