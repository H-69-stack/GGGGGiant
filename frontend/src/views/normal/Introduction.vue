<template>
  <div class="page-card">
    <h2 class="page-title">普通抽 - 模块介绍</h2>

    <p>普通抽涉及不同机床和模具，其操作流程主要包括四部分：</p>

    <el-steps direction="vertical" :active="4" style="margin: 24px 0">
      <el-step title="导入管件的DXF文件" description="将标准DXF文件上传到系统中" />
      <el-step title="计算模具参数" description="选择机床类型和模具，自动计算参数" />
      <el-step title="查看与修改参数" description="查看计算结果并可手动修改" />
      <el-step title="保存与导出" description="保存到模具数据库，导出DXF和Excel" />
    </el-steps>

    <el-divider />

    <h3 class="section-title">管件参数说明</h3>
    <el-table :data="paramTable" border stripe size="small">
      <el-table-column prop="two" label="两段不同壁厚" width="160" />
      <el-table-column prop="three" label="三段不同壁厚" width="160" />
      <el-table-column prop="desc" label="描述" />
    </el-table>

    <el-divider />

    <h3 class="section-title">支持的机床</h3>
    <el-tag style="margin-right: 8px">DC0124</el-tag>
    <el-tag style="margin-right: 8px">DC0121</el-tag>
    <el-tag>DC0125</el-tag>
  </div>
</template>

<script setup>
const paramTable = [
  { two: '车种规格', three: '车种规格', desc: '当前管件所属车种规格' },
  { two: 'D', three: 'D', desc: '抽管外径' },
  { two: 'L', three: 'L', desc: '抽管总长' },
  { two: 'L1', three: 'L1', desc: '壁厚从厚开始，第一段长度' },
  { two: 'T1', three: 'T1', desc: '第一段壁厚' },
  { two: 'M1', three: 'M1', desc: '第一段过渡段长度' },
  { two: 'L2', three: 'L2', desc: '壁厚从厚开始，第二段长度' },
  { two: '', three: 'T2', desc: '第二段壁厚' },
  { two: '', three: 'M2', desc: '第二段过渡段长度' },
  { two: '', three: 'L3', desc: '壁厚从厚开始，第三段长度' },
  { two: '', three: 'T3', desc: '第三段壁厚' },
]
</script>
