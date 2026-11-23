<template>
  <div class="params-table">
    <el-table :data="modelValue" border size="small" :max-height="maxHeight" style="width: 100%;">
      <el-table-column prop="参数" label="参数" :width="paramWidth" />
      <el-table-column prop="值" label="值" :min-width="120">
        <template #default="{ row }">
          <el-input
            v-if="editable"
            v-model="row['值']"
            size="small"
            @change="handleChange(row)"
          />
          <span v-else>{{ row['值'] }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="showDesc" prop="描述" label="描述" min-width="150" />
    </el-table>
  </div>
</template>

<script setup>
const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  editable: { type: Boolean, default: false },
  showDesc: { type: Boolean, default: false },
  maxHeight: { type: Number, default: 400 },
  paramWidth: { type: Number, default: 200 }
})

const emit = defineEmits(['update:modelValue', 'change'])

function handleChange(row) {
  emit('change', row)
}
</script>

<style scoped>
.params-table {
  width: 100%;
}
</style>
