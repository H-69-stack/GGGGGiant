<template>
  <div class="progress-bar-container" v-if="visible">
    <div class="progress-info">
      <span>{{ label }}</span>
      <span>{{ percentage }}%</span>
    </div>
    <el-progress
      :percentage="percentage"
      :status="status"
      :stroke-width="strokeWidth"
    />
    <div v-if="message" class="progress-message">{{ message }}</div>
  </div>
</template>

<script setup>
defineProps({
  visible: { type: Boolean, default: true },
  percentage: { type: Number, default: 0 },
  label: { type: String, default: '处理中...' },
  message: { type: String, default: '' },
  status: { type: String, default: '' },
  strokeWidth: { type: Number, default: 20 }
})
</script>

<style scoped>
.progress-bar-container {
  padding: 16px 0;
}
.progress-info {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 14px;
  color: #606266;
}
.progress-message {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
}
</style>
