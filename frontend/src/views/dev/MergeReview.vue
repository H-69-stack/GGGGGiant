<template>
  <main class="review">
    <header>
      <div><h1>{{ active?.case || '加载案例…' }}</h1><p>GitHub 最新版 + 原生 STP 读取 / CAD 成型 / 反切 · 单位 mm</p></div>
      <select v-model="selected" aria-label="测试案例"><option v-for="item in records" :key="item.case" :value="item.case">{{ item.case }}</option></select>
    </header>
    <div class="toolbar"><button v-for="mode in ['成型对照', 'TP 套模', '原始 STP']" :key="mode" :class="{on: tab === mode}" @click="tab = mode">{{ mode }}</button></div>
    <template v-if="active">
      <div class="metrics">
        <span>有效中心线 <b>{{ num(active.measurements.length_mm) }}</b></span>
        <span>外周长 <b>{{ num(active.measurements.perimeter_min_mm) }} ～ {{ num(active.measurements.perimeter_max_mm) }}</b></span>
        <span>壁厚中位数 <b>{{ num(active.measurements.wall_median_mm, 3) }}</b></span>
        <span>截面 <b>{{ active.measurements.stations }}</b></span>
      </div>
      <div v-if="tab === '成型对照'" class="pair">
        <article v-for="[kind,title] in [['forming','关闭反切 · 外径实心腔体'],['mold','开启反切 · 长方体模坯剖视']]" :key="kind">
          <h2>{{ title }}</h2>
          <StlViewer v-if="passed(kind)" :key="selected+kind" :url="url(kind)" :cutaway="kind === 'mold'" :height="360"/>
          <div v-else class="blocked">{{ error(kind) }}</div>
          <p v-if="passed(kind)">CAD 实体有效 · STEP 导出并重读通过</p>
          <p v-if="kind === 'mold' && passed(kind)">XYZ：{{ active.operations.mold.mold_info.size_xyz_mm.join(' × ') }}；最小单侧余量 {{ num(active.operations.mold.mold_info.minimum_margin_mm) }}。</p>
        </article>
      </div>
      <template v-else-if="tab === 'TP 套模'">
        <div class="tp-params">大端内径 T_D：<b>{{ num(active.tp_inputs.T_D_mm, 3) }}</b>　小端内径 D：<b>{{ num(active.tp_inputs.D_mm, 3) }}</b>　型线段长 T_L：<b>{{ num(active.tp_inputs.T_L_mm, 3) }}</b></div>
        <div v-if="passed('tp')" class="pair">
          <article><h2>TP 套模 · 完整旋转实体</h2><StlViewer :key="selected+'tp'" :url="url('tp')" :height="330"/></article>
          <article><h2>TP 套模 · 剖开查看内部型线</h2><StlViewer :key="selected+'tp-cut'" :url="url('tp')" :cutaway="true" :height="330"/></article>
        </div>
        <div v-else class="blocked">{{ error('tp') }}</div>
        <p class="note">TP 几何回归参数取自本管件测量周长的极值站位；尚非工程师确认的生产工艺。主体外径 95、法兰外径 125；预览与 STEP 来源相同。</p>
      </template>
      <article v-else><h2>导入的 {{ active.source.file }}</h2><StlViewer :key="selected+'source'" :url="url('source')" :height="340"/>
        <p v-for="warning in active.quality.warnings" :key="warning">{{ warning }}</p>
      </article>
      <footer>实际接口生成结果 · 使用生产三维组件显示 · 剖视仅影响显示，导出文件保留完整实体。整体模坯尚未分模。</footer>
    </template>
  </main>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import StlViewer from '@/components/StlViewer.vue'
const records = ref([]), selected = ref(''), tab = ref('成型对照')
const active = computed(() => records.value.find(r => r.case === selected.value))
const num = (v, n=2) => Number(v).toFixed(n)
const passed = kind => active.value?.operations[kind]?.status === 'passed'
const error = kind => '已拦截：'+(active.value?.operations[kind]?.error || '尚未完成此工序')
const url = kind => '/validation/models/'+encodeURIComponent(selected.value+'_'+kind+'.stl')
onMounted(async () => { records.value = await (await fetch('/validation/manifest')).json(); selected.value = records.value[0]?.case || '' })
</script>
<style scoped>
.review{max-width:1180px;margin:auto;padding:16px 24px;color:#18344e}
header{display:flex;align-items:center;justify-content:space-between}h1{margin:0;font-size:26px}header p{margin:7px 0;color:#526c83;font-size:13px}
select{padding:9px 16px;border:1px solid #cad8e6;border-radius:6px;color:#18344e}
.toolbar{display:flex;gap:8px;margin:12px 0}.toolbar button{padding:8px 20px;background:white;border:1px solid #becfdf;border-radius:5px;cursor:pointer}.toolbar .on{background:#174f87;color:white}
.metrics{display:flex;justify-content:space-between;background:#e5edf5;padding:13px 16px;border-radius:6px;font-size:14px;margin-bottom:14px}.metrics b{margin-left:6px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}article{background:white;border:1px solid #d5e0ec;border-radius:8px;padding:14px;min-width:0}h2{font-size:17px;margin:0 0 12px}article p{margin:9px 0 0;font-size:12px;color:#45627c}.blocked{padding:35px 20px;background:#fff0e6;color:#9c3b13;border-radius:7px;line-height:1.8}
.tp-params{padding:0 0 12px;font-size:14px}.note,footer{font-size:12px;line-height:1.6;color:#587087}footer{margin-top:13px}
</style>
