<template>
  <div class="page-container">
    <el-card class="page-card" style="max-width: 520px; margin: 0 auto;">
      <template #header>
        <span>注册新用户</span>
      </template>

      <el-alert
        v-if="!licenseValid"
        :title="licenseReason || '当前服务器许可证无效，无法注册账户'"
        type="error"
        :closable="false"
        style="margin-bottom: 16px;"
      />
      <el-alert
        v-else
        :title="`本许可证可注册账户类型：${allowedRoles.join('、') || '（无）'}｜服务器到期：${licenseExpiresAt || '—'}`"
        type="success"
        :closable="false"
        style="margin-bottom: 16px;"
      />
      <el-alert
        title="管理员代注册的用户将自动审批通过；用户首次登录时自动绑定当前浏览器。"
        type="info"
        :closable="false"
        style="margin-bottom: 16px;"
      />

      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="账号" prop="id">
          <el-input-number v-model="form.id" :min="1" :step="1" style="width: 100%;" />
        </el-form-item>

        <el-form-item label="姓名" prop="name">
          <el-input v-model="form.name" placeholder="请输入姓名" />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="请输入密码" />
        </el-form-item>

        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input v-model="form.confirmPassword" type="password" show-password placeholder="请再次输入密码" />
        </el-form-item>

        <el-form-item label="工作角色" prop="workrole">
          <el-select v-model="form.workrole" placeholder="选择工作角色">
            <el-option
              v-for="role in selectableRoles"
              :key="role"
              :label="role === 'ALL' ? 'ALL (全部权限)' : role"
              :value="role"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="权限" prop="authority">
          <el-select v-model="form.authority" placeholder="选择权限">
            <el-option label="user" value="user" />
            <el-option label="admin" value="admin" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="form.authority !== 'admin'" label="账户有效期">
          <div class="expires-row">
            <el-date-picker
              v-model="form.expires_at"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="默认与服务器许可证一致"
              :disabled="form.permanent"
              :disabled-date="disableAfterLicense"
              style="flex: 1;"
            />
            <el-checkbox v-model="form.permanent" @change="onPermanentChange">永久</el-checkbox>
          </div>
          <div class="hint">留空且未勾选永久时，默认到期日与服务器许可证相同</div>
        </el-form-item>
        <el-form-item v-else label="账户有效期">
          <el-text type="info">管理员账户永久有效</el-text>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="handleRegister" :loading="loading"
            :disabled="!licenseValid" style="width: 100%;">
            注册
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const formRef = ref()
const loading = ref(false)

const ALL_ROLES = ['ALL', '工艺规划', '模具设计', '硬模成型']
const licenseValid = ref(true)
const licenseReason = ref('')
const allowedRoles = ref([])
const licenseExpiresAt = ref('')

const selectableRoles = computed(() => {
  if (!allowedRoles.value.length) return ALL_ROLES
  if (allowedRoles.value.includes('ALL')) return ALL_ROLES
  return ALL_ROLES.filter((r) => allowedRoles.value.includes(r))
})

const form = reactive({
  id: 1,
  name: '',
  password: '',
  confirmPassword: '',
  workrole: 'ALL',
  authority: 'user',
  expires_at: '',
  permanent: false,
})

onMounted(async () => {
  try {
    const [rolesRes, licRes] = await Promise.all([
      axios.get('/api/auth/allowed-roles'),
      axios.get('/api/auth/license-status'),
    ])
    licenseValid.value = rolesRes.data.valid
    licenseReason.value = rolesRes.data.reason || ''
    allowedRoles.value = rolesRes.data.allowed_workroles || []
    licenseExpiresAt.value = licRes.data.license?.expires_at || ''
    form.expires_at = licenseExpiresAt.value
    if (selectableRoles.value.length && !selectableRoles.value.includes(form.workrole)) {
      form.workrole = selectableRoles.value[0]
    }
  } catch (e) {
    licenseValid.value = false
    licenseReason.value = '无法获取许可证状态'
  }
})

function disableAfterLicense(date) {
  if (!licenseExpiresAt.value) return false
  const lic = new Date(licenseExpiresAt.value + 'T00:00:00')
  return date.getTime() > lic.getTime()
}

function onPermanentChange(val) {
  if (val) form.expires_at = ''
  else form.expires_at = licenseExpiresAt.value
}

const validateConfirm = (rule, value, callback) => {
  if (value !== form.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

const rules = {
  id: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码长度至少8位', trigger: 'blur' }
  ],
  confirmPassword: [
    { required: true, message: '请确认密码', trigger: 'blur' },
    { validator: validateConfirm, trigger: 'blur' }
  ],
  workrole: [{ required: true, message: '请选择工作角色', trigger: 'change' }],
  authority: [{ required: true, message: '请选择权限', trigger: 'change' }]
}

async function handleRegister() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const payload = {
      id: form.id,
      name: form.name,
      password: form.password,
      workrole: form.workrole,
      authority: form.authority,
    }
    if (form.authority !== 'admin') {
      if (form.permanent) {
        payload.expires_at = null
      } else if (form.expires_at) {
        payload.expires_at = form.expires_at
      }
    }
    const res = await axios.post('/api/auth/register', payload)
    if (res.data.success) {
      ElMessage.success('注册成功')
      formRef.value.resetFields()
      form.expires_at = licenseExpiresAt.value
      form.permanent = false
    } else {
      ElMessage.error(res.data.message || '注册失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '注册失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
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
</style>
