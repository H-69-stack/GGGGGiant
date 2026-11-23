<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <div class="card-header">
          <span>TP 模具参数计算</span>
        </div>
      </template>

      <el-alert
        v-if="qaBlock"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 12px"
      >
        <template #title>{{ qaBlock.message }}</template>
        <div class="qa-block-body">
          <span>
            待复核 {{ qaBlock.review?.review_station_count || 0 }} 站<template
              v-if="qaBlock.review?.max_normal_deviation_deg != null"
            >，最大法向偏差 {{ Number(qaBlock.review.max_normal_deviation_deg).toFixed(2) }}°</template>。
            若确认只是测量噪声，可人工放行后继续。
          </span>
          <el-button type="warning" size="small" :loading="qaAckLoading" @click="ackReview">
            确认放行并继续
          </el-button>
        </div>
      </el-alert>

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
              <el-input value="DC0128" disabled />
            </el-form-item>
          </el-col>
        </el-row>

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
          <p v-if="profileMeta.D_length != null">TP抽起点长度(mm): {{ Number(profileMeta.D_length).toFixed(2) }}</p>
          <p v-if="profileMeta.T_D_length != null">TP抽终点长度(mm): {{ Number(profileMeta.T_D_length).toFixed(2) }}</p>
          <p v-if="profileMeta.TP_segment_source" style="font-size:12px; color:#909399;">
            TP段长度(T_L): {{ profileMeta.T_L != null ? Number(profileMeta.T_L).toFixed(1) + ' mm' : '-' }} |
            来源: {{ profileMeta.TP_segment_source === 'auto' ? '自动检测' : profileMeta.TP_segment_source === 'full_range' ? '数据范围' : '工艺参数' }}
          </p>
          <p v-if="profileMeta.T_D_diam != null" style="font-size:12px; color:#909399;">
            大端直径: {{ Number(profileMeta.T_D_diam).toFixed(3) }} mm |
            小端直径: {{ profileMeta.D_diam != null ? Number(profileMeta.D_diam).toFixed(3) + ' mm' : '-' }}
          </p>
          <el-table :data="profileData" border size="small" max-height="240">
            <el-table-column prop="X" label="X" />
            <el-table-column prop="Y" label="Y" />
          </el-table>
        </div>

        <div v-if="profileReady" class="section">
          <h4>3D套模预览</h4>
          <p class="tip-text">基于管件外径轮廓数据生成的抽套模（旋转体），直接导出为STEP文件供CAD使用</p>
          <el-button type="primary" :loading="loadingViz3d" @click="generateMold">
            生成套模
          </el-button>
          <div v-if="moldPreviewUrl" class="viz3d-container" style="margin-top:12px;">
            <StlViewer :url="moldPreviewUrl" :key="moldSessionId" height="500" color="#5B8DEE" />
          </div>
          <div v-if="moldSessionId" style="margin-top:8px; display:flex; gap:8px;">
            <el-button type="success" size="small" :loading="loadingMoldDownload" @click="downloadMold('step')">
              下载 STEP
            </el-button>
            <el-button type="info" size="small" :loading="loadingMoldDownload" @click="downloadMold('stl')">
              下载 STL
            </el-button>
          </div>
        </div>

        <div v-if="tubeParams.length > 0 && profileReady" class="section">
          <h4>参数设定</h4>
          <el-form-item label="干涉长度(mm)">
            <el-input-number v-model="form.interferenceLength" :min="0" :max="100" :step="1" />
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
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'
import { getCalcAuth, licenseRequestConfig, modifyMoldParam } from '@/utils/calc'
import { saveBlob, saveZipResponse, exportErrorMessage } from '@/utils/download'
import StlViewer from '@/components/StlViewer.vue'

const allMoldsMap = ref({})
const allMolds = ref([])
const designOptions = ref([])
const selectedDesignId = ref(null)

const form = reactive({
  loadMode: 'design',
  moldList: [],
  interferenceLength: 50,
})

const selectedFile = ref(null)
const tubeParams = ref([])
const profileReady = ref(false)
const profileData = ref([])
const profileMeta = ref({})
const centerlineData = ref([])
const results = ref(null)
const activeTab = ref('')
const sessionId = ref(null)
const loadingLoad = ref(false)
const loadingProfile = ref(false)
const loadingCalc = ref(false)
const loadingSave = ref(false)
const loadingExport = ref(false)
const viz3dHtml = ref('')
const loadingViz3d = ref(false)
const moldPreviewUrl = ref('')
const moldSessionId = ref('')
const qaBlock = ref(null)
const qaAckLoading = ref(false)

async function ackReview() {
  const info = qaBlock.value
  if (!info?.tube_id) return
  try {
    await ElMessageBox.confirm(
      '放行后该管件的待复核标记会被记录为“已人工确认”，TP/硬模等页面将允许继续使用这份测量数据。仅在确认偏差属于测量噪声时使用。',
      '确认放行测量校核',
      { type: 'warning', confirmButtonText: '确认放行', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  qaAckLoading.value = true
  try {
    const res = await axios.post(`/api/pp/tubes/${info.tube_id}/ack-review`, { note: 'TP 页面人工确认放行' })
    if (res.data?.success) {
      ElMessage.success('已放行，正在重新加载…')
      qaBlock.value = null
      await handleLoad()
    } else {
      ElMessage.error(res.data?.message || '放行失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '放行失败')
  } finally {
    qaAckLoading.value = false
  }
}

function applyProfileResponse(data) {
  if (data.profile_ready) {
    profileReady.value = true
    profileData.value = data.profile_data || []
    profileMeta.value = {
      T_D_length: data.T_D_length,
      D_length: data.D_length,
      T_D_diam: data.T_D_diam,
      D_diam: data.D_diam,
      T_L: data.T_L,
      TP_segment_length: data.TP_segment_length,
      TP_segment_source: data.TP_segment_source,
    }
    centerlineData.value = data.centerline_perimeter_data || centerlineData.value
    // Use session_id from response if available
    if (data.session_id) sessionId.value = data.session_id
    // Reset 3D preview when new profile is loaded
    viz3dHtml.value = ''
    moldPreviewUrl.value = ''
    moldSessionId.value = ''
    return true
  }
  profileReady.value = false
  profileData.value = []
  profileMeta.value = {}
  if (data.profile_error) {
    ElMessage.warning(data.profile_error)
  }
  return false
}

async function loadViz3d() {
  if (!sessionId.value) return
  loadingViz3d.value = true
  try {
    const res = await axios.get(`/api/tp/viz-3d?session_id=${encodeURIComponent(sessionId.value)}`)
    if (res.data.success) {
      viz3dHtml.value = res.data.html
    } else {
      ElMessage.warning(res.data.message || '加载3D预览失败')
    }
  } catch (e) {
    ElMessage.error('加载3D预览失败')
  } finally {
    loadingViz3d.value = false
  }
}

async function generateMold() {
  if (!profileReady.value) {
    ElMessage.warning('请先生成形线')
    return
  }
  loadingViz3d.value = true
  try {
    const res = await axios.post('/api/tp/generate-mold', {
      design_id: form.loadMode === 'design' ? selectedDesignId.value : undefined,
      session_id: sessionId.value || undefined,
      profile_data: profileData.value,
      centerline_perimeter_data: centerlineData.value,
      T_D_length: profileMeta.value.T_D_length,
      D_length: profileMeta.value.D_length,
      T_D_diam: profileMeta.value.T_D_diam,
      D_diam: profileMeta.value.D_diam,
    })
    if (res.data.success) {
      moldPreviewUrl.value = res.data.stl_download_url
      moldSessionId.value = res.data.session_id
      ElMessage.success(`套模已生成：${res.data.vertices}顶点 ${res.data.faces}面`)
    } else {
      ElMessage.error(res.data.message || '套模生成失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '套模生成失败')
  } finally {
    loadingViz3d.value = false
  }
}

const loadingMoldDownload = ref(false)

async function downloadMold(format) {
  if (!moldSessionId.value) return
  loadingMoldDownload.value = true
  try {
    const url = format === 'stl'
      ? `/api/tp/download-mold/${moldSessionId.value}?format=stl`
      : `/api/tp/download-mold/${moldSessionId.value}`
    const res = await axios.get(url, { responseType: 'blob' })
    const filename = format === 'stl' ? 'TPDrawMold.stl' : 'TPDrawMold.step'
    saveBlob(res.data, filename)
    ElMessage.success(`已下载 ${format.toUpperCase()} 文件`)
  } catch (e) {
    ElMessage.error('下载失败，请确认已登录')
  } finally {
    loadingMoldDownload.value = false
  }
}

onMounted(async () => {
  try {
    const res = await axios.get('/api/tp/mold-options')
    if (res.data.success) {
      allMoldsMap.value = res.data.molds
      const firstKey = Object.keys(res.data.molds)[0] || ''
      allMolds.value = res.data.molds[firstKey] || []
      form.moldList = [...allMolds.value]
    }
  } catch (e) {
    console.error('Failed to load mold options', e)
  }
  try {
    const res = await axios.get('/api/tp/design-options')
    if (res.data.success) {
      designOptions.value = res.data.designs || []
      if (designOptions.value.length) selectedDesignId.value = designOptions.value[0].id
    }
  } catch (e) {
    console.error('Failed to load design options', e)
  }
})

function handleFileChange(file) {
  selectedFile.value = file.raw
}
function handleFileRemove() {
  selectedFile.value = null
  tubeParams.value = []
  profileReady.value = false
  profileData.value = []
  viz3dHtml.value = ''
}

async function handleStlChange(file) {
  if (!file?.raw || tubeParams.value.length === 0) return
  loadingProfile.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.raw)
    fd.append('tube_params', JSON.stringify(tubeParams.value))
    fd.append('session_id', sessionId.value || '')
    const res = await axios.post('/api/tp/analyze-stl', fd, {
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
  qaBlock.value = null
  try {
    let res
    if (form.loadMode === 'dxf') {
      const fd = new FormData()
      fd.append('file', selectedFile.value)
      res = await axios.post('/api/tp/load', fd, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
    } else {
      res = await axios.post('/api/tp/load-design', { design_id: selectedDesignId.value })
    }
    if (res.data.success) {
      tubeParams.value = res.data.params_table || []
      results.value = null
      sessionId.value = res.data.session_id || null
      if (form.loadMode === 'design') {
        centerlineData.value = res.data.centerline_perimeter_data || []
        applyProfileResponse(res.data)
      } else {
        profileReady.value = false
        profileData.value = []
        viz3dHtml.value = ''
        moldPreviewUrl.value = ''
        moldSessionId.value = ''
      }
      ElMessage.success('管件参数加载成功')
    } else {
      ElMessage.error(res.data.message || '加载失败')
    }
  } catch (e) {
    const data = e.response?.data || {}
    if (data.code === 'measurement_review') {
      qaBlock.value = {
        message: data.message,
        tube_id: data.tube_id,
        review: data.review || {},
      }
      ElMessage.warning('测量数据存在待复核截面，可在页面上人工确认放行')
    } else {
      ElMessage.error(data.message || '加载失败')
    }
  } finally {
    loadingLoad.value = false
  }
}

async function handleCalculate() {
  loadingCalc.value = true
  try {
    const { userName, license } = getCalcAuth()
    const res = await axios.post('/api/tp/calculate', {
      tube_params: tubeParams.value,
      user_name: userName,
      mold_list: form.moldList,
      machine_type: 'DC0128',
      session_id: sessionId.value,
      interference_length: form.interferenceLength,
      apply_taper: true,
      profile_data: profileData.value,
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
    const data = e.response?.data || {}
    if (data.code === 'measurement_review') {
      qaBlock.value = { message: data.message, review: data.review, tube_id: data.tube_id }
      ElMessage.error(data.message)
    } else {
      ElMessage.error(data.message || '计算失败')
    }
  } finally {
    loadingCalc.value = false
  }
}

async function onParamChange(moldName, row) {
  if (!sessionId.value) return
  try {
    const res = await modifyMoldParam('/api/tp', sessionId.value, moldName, row)
    if (res.data.success) results.value = res.data.molds
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '参数修改失败')
  }
}

async function handleSave() {
  loadingSave.value = true
  try {
    const res = await axios.post('/api/tp/save', { session_id: sessionId.value })
    if (res.data.success) ElMessage.success(res.data.message || '保存成功')
    else ElMessage.error(res.data.message || '保存失败')
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    loadingSave.value = false
  }
}

async function handleExport() {
  loadingExport.value = true
  try {
    const { userName } = getCalcAuth()
    const res = await axios.post('/api/tp/export', {
      session_id: sessionId.value,
      tube_params: tubeParams.value,
      user_name: userName,
      mold_list: form.moldList,
      machine_type: 'DC0128',
      interference_length: form.interferenceLength,
      apply_taper: true,
      profile_data: profileData.value,
    }, { responseType: 'blob' })
    await saveZipResponse(res, 'tp_mold_export.zip')
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

.calc-form { max-width: 900px; }
.section { margin: 20px 0; }
.section h4 { margin-bottom: 12px; color: var(--primary-color); }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.viz3d-container {
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  overflow: hidden;
  background: #fafafa;
}
.viz3d-frame {
  width: 100%;
  height: 400px;
  border: none;
  display: block;
}
</style>
