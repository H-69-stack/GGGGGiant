// Isolated fixture page using the actual production Belling.vue and API client.
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const {createServer, mergeConfig} = require('vite');
const {default: config} = await import('../../frontend/vite.config.js');
const root = fileURLToPath(new URL('../../frontend/', import.meta.url));
const dir = path.join(root,'.belling-validation');
await fs.mkdir(dir,{recursive:true});
await fs.writeFile(path.join(dir,'index.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>扩口芯轴配套验证</title><style>body{margin:0;background:#f4f7fb;font-family:'Microsoft YaHei',sans-serif}.validation-title{margin:20px auto 0;max-width:1000px;color:#29455e;font-size:14px}</style><div class="validation-title">24TK_1324-1 · 扩口配套模型验证 · 正式扩口组件 / 独立测试数据库</div><div id="app"></div><script type="module" src="./entry.js"></script></html>`);
await fs.writeFile(path.join(dir,'entry.js'),`import {createApp,h} from 'vue';
import {createPinia} from 'pinia';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
import Belling from '@/views/rfm/Belling.vue';
import {useRfmStore} from '@/stores/rfm';
const app=createApp({render:()=>h(Belling)});const pinia=createPinia();app.use(pinia);app.use(ElementPlus);
const fixture=await(await fetch('/api/validation/belling-fixture')).json();
const store=useRfmStore(pinia);await store.loadDesign(fixture.design_id);
store.processConfigs.belling={belling_length:80.5,length_allowance:0,wall_thickness_source:'data',mold_enabled:true,mold_auto_size:true,mold_margin:15};
app.mount('#app');`);
const server = await createServer(mergeConfig(config,{configFile:false,server:{port:3003,strictPort:true,
  host:'127.0.0.1',proxy:{'/api':{target:'http://127.0.0.1:5056'}}}}));
await server.listen();
console.log('http://127.0.0.1:3003/.belling-validation/index.html');
