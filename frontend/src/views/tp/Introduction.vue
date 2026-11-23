<template>
  <div class="page-container">
    <el-card class="page-card">
      <template #header>
        <span>TP 抽管模具设计 - 简介</span>
      </template>

      <div class="intro-content">
        <h3>功能说明</h3>
        <p>TP抽管模具设计模块用于DC0128机台的模具参数计算。通过上传管件DXF图纸，系统自动计算各模具的尺寸参数，并生成对应的DXF工程图。</p>

        <h3>操作步骤</h3>
        <el-steps :active="0" direction="vertical" class="intro-steps">
          <el-step title="上传管件DXF" description="选择管件标准DXF图纸文件，系统自动解析管件尺寸参数" />
          <el-step title="选择模具" description="选择需要计算的模具类型（ADIE、ADIE_、ADBT、ADBT_、AD01、AD04）" />
          <el-step title="计算参数" description="系统根据管件参数和公式自动计算各模具尺寸" />
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
  { name: 'ADIE', desc: '成型模（正）' },
  { name: 'ADIE_', desc: '成型模（反）' },
  { name: 'ADBT', desc: '成型芯棒（正）' },
  { name: 'ADBT_', desc: '成型芯棒（反）' },
  { name: 'AD01', desc: '裁剪夹模1/2（共用图号前缀，序号递增）' },
  { name: 'AD04', desc: '切断夹模' }
]
</script>

<style scoped>
.intro-content h3 {
  color: var(--primary-color);
  margin: 24px 0 12px 0;
}
.intro-content h3:first-child {
  margin-top: 0;
}
.intro-content p {
  color: #555;
  line-height: 1.8;
  margin-bottom: 16px;
}
.intro-steps {
  margin: 16px 0;
}
</style>
