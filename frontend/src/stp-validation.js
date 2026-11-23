// Local verification entry point. The production build uses index.html.
import { createApp, h } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import Tab1ModelLoader from './views/pp/Tab1ModelLoader.vue'

createApp({ render: () => h('main', { style: 'max-width:1400px;margin:auto;padding:24px;font-family:Microsoft YaHei,sans-serif' }, [
  h('h1', { style: 'font-size:24px;margin:0 0 8px' }, 'STP 原生截面 · 软件运行验证'),
  h('p', { style: 'color:#64748b;margin-bottom:24px' }, '原软件导入组件 + 原 PP API · 独立测试数据库 · 红色为实测中心线'),
  h(Tab1ModelLoader),
]) }).use(ElementPlus).mount('#app')
