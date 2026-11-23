// Actual production pages; only the surrounding navigation and database are isolated.
import { createApp, ref, h } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import LoadData from './views/rfm/LoadData.vue'
import Belling from './views/rfm/Belling.vue'
import Flattening from './views/rfm/Flattening.vue'
import Bending from './views/rfm/Bending.vue'
import Shaping from './views/rfm/Shaping.vue'
const pages = { '加载数据': LoadData, '扩口': Belling, '压扁': Flattening, '压弯': Bending, '成型': Shaping }
createApp({ setup() {
  const selected = ref('加载数据')
  return () => h('main', { style: 'font-family:Arial,sans-serif;max-width:1100px;margin:auto;background:#f3f6fa' }, [
    h('header', { style: 'padding:16px;background:#153354;color:white' }, [
      h('h2', 'GIANT 管件工艺 · 硬模功能验证'),
      h('p', '生产页面与生产接口 / 隔离测试数据库 / 单位 mm'),
      ...Object.keys(pages).map(name => h('button', { onClick: () => selected.value = name,
        style: 'padding:8px 20px;margin:4px;cursor:pointer' }, name)),
    ]), h(pages[selected.value], { key: selected.value }),
  ])
} }).use(createPinia()).use(ElementPlus).mount('#app')
