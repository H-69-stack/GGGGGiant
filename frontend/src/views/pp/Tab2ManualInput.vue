<template>
  <div class="tab2-manual-input">
    <el-alert
      title="这个页面允许您输入或上传壁厚形线数据来测试工艺参数优化功能。"
      type="info"
      :closable="false"
      class="mb-4"
    />

    <!-- Mode selector -->
    <el-radio-group v-model="inputMode" class="mb-4">
      <el-radio-button label="table">数据表编辑</el-radio-button>
      <el-radio-button label="file">文件上传</el-radio-button>
    </el-radio-group>

    <!-- Tab 1: Table Edit -->
    <div v-if="inputMode === 'table'">
      <h4 class="mb-2">长度信息和壁厚数据</h4>

      <el-row :gutter="20">
        <!-- Left column: Length info -->
        <el-col :span="12">
          <el-card shadow="hover">
            <template #header><strong>长度信息 (mm)</strong></template>
            <el-form label-position="top" label-width="100px">
              <el-form-item label="TL1">
                <el-input-number v-model="form.TL1" :min="0" :step="1" :precision="1" style="width: 100%" />
              </el-form-item>
              <el-form-item label="TLM1长度 (mm)">
                <el-input-number v-model="form.TLM1" :min="0" :step="0.1" :precision="1" style="width: 100%" />
                <template #extra>
                  <span class="help-text">过渡段长度不为0时，TL2段的壁厚必须有值</span>
                </template>
              </el-form-item>
              <el-form-item label="TL2">
                <el-input-number v-model="form.TL2" :min="0" :step="1" :precision="1" style="width: 100%" />
              </el-form-item>
              <el-form-item label="TLM2长度 (mm)">
                <el-input-number v-model="form.TLM2" :min="0" :step="0.1" :precision="1" style="width: 100%" />
                <template #extra>
                  <span class="help-text">过渡段长度不为0时，TL3段的壁厚必须有值</span>
                </template>
              </el-form-item>
              <el-form-item label="TL3">
                <el-input-number v-model="form.TL3" :min="0" :step="1" :precision="1" style="width: 100%" />
              </el-form-item>
              <el-form-item label="TLM3长度 (mm)">
                <el-input-number v-model="form.TLM3" :min="0" :step="0.1" :precision="1" style="width: 100%" />
                <template #extra>
                  <span class="help-text">过渡段长度不为0时，TL4段的壁厚必须有值</span>
                </template>
              </el-form-item>
              <el-form-item label="TL4">
                <el-input-number v-model="form.TL4" :min="0" :step="1" :precision="1" style="width: 100%" />
              </el-form-item>
            </el-form>
          </el-card>
        </el-col>

        <!-- Right column: Wall thickness -->
        <el-col :span="12">
          <el-card shadow="hover">
            <template #header><strong>壁厚数据 (mm)</strong></template>
            <el-form label-position="top">
              <el-form-item label="TL1对应壁厚(T1)">
                <el-input-number v-model="form.T1" :min="0" :step="0.1" :precision="1" style="width: 100%" />
              </el-form-item>
              <div style="height: 85px; color: #999; font-style: italic; padding: 20px 0; text-align: center;">
                TLM1无对应壁厚
              </div>
              <el-form-item label="TL2对应壁厚(T2)">
                <el-input-number v-model="form.T2" :min="0" :step="0.1" :precision="1" style="width: 100%" />
              </el-form-item>
              <div style="height: 85px; color: #999; font-style: italic; padding: 20px 0; text-align: center;">
                TLM2无对应壁厚
              </div>
              <el-form-item label="TL3对应壁厚(T3)">
                <el-input-number v-model="form.T3" :min="0" :step="0.1" :precision="1" style="width: 100%" />
              </el-form-item>
              <div style="height: 85px; color: #999; font-style: italic; padding: 20px 0; text-align: center;">
                TLM3无对应壁厚
              </div>
              <el-form-item label="TL4对应壁厚(T4)">
                <el-input-number v-model="form.T4" :min="0" :step="0.1" :precision="1" style="width: 100%" />
              </el-form-item>
            </el-form>
          </el-card>
        </el-col>
      </el-row>

      <!-- Length info summary -->
      <el-row :gutter="20" class="mt-4">
        <el-col :span="12">
          <el-statistic
            v-if="modelLength > 0"
            :title="`模型文件中心线长度`"
            :value="modelLength"
            suffix="mm"
            :precision="1"
          />
          <el-alert v-else title="未检测到模型文件，无法进行长度校验" type="warning" :closable="false" />
        </el-col>
        <el-col :span="12">
          <el-statistic title="用户输入总长度" :value="totalInputLength" suffix="mm" :precision="1" />
        </el-col>
      </el-row>

      <!-- Length validation -->
      <div v-if="modelLength > 0" class="mt-2">
        <el-alert
          v-if="lengthDiff <= 5"
          :title="`长度校验通过！差异: ${lengthDiff.toFixed(1)} mm (允许范围: ±5 mm)`"
          type="success"
          :closable="false"
        />
        <el-alert
          v-else
          :title="`长度差异: ${lengthDiff.toFixed(1)} mm (允许范围: ±5 mm)`"
          type="warning"
          :closable="false"
        />
      </div>

      <!-- Error display -->
      <el-alert
        v-if="errors.length > 0"
        title="输入验证失败"
        type="error"
        :closable="false"
        class="mt-4"
      >
        <template #default>
          <ul style="margin: 8px 0; padding-left: 20px;">
            <li v-for="(err, idx) in errors" :key="idx">{{ err }}</li>
          </ul>
        </template>
      </el-alert>

      <!-- Save button -->
      <div class="mt-4">
        <el-button type="primary" @click="handleSave" :loading="saving">
          保存数据
        </el-button>
      </div>

      <!-- Data preview after save -->
      <div v-if="saveResult" class="mt-4">
        <h4>数据预览</h4>
        <el-table :data="previewData" size="small" max-height="300" border>
          <el-table-column prop="中心线长度(mm)" label="中心线长度(mm)" width="140" />
          <el-table-column prop="壁厚(mm)" label="壁厚(mm)" width="100" />
          <el-table-column prop="外径周长(mm)" label="外径周长(mm)" width="120" />
          <el-table-column prop="x_length" label="x_length" width="100" v-if="showXY" />
          <el-table-column prop="y_length" label="y_length" width="100" v-if="showXY" />
        </el-table>
        <el-alert :title="`数据已保存: ${saveResult.data_points} 个数据点, 总长度 ${saveResult.total_length.toFixed(1)} mm`" type="success" :closable="false" class="mt-2" />
      </div>
    </div>

    <!-- Tab 2: File Upload -->
    <div v-if="inputMode === 'file'">
      <h4 class="mb-2">上传壁厚形线数据文件</h4>
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept=".csv,.xlsx,.xls"
        :on-change="handleFileChange"
      >
        <el-icon class="el-icon--upload"><Upload /></el-icon>
        <div class="el-upload__text">
          拖拽文件到此处或 <em>点击上传</em>
        </div>
        <template #tip>
          <div class="el-upload__tip">支持 CSV / XLSX / XLS 格式，文件需包含"中心线长度(mm)"和"外径周长(mm)"列</div>
        </template>
      </el-upload>

      <div v-if="selectedFile" class="mt-4">
        <p>已选择文件: <strong>{{ selectedFile.name }}</strong> ({{ (selectedFile.size / 1024).toFixed(1) }} KB)</p>
        <el-button type="primary" @click="handleFileUpload" :loading="uploading">
          上传并解析
        </el-button>
      </div>

      <el-alert v-if="fileUploadResult" :title="fileUploadResult.message" :type="fileUploadResult.success ? 'success' : 'error'" :closable="false" class="mt-2" />
    </div>

    <!-- Bottom action buttons -->
    <el-divider />
    <div class="mt-4">
      <el-button type="primary" size="large" @click="handleApplyToTab3" :loading="applying">
        应用数据到工艺优化
      </el-button>
      <el-text type="info" class="ml-2" v-if="!canApply">请先保存数据后再应用</el-text>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import { ppApi } from '@/api/pp'

const props = defineProps({
  sessionId: { type: String, default: '' },
  modelLength: { type: Number, default: 0 },
})

const emit = defineEmits(['ready'])

const inputMode = ref('table')
const saving = ref(false)
const uploading = ref(false)
const applying = ref(false)
const errors = ref([])
const saveResult = ref(null)
const fileUploadResult = ref(null)
const selectedFile = ref(null)
const previewData = ref([])
const dataSaved = ref(false)

// Form data
const form = reactive({
  TL1: 0, TLM1: 0,
  TL2: 0, TLM2: 0,
  TL3: 0, TLM3: 0,
  TL4: 0,
  T1: 0, T2: 0, T3: 0, T4: 0,
})

// Computed
const totalInputLength = computed(() => {
  return form.TL1 + form.TLM1 + form.TL2 + form.TLM2 + form.TL3 + form.TLM3 + form.TL4
})

const lengthDiff = computed(() => {
  if (props.modelLength <= 0) return 0
  return Math.abs(totalInputLength.value - props.modelLength)
})

const showXY = computed(() => {
  return previewData.value.length > 0 && 'x_length' in previewData.value[0]
})

const canApply = computed(() => dataSaved.value)

// Methods
async function handleSave() {
  if (!props.sessionId) {
    ElMessage.error('会话不存在，请先在TAB1中上传模型')
    return
  }

  saving.value = true
  errors.value = []
  saveResult.value = null
  previewData.value = []

  if (form.T1 > 0 && !(form.TL1 > 0)) {
    errors.value = ['请填写壁厚 T1 对应的长度 TL1。']
    ElMessage.error('请填写壁厚 T1 对应的长度 TL1。')
    saving.value = false
    return
  }

  try {
    const res = await ppApi.saveManualParams(props.sessionId, { ...form })
    if (res.data.success) {
      saveResult.value = res.data
      dataSaved.value = true
      ElMessage.success(res.data.message)

      // Build preview data (first 20 rows)
      const dataPoints = res.data.data_points || 0
      previewData.value = Array.from({ length: Math.min(dataPoints, 20) }, (_, i) => ({
        '中心线长度(mm)': i,
        '壁厚(mm)': 0,
        '外径周长(mm)': 0,
      }))
    } else {
      errors.value = res.data.errors || [res.data.message]
      ElMessage.error('保存失败: ' + (res.data.errors?.join('; ') || res.data.message))
    }
  } catch (e) {
    errors.value = [e.response?.data?.message || e.message || '保存异常']
    ElMessage.error('保存异常')
  } finally {
    saving.value = false
  }
}

function handleFileChange(file) {
  selectedFile.value = file.raw
  fileUploadResult.value = null
}

async function handleFileUpload() {
  if (!selectedFile.value) {
    ElMessage.warning('请先选择文件')
    return
  }
  if (!props.sessionId) {
    ElMessage.error('会话不存在，请先在TAB1中上传模型')
    return
  }

  uploading.value = true
  fileUploadResult.value = null

  const formData = new FormData()
  formData.append('file', selectedFile.value)
  formData.append('session_id', props.sessionId)

  try {
    const res = await ppApi.uploadManualFile(formData)
    if (res.data.success) {
      fileUploadResult.value = { success: true, message: res.data.message }
      dataSaved.value = true
      ElMessage.success(res.data.message)
    } else {
      fileUploadResult.value = { success: false, message: res.data.message }
      ElMessage.error(res.data.message)
    }
  } catch (e) {
    fileUploadResult.value = { success: false, message: e.message }
    ElMessage.error('上传异常')
  } finally {
    uploading.value = false
  }
}

async function handleApplyToTab3() {
  if (!dataSaved.value) {
    ElMessage.warning('请先保存数据后再应用')
    return
  }
  if (!props.sessionId) {
    ElMessage.error('会话不存在')
    return
  }

  applying.value = true
  try {
    const res = await ppApi.applyToTab3(props.sessionId)
    if (res.data.success) {
      ElMessage.success('已解锁工艺参数优化')
      emit('ready')
    } else {
      ElMessage.error(res.data.message)
    }
  } catch (e) {
    ElMessage.error('应用失败')
  } finally {
    applying.value = false
  }
}
</script>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.mb-2 { margin-bottom: 8px; }
.mt-4 { margin-top: 16px; }
.mt-2 { margin-top: 8px; }
.ml-2 { margin-left: 8px; }
.help-text { font-size: 12px; color: #999; }
</style>
