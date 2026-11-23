import axios from 'axios'

export function uploadRotation(data) {
  return axios.post('/api/rotation/upload', data, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

export function transformRotation(data) {
  return axios.post('/api/rotation/transform', data)
}

export function resetRotation(data) {
  return axios.post('/api/rotation/reset', data)
}

export function downloadRotation(sessionId) {
  return axios.get(`/api/rotation/download/${sessionId}`, {
    responseType: 'blob'
  })
}
