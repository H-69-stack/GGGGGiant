<template>
  <header class="app-header">
    <div class="header-left">
      <el-button
        :icon="appStore.sidebarCollapsed ? 'Expand' : 'Fold'"
        text
        @click="appStore.toggleSidebar"
      />
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
        <el-breadcrumb-item v-if="$route.meta.title">
          {{ $route.meta.title }}
        </el-breadcrumb-item>
      </el-breadcrumb>
    </div>
    <div class="header-right">
      <el-tag v-if="authStore.licenseValid" type="success" size="small">
        许可有效
      </el-tag>
      <el-tag v-else type="danger" size="small">
        许可无效
      </el-tag>
      <el-dropdown @command="handleCommand">
        <span class="user-info">
          <el-icon><UserFilled /></el-icon>
          {{ authStore.userName }}
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled>
              角色: {{ authStore.workrole }}
            </el-dropdown-item>
            <el-dropdown-item divided command="logout">
              退出登录
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useAppStore } from '../../stores/app'
import { useAuthStore } from '../../stores/auth'

const router = useRouter()
const appStore = useAppStore()
const authStore = useAuthStore()

function handleCommand(command) {
  if (command === 'logout') {
    authStore.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.app-header {
  height: 64px;
  background: #fff;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  box-shadow: var(--shadow-sm);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.user-info {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: var(--text-primary);
  font-size: 14px;
}
</style>
