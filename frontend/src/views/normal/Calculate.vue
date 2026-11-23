<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <div class="card-header">
          <span>Normal 模具参数计算</span>
        </div>
      </template>

      <!-- Step 1: Upload & Config -->
      <el-form :model="form" label-width="120px" class="calc-form">
        <el-form-item label="数据来源">
          <el-radio-group v-model="form.loadMode">
            <el-radio-button label="design">来自工艺设计数据库</el-radio-button>
            <el-radio-button label="dxf">来自DXF文件</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.loadMode === 'design'" label="工艺设计记录">
          <el-select v-model="selectedDesignId" placeholder="请选择工艺设计记录" style="width: 100%">
            <el-option
              v-for="d in designOptions"
              :key="d.id"
              :label="`${d.display_name || '未命名'} | ${d.pp_method || '-'} | ${d.vehicle_spec || '-'}`"
              :value="d.id"
            />
          </el-select>
        </el-form-item>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="管件DXF文件" v-if="form.loadMode === 'dxf'">
              <el-upload
                ref="uploadRef"
                :auto-upload="false"
                :limit="1"
                accept=".dxf"
                :on-change="handleFileChange"
                :on-remove="handleFileRemove"
              >
                <template #trigger>
                  <el-button type="primary">选择文件</el-button>
                </template>
                <template #tip>
                  <div class="el-upload__tip">请上传管件标准DXF文件</div>
                </template>
              </el-upload>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="机台类型">
              <el-select v-model="form.machineType" placeholder="选择机台">
                <el-option v-for="mt in machineTypes" :key="mt" :label="mt" :value="mt" />
              </el-select>
            </el-form-item>
            <el-form-item label="额外壁厚">
              <el-switch v-model="form.normalAdd" :disabled="extraWallLocked" />
              <span v-if="extraWallLocked" class="switch-hint">壁厚不超过两段时不能开启</span>
            </el-form-item>
          </el-col>
        </el-row>

        <!-- Tube params table (shown after load) -->
        <div v-if="tubeParams.length > 0" class="section">
          <h4>管件参数</h4>
          <el-table :data="tubeParams" border size="small">
            <el-table-column prop="参数" label="参数" width="200" />
            <el-table-column prop="值" label="值">
              <template #default="{ row }">
                <el-input v-model="row['值']" size="small" />
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!-- Mold Selection -->
        <div class="section">
          <h4>模具选择</h4>
          <el-checkbox-group v-model="form.moldList">
            <el-checkbox
              v-for="mold in availableMolds"
              :key="mold"
              :label="mold"
            >{{ mold }}</el-checkbox>
          </el-checkbox-group>
        </div>

        <el-form-item>
          <el-button type="primary" @click="handleLoad" :loading="loadingLoad" :disabled="form.loadMode === 'dxf' ? !selectedFile : !selectedDesignId">
            加载管件
          </el-button>
          <el-button type="success" @click="handleCalculate" :loading="loadingCalc" :disabled="tubeParams.length === 0">
            计算
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- Results -->
    <el-card v-if="results" class="page-card" style="margin-top: 16px;">
      <template #header>
        <div class="card-header">
          <span>计算结果</span>
          <div>
            <el-button type="primary" size="small" @click="handleSave" :loading="loadingSave">保存到模具数据库</el-button>
            <el-button type="warning" size="small" @click="handleExport" :loading="loadingExport">导出ZIP</el-button>
          </div>
        </div>
      </template>

      <el-tabs v-model="activeTab">
        <el-tab-pane
          v-for="(moldData, moldName) in results"
          :key="moldName"
          :label="moldName"
          :name="moldName"
        >
          <el-table :data="moldData" border size="small" max-height="400">
            <el-table-column prop="参数" label="参数" width="200" />
            <el-table-column prop="值" label="值">
              <template #default="{ row }">
                <el-input v-model="row['值']" size="small" @change="onParamChange(moldName, row)" />
              </template>
            </el-table-column>
            <el-table-column prop="描述" label="描述" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'
import { getCalcAuth, licenseRequestConfig, modifyMoldParam } from '@/utils/calc'
import { saveZipResponse, exportErrorMessage } from '@/utils/download'

const form = reactive({
  loadMode: 'design',
  machineType: 'DC0124',
  normalAdd: true,
  moldList: []
})

const selectedFile = ref(null)
const tubeParams = ref([])
const results = ref(null)
const activeTab = ref('')
const sessionId = ref(null)
const allMoldsMap = ref({})
const designOptions = ref([])
const selectedDesignId = ref(null)

function positiveWallCount(rows) {
  let count = 0
  for (const row of rows || []) {
    const name = String(row['参数'] ?? '').trim()
    if (!/^T\d+$/.test(name)) continue
    const value = Number.parseFloat(row['值'])
    if (Number.isFinite(value) && value > 0) count += 1
  }
  return count
}

const extraWallLocked = computed(() => {
  if (!tubeParams.value.length) return false
  return positiveWallCount(tubeParams.value) <= 2
})

const availableMolds = computed(() => {
  const moldKey = form.normalAdd ? `${form.machineType} forming` : form.machineType
  return allMoldsMap.value[moldKey] || allMoldsMap.value[form.machineType] || []
})

const machineTypes = computed(() => {
  return Object.keys(allMoldsMap.value).filter((key) => !key.includes(' forming'))
})

const loadingLoad = ref(false)
const loadingCalc = ref(false)
const loadingSave = ref(false)
const loadingExport = ref(false)

onMounted(async () => {
  try {
    const res = await axios.get('/api/normal/mold-options')
    if (res.data.success) {
      allMoldsMap.value = res.data.molds
      const moldKey = form.normalAdd ? `${form.machineType} forming` : form.machineType
      form.moldList = [...(allMoldsMap.value[moldKey] || allMoldsMap.value[form.machineType] || [])]
    }
  } catch (e) {
    console.error('Failed to load mold options', e)
  }
  try {
    const res = await axios.get('/api/normal/design-options')
    if (res.data.success) {
      designOptions.value = res.data.designs || []
      if (designOptions.value.length) selectedDesignId.value = designOptions.value[0].id
    }
  } catch (e) {
    console.error('Failed to load design options', e)
  }
})

watch(() => form.machineType, (newType) => {
  const moldKey = form.normalAdd ? `${newType} forming` : newType
  form.moldList = [...(allMoldsMap.value[moldKey] || allMoldsMap.value[newType] || [])]
})

watch(extraWallLocked, (locked) => {
  if (locked) form.normalAdd = false
})

watch(() => form.normalAdd, () => {
  const moldKey = form.normalAdd ? `${form.machineType} forming` : form.machineType
  form.moldList = [...(allMoldsMap.value[moldKey] || allMoldsMap.value[form.machineType] || [])]
})

function handleFileChange(file) {
  selectedFile.value = file.raw
}

function handleFileRemove() {
  selectedFile.value = null
  tubeParams.value = []
}

async function handleLoad() {
  if (form.loadMode === 'dxf' && !selectedFile.value) return
  if (form.loadMode === 'design' && !selectedDesignId.value) {
    ElMessage.warning('请先选择工艺设计记录')
    return
  }
  loadingLoad.value = true
  try {
    let res
    if (form.loadMode === 'dxf') {
      const formData = new FormData()
      formData.append('file', selectedFile.value)
      res = await axios.post('/api/normal/load', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
    } else {
      res = await axios.post('/api/normal/load-design', { design_id: selectedDesignId.value })
    }
    if (res.data.success) {
      tubeParams.value = res.data.params_table || []
      ElMessage.success('管件参数加载成功')
    } else {
      ElMessage.error(res.data.message || '加载失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '加载失败')
  } finally {
    loadingLoad.value = false
  }
}

async function handleCalculate() {
  if (extraWallLocked.value) form.normalAdd = false
  loadingCalc.value = true
  try {
    const { userName, license } = getCalcAuth()
    const res = await axios.post('/api/normal/calculate', {
      tube_params: tubeParams.value,
      user_name: userName,
      normal_add: form.normalAdd,
      mold_list: form.moldList,
      machine_type: form.machineType,
      session_id: sessionId.value,
      license,
    }, licenseRequestConfig(license))
    if (res.data.success) {
      results.value = res.data.molds
      sessionId.value = res.data.session_id
      const keys = Object.keys(res.data.molds)
      if (keys.length > 0) activeTab.value = keys[0]
      ElMessage.success('计算完成')
    } else {
      ElMessage.error(res.data.message || '计算失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '计算失败')
  } finally {
    loadingCalc.value = false
  }
}

async function onParamChange(moldName, row) {
  if (!sessionId.value) return
  try {
    const res = await modifyMoldParam('/api/normal', sessionId.value, moldName, row)
    if (res.data.success) {
      results.value = res.data.molds
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '参数修改失败')
  }
}

async function handleSave() {
  loadingSave.value = true
  try {
    const res = await axios.post('/api/normal/save', {
      session_id: sessionId.value,
    })
    if (res.data.success) {
      ElMessage.success(res.data.message || '保存成功')
    } else {
      ElMessage.error(res.data.message || '保存失败')
    }
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    loadingSave.value = false
  }
}

async function handleExport() {
  loadingExport.value = true
  try {
    const { userName, license } = getCalcAuth()
    const res = await axios.post('/api/normal/export', {
      session_id: sessionId.value,
      tube_params: tubeParams.value,
      user_name: userName,
      normal_add: form.normalAdd,
      mold_list: form.moldList,
      machine_type: form.machineType,
      license,
    }, {
      responseType: 'blob',
      ...licenseRequestConfig(license),
    })

    await saveZipResponse(res, 'mold_export.zip')
    ElMessage.success('导出成功')
  } catch (e) {
    ElMessage.error(await exportErrorMessage(e))
  } finally {
    loadingExport.value = false
  }
}
</script>

<style scoped>
.page-container {
  min-height: 100%;
  overflow-y: auto;
  box-sizing: border-box;
  padding-bottom: 24px;
}

.page-card {
  overflow: visible;
}

.calc-form {
  max-width: 900px;
}
.section {
  margin: 20px 0;
}
.section h4 {
  margin-bottom: 12px;
  color: var(--primary-color);
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.switch-hint {
  margin-left: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
