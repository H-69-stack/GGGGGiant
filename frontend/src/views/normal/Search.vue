<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <div class="card-header">
          <span>Normal 模具数据查询</span>
        </div>
      </template>

      <el-form :inline="true" :model="searchForm" class="search-form">
        <el-form-item label="机台类型">
          <el-select v-model="searchForm.machineType" placeholder="选择机台">
            <el-option label="DC0124" value="DC0124" />
            <el-option label="DC0121" value="DC0121" />
            <el-option label="DC0125" value="DC0125" />
          </el-select>
        </el-form-item>
        <el-form-item label="模具类型">
          <el-select v-model="searchForm.moldType" placeholder="选择模具">
            <el-option v-for="m in moldTypes" :key="m" :label="m" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch" :loading="loading">查询</el-button>
        </el-form-item>
      </el-form>

      <div v-if="tableData.length > 0" class="result-section">
        <el-table :data="tableData" border stripe size="small" max-height="500" style="width: 100%">
          <el-table-column
            v-for="col in columns"
            :key="col"
            :prop="col"
            :label="col"
            min-width="120"
            show-overflow-tooltip
          />
        </el-table>
        <div class="table-footer">
          共 {{ tableData.length }} 条记录
          <el-button type="success" size="small" style="margin-left: 16px;" @click="downloadCsv">
            下载CSV
          </el-button>
        </div>
      </div>

      <el-empty v-else-if="searched" description="未找到数据" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const searchForm = reactive({
  machineType: 'DC0124',
  moldType: 'AD03'
})

const moldTypes = ['ADIE', 'DIEO', 'ADBT', 'AD03', 'AD01', 'AD02', 'SS01', 'AD04', 'AD07', 'AD06_F', 'AD06_S']
const tableData = ref([])
const loading = ref(false)
const searched = ref(false)

const columns = computed(() => {
  if (tableData.value.length === 0) return []
  return Object.keys(tableData.value[0])
})

async function handleSearch() {
  loading.value = true
  searched.value = true
  try {
    const res = await axios.get('/api/normal/search', {
      params: {
        machine_type: searchForm.machineType,
        mold_type: searchForm.moldType
      }
    })
    if (res.data.success) {
      tableData.value = res.data.data || []
      if (tableData.value.length === 0) {
        ElMessage.info('未找到相关数据')
      }
    } else {
      ElMessage.error(res.data.message || '查询失败')
    }
  } catch (e) {
    ElMessage.error('查询失败')
  } finally {
    loading.value = false
  }
}

function downloadCsv() {
  if (!tableData.value.length) return
  const headers = Object.keys(tableData.value[0])
  const rows = tableData.value.map(row => headers.map(h => {
    const val = String(row[h] ?? '')
    return val.includes(',') || val.includes('"') ? `"${val.replace(/"/g, '""')}"` : val
  }))
  const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', `${searchForm.machineType}_${searchForm.moldType}_data.csv`)
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.search-form {
  margin-bottom: 20px;
}
.search-form :deep(.el-select) {
  min-width: 130px;
}
.result-section {
  margin-top: 16px;
}
.table-footer {
  margin-top: 12px;
  color: #666;
  font-size: 13px;
}
</style>
