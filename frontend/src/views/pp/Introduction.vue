<template>
  <div class="page-card">
    <h2 class="page-title">总体工艺规划 - 模块介绍</h2>
    <el-descriptions border :column="1">
      <el-descriptions-item label="功能">
        通过上传管件的STL文件，自动分析管件的壁厚、外径等参数，并使用遗传算法优化工艺参数。
      </el-descriptions-item>
      <el-descriptions-item label="支持的工艺方法">
        Taper+TP抽+扩口、普通抽+TP抽+Taper+扩口、加长Taper+TP抽+扩口、仅TP抽、普通抽+TP抽
      </el-descriptions-item>
      <el-descriptions-item label="输入">STL格式的管件3D模型文件</el-descriptions-item>
      <el-descriptions-item label="输出">优化后的工艺参数方案（可传递到模具设计模块）</el-descriptions-item>
    </el-descriptions>
  </div>
</template>
