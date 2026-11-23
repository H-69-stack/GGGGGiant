<template>
  <div class="page-card">
    <h2 class="page-title">硬模成型 - 模块介绍</h2>

    <p>硬模成型（RFM）包含四个操作工序，使用前需先加载分析数据。</p>

    <el-steps direction="vertical" :active="4" style="margin: 24px 0">
      <el-step title="加载数据" description="从数据库加载管件的中心线长度-外径周长数据" />
      <el-step title="选择工序" description="扩口 / 压扁 / 压弯 / 成型" />
      <el-step title="配置参数并执行处理" description="设置工序相关参数后执行处理" />
      <el-step title="生成模型并保存" description="生成结果STL模型或保存到数据库" />
    </el-steps>

    <el-divider />

    <h3 class="section-title">操作工序说明</h3>
    <el-table :data="opTable" border stripe size="small">
      <el-table-column prop="name" label="工序" width="100" />
      <el-table-column prop="desc" label="说明" />
    </el-table>
  </div>
</template>

<script setup>
const opTable = [
  { name: '扩口', desc: '对管件端口进行扩口处理' },
  { name: '压扁', desc: '对管件进行压扁处理' },
  { name: '压弯', desc: '对管件进行弯曲变形处理' },
  { name: '成型', desc: '对管件进行整体成型处理' },
]
</script>
