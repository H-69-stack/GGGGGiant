import axios from 'axios'

export function getMe(userId) {
  return axios.get('/api/auth/me', { params: { id: userId } })
}

export function signup(data) {
  return axios.post('/api/auth/signup', data)
}

export function updateUser(userId, data) {
  return axios.put(`/api/auth/users/${userId}`, data)
}
