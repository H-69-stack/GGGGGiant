<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <span>Taper 锥度模具设计 - 简介</span>
      </template>

      <div class="intro-content">
        <h3>功能说明</h3>
        <p>Taper锥度模具设计模块用于EC0120/EC0121机台的锥度模具参数计算。适用于异形管件的锥度成型工艺。</p>

        <h3>操作步骤</h3>
        <el-steps :active="0" direction="vertical" class="intro-steps">
          <el-step title="上传管件DXF" description="选择管件标准DXF图纸文件，系统自动解析管件尺寸参数" />
          <el-step title="选择机台和模具" description="选择EC0120或EC0121机台，选择需要计算的模具类型" />
          <el-step title="计算参数" description="系统根据管件参数自动计算锥度模具尺寸" />
          <el-step title="保存/导出" description="将计算结果保存到模具数据库或导出为DXF+Excel压缩包" />
        </el-steps>

        <h3>支持的模具类型</h3>
        <el-table :data="moldInfo" border size="small" style="max-width: 600px;">
          <el-table-column prop="name" label="模具名称" width="150" />
          <el-table-column prop="desc" label="说明" />
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script setup>
const moldInfo = [
  { name: 'B000', desc: '后定位模' },
  { name: 'C000', desc: '夹模' },
  { name: 'J000', desc: '锥度模' },
  { name: 'E000', desc: '内定位模' },
  { name: 'I000', desc: '拉杆模' },
  { name: 'H000', desc: '定位模' }
]
</script>

<style scoped>
.intro-content h3 {
  color: var(--primary-color);
  margin: 24px 0 12px 0;
}
.intro-content h3:first-child { margin-top: 0; }
.intro-content p { color: #555; line-height: 1.8; margin-bottom: 16px; }
.intro-steps { margin: 16px 0; }
</style>
