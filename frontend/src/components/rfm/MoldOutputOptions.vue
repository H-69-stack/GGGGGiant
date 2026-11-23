<template>
  <el-divider />
  <el-form label-width="170px">
    <el-form-item label="生成反切实体模具">
      <el-switch v-model="config.mold_enabled" />
      <span class="hint">关闭保留原模型；开启后用外径腔体反切长方体。</span>
    </el-form-item>
    <template v-if="config.mold_enabled">
      <el-form-item label="自动推荐外形尺寸">
        <el-switch v-model="config.mold_auto_size" />
      </el-form-item>
      <el-form-item label="单侧余量 (mm)">
        <el-input-number v-model="config.mold_margin" :min="1" :max="500" :precision="1" />
      </el-form-item>
      <template v-if="!config.mold_auto_size">
        <el-form-item v-for="axis in ['x', 'y', 'z']" :key="axis" :label="`模体 ${axis.toUpperCase()} 尺寸 (mm)`">
          <el-input-number v-model="config['mold_size_' + axis]" :min="1" :max="100000" :precision="1" />
        </el-form-item>
      </template>
      <p class="hint">模体居中包围腔体，尺寸沿模型 XYZ 轴。先用自动尺寸生成后可修改推荐值。当前输出为带封闭型腔的整体模坯，尚未分模。</p>
    </template>
  </el-form>
</template>
<script setup>
defineProps({ config: { type: Object, required: true } })
</script>
<style scoped>.hint { color: #606266; margin-left: 12px; font-size: 13px; }</style>
