import { createApp, ref, h } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import MergeReview from './views/dev/MergeReview.vue'
import LoadData from './views/rfm/LoadData.vue'
import Shaping from './views/rfm/Shaping.vue'
import TP from './views/tp/Calculate.vue'
import ModelUpload from './views/upload/ModelUpload.vue'
const pages = { '案例模型对照': MergeReview, '读取页面': ModelUpload, '加载工艺': LoadData, '成型页面': Shaping, 'TP 页面': TP }
createApp({ setup() {
  const selected = ref('案例模型对照')
  return () => h('main', { style: 'font-family:Arial,sans-serif;background:#f3f6fa;min-height:100vh' }, [
    h('nav', { style: 'padding:10px 22px;background:#173451;color:white;display:flex;align-items:center;gap:10px' }, [
      h('strong', 'GIANT · 合并版本验证'),
      ...Object.keys(pages).map(name => h('button', { onClick: () => selected.value = name,
        style: 'padding:6px 14px;border:0;border-radius:4px;cursor:pointer;background:'+(selected.value===name?'#e1efff':'white') }, name)),
    ]), h(pages[selected.value], { key: selected.value }),
  ])
} }).use(createPinia()).use(ElementPlus).mount('#app')
