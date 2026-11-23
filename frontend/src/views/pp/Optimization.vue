<template>
  <div class="page-content">
    <div class="page-card">
      <h2 class="page-title">工艺参数优化</h2>

      <el-card class="loader-card" shadow="never">
        <template #header>
          <div class="loader-head">
            <span>第一步：从数据库加载分析结果</span>
            <el-button size="small" @click="loadTubes">刷新</el-button>
          </div>
        </template>

        <div class="loader-toolbar">
          <el-input
            v-model="dbSearch"
            placeholder="搜索文件名 / 显示名称"
            clearable
            @input="loadTubes"
          />
          <el-select v-model="dbSortKey" style="width: 180px">
            <el-option label="最新优先" value="created_desc" />
            <el-option label="最早优先" value="created_asc" />
            <el-option label="名称 A→Z" value="name_asc" />
            <el-option label="名称 Z→A" value="name_desc" />
            <el-option label="中心线长度 ↓" value="length_desc" />
            <el-option label="中心线长度 ↑" value="length_asc" />
          </el-select>
        </div>

        <div class="tube-list" v-if="sortedTubes.length">
          <div
            v-for="tube in sortedTubes"
            :key="tube.ID"
            class="tube-row"
            :class="{ active: selectedTubeId === tube.ID }"
            @click="selectedTubeId = tube.ID"
          >
            <div class="tube-main">
              <div class="tube-name-row">
                <div class="tube-name">{{ tube.显示名称 || tube.文件名 }}</div>
                <el-tag v-if="tube['创建时间']" size="small" type="info" effect="plain" class="tube-time-tag">
                  {{ formatSavedAt(tube['创建时间']) }}
                </el-tag>
              </div>
              <div class="tube-meta">
                <span>#{{ tube.ID }}</span>
                <span>L {{ Number(tube['中心线长度(mm)'] || 0).toFixed(1) }} mm</span>
                <span>{{ tube.截面数 || 0 }} 截面</span>
                <span v-if="tube.算法">算法 {{ tube.算法 }}</span>
                <span v-if="tube['文件类型']">{{ String(tube['文件类型']).toUpperCase() }}</span>
              </div>
            </div>
            <div class="tube-actions" @click.stop>
              <el-button
                size="small"
                type="primary"
                :loading="loadingTubeId === tube.ID"
                @click="loadTubeIntoOptimization(tube)"
              >
                加载
              </el-button>
              <el-button
                size="small"
                type="danger"
                plain
                :disabled="deletingTubeId === tube.ID"
                @click="deleteTube(tube)"
              >
                删除
              </el-button>
            </div>
          </div>
        </div>
        <el-empty v-else description="数据库中暂无可用记录" :image-size="90" />
      </el-card>

      <el-alert
        v-if="sessionId"
        type="success"
        :closable="false"
        style="margin-bottom: 12px"
        :title="loadedAlertTitle"
      />
      <el-alert
        v-else
        title="请先从数据库加载一条已分析记录"
        type="warning"
        :closable="false"
        style="margin-bottom: 12px"
      />

      <Tab3Optimization v-if="sessionId" :session-id="sessionId" />
      <el-empty v-else description="加载后即可开始工艺优化" :image-size="120" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import Tab3Optimization from './Tab3Optimization.vue'
import { ppApi } from '@/api/pp'

const sessionId = ref('')
const dbSearch = ref('')
const dbSortKey = ref('created_desc')
const dbTubes = ref([])
const loadingTubeId = ref(null)
const deletingTubeId = ref(null)
const selectedTubeId = ref(null)
const loadedTube = ref(null)

function formatSavedAt(value) {
  if (!value) return ''
  return String(value).replace('T', ' ').slice(0, 19)
}

const loadedAlertTitle = computed(() => {
  const name = loadedTube.value?.显示名称 || loadedTube.value?.文件名
  const when = formatSavedAt(loadedTube.value?.['创建时间'])
  if (name && when) return `已加载：${name}（保存于 ${when}）`
  if (name) return `已加载：${name}`
  return `已加载会话：${sessionId.value}`
})

const sortedTubes = computed(() => {
  const arr = [...dbTubes.value]
  const k = dbSortKey.value
  const num = (v) => Number(v || 0)
  const name = (t) => String(t.显示名称 || t.文件名 || '')
  const time = (t) => String(t.创建时间 || '')
  switch (k) {
    case 'created_asc': arr.sort((a, b) => time(a).localeCompare(time(b))); break
    case 'name_asc': arr.sort((a, b) => name(a).localeCompare(name(b))); break
    case 'name_desc': arr.sort((a, b) => name(b).localeCompare(name(a))); break
    case 'length_desc': arr.sort((a, b) => num(b['中心线长度(mm)']) - num(a['中心线长度(mm)'])); break
    case 'length_asc': arr.sort((a, b) => num(a['中心线长度(mm)']) - num(b['中心线长度(mm)'])); break
    default: arr.sort((a, b) => time(b).localeCompare(time(a)))
  }
  return arr
})

onMounted(async () => {
  sessionId.value = sessionStorage.getItem('pp_session_id') || ''
  await loadTubes()
  restoreLoadedTubeFromSession()
})

function restoreLoadedTubeFromSession() {
  const match = /^db_(\d+)$/.exec(sessionId.value || '')
  if (!match) return
  const id = Number(match[1])
  const tube = dbTubes.value.find((t) => Number(t.ID) === id)
  if (!tube) return
  loadedTube.value = tube
  selectedTubeId.value = tube.ID
}

async function loadTubes() {
  try {
    const res = await ppApi.listTubes(dbSearch.value, 'pp')
    if (res.data.success) dbTubes.value = res.data.tubes || []
  } catch (e) {
    ElMessage.error('读取数据库列表失败')
  }
}

async function loadTubeIntoOptimization(tube) {
  const tubeId = tube?.ID
  if (!tubeId) return
  loadingTubeId.value = tubeId
  try {
    const res = await ppApi.loadTube(tubeId)
    if (!res.data.success) {
      ElMessage.error(res.data.message || '加载失败')
      return
    }
    sessionId.value = res.data.session_id
    sessionStorage.setItem('pp_session_id', sessionId.value)
    loadedTube.value = tube
    selectedTubeId.value = tubeId
    ElMessage.success(`已加载：${res.data.display_name || res.data.file_name || tubeId}（${formatSavedAt(tube['创建时间']) || '无保存时间'}）`)
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '加载失败')
  } finally {
    loadingTubeId.value = null
  }
}

async function deleteTube(tube) {
  const tubeId = tube?.ID
  if (!tubeId) return
  const name = tube.显示名称 || tube.文件名 || `#${tubeId}`
  const when = formatSavedAt(tube['创建时间'])
  try {
    await ElMessageBox.confirm(
      `确定删除「${name}」${when ? `（保存于 ${when}）` : ''}？此操作不可撤销。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  deletingTubeId.value = tubeId
  try {
    const res = await ppApi.deleteTubes([tubeId])
    if (!res.data?.success) {
      ElMessage.error(res.data?.message || '删除失败')
      return
    }
    ElMessage.success('已删除')
    if (selectedTubeId.value === tubeId) selectedTubeId.value = null
    if (sessionId.value === `db_${tubeId}` || loadedTube.value?.ID === tubeId) {
      sessionId.value = ''
      loadedTube.value = null
      sessionStorage.removeItem('pp_session_id')
    }
    await loadTubes()
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '删除失败')
  } finally {
    deletingTubeId.value = null
  }
}
</script>

<style scoped>
.page-content {
  height: 100%;
  overflow: auto;
  box-sizing: border-box;
  padding: 20px;
}
.page-card {
  min-height: 100%;
  box-sizing: border-box;
  padding: 20px;
}
.page-title {
  margin: 0 0 20px 0;
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}
.loader-card {
  margin-bottom: 16px;
}
.loader-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.loader-toolbar {
  display: grid;
  grid-template-columns: 1fr 180px;
  gap: 8px;
  margin-bottom: 10px;
}
.tube-list {
  max-height: 260px;
  overflow-y: auto;
  border: 1px solid #ebeef5;
  border-radius: 6px;
}
.tube-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid #f2f3f5;
  cursor: pointer;
}
.tube-row:last-child {
  border-bottom: none;
}
.tube-row.active {
  background: rgba(64, 158, 255, 0.08);
}
.tube-main {
  min-width: 0;
}
.tube-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.tube-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tube-time-tag {
  flex-shrink: 0;
}
.tube-meta {
  margin-top: 3px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 12px;
  color: #909399;
}
.tube-actions {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
}
</style>
