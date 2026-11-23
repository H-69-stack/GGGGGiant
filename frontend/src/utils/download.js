/** 触发浏览器保存已获取的二进制响应（需调用方用带鉴权的 api/axios 请求）。 */
export function saveBlob(data, filename) {
  const blob = data instanceof Blob ? data : new Blob([data])
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', filename)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

async function readBlobJsonMessage(data) {
  const blob = data instanceof Blob ? data : new Blob([data])
  const text = await blob.text()
  try {
    const json = JSON.parse(text)
    if (json && typeof json === 'object') {
      return json.message || json.detail || null
    }
  } catch {
    /* not JSON */
  }
  return null
}

function contentType(res) {
  const headers = res?.headers || {}
  return String(headers['content-type'] || headers['Content-Type'] || '')
}

/** 若响应是 JSON 错误则抛出，否则保存为 zip。 */
export async function saveZipResponse(res, filename) {
  const raw = res?.data
  const blob = raw instanceof Blob ? raw : new Blob([raw])
  const ctype = contentType(res)
  const looksJson = ctype.includes('json')
  if (looksJson) {
    const msg = await readBlobJsonMessage(blob)
    throw new Error(msg || '导出失败')
  }
  const head = await blob.slice(0, 1).text()
  if (head === '{' || head === '[') {
    const msg = await readBlobJsonMessage(blob)
    throw new Error(msg || '导出失败')
  }
  saveBlob(blob, filename)
}

/** axios blob 失败时取出后端 message。 */
export async function exportErrorMessage(error, fallback = '导出失败') {
  const data = error?.response?.data
  if (data instanceof Blob) {
    const msg = await readBlobJsonMessage(data)
    if (msg) return msg
  } else if (data && typeof data === 'object' && data.message) {
    return data.message
  }
  if (error?.message && error.message !== 'Error') return error.message
  return fallback
}
