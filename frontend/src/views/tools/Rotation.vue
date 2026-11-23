<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <span>模型旋转工具</span>
      </template>

      <el-alert
        title='说明：角度遵循右手系；"顺时针/逆时针"按"从 +轴 方向朝向原点看"来定义。'
        type="info"
        :closable="false"
        style="margin-bottom: 20px;"
      />

      <el-form label-width="120px">
        <el-form-item label="模型文件">
          <el-upload
            drag
            :auto-upload="false"
            :limit="1"
            accept=".stp,.STP,.step,.STEP"
            :on-change="handleFileChange"
            :on-remove="handleFileRemove"
          >
            <div class="upload-area">
              <el-icon class="el-icon--upload"><upload-filled /></el-icon>
              <div class="el-upload__text">
                拖拽 STP/STEP 文件到此处，或 <em>点击选择文件</em>
              </div>
              <div class="el-upload__tip">
                仅支持 .stp、.step，旋转后下载为 STP/STEP
              </div>
            </div>
          </el-upload>
          <div v-if="selectedFile" class="selected-file">已选择：{{ selectedFile.name }}</div>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="handleUpload" :loading="loadingUpload" :disabled="!selectedFile">
            上传模型
          </el-button>
        </el-form-item>
      </el-form>

      <div v-if="sessionId" class="section">
        <h4>模型预览</h4>
        <StlViewer :key="previewKey" :url="previewUrl" height="360" />
      </div>

      <div v-if="sessionId" class="section">
        <h4>旋转操作</h4>

        <el-row :gutter="16">
          <el-col :span="8">
            <h5>X 轴</h5>
            <el-button @click="handleRotate('x', -90)" :disabled="!sessionId" style="width: 100%; margin-bottom: 8px;">
              顺时针 90°
            </el-button>
            <el-button @click="handleRotate('x', 90)" :disabled="!sessionId" style="width: 100%; margin-bottom: 8px;">
              逆时针 90°
            </el-button>
            <el-button @click="handleRotate('x', 180)" :disabled="!sessionId" style="width: 100%;">
              旋转 180°
            </el-button>
          </el-col>

          <el-col :span="8">
            <h5>Y 轴</h5>
            <el-button @click="handleRotate('y', -90)" :disabled="!sessionId" style="width: 100%; margin-bottom: 8px;">
              顺时针 90°
            </el-button>
            <el-button @click="handleRotate('y', 90)" :disabled="!sessionId" style="width: 100%; margin-bottom: 8px;">
              逆时针 90°
            </el-button>
            <el-button @click="handleRotate('y', 180)" :disabled="!sessionId" style="width: 100%;">
              旋转 180°
            </el-button>
          </el-col>

          <el-col :span="8">
            <h5>Z 轴</h5>
            <el-button @click="handleRotate('z', -90)" :disabled="!sessionId" style="width: 100%; margin-bottom: 8px;">
              顺时针 90°
            </el-button>
            <el-button @click="handleRotate('z', 90)" :disabled="!sessionId" style="width: 100%; margin-bottom: 8px;">
              逆时针 90°
            </el-button>
            <el-button @click="handleRotate('z', 180)" :disabled="!sessionId" style="width: 100%;">
              旋转 180°
            </el-button>
          </el-col>
        </el-row>

        <el-divider />

        <el-row :gutter="16">
          <el-col :span="12">
            <el-button @click="handleReset" :disabled="!sessionId">重置旋转</el-button>
          </el-col>
          <el-col :span="12">
            <el-button type="primary" @click="handleDownload" :disabled="!sessionId">下载旋转后模型（STP/STEP）</el-button>
          </el-col>
        </el-row>

        <div v-if="rotationMatrix" style="margin-top: 16px;">
          <h5>累计旋转矩阵（3×3）</h5>
          <pre class="matrix-text">{{ rotationMatrix }}</pre>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import axios from 'axios'
import StlViewer from '@/components/StlViewer.vue'

const selectedFile = ref(null)
const sessionId = ref(null)
const loadingUpload = ref(false)
const rotationMatrix = ref(null)
const filename = ref('rotated.step')
const previewUrl = ref('')
const previewKey = ref(0)

function handleFileChange(file) { selectedFile.value = file.raw }
function handleFileRemove() {
  selectedFile.value = null
  sessionId.value = null
  rotationMatrix.value = null
  previewUrl.value = ''
}

function refreshPreview() {
  if (!sessionId.value) return
  previewUrl.value = `/api/rotation/preview/${sessionId.value}?v=${Date.now()}`
  previewKey.value++
}

async function handleUpload() {
  if (!selectedFile.value) return
  loadingUpload.value = true
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    const res = await axios.post('/api/rotation/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    if (res.data.success) {
      sessionId.value = res.data.session_id
      filename.value = res.data.filename
      rotationMatrix.value = null
      refreshPreview()
      ElMessage.success('上传成功')
    } else {
      ElMessage.error(res.data.message || '上传失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '上传失败')
  } finally {
    loadingUpload.value = false
  }
}

async function handleRotate(axis, degrees) {
  try {
    const res = await axios.post('/api/rotation/transform', {
      session_id: sessionId.value,
      axis,
      degrees
    })
    if (res.data.success) {
      ElMessage.success(`旋转 ${axis.toUpperCase()} ${degrees}° 完成`)
      // Update rotation matrix display (accumulate)
      updateRotationMatrix(axis, degrees)
      refreshPreview()
    } else {
      ElMessage.error(res.data.message || '旋转失败')
    }
  } catch (e) { ElMessage.error('旋转失败') }
}

function updateRotationMatrix(axis, degrees) {
  // Simple display: just show the last operation
  const rad = degrees * Math.PI / 180
  const c = Math.cos(rad).toFixed(4)
  const s = Math.sin(rad).toFixed(4)
  let mat = ''
  if (axis === 'x') {
    mat = `[[1.0000, 0.0000, 0.0000],\n [0.0000, ${c}, ${-s}],\n [0.0000, ${s}, ${c}]]`
  } else if (axis === 'y') {
    mat = `[[${c}, 0.0000, ${s}],\n [0.0000, 1.0000, 0.0000],\n [${-s}, 0.0000, ${c}]]`
  } else {
    mat = `[[${c}, ${-s}, 0.0000],\n [${s}, ${c}, 0.0000],\n [0.0000, 0.0000, 1.0000]]`
  }
  rotationMatrix.value = mat
}

async function handleReset() {
  try {
    const res = await axios.post('/api/rotation/reset', { session_id: sessionId.value })
    if (res.data.success) {
      rotationMatrix.value = null
      refreshPreview()
      ElMessage.success('已重置')
    }
  } catch (e) { ElMessage.error('重置失败') }
}

async function handleDownload() {
  try {
    const res = await axios.get(`/api/rotation/download/${sessionId.value}`, {
      responseType: 'blob'
    })
    const url = window.URL.createObjectURL(new Blob([res.data]))
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', filename.value)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch (e) { ElMessage.error('下载失败') }
}
</script>

<style scoped>
.section { margin: 24px 0; }
.section h4 { margin-bottom: 12px; color: var(--el-color-primary); }
.section h5 { margin: 8px 0; font-weight: 600; }
.matrix-text {
  font-family: monospace;
  background: var(--el-fill-color-light);
  padding: 8px 12px;
  border-radius: 4px;
  white-space: pre;
}
.upload-area { padding: 8px 0; }
.selected-file {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
:deep(.el-upload-dragger) {
  padding: 14px 20px;
  min-height: 104px;
}
:deep(.el-upload-dragger .el-icon--upload) {
  margin-bottom: 6px;
  font-size: 34px;
}
:deep(.el-upload__tip) {
  margin-top: 4px;
  line-height: 1.2;
}
</style>
