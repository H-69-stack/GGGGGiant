<template>
  <aside class="sidebar" :class="{ collapsed: appStore.sidebarCollapsed }">
    <div class="sidebar-logo">
      <span v-if="!appStore.sidebarCollapsed">{{ APP_NAME }}</span>
      <span v-else>自</span>
    </div>
    <el-menu
      :default-active="$route.path"
      :collapse="appStore.sidebarCollapsed"
      router
      class="sidebar-menu"
    >
      <el-menu-item index="/">
        <el-icon><HomeFilled /></el-icon>
        <span>首页</span>
      </el-menu-item>

      <!-- Upload / 上传分析 -->
      <el-sub-menu index="upload" v-if="canAccess('工艺规划')">
        <template #title>
          <el-icon><Upload /></el-icon>
          <span>上传分析</span>
        </template>
        <el-menu-item index="/upload/introduction">模块介绍</el-menu-item>
        <el-menu-item index="/upload/model">模型文件上传</el-menu-item>
      </el-sub-menu>

      <!-- Process Optimization / 工艺优化 -->
      <el-sub-menu index="pp" v-if="canAccess('工艺规划')">
        <template #title>
          <el-icon><DataAnalysis /></el-icon>
          <span>工艺优化</span>
        </template>
        <el-menu-item index="/pp/introduction">模块介绍</el-menu-item>
        <el-menu-item index="/pp/optimization">工艺参数优化</el-menu-item>
      </el-sub-menu>

      <!-- Normal -->
      <el-sub-menu index="normal" v-if="canAccess('模具设计')">
        <template #title>
          <el-icon><SetUp /></el-icon>
          <span>普通抽</span>
        </template>
        <el-menu-item index="/normal/introduction">模块介绍</el-menu-item>
        <el-menu-item index="/normal/calculate">普通抽计算</el-menu-item>
        <el-menu-item index="/normal/search">查找数据</el-menu-item>
      </el-sub-menu>

      <!-- TP -->
      <el-sub-menu index="tp" v-if="canAccess('模具设计')">
        <template #title>
          <el-icon><Operation /></el-icon>
          <span>TP抽</span>
        </template>
        <el-menu-item index="/tp/introduction">模块介绍</el-menu-item>
        <el-menu-item index="/tp/calculate">TP抽计算</el-menu-item>
        <el-menu-item index="/tp/search">查找数据</el-menu-item>
      </el-sub-menu>

      <!-- Taper -->
      <el-sub-menu index="taper" v-if="canAccess('模具设计')">
        <template #title>
          <el-icon><Aim /></el-icon>
          <span>Taper</span>
        </template>
        <el-menu-item index="/taper/introduction">模块介绍</el-menu-item>
        <el-menu-item index="/taper/calculate">Taper计算</el-menu-item>
        <el-menu-item index="/taper/search">查找数据</el-menu-item>
      </el-sub-menu>

      <!-- RFM -->
      <el-sub-menu index="rfm" v-if="canAccess('硬模成型')">
        <template #title>
          <el-icon><MagicStick /></el-icon>
          <span>硬模成型</span>
        </template>
        <el-menu-item index="/rfm/introduction">模块介绍</el-menu-item>
        <el-menu-item index="/rfm/load-data">加载数据</el-menu-item>
        <el-menu-item index="/rfm/belling" :disabled="!rfmStore.hasSession">扩口</el-menu-item>
        <el-menu-item index="/rfm/flattening" :disabled="!rfmStore.hasSession">压扁</el-menu-item>
        <el-menu-item index="/rfm/bending" :disabled="!rfmStore.hasSession">压弯</el-menu-item>
        <el-menu-item index="/rfm/shaping" :disabled="!rfmStore.hasSession">成型</el-menu-item>
      </el-sub-menu>

      <!-- Entity pipeline / 一体化流水线 -->
      <el-menu-item index="/entity">
        <el-icon><Cpu /></el-icon>
        <span>一体化流水线</span>
      </el-menu-item>

      <!-- Admin -->
      <el-sub-menu index="admin" v-if="authStore.isAdmin">
        <template #title>
          <el-icon><UserFilled /></el-icon>
          <span>管理</span>
        </template>
        <el-menu-item index="/admin/users">用户管理</el-menu-item>
        <el-menu-item index="/admin/register">注册用户</el-menu-item>
        <el-menu-item index="/admin/deployment">部署设置</el-menu-item>
      </el-sub-menu>

      <!-- Tools -->
      <el-sub-menu index="tools">
        <template #title>
          <el-icon><Tools /></el-icon>
          <span>工具</span>
        </template>
        <el-menu-item index="/tools/rotation">模型旋转</el-menu-item>
      </el-sub-menu>

      <!-- Account -->
      <el-menu-item index="/account">
        <el-icon><User /></el-icon>
        <span>个人信息</span>
      </el-menu-item>
    </el-menu>
  </aside>
</template>

<script setup>
import { useAppStore } from '../../stores/app'
import { useAuthStore } from '../../stores/auth'
import { useRfmStore } from '../../stores/rfm'
import { canAccessWorkrole } from '../../utils/workrole'
import { APP_NAME } from '../../config/brand'

const appStore = useAppStore()
const authStore = useAuthStore()
const rfmStore = useRfmStore()

function canAccess(role) {
  return canAccessWorkrole(authStore.user, role)
}
</script>

<style scoped>
.sidebar {
  position: fixed;
  left: 0;
  top: 0;
  bottom: 0;
  width: 240px;
  background: #fff;
  border-right: 1px solid var(--border-color);
  box-shadow: var(--shadow-sm);
  transition: width 0.3s;
  z-index: 100;
  overflow-y: auto;
}
.sidebar.collapsed {
  width: 64px;
}
.sidebar-menu {
  border-right: none;
}
.sidebar-logo {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 700;
  color: var(--primary-color);
  border-bottom: 1px solid var(--border-color);
}
</style>
