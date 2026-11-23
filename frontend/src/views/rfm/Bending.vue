<template>
  <div class="rfm-page">
    <el-card class="page-card">
      <template #header><span>RFM - 压弯</span></template>
      <RfmShell operation="bending">
        <template #default="{ config }">
          <el-form :model="config" label-width="160px">
            <el-form-item v-if="config.flattening_executed" label="截面数量（轴向）">
              <el-input-number v-model="config.num_sections" :min="20" :max="500" :step="10" :precision="0" />
            </el-form-item>

            <el-form-item label="先执行压扁工序">
              <el-switch v-model="config.flattening_executed" />
            </el-form-item>
            <template v-if="config.flattening_executed">
              <el-form-item label="压扁段长度 (mm)"><el-input-number v-model="config.flattening_length" :min="0.01" :precision="2" /></el-form-item>
              <el-form-item label="过渡段长度 (mm)"><el-input-number v-model="config.transition_length" :min="0" :precision="2" /></el-form-item>
              <el-form-item label="压扁余量 (mm)"><el-input-number v-model="config.flattening_allowance" :min="0" :precision="2" /></el-form-item>
              <EllipseOptions :config="config" />
              <p class="form-hint">参数须与前一道压扁工序相同，压弯只改变中心线。</p>
            </template>
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
