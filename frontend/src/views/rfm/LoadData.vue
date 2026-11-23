<template>
  <div class="page-content">
    <div class="page-card">
      <h2 class="page-title">硬模成型 - 加载数据</h2>
      <el-alert
        v-if="rfmStore.hasSession"
        type="success"
        :closable="false"
        style="margin-bottom: 12px"
        :title="`已加载：${rfmStore.displayName}  |  ${rfmStore.vehicleSpec}  |  ${rfmStore.ppMethod}  |  中心线 ${rfmStore.centerlineLength?.toFixed(1)} mm`"
      />
      <el-alert
        v-else
        title="请先从工艺设计数据库加载"
        type="warning"
        :closable="false"
        style="margin-bottom: 12px"
      />

      <el-card class="loader-card" shadow="never">
        <template #header>
          <div class="loader-head">
            <span>工艺设计数据库</span>
            <el-button size="small" @click="loadDesigns">刷新</el-button>
          </div>
        </template>

        <div class="loader-toolbar">
          <el-input
            v-model="search"
            placeholder="搜索 工艺名称 / 车种规格 / 管件号"
            clearable
            @input="loadDesigns"
          />
          <el-select v-model="sortKey" style="width: 160px">
            <el-option label="最新优先" value="created_desc" />
            <el-option label="最早优先" value="created_asc" />
            <el-option label="名称 A→Z" value="name_asc" />
            <el-option label="名称 Z→A" value="name_desc" />
          </el-select>
        </div>

        <div class="tube-list" v-if="sortedDesigns.length">
          <div
            v-for="d in sortedDesigns"
            :key="d.id"
            class="tube-row"
            :class="{ active: selectedId === d.id }"
            @click="selectedId = d.id"
          >
            <div class="tube-main">
              <div class="tube-name">{{ d.display_name || d.tube_number || `ID ${d.id}` }}</div>
              <div class="tube-meta">
                <span>{{ d.pp_method || '未知方式' }}</span>
                <span>{{ d.vehicle_spec || '未知车种' }}</span>
                <span>{{ d.tube_number ? `管件#${d.tube_number}` : '' }}</span>
              </div>
            </div>
            <el-button
              size="small"
              type="primary"
              :loading="loadingId === d.id"
              @click.stop="doLoad(d)"
            >
              加载
            </el-button>
          </div>
        </div>
        <el-empty v-else description="工艺设计数据库中暂无记录" :image-size="90" />
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRfmStore } from '@/stores/rfm'
import { ppApi } from '@/api/pp'

const rfmStore = useRfmStore()
const search = ref('')
const sortKey = ref('created_desc')
const designs = ref([])
const loadingId = ref(null)
const selectedId = ref(null)

const sortedDesigns = computed(() => {
  const arr = [...designs.value]
  const k = sortKey.value
  const name = (d) => String(d.display_name || d.tube_number || d.id || '')
  const time = (d) => String(d.created_at || '')
  switch (k) {
    case 'created_asc': arr.sort((a, b) => time(a).localeCompare(time(b))); break
    case 'name_asc': arr.sort((a, b) => name(a).localeCompare(name(b))); break
    case 'name_desc': arr.sort((a, b) => name(b).localeCompare(name(a))); break
    default: arr.sort((a, b) => time(b).localeCompare(time(a)))
  }
  return arr
})

onMounted(() => { loadDesigns() })

async function loadDesigns() {
  try {
    const res = await ppApi.listDesigns()
    if (res.data.success) {
      let rows = res.data.designs || []
      // Client-side search filter
      if (search.value.trim()) {
        const q = search.value.toLowerCase()
        rows = rows.filter(d =>
          (d.display_name || '').toLowerCase().includes(q) ||
          (d.vehicle_spec || '').toLowerCase().includes(q) ||
          (d.tube_number || '').toLowerCase().includes(q) ||
          (d.pp_method || '').toLowerCase().includes(q)
        )
      }
      designs.value = rows
    }
  } catch {
    ElMessage.error('读取工艺设计列表失败')
  }
}

async function doLoad(design) {
  loadingId.value = design.id
  try {
    await rfmStore.loadDesign(design.id)
    ElMessage.success(`已加载：${rfmStore.displayName}`)
  } catch (e) {
    ElMessage.error(e.message || e.response?.data?.message || '加载失败')
  } finally {
    loadingId.value = null
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
.loader-card { margin-bottom: 16px; }
.loader-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.loader-toolbar {
  display: grid;
  grid-template-columns: 1fr 160px;
  gap: 8px;
  margin-bottom: 10px;
}
.tube-list {
  max-height: 420px;
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
.tube-row:last-child { border-bottom: none; }
.tube-row.active { background: rgba(64, 158, 255, 0.08); }
.tube-main { min-width: 0; }
.tube-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tube-meta {
  margin-top: 3px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 12px;
  color: #909399;
}
</style>
