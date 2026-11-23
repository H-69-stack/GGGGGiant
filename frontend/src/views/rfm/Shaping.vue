<template>
  <div class="rfm-page">
    <el-card class="page-card">
      <template #header><span>RFM - 成型</span></template>
      <RfmShell operation="shaping" />
    </el-card>
  </div>
</template>

<script setup>
import RfmShell from '@/components/rfm/RfmShell.vue'
</script>

<style scoped>
.rfm-page { padding: 16px; }
.page-card { max-width: 1000px; margin: 0 auto; }
</style>
