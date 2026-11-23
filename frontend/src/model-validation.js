// The current router's ModelUpload page, with the isolated validation PP API.
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import ModelUpload from './views/upload/ModelUpload.vue'
createApp(ModelUpload).use(ElementPlus).mount('#app')
