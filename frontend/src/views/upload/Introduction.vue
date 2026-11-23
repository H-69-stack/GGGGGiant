<template>
  <div class="page-content">
    <div class="page-card">
    <h2 class="page-title">上传分析 - 模块介绍</h2>
    <el-descriptions border :column="1">
      <el-descriptions-item label="功能">
        上传管件的STL/STP文件进行自动分析，提取中心线长度、外径周长、壁厚等关键几何参数，
        形成完整的管件数据表。支持手工输入或文件上传壁厚形线数据，汇总后传递给工艺优化模块。
      </el-descriptions-item>
      <el-descriptions-item label="分析算法">
        A算法（PCA主轴对齐 + 中心线法向截面）或 B算法（Z向切片质心中心线）
      </el-descriptions-item>
      <el-descriptions-item label="手工输入">
        支持数据表编辑（TL1-TL4段长度 + T1-T4壁厚）或CSV/XLSX文件上传壁厚形线数据
      </el-descriptions-item>
      <el-descriptions-item label="输入">STL/STP格式的管件3D模型文件，或CSV/XLSX壁厚数据文件</el-descriptions-item>
      <el-descriptions-item label="输出">中心线长度-外径周长-壁厚信息数据表（可传递到工艺优化模块）</el-descriptions-item>
    </el-descriptions>
    </div>
  </div>
</template>

<style scoped>
.page-content {
  padding: 20px;
}
.page-card {
  background: #fff;
  border-radius: 8px;
  padding: 20px;
}
.page-title {
  margin: 0 0 16px 0;
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}
</style>
