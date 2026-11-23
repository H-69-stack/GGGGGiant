<template>
  <div class="rfm-page">
    <el-card class="page-card">
      <template #header><span>RFM - 压扁</span></template>
      <RfmShell operation="flattening">
        <template #default="{ config }">
          <el-form :model="config" label-width="150px">
            <el-form-item label="压扁长度 (mm)">
              <el-input-number v-model="config.flattening_length" :min="10" :max="2000" :step="5" :precision="1" />
            </el-form-item>
            <el-form-item label="整管长度 (mm)">
              <el-input-number
                v-model="config.total_length"
                :min="0"
                :max="10000"
                :step="10"
                :precision="1"
                placeholder="不填则按原始数据"
              />
              <span class="form-hint">不填时使用数据范围；不能超出有效测量长度</span>
            </el-form-item>
            <el-form-item label="压扁余量 (mm)">
              <el-input-number v-model="config.flattening_allowance" :min="0" :max="50" :step="0.1" :precision="2" />
            </el-form-item>
            <el-form-item label="过渡段长度 (mm)">
              <el-input-number v-model="config.transition_length" :min="0" :max="200" :step="1" :precision="1" />
            </el-form-item>
            <el-form-item label="截面数量（轴向）">
              <el-input-number v-model="config.num_sections" :min="50" :max="500" :step="10" :precision="0" />
            </el-form-item>

            <EllipseOptions :config="config" />
          </el-form>
        </template>
      </RfmShell>
    </el-card>
  </div>
</template>

<script setup>
import RfmShell from '@/components/rfm/RfmShell.vue'
import EllipseOptions from '@/components/rfm/EllipseOptions.vue'

</script>

<style scoped>
.rfm-page { padding: 16px; }
.page-card { max-width: 1000px; margin: 0 auto; }
.form-hint { margin-left: 8px; color: #909399; font-size: 12px; }
</style>
