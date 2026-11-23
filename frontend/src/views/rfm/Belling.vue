<template>
  <div class="rfm-page">
    <el-card class="page-card">
      <template #header><span>RFM - 扩口</span></template>
      <RfmShell operation="belling">
        <template #default="{ config }">
          <el-form :model="config" label-width="130px">
            <el-form-item label="扩口段长度 (mm)">
              <el-input-number v-model="config.belling_length" :min="0.01" :max="rfmStore.centerlineLength || 100000" :step="5" :precision="3" />
            </el-form-item>
            <el-form-item label="芯轴延长余量(mm)">
              <el-input-number v-model="config.length_allowance" :min="0" :max="100" :step="1" :precision="1" />
            </el-form-item>
            <el-form-item label="芯轴壁厚来源">
              <el-radio-group v-model="config.wall_thickness_source">
                <el-radio-button value="data">沿用前序数据表</el-radio-button>
                <el-radio-button value="constant">指定统一壁厚</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item v-if="config.wall_thickness_source === 'constant'" label="壁厚 (mm)">
              <el-input-number v-model="config.wall_thickness" :min="0.5" :max="20" :step="0.1" :precision="2" />
            </el-form-item>
            <p class="form-hint">芯轴取大端 s=0 至扩口段长度的型线，截面为正圆：芯轴直径 = 外径周长 ÷ π − 2×壁厚。轴向展开为直线；芯轴长度 = 扩口段长度 + 延长余量（默认 0）。</p>
            <p class="form-hint">外径型腔沿用完整管件型线。开启反切后，另生成外部模具，芯轴和型腔仍可分别查看、下载。</p>
          </el-form>
        </template>
      </RfmShell>
    </el-card>
  </div>
</template>

<script setup>
import RfmShell from '@/components/rfm/RfmShell.vue'
import { useRfmStore } from '@/stores/rfm'
const rfmStore = useRfmStore()
</script>

<style scoped>
.rfm-page { padding: 16px; }
.page-card { max-width: 1000px; margin: 0 auto; }
.form-hint { margin-left: 8px; color: #909399; font-size: 12px; }
</style>
