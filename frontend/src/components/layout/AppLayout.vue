<template>
  <div class="app-layout">
    <AppSidebar />
    <div class="main-area" :class="{ 'sidebar-collapsed': appStore.sidebarCollapsed }">
      <AppHeader />
      <main class="main-content">
        <!-- 保留页面状态：切到别的菜单再回来时，加载/计算结果和轮询不丢。
             最多缓存 12 个页面，避免长时间使用后内存无限增长。 -->
        <router-view v-slot="{ Component }">
          <keep-alive :max="12">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup>
import AppSidebar from './AppSidebar.vue'
import AppHeader from './AppHeader.vue'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  margin-left: 240px;
  transition: margin-left 0.3s ease;
}

.main-area.sidebar-collapsed {
  margin-left: 64px;
}

.main-content {
  flex: 1;
  height: calc(100vh - 64px);
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  position: relative;
  background-color: #f5f7fa;
}
</style>
