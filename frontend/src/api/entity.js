import api from './index'

/** 一体化流水线 API 封装（后端蓝图 /api/entity） */

export function getStatus() {
  return api.get('/api/entity/status').then((res) => res.data)
}

export function getParts() {
  return api.get('/api/entity/parts').then((res) => res.data)
}

export function getPartProfile(part) {
  return api.get(`/api/entity/parts/${encodeURIComponent(part)}/profile`).then((res) => res.data)
}

/**
 * 单件全流程。优先走 /flow（含 process_summary / route_pref / c1_impl）；
 * 旧后端没有该别名时回落到 /parts/<part>，页面仍然可用。
 */
export async function getPartFlow(part) {
  const encoded = encodeURIComponent(part)
  try {
    const res = await api.get(`/api/entity/parts/${encoded}/flow`)
    return res.data
  } catch (err) {
    if (err?.response?.status !== 404) throw err
    const res = await api.get(`/api/entity/parts/${encoded}`)
    return res.data
  }
}

/** 外径形腔路线偏好（c1 / 121 + 分型方向 auto/X/Y/Z）。 */
export function getRoutePref(part) {
  return api
    .get(`/api/entity/parts/${encodeURIComponent(part)}/route-pref`)
    .then((res) => res.data)
}

export function setRoutePref(part, payload = {}) {
  return api
    .post(`/api/entity/parts/${encodeURIComponent(part)}/route-pref`, payload)
    .then((res) => res.data)
}

/** 上传新管件（multipart，字段 file）：后台测量并入库。 */
export function uploadPart(file, onProgress) {
  const formData = new FormData()
  formData.append('file', file)
  const config = { headers: { 'Content-Type': 'multipart/form-data' } }
  if (typeof onProgress === 'function') {
    config.onUploadProgress = (event) => {
      if (event?.total) onProgress(Math.round((event.loaded / event.total) * 100))
    }
  }
  return api.post('/api/entity/upload', formData, config).then((res) => res.data)
}

/** 批量上传新管件（multipart，字段 files）：一次提交多个，后台并行测量入库。 */
export function uploadParts(files, onProgress) {
  const formData = new FormData()
  Array.from(files || []).forEach((file) => formData.append('files', file))
  const config = { headers: { 'Content-Type': 'multipart/form-data' } }
  if (typeof onProgress === 'function') {
    config.onUploadProgress = (event) => {
      if (event?.total) onProgress(Math.round((event.loaded / event.total) * 100))
    }
  }
  return api.post('/api/entity/upload', formData, config).then((res) => res.data)
}

/** 上传任务列表（测量中/排队/完成/失败）。 */
export function getUploads() {
  return api.get('/api/entity/uploads').then((res) => res.data)
}

export function getUpload(taskId) {
  return api.get(`/api/entity/uploads/${encodeURIComponent(taskId)}`).then((res) => res.data)
}

/** 停止一个上传测量任务（排队中或测量中都行）；后端会杀掉测量子进程树。 */
export function cancelUpload(taskId) {
  return api.post(`/api/entity/uploads/${encodeURIComponent(taskId)}/cancel`).then((res) => res.data)
}

/** 清空上传任务记录（只清内存表；keepActive=false 时连进行中的任务也一起清）。 */
export function clearUploads(payload = {}) {
  return api.post('/api/entity/uploads/clear', payload).then((res) => res.data)
}

/** 补算该件的上游工艺设计（幂等：已登记直接返回；不可行时返回 infeasible + 原因）。 */
export function createDesign(part, payload = {}) {
  return api
    .post(`/api/entity/parts/${encodeURIComponent(part)}/design`, payload)
    .then((res) => res.data)
}

export function getPlan(params = {}) {
  return api.get('/api/entity/plan', { params }).then((res) => res.data)
}

export function runPipeline(payload) {
  return api.post('/api/entity/run', payload).then((res) => res.data)
}

/** 停止流水线：mode = graceful（置 stop_event）| force（杀子进程）。 */
export function stopPipeline(mode = 'graceful') {
  return api.post('/api/entity/stop', { mode }).then((res) => res.data)
}

export function getJobs(limit = 20) {
  return api.get('/api/entity/jobs', { params: { limit } }).then((res) => res.data)
}

export function getJob(jobId) {
  return api.get(`/api/entity/jobs/${encodeURIComponent(jobId)}`).then((res) => res.data)
}

export function getJobFiles(jobId) {
  return api
    .get(`/api/entity/jobs/${encodeURIComponent(jobId)}/files`)
    .then((res) => res.data)
}

export function downloadUrl(relPath) {
  return `/api/entity/download?path=${encodeURIComponent(relPath)}`
}

/**
 * 下载 out_root 内的 STEP / 报告。
 * 走 axios 以携带 X-Auth-Token，再用 blob 触发浏览器保存。
 */
export async function downloadFile(relPath, name = '') {
  const params = { path: relPath }
  if (name) params.name = name
  const res = await api.get('/api/entity/download', {
    params,
    responseType: 'blob',
  })

  let filename = name || String(relPath || '').split('/').pop() || 'download'
  const disposition = res.headers?.['content-disposition'] || ''
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
  if (match && match[1]) {
    try {
      filename = decodeURIComponent(match[1])
    } catch (e) {
      filename = match[1]
    }
  }

  const blobUrl = window.URL.createObjectURL(new Blob([res.data]))
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(blobUrl)
  return filename
}

/** 已导入主流程的队列（README: entity_out/_config/import_queue.json）。 */
export function getImportQueue() {
  return api.get('/api/entity/import').then((res) => res.data)
}

/**
 * 把并行测量完的上传件一键导入主流程队列（只登记，不启动任务）。
 * payload: { tasks: [taskId] } | { parts: [名] } | { source: 'upload' }，可带 replace。
 */
export function importToPipeline(payload = {}) {
  return api.post('/api/entity/import', payload).then((res) => res.data)
}

export default {
  getStatus,
  getParts,
  getPartFlow,
  getPartProfile,
  createDesign,
  getRoutePref,
  setRoutePref,
  uploadPart,
  uploadParts,
  getUploads,
  getUpload,
  cancelUpload,
  clearUploads,
  getImportQueue,
  importToPipeline,
  getPlan,
  runPipeline,
  stopPipeline,
  getJobs,
  getJob,
  getJobFiles,
  downloadUrl,
  downloadFile,
}
