<template>
  <div>
    <div class="page-card">
      <h2 class="page-title">总体工艺规划</h2>

      <el-tabs v-model="activeTab" type="border-card">
        <el-tab-pane label="上传模型文件" name="tab1">
          <Tab1ModelLoader @ready="handleTab1Ready" />
        </el-tab-pane>
        <el-tab-pane label="手工输入参数" name="tab2" :disabled="!tab2Enabled">
          <Tab2ManualInput
            v-if="tab2Enabled"
            :session-id="sessionId"
            :model-length="modelLength"
            @ready="tab3Enabled = true"
          />
        </el-tab-pane>
        <el-tab-pane label="工艺参数优化" name="tab3" :disabled="!tab3Enabled">
          <Tab3Optimization v-if="tab3Enabled" :session-id="sessionId" />
          <el-empty v-else description="请先在「手工输入参数」中确认参数" :image-size="120" />
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import Tab1ModelLoader from './Tab1ModelLoader.vue'
import Tab2ManualInput from './Tab2ManualInput.vue'
import Tab3Optimization from './Tab3Optimization.vue'

const activeTab = ref('tab1')
const tab2Enabled = ref(false)
const tab3Enabled = ref(false)
const sessionId = ref('')
const modelLength = ref(0)

function handleTab1Ready(payload) {
  tab2Enabled.value = true
  sessionId.value = payload?.sessionId || ''
  modelLength.value = payload?.modelLength || 0
}
</script>

<style scoped>
.placeholder {
  padding: 40px;
  text-align: center;
  color: #999;
  font-size: 14px;
}
</style>
