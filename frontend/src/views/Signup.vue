<template>
  <div class="signup-container">
    <el-card class="signup-card">
      <template #header>
        <div class="card-header">
          <h2>用户注册</h2>
        </div>
      </template>

      <el-alert
        title="注册提交后需等待管理员审批，审批通过后方可登录。"
        type="warning"
        :closable="false"
        style="margin-bottom: 12px;"
      />

      <el-alert
        title="首次登录将自动绑定当前浏览器，账户不可在其他浏览器上使用（换电脑需联系管理员解绑）。"
        type="info"
        :closable="false"
        style="margin-bottom: 12px;"
      />

      <el-alert
        title="密码要求：至少8位，包含大小写字母、数字、特殊字符（!@#$%^&*等）"
        type="info"
        :closable="false"
        style="margin-bottom: 20px;"
      />

      <el-alert
        v-if="!licenseValid"
        :title="licenseReason || '当前服务器许可证无效，暂时无法注册账户'"
        type="error"
        :closable="false"
        style="margin-bottom: 20px;"
      />

      <el-form :model="form" label-width="180px" style="max-width: 500px; margin: 0 auto;">
        <el-form-item label="账号（只能使用数字）">
          <el-input-number v-model="form.id" :min="1" :step="1" style="width: 100%;" />
        </el-form-item>

        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password />
        </el-form-item>

        <el-form-item label="姓名">
          <el-input v-model="form.name" />
        </el-form-item>

        <el-form-item label="用户类型">
          <el-radio-group v-model="form.workrole">
            <el-radio
              v-for="role in selectableRoles"
              :key="role"
              :label="role"
            >{{ role }}</el-radio>
          </el-radio-group>
          <div v-if="licenseValid" class="role-hint">
            本许可证可注册的账户类型：{{ allowedRoles.join('、') || '（无）' }}
          </div>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="handleSignup" :loading="loading"
            :disabled="!licenseValid" style="width: 100%;">
            提交注册（待审批）
          </el-button>
          <div class="submit-hint">提交后请耐心等待管理员审批。</div>
        </el-form-item>

        <el-form-item>
          <router-link to="/login" class="back-link">返回登录</router-link>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const router = useRouter()
const loading = ref(false)

const ALL_ROLES = ['工艺规划', '模具设计', '硬模成型', 'ALL']
const licenseValid = ref(true)
const licenseReason = ref('')
const allowedRoles = ref([])

const selectableRoles = computed(() => {
  if (!allowedRoles.value.length) return ALL_ROLES
  if (allowedRoles.value.includes('ALL')) return ALL_ROLES
  return ALL_ROLES.filter((r) => allowedRoles.value.includes(r))
})

const form = reactive({
  id: 1,
  password: '',
  name: '',
  workrole: '工艺规划',
})

onMounted(async () => {
  try {
    const res = await axios.get('/api/auth/allowed-roles', { withCredentials: true })
    licenseValid.value = res.data.valid
    licenseReason.value = res.data.reason || ''
    allowedRoles.value = res.data.allowed_workroles || []
    if (selectableRoles.value.length && !selectableRoles.value.includes(form.workrole)) {
      form.workrole = selectableRoles.value[0]
    }
  } catch (e) {
    licenseValid.value = false
    licenseReason.value = '无法获取许可证状态'
  }
})

async function handleSignup() {
  if (!form.password || !form.name) {
    ElMessage.error('输入信息不能为空')
    return
  }

  loading.value = true
  try {
    const res = await axios.post('/api/auth/signup', {
      id: form.id,
      name: form.name,
      password: form.password,
      workrole: form.workrole,
    }, { withCredentials: true })
    if (res.data.success) {
      ElMessage.success(res.data.message || '注册已提交，请等待管理员审批')
      router.push('/login')
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
.signup-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: var(--el-fill-color-lighter);
}
.signup-card {
  width: 600px;
}
.card-header h2 {
  margin: 0;
  color: var(--el-color-primary);
}
.back-link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.role-hint,
.submit-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
</style>
