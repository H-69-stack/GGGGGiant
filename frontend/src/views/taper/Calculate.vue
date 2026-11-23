<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <div class="card-header">
          <span>Taper 锥度模具参数计算</span>
        </div>
      </template>

      <el-form :model="form" label-width="140px" class="calc-form">
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
                <el-option label="EC0120" value="EC0120" />
                <el-option label="EC0121" value="EC0121" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <div v-if="tubeParams.length > 0" class="section">
          <h4>管件参数</h4>
          <el-alert
            v-if="taperLengthWarning"
            type="warning"
            :closable="false"
            title="由于Taper操作长度超过480mm，请选择EC0121机床"
            style="margin-bottom: 12px;"
          />
          <el-table :data="tubeParams" border size="small">
            <el-table-column prop="参数" label="参数" width="200" />
            <el-table-column prop="值" label="值">
              <template #default="{ row }">
                <el-input v-model="row['值']" size="small" />
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div v-if="tubeParams.length > 0 && form.loadMode === 'dxf' && !profileReady" class="section">
          <h4>型线加载</h4>
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".stl"
            :on-change="handleStlChange"
          >
            <template #trigger>
              <el-button type="primary" :loading="loadingProfile">上传STL并分析形线</el-button>
            </template>
            <template #tip>
              <div class="el-upload__tip">DXF路径需上传STL提取形线（与老程序一致）</div>
            </template>
          </el-upload>
        </div>

        <div v-if="profileReady" class="section">
          <h4>型线参数</h4>
          <p v-if="profileMeta.m_D_length != null">m_D长度(mm): {{ Number(profileMeta.m_D_length).toFixed(2) }}</p>
          <p v-if="profileMeta.B_D_length != null">B_D长度(mm): {{ Number(profileMeta.B_D_length).toFixed(2) }}</p>
          <el-table :data="lineData" border size="small" max-height="240">
            <el-table-column prop="X" label="X" />
            <el-table-column prop="Y" label="Y" />
          </el-table>
        </div>

        <div v-if="tubeParams.length > 0 && profileReady" class="section">
          <h4>参数设定</h4>
          <el-form-item label="TP过渡形线长度(mm)">
            <el-input-number v-model="form.tpLength" :min="0" :max="200" :step="1" />
          </el-form-item>
          <h4>模具选择</h4>
          <el-checkbox-group v-model="form.moldList">
            <el-checkbox v-for="mold in allMolds" :key="mold" :label="mold">{{ mold }}</el-checkbox>
          </el-checkbox-group>
        </div>

        <el-form-item>
          <el-button type="primary" @click="handleLoad" :loading="loadingLoad" :disabled="form.loadMode === 'dxf' ? !selectedFile : !selectedDesignId">加载管件</el-button>
          <el-button type="success" @click="handleCalculate" :loading="loadingCalc" :disabled="tubeParams.length === 0 || !profileReady">计算</el-button>
        </el-form-item>
      </el-form>
    </el-card>

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
        <el-tab-pane v-for="(moldData, moldName) in results" :key="moldName" :label="moldName" :name="moldName">
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

const allMoldsMap = ref({})
const designOptions = ref([])
const selectedDesignId = ref(null)

const form = reactive({
  loadMode: 'design',
  machineType: 'EC0120',
  moldList: [],
  tpLength: 25,
})

const allMolds = computed(() => {
  return allMoldsMap.value[form.machineType] || []
})

const taperLengthWarning = computed(() => {
  const tl = tubeParams.value.find((row) => row['参数'] === 'TL')
  if (!tl) return false
  const val = parseFloat(tl['值'])
  return !Number.isNaN(val) && val > 480
})

const selectedFile = ref(null)
const tubeParams = ref([])
const profileReady = ref(false)
const lineData = ref([])
const profileMeta = ref({})
const results = ref(null)
const activeTab = ref('')
const sessionId = ref(null)
const loadingLoad = ref(false)
const loadingProfile = ref(false)
const loadingCalc = ref(false)
const loadingSave = ref(false)
const loadingExport = ref(false)

function applyProfileResponse(data) {
  if (data.profile_ready && (data.line_data || []).length) {
    profileReady.value = true
    lineData.value = data.line_data || []
    profileMeta.value = {
      m_D_length: data.m_D_length,
      B_D_length: data.B_D_length,
    }
    if (data.tp_length_suggested != null) {
      form.tpLength = Number(data.tp_length_suggested) || 25
    }
    if (data.session_id) sessionId.value = data.session_id
    return true
  }
  profileReady.value = false
  lineData.value = []
  profileMeta.value = {}
  if (data.profile_error) {
    ElMessage.warning(data.profile_error)
  }
  return false
}

onMounted(async () => {
  try {
    const res = await axios.get('/api/taper/mold-options')
    if (res.data.success) {
      allMoldsMap.value = res.data.molds
      form.moldList = [...(allMoldsMap.value[form.machineType] || [])]
    }
  } catch (e) {
    console.error('Failed to load mold options', e)
  }
  try {
    const res = await axios.get('/api/taper/design-options')
    if (res.data.success) {
      designOptions.value = res.data.designs || []
      if (designOptions.value.length) selectedDesignId.value = designOptions.value[0].id
    }
  } catch (e) {
    console.error('Failed to load design options', e)
  }
})

watch(() => form.machineType, (newType) => {
  form.moldList = [...(allMoldsMap.value[newType] || [])]
})

function handleFileChange(file) { selectedFile.value = file.raw }
function handleFileRemove() {
  selectedFile.value = null
  tubeParams.value = []
  profileReady.value = false
  lineData.value = []
}

async function handleStlChange(file) {
  if (!file?.raw || tubeParams.value.length === 0) return
  loadingProfile.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.raw)
    fd.append('tube_params', JSON.stringify(tubeParams.value))
    fd.append('session_id', sessionId.value || '')
    const res = await axios.post('/api/taper/analyze-stl', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    if (res.data.success) {
      applyProfileResponse(res.data)
      ElMessage.success('形线分析完成')
    } else {
      ElMessage.error(res.data.message || '形线分析失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '形线分析失败')
  } finally {
    loadingProfile.value = false
  }
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
      const fd = new FormData()
      fd.append('file', selectedFile.value)
      res = await axios.post('/api/taper/load', fd, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
    } else {
      res = await axios.post('/api/taper/load-design', { design_id: selectedDesignId.value })
    }
    if (res.data.success) {
      tubeParams.value = res.data.params_table || []
      results.value = null
      sessionId.value = res.data.session_id || null
      if (form.loadMode === 'design') {
        applyProfileResponse(res.data)
      } else {
        profileReady.value = false
        lineData.value = []
      }
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
  loadingCalc.value = true
  try {
    const { userName, license } = getCalcAuth()
    const res = await axios.post('/api/taper/calculate', {
      tube_params: tubeParams.value,
      user_name: userName,
      mold_list: form.moldList,
      machine_type: form.machineType,
      session_id: sessionId.value,
      tp_length: form.tpLength,
      line_data: lineData.value,
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
    const res = await modifyMoldParam('/api/taper', sessionId.value, moldName, row)
    if (res.data.success) results.value = res.data.molds
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '参数修改失败')
  }
}

async function handleSave() {
  loadingSave.value = true
  try {
    const res = await axios.post('/api/taper/save', { session_id: sessionId.value })
    if (res.data.success) ElMessage.success(res.data.message || '保存成功')
    else ElMessage.error(res.data.message || '保存失败')
  } catch (e) { ElMessage.error('保存失败') }
  finally { loadingSave.value = false }
}

async function handleExport() {
  loadingExport.value = true
  try {
    const { userName } = getCalcAuth()
    const res = await axios.post('/api/taper/export', {
      session_id: sessionId.value,
      tube_params: tubeParams.value,
      user_name: userName,
      mold_list: form.moldList,
      machine_type: form.machineType,
      tp_length: form.tpLength,
      line_data: lineData.value,
    }, { responseType: 'blob' })
    await saveZipResponse(res, 'taper_mold_export.zip')
    ElMessage.success('导出成功')
  } catch (e) {
    ElMessage.error(await exportErrorMessage(e))
  } finally { loadingExport.value = false }
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

.calc-form { max-width: 900px; }
.section { margin: 20px 0; }
.section h4 { margin-bottom: 12px; color: var(--primary-color); }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>
