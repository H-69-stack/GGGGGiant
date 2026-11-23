<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <div class="card-header">
          <span>用户管理</span>
          <span class="header-hint">待审批用户需点击「通过」后方可登录</span>
        </div>
      </template>

      <el-table :data="users" border stripe v-loading="loading" style="width: 100%">
        <el-table-column prop="id" label="账号" width="90" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="workrole" label="工作角色" width="110">
          <template #default="{ row }">
            <el-tag :type="getRoleTagType(row.workrole)">{{ row.workrole }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="authority" label="权限" width="80">
          <template #default="{ row }">
            <el-tag :type="row.authority === 'admin' ? 'danger' : 'info'" size="small">{{ row.authority }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="审批" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.authority === 'admin'" type="info" size="small">—</el-tag>
            <el-tag v-else :type="approvalTagType(row.approval_status)" size="small">
              {{ approvalLabel(row.approval_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="设备绑定" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.authority === 'admin'" type="info" size="small">—</el-tag>
            <el-tag v-else :type="row.device_bound ? 'success' : 'info'" size="small">
              {{ row.device_bound ? '已绑定' : '未绑定' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="账户有效期" width="120">
          <template #default="{ row }">
            <span :class="{ 'text-expired': isExpired(row) }">{{ formatExpires(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="320" fixed="right">
          <template #default="{ row }">
            <template v-if="row.authority !== 'admin' && row.approval_status === 'pending'">
              <el-button size="small" type="success" @click="handleApproval(row, 'approved')">通过</el-button>
              <el-button size="small" type="warning" @click="handleApproval(row, 'rejected')">拒绝</el-button>
            </template>
            <el-popconfirm
              v-if="row.authority !== 'admin' && row.device_bound"
              title="解绑后用户可在新浏览器登录并重新绑定，确定解绑？"
              @confirm="handleUnbindDevice(row)"
            >
              <template #reference>
                <el-button size="small" type="info">解绑设备</el-button>
              </template>
            </el-popconfirm>
            <el-button size="small" @click="handleEdit(row)">编辑</el-button>
            <el-popconfirm title="确定删除该用户?" @confirm="handleDelete(row)">
              <template #reference>
                <el-button size="small" type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="editDialogVisible" title="编辑用户" width="480px">
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="账号">
          <el-input v-model="editForm.id" disabled />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="工作角色">
          <el-select v-model="editForm.workrole">
            <el-option
              v-for="role in selectableRoles"
              :key="role"
              :label="role === 'ALL' ? 'ALL (全部权限)' : role"
              :value="role"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="权限">
          <el-select v-model="editForm.authority">
            <el-option label="user" value="user" />
            <el-option label="admin" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editForm.authority !== 'admin'" label="账户有效期">
          <div class="expires-row">
            <el-date-picker
              v-model="editForm.expires_at"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择到期日"
              :disabled="editForm.permanent"
              :disabled-date="disableAfterLicense"
              style="flex: 1;"
            />
            <el-checkbox v-model="editForm.permanent" @change="onPermanentChange">永久</el-checkbox>
          </div>
          <div class="hint">不得超过服务器许可证到期日（{{ licenseExpiresAt || '—' }}）</div>
        </el-form-item>
        <el-form-item v-else label="账户有效期">
          <el-text type="info">管理员账户永久有效</el-text>
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="editForm.password" type="password" placeholder="留空则不修改" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleUpdate" :loading="updating">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const users = ref([])
const loading = ref(false)
const editDialogVisible = ref(false)
const updating = ref(false)
const allowedRoles = ref([])
const licenseExpiresAt = ref('')

const ALL_ROLES = ['ALL', '工艺规划', '模具设计', '硬模成型']

const selectableRoles = computed(() => {
  if (!allowedRoles.value.length) return ALL_ROLES
  if (allowedRoles.value.includes('ALL')) return ALL_ROLES
  return ALL_ROLES.filter((r) => allowedRoles.value.includes(r))
})

const editForm = reactive({
  id: '',
  name: '',
  workrole: '',
  authority: '',
  password: '',
  expires_at: '',
  permanent: false,
})

let editingUserId = null

onMounted(() => {
  fetchUsers()
  loadAllowedRoles()
  loadLicenseExpires()
})

async function loadLicenseExpires() {
  try {
    const res = await axios.get('/api/auth/license-status')
    licenseExpiresAt.value = res.data.license?.expires_at || ''
  } catch (e) {
    licenseExpiresAt.value = ''
  }
}

async function loadAllowedRoles() {
  try {
    const res = await axios.get('/api/auth/allowed-roles')
    allowedRoles.value = res.data.allowed_workroles || []
  } catch (e) {
    allowedRoles.value = []
  }
}

function formatExpires(row) {
  if (row.authority === 'admin' || !row.expires_at) return '永久'
  return row.expires_at
}

function isExpired(row) {
  if (row.authority === 'admin' || !row.expires_at) return false
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const exp = new Date(row.expires_at + 'T00:00:00')
  return exp < today
}

function disableAfterLicense(date) {
  if (!licenseExpiresAt.value) return false
  const lic = new Date(licenseExpiresAt.value + 'T00:00:00')
  return date.getTime() > lic.getTime()
}

function onPermanentChange(val) {
  if (val) editForm.expires_at = ''
}

async function fetchUsers() {
  loading.value = true
  try {
    const res = await axios.get('/api/auth/users')
    if (res.data.success) {
      users.value = res.data.users || []
    }
  } catch (e) {
    ElMessage.error('获取用户列表失败')
  } finally {
    loading.value = false
  }
}

function getRoleTagType(role) {
  const map = { 'ALL': '', '工艺规划': 'success', '模具设计': 'warning', '硬模成型': 'danger' }
  return map[role] || 'info'
}

function approvalLabel(status) {
  const map = { pending: '待审批', approved: '已通过', rejected: '已拒绝' }
  return map[status] || status || '已通过'
}

function approvalTagType(status) {
  const map = { pending: 'warning', approved: 'success', rejected: 'danger' }
  return map[status] || 'info'
}

async function handleUnbindDevice(row) {
  try {
    const res = await axios.put(`/api/auth/users/${row.id}/unbind-device`)
    if (res.data.success) {
      ElMessage.success(res.data.message || '已解绑设备')
      fetchUsers()
    } else {
      ElMessage.error(res.data.message || '解绑失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '解绑失败')
  }
}

async function handleApproval(row, status) {
  try {
    const res = await axios.put(`/api/auth/users/${row.id}/approval`, { approval_status: status })
    if (res.data.success) {
      ElMessage.success(res.data.message || '操作成功')
      fetchUsers()
    } else {
      ElMessage.error(res.data.message || '操作失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '操作失败')
  }
}

function handleEdit(row) {
  editingUserId = row.id
  editForm.id = row.id
  editForm.name = row.name || ''
  editForm.workrole = row.workrole || 'ALL'
  editForm.authority = row.authority || 'user'
  editForm.password = ''
  editForm.permanent = !row.expires_at
  editForm.expires_at = row.expires_at || licenseExpiresAt.value || ''
  editDialogVisible.value = true
}

async function handleUpdate() {
  updating.value = true
  try {
    const payload = {
      name: editForm.name,
      workrole: editForm.workrole,
      authority: editForm.authority,
    }
    if (editForm.password) payload.password = editForm.password
    if (editForm.authority !== 'admin') {
      payload.expires_at = editForm.permanent ? null : editForm.expires_at
    }
    const res = await axios.put(`/api/auth/users/${editingUserId}`, payload)
    if (res.data.success) {
      ElMessage.success('更新成功')
      editDialogVisible.value = false
      fetchUsers()
    } else {
      ElMessage.error(res.data.message || '更新失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '更新失败')
  } finally {
    updating.value = false
  }
}

async function handleDelete(row) {
  try {
    const res = await axios.delete(`/api/auth/users/${row.id}`)
    if (res.data.success) {
      ElMessage.success('删除成功')
      fetchUsers()
    } else {
      ElMessage.error(res.data.message || '删除失败')
    }
  } catch (e) {
    ElMessage.error('删除失败')
  }
}
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.header-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-weight: normal;
}
.text-muted {
  color: var(--el-text-color-secondary);
}
code {
  font-size: 12px;
}
.expires-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
.text-expired {
  color: var(--el-color-danger);
}
</style>
