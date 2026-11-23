<template>
  <div class="page-content">
    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">工作台</h1>
        <p class="page-subtitle">欢迎回来，{{ authStore.user?.name }}</p>
      </div>
      <div class="license-tag">
        <el-tag v-if="authStore.licenseValid" type="success" effect="plain" size="default">
          许可有效至 {{ authStore.licenseInfo?.end }}
        </el-tag>
        <el-tag v-else type="danger" effect="plain" size="default">
          许可证无效
        </el-tag>
      </div>
    </div>

    <!-- Primary modules: 2 columns -->
    <div v-if="canAccessPP" class="primary-grid">
      <!-- 上传分析 -->
      <div class="module-card" @click="$router.push('/upload/model')">
        <div class="card-accent"></div>
        <div class="card-body">
          <div class="card-icon">
            <el-icon size="22"><Upload /></el-icon>
          </div>
          <div class="card-text">
            <h3>上传分析</h3>
            <p>上传 STEP/STL 模型，自动完成三维重建、中心线提取与壁厚分析</p>
          </div>
          <div class="card-arrow">
            <el-icon><ArrowRight /></el-icon>
          </div>
        </div>
      </div>

      <!-- 工艺优化 -->
      <div class="module-card" @click="$router.push('/pp/optimization')">
        <div class="card-accent"></div>
        <div class="card-body">
          <div class="card-icon">
            <el-icon size="22"><DataAnalysis /></el-icon>
          </div>
          <div class="card-text">
            <h3>工艺优化</h3>
            <p>基于壁厚数据优化普通抽、TP抽、Taper 工艺参数</p>
          </div>
          <div class="card-arrow">
            <el-icon><ArrowRight /></el-icon>
          </div>
        </div>
      </div>
    </div>

    <!-- Section: 模具设计 -->
    <template v-if="canAccessMold">
    <div class="section-label">
      <span class="section-dot"></span>
      模具设计
    </div>
    <div class="die-grid">
      <div class="die-card" @click="$router.push('/normal/calculate')">
        <el-icon size="20"><SetUp /></el-icon>
        <span>普通抽</span>
        <div class="die-arrow"><el-icon><ArrowRight /></el-icon></div>
      </div>
      <div class="die-card" @click="$router.push('/tp/calculate')">
        <el-icon size="20"><Operation /></el-icon>
        <span>TP抽</span>
        <div class="die-arrow"><el-icon><ArrowRight /></el-icon></div>
      </div>
      <div class="die-card" @click="$router.push('/taper/calculate')">
        <el-icon size="20"><Aim /></el-icon>
        <span>Taper</span>
        <div class="die-arrow"><el-icon><ArrowRight /></el-icon></div>
      </div>
    </div>
    </template>

    <!-- Section: 硬模成型 -->
    <template v-if="canAccessRFM">
    <div class="section-label">
      <span class="section-dot"></span>
      硬模成型
    </div>
    <div class="module-card" @click="$router.push('/rfm/load-data')">
      <div class="card-accent"></div>
      <div class="card-body">
        <div class="card-icon">
          <el-icon size="22"><MagicStick /></el-icon>
        </div>
        <div class="card-text">
          <h3>硬模成型</h3>
          <p>从工艺设计出发，完成扩口、压扁、压弯、成型四道工序的模具生成</p>
        </div>
        <div class="rfm-steps">
          <span class="step" :class="{ active: rfmStore.hasSession }">
            <span class="step-num">1</span>加载数据
          </span>
          <span class="step-line"></span>
          <span class="step" :class="{ done: currentStep >= 2 }">
            <span class="step-num">2</span>扩口
          </span>
          <span class="step-line"></span>
          <span class="step" :class="{ done: currentStep >= 3 }">
            <span class="step-num">3</span>压扁
          </span>
          <span class="step-line"></span>
          <span class="step" :class="{ done: currentStep >= 4 }">
            <span class="step-num">4</span>压弯
          </span>
          <span class="step-line"></span>
          <span class="step" :class="{ done: currentStep >= 5 }">
            <span class="step-num">5</span>成型
          </span>
        </div>
        <div class="card-arrow card-arrow-standalone">
          <el-icon><ArrowRight /></el-icon>
        </div>
      </div>
    </div>
    </template>

    <!-- Bottom row -->
    <div class="bottom-row">
      <div class="module-card" @click="$router.push('/account')">
        <div class="card-accent"></div>
        <div class="card-body card-body-compact">
          <div class="card-icon">
            <el-icon size="22"><User /></el-icon>
          </div>
          <div class="card-text">
            <h3>用户与设置</h3>
            <p>个人信息 · 账户设置</p>
          </div>
          <div class="card-arrow">
            <el-icon><ArrowRight /></el-icon>
          </div>
        </div>
      </div>
      <div v-if="authStore.isAdmin" class="module-card" @click="$router.push('/admin/users')">
        <div class="card-accent"></div>
        <div class="card-body card-body-compact">
          <div class="card-icon">
            <el-icon size="22"><UserFilled /></el-icon>
          </div>
          <div class="card-text">
            <h3>用户管理</h3>
            <p>管理系统用户</p>
          </div>
          <div class="card-arrow">
            <el-icon><ArrowRight /></el-icon>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useRfmStore } from '../stores/rfm'
import { canAccessWorkrole, WORKROLES } from '../utils/workrole'
import {
  Upload, DataAnalysis, SetUp, Operation, Aim,
  MagicStick, User, UserFilled, ArrowRight
} from '@element-plus/icons-vue'

const authStore = useAuthStore()
const rfmStore = useRfmStore()
const route = useRoute()

const canAccessPP = computed(() => canAccessWorkrole(authStore.user, WORKROLES.PP))
const canAccessMold = computed(() => canAccessWorkrole(authStore.user, WORKROLES.MOLD))
const canAccessRFM = computed(() => canAccessWorkrole(authStore.user, WORKROLES.RFM))

const currentStep = computed(() => {
  if (!rfmStore.hasSession) return 0
  const p = route.path
  if (p.includes('shaping')) return 5
  if (p.includes('belling')) return 2
  if (p.includes('flattening')) return 3
  if (p.includes('bending')) return 4
  return 1
})
</script>

<style scoped>
.page-content {
  padding: 28px 32px;
  max-width: 1000px;
}

/* Header */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 28px;
}
.page-title {
  font-size: 22px;
  font-weight: 600;
  color: #262626;
  margin: 0 0 4px;
}
.page-subtitle {
  font-size: 13px;
  color: #8c8c8c;
  margin: 0;
}

/* Section label */
.section-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #8c8c8c;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
  margin-top: 24px;
}
.section-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #1890ff;
}

/* Module card */
.module-card {
  background: #fff;
  border-radius: 8px;
  border: 1px solid #e8eef8;
  overflow: hidden;
  cursor: pointer;
  transition: all 0.2s ease;
}
.module-card:hover {
  border-color: #1890ff;
  box-shadow: 0 4px 16px rgba(24, 144, 255, 0.1);
}
.card-accent {
  height: 3px;
  background: #1890ff;
}
.card-body {
  padding: 20px 20px 18px;
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
.card-body-compact {
  padding: 16px 18px 14px;
}
.card-icon {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: #e6f7ff;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #1890ff;
  flex-shrink: 0;
}
.card-text {
  flex: 1;
  min-width: 0;
}
.card-text h3 {
  font-size: 15px;
  font-weight: 600;
  color: #262626;
  margin: 0 0 5px;
  line-height: 1.3;
}
.card-text p {
  font-size: 12px;
  color: #8c8c8c;
  margin: 0;
  line-height: 1.6;
}
.card-arrow {
  color: #d9d9d9;
  flex-shrink: 0;
  transition: color 0.2s, transform 0.2s;
  align-self: center;
}
.card-arrow-standalone {
  margin-left: auto;
  align-self: center;
}
.module-card:hover .card-arrow {
  color: #1890ff;
  transform: translateX(3px);
}

/* Primary grid: 2 columns */
.primary-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

/* Die grid */
.die-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.die-card {
  background: #fff;
  border: 1px solid #e8eef8;
  border-radius: 8px;
  padding: 16px 18px;
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  transition: all 0.2s ease;
  color: #595959;
  font-size: 14px;
  font-weight: 500;
}
.die-card:hover {
  border-color: #1890ff;
  color: #1890ff;
  box-shadow: 0 2px 10px rgba(24, 144, 255, 0.08);
}
.die-card .el-icon {
  color: #1890ff;
  flex-shrink: 0;
}
.die-card span {
  flex: 1;
}
.die-arrow {
  color: #d9d9d9;
  transition: color 0.2s, transform 0.2s;
}
.die-card:hover .die-arrow {
  color: #1890ff;
  transform: translateX(3px);
}

/* RFM steps */
.rfm-steps {
  display: flex;
  align-items: center;
  gap: 0;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid #f0f5ff;
}
.step {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: #bfbfbf;
  white-space: nowrap;
  transition: color 0.2s;
}
.step.done {
  color: #1890ff;
}
.step.active {
  color: #52c41a;
}
.step-num {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid currentColor;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 600;
  flex-shrink: 0;
}
.step-line {
  width: 20px;
  height: 1px;
  background: #e8eef8;
  flex-shrink: 0;
  margin: 0 2px;
}

/* Bottom row */
.bottom-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin-top: 14px;
}
@media (max-width: 768px) {
  .page-content { padding: 16px; }
  .primary-grid { grid-template-columns: 1fr; }
  .die-grid { grid-template-columns: 1fr; }
  .bottom-row { grid-template-columns: 1fr; }
}
</style>
