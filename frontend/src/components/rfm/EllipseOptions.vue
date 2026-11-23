<template>
  <el-form-item label="椭圆 X 全轴长 (mm)">
    <el-input-number v-model="config.ellipse_x" :min="0.01" :max="1000" :precision="3" placeholder="优先读取前序数据" />
  </el-form-item>
  <el-form-item label="椭圆 Y 全轴长 (mm)">
    <el-input-number v-model="config.ellipse_y" :min="0.01" :max="1000" :precision="3" placeholder="优先读取前序数据" />
  </el-form-item>
  <p class="hint">轴长是完整直径。两项留空时使用前序逐截面的 X/Y 轴长；填写两项时用于整个压扁段。程序会校验椭圆周长与前序周长是否一致。</p>
</template>
<script setup>defineProps({ config: { type: Object, required: true } })</script>
<style scoped>.hint { color: #606266; font-size: 13px; }</style>
