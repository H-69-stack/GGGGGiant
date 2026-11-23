<template>
  <div class="tab3-container">
    <!-- 工艺路线选择 -->
    <el-card class="route-card">
      <h2 class="page-title">工艺路线选择</h2>

      <!-- 自动推荐信息 -->
      <div v-if="recommendInfo" class="recommend-banner">
        <div class="recommend-icon">
          <el-icon size="20"><InfoFilled /></el-icon>
        </div>
        <div class="recommend-body">
          <div class="recommend-reason">{{ recommendInfo.reason }}</div>
          <div class="recommend-meta">
            <span>形线长度：{{ recommendInfo.line_length }}mm</span>
            <span>外径变化率：{{ recommendInfo.D_change }}%</span>
          </div>
        </div>
      </div>

      <!-- GA 算法工艺组 -->
      <div class="method-group">
        <div class="group-header">
          <span class="group-tag ga">GA 遗传算法</span>
          <span class="group-desc">适合复杂长管，搜索全局最优解</span>
        </div>
        <div class="method-grid">
          <div
            v-for="m in gaMethods"
            :key="m.key"
            class="method-card"
            :class="{
              selected: confirmedMethod === m.key,
              recommended: recommendInfo?.recommended === m.key,
            }"
            @click="selectMethod(m.key)"
          >
            <div class="card-header">
              <span class="card-title">{{ m.label }}</span>
              <span v-if="recommendInfo?.recommended === m.key" class="rec-badge">推荐</span>
            </div>
            <div class="card-steps">
              <span v-for="(step, si) in m.steps" :key="si" class="step-tag">{{ step }}</span>
            </div>
            <div class="card-meta">
              <span class="card-condition">{{ m.condition }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 暴力枚举工艺组 -->
      <div class="method-group">
        <div class="group-header">
          <span class="group-tag brute">暴力枚举</span>
          <span class="group-desc">适合简单短管，精确遍历求解</span>
        </div>
        <div class="method-grid">
          <div
            v-for="m in bruteMethods"
            :key="m.key"
            class="method-card"
            :class="{
              selected: confirmedMethod === m.key,
              recommended: recommendInfo?.recommended === m.key,
            }"
            @click="selectMethod(m.key)"
          >
            <div class="card-header">
              <span class="card-title">{{ m.label }}</span>
              <span v-if="recommendInfo?.recommended === m.key" class="rec-badge">推荐</span>
            </div>
            <div class="card-steps">
              <span v-for="(step, si) in m.steps" :key="si" class="step-tag">{{ step }}</span>
            </div>
            <div class="card-meta">
              <span class="card-condition">{{ m.condition }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 自由组合工艺组 -->
      <div class="method-group">
        <div class="group-header">
          <span class="group-tag custom">自由组合</span>
          <span class="group-desc">手动选择工艺步骤，组合任意工艺路线</span>
        </div>
        <div class="custom-steps-grid">
          <div class="custom-steps-row">
            <span class="step-option" :class="{ active: freeSteps.includes('normal') }" @click="toggleFreeStep('normal')">普通抽</span>
            <span class="step-connector" v-if="freeSteps.length > 0">&rarr;</span>
            <span class="step-option" :class="{ active: freeSteps.includes('tp') }" @click="toggleFreeStep('tp')">TP抽</span>
            <span class="step-connector" v-if="freeSteps.length > 0">&rarr;</span>
            <span class="step-option" :class="{ active: freeSteps.includes('taper') }" @click="toggleFreeStep('taper')">Taper</span>
            <span class="step-connector" v-if="freeSteps.length > 0">&rarr;</span>
            <span class="step-option" :class="{ active: freeSteps.includes('flaring') }" @click="toggleFreeStep('flaring')">扩口</span>
          </div>
          <div class="custom-preview" v-if="freeSteps.length > 0">
            当前组合预览：<span class="preview-text">{{ freeStepsLabel }}</span>
            <span class="algo-tag" :class="freeStepsAlgoClass">{{ freeStepsAlgoLabel }}</span>
          </div>
          <div class="custom-actions">
            <el-button type="primary" size="small" @click="confirmFreeRoute" :disabled="freeSteps.length === 0">
              确认此组合
            </el-button>
            <el-button size="small" @click="freeSteps = []">重置</el-button>
          </div>
        </div>
      </div>

      <!-- 当前已选工艺 + 操作 -->
      <div class="selection-bar">
        <div v-if="confirmedMethod" class="confirmed-display">
          已选择：
          <span class="confirmed-label">{{ confirmedRouteLabel }}</span>
          <span class="algo-tag" :class="methodMeta.ga ? 'ga' : 'brute'">
            {{ methodMeta.ga ? 'GA 遗传算法' : '暴力枚举' }}
          </span>
        </div>
        <div v-else class="selection-hint">请从上方选择工艺方法</div>
        <div class="selection-actions">
          <el-button @click="resetRoute">重置</el-button>
          <el-button @click="showHistory = !showHistory">查看历史</el-button>
        </div>
      </div>

      <!-- 历史记录 -->
      <div v-if="showHistory" class="history-box">
        <div v-if="!history.length" class="history-empty">暂无历史工艺路线</div>
        <div v-else>
          <div
            v-for="(h, i) in history"
            :key="i"
            class="history-item"
            @click="loadHistory(h)"
          >
            {{ i + 1 }}. {{ h.label }}
            <span class="history-algo" :class="getHistoryAlgoClass(h)">
              {{ getHistoryAlgoLabel(h) }}
            </span>
            <span class="history-time">{{ h.time }}</span>
          </div>
        </div>
      </div>
    </el-card>

    <!-- 工艺优化模式 -->
    <el-card v-if="confirmedMethod" class="mode-card">
      <h2 class="page-title mode-title">[{{ confirmedRouteLabel }}]工艺优化模式</h2>

      <!-- 参数精度控制 -->
      <section class="param-block">
        <h3 class="block-title">参数精度控制</h3>
        <div class="block-desc">设置结果中不同类型参数的显示精度（小数点后位数）</div>
        <div class="param-row">
          <div class="param-item">
            <div class="param-label">外径参数(D)精度</div>
            <el-input-number v-model="configForm.dPrecision" :min="0" :max="3" :step="1" />
          </div>
          <div class="param-item">
            <div class="param-label">壁厚参数(T)精度</div>
            <el-input-number v-model="configForm.tPrecision" :min="0" :max="3" :step="1" />
          </div>
          <div class="param-item">
            <div class="param-label">长度参数(L)精度</div>
            <el-input-number v-model="configForm.lPrecision" :min="0" :max="3" :step="1" />
          </div>
        </div>
      </section>

      <!-- 优化目标权重 -->
      <section v-if="shouldShowWeights" class="param-block">
        <h3 class="block-title">优化目标权重</h3>
        <div class="block-desc">
          调节各子目标在适应度函数中的权重；越大越重视该项。<br />
          点击 <el-icon class="hint-icon"><Unlock /></el-icon> 可锁定该项使其不参与联动；
          拖动或在右侧输入框中输入数值后，未锁定的其它项会按比例联动，
          总和保持 {{ weightTotalSum.toFixed(1) }} %（与原算法一致）。
        </div>
        <div class="weight-list">
          <div
            v-for="(item, idx) in currentWeightSchema"
            :key="item.key"
            class="weight-row"
            :class="{ 'is-locked': isLocked(item.key) }"
          >
            <el-button
              :type="isLocked(item.key) ? 'warning' : 'default'"
              :title="isLocked(item.key) ? '已锁定（点击解锁）' : '点击锁定该项'"
              size="small"
              circle
              class="weight-lock-btn"
              @click="toggleLock(item.key)"
            >
              <el-icon><Lock v-if="isLocked(item.key)" /><Unlock v-else /></el-icon>
            </el-button>
            <div class="weight-meta">
              <div class="weight-name">{{ item.label }}</div>
              <div v-if="item.hint" class="weight-hint">{{ item.hint }}</div>
            </div>
            <el-slider
              :model-value="Number(weights[item.key] || 0)"
              :max="weightMaxList[idx]"
              :min="0"
              :step="1"
              :disabled="isLocked(item.key)"
              :format-tooltip="(v) => Number(v).toFixed(0) + '%'"
              class="weight-slider"
              @input="(v) => onWeightChange(idx, v)"
            />
            <el-input-number
              :model-value="Number(weights[item.key] || 0)"
              :min="0"
              :max="weightMaxList[idx]"
              :step="1"
              :precision="1"
              :disabled="isLocked(item.key)"
              size="small"
              controls-position="right"
              class="weight-input"
              @change="(v) => onWeightChange(idx, v)"
            />
            <span class="weight-percent-sign">%</span>
          </div>
        </div>
        <div class="weight-actions">
          <span class="weight-sum-tip">当前合计：{{
            currentWeightSchema.reduce((s, it) => s + Number(weights[it.key] || 0), 0).toFixed(1)
          }} %</span>
          <el-button size="small" @click="resetWeights">重置为默认值</el-button>
        </div>
      </section>

      <!-- 素材长度加工余量：全部优化模式共用 -->
      <section class="param-block">
        <h3 class="block-title">加工余量</h3>
        <div class="block-desc">
          用于计算素材长度。默认 1.3，确认后才会传给本次及之后的优化和重算。
          当前已确认：{{ confirmedAllowance }}
        </div>
        <div class="param-row">
          <div class="param-item">
            <div class="param-label">加工余量</div>
            <el-input-number v-model="allowanceDraft" :min="0.01" :max="10" :step="0.1" :precision="2" />
          </div>
          <div class="param-item">
            <div class="param-label">&nbsp;</div>
            <el-button type="primary" @click="confirmAllowance">确认</el-button>
          </div>
        </div>
      </section>

      <!-- 素材长度控制 -->
      <section v-if="methodMeta.useAdd1Add2 || methodMeta.useAdd3" class="param-block">
        <h3 class="block-title">素材长度控制</h3>
        <div class="param-row">
          <div v-if="methodMeta.useAdd1Add2" class="param-item">
            <div class="param-label">因封口添加长度1</div>
            <el-input-number v-model="configForm.add1" :min="0" :max="200" :step="1" :precision="2" />
          </div>
          <div v-if="methodMeta.useAdd1Add2" class="param-item">
            <div class="param-label">因taper添加长度2</div>
            <el-input-number v-model="configForm.add2" :min="0" :max="200" :step="1" :precision="2" />
          </div>
          <div v-if="methodMeta.useAdd3" class="param-item">
            <div class="param-label">扩口段补偿长度3</div>
            <el-input-number v-model="configForm.add3" :min="0" :max="200" :step="1" :precision="2" />
          </div>
        </div>
      </section>

      <div class="run-row">
        <el-button
          type="primary"
          size="large"
          :loading="optimizing"
          :disabled="!sessionId"
          @click="startOptimization()"
        >
          开始优化工艺参数
        </el-button>
        <span v-if="!sessionId" class="hint">请先从数据库加载分析数据</span>
      </div>

      <div v-if="warningText" class="warn-line">
        <el-alert :title="warningText" type="warning" :closable="false" show-icon />
      </div>

      <div class="fallback-buttons" v-if="showFallbackButtons">
        <el-button
          v-if="showFallback1"
          @click="startOptimization('normal+tp+taper+flaring')"
        >选择补充方案</el-button>
        <el-button
          v-if="showFallback2"
          @click="startOptimization('tp+taper+flaring-extended')"
        >选择补充方案2</el-button>
      </div>
    </el-card>

    <!-- 进度条 -->
    <el-card v-if="optimizing || progressMessage" class="progress-card">
      <el-progress
        :percentage="Math.round(progressPct)"
        :status="progressPct >= 100 ? 'success' : ''"
        :format="() => progressMessage || `${Math.round(progressPct)}%`"
      />
    </el-card>

    <!-- 优化结果 -->
    <el-card v-if="solutions.length > 0" class="results-card">
      <template #header>
        <div class="card-header">
          <span>优化结果</span>
          <el-tag v-if="displayIsFeasible" type="success">
            可行解 (工艺优化度 ≥ {{ feasibleThreshold.toFixed(2) }})
          </el-tag>
          <el-tag v-else type="warning">
            当前方案未满足可行阈值 {{ feasibleThreshold.toFixed(2) }}
          </el-tag>
        </div>
      </template>

      <div class="solution-row">
        <el-select v-model="selectedSolutionIdx" style="width: 380px">
          <el-option
            v-for="(sol, idx) in solutions"
            :key="idx"
            :label="`${sol.name || `方案${idx + 1}`} - 工艺优化度：${Number(sol.fitness || 0).toFixed(4)}`"
            :value="idx"
          />
        </el-select>
        <el-button @click="toggleEdit">{{ editing ? '取消编辑' : '编辑参数' }}</el-button>
        <el-button :disabled="!editing" @click="resetEdit">重置参数</el-button>
        <el-button type="primary" :disabled="!editing || recalcing" :loading="recalcing" @click="applyRecalc">
          应用更改
        </el-button>
      </div>

      <div v-if="selectedSolution" class="solution-detail">
        <!-- 工艺优化度 + 优化指标 -->
        <el-descriptions title="优化指标" :column="3" border size="small">
          <el-descriptions-item label="工艺优化度">{{ Number(selectedSolution.fitness || 0).toFixed(4) }}</el-descriptions-item>
          <el-descriptions-item label="截面积变化率">{{ formatAreaChange(selectedSolution.area_change_rate) }}</el-descriptions-item>
          <el-descriptions-item label="延伸率">{{ formatExt(selectedSolution.outer_extension_ratio || selectedSolution.extension_ratio) }}</el-descriptions-item>
        </el-descriptions>

        <!-- 工艺参数详情（按工艺段分组 + 评估提示） -->
        <h3 class="block-title" style="margin-top: 20px">工艺参数详情</h3>
        <div v-for="(sec, sIdx) in previewSections" :key="sIdx" class="seg-section">
          <div class="seg-title">{{ sec.title }}</div>
          <el-row :gutter="12">
            <el-col v-for="(item, iIdx) in sectionItems(sec)" :key="item.key || iIdx" :span="8">
              <div class="seg-item">
                <div class="seg-label">{{ sectionItemLabel(item) }}</div>
                <el-input-number
                  v-if="editing && item.editable"
                  v-model="editModel[item.key]"
                  :step="0.1"
                  size="small"
                  controls-position="right"
                />
                <div v-else class="seg-value">{{ item.value }}</div>
              </div>
            </el-col>
          </el-row>
          <div v-if="sec.evals?.length" class="seg-evals">
            <el-alert
              v-for="(ev, eIdx) in sec.evals"
              :key="eIdx"
              :type="evalType(ev.level)"
              :title="ev.text"
              :closable="false"
              show-icon
              size="small"
              style="margin-top: 4px"
            />
          </div>
        </div>

        <div class="chart-container" v-if="fitnessChart">
          <PlotlyChart :figure="fitnessChart" />
        </div>

        <!-- 壁厚数据表 (含修改后壁厚) -->
        <h3 class="block-title" style="margin-top: 16px">普通抽预留壁厚数据修改表</h3>
        <div class="wt-hint">"修改后壁厚"是按当前工艺方案重新计算的目标壁厚曲线；可在表格内直接编辑。</div>
        <el-table
          :data="modifiedWtView"
          :max-height="360"
          size="small"
          border
          stripe
        >
          <el-table-column prop="中心线长度(mm)" label="中心线长度(mm)" width="140" />
          <el-table-column prop="壁厚(mm)" label="原壁厚(mm)" width="120" />
          <el-table-column label="修改后壁厚(mm)" width="160">
            <template #default="scope">
              <el-input-number
                v-model="modifiedWtData[(wtPage - 1) * wtPageSize + scope.$index]['修改后壁厚(mm)']"
                :step="0.01"
                :precision="3"
                size="small"
                controls-position="right"
                style="width: 130px"
              />
            </template>
          </el-table-column>
          <el-table-column label="普通抽外径Dn">
            <template #default>
              {{ normalDrawDn }}
            </template>
          </el-table-column>
          <el-table-column prop="外径周长(mm)" label="成品外径周长" />
        </el-table>
        <div class="wt-pagination" v-if="modifiedWtData.length > wtPageSize">
          <el-pagination
            v-model:current-page="wtPage"
            :page-size="wtPageSize"
            :total="modifiedWtData.length"
            layout="prev, pager, next, total"
            small
          />
        </div>

        <!-- 修改后壁厚截面积变化率验算（Dn、Ln 有效时可开关） -->
        <div v-if="showAreaCheckSection" class="wt-area-checks">
          <div class="wt-area-checks-header">
            <h3 class="block-title">修改后壁厚截面积变化率验算</h3>
            <div class="wt-area-check-toggle">
              <span class="meta-label">启用验算</span>
              <el-switch
                v-model="modifiedWtAreaCheckEnabled"
                :loading="areaCheckToggleLoading"
                @change="onModifiedWtAreaCheckToggle"
              />
            </div>
          </div>
          <template v-if="modifiedWtAreaCheckEnabled">
          <div class="wt-hint">
            原截面积按素材 D0={{ areaCheckBase.d0 }} mm、T0={{ areaCheckBase.t0 }} mm 计算；
            修改后各档按 Dn={{ areaCheckBase.dn }} mm、Ln={{ areaCheckBase.ln }} mm 与修改后壁厚 T1/T2/T3 计算（理想 20% ~ 50%）。
          </div>
          <template v-if="modifiedWtAreaChecks.length">
            <el-alert
              v-for="(item, idx) in modifiedWtAreaChecks"
              :key="item.key || idx"
              :type="evalType(item.level)"
              :title="item.text"
              :closable="false"
              show-icon
              size="small"
              style="margin-top: 6px"
            />
          </template>
          <el-alert
            v-else
            type="info"
            title="验算数据加载中，请稍候或点击「刷新表格」"
            :closable="false"
            show-icon
            size="small"
            style="margin-top: 6px"
          />
          </template>
        </div>

        <!-- 总参数表 All_data (30 项) -->
        <h3 class="block-title" style="margin-top: 16px">总参数表（All_data）</h3>
        <div class="wt-hint">所有参数均可编辑；保存设计时以此表的最终值为准。</div>
        <el-table
          :data="allDataRows"
          :max-height="420"
          size="small"
          border
          stripe
        >
          <el-table-column prop="参数" label="参数" width="100" />
          <el-table-column prop="含义" label="含义" min-width="280" />
          <el-table-column label="值" width="160">
            <template #default="scope">
              <el-input-number
                v-model="allDataRows[scope.$index]['值']"
                :step="0.1"
                :precision="3"
                size="small"
                controls-position="right"
                style="width: 140px"
              />
            </template>
          </el-table-column>
        </el-table>

        <div v-if="modifiedWtCurvePoints.length" class="modified-wt-chart-wrap">
          <h3 class="block-title" style="margin-top: 16px">修改后壁厚曲线</h3>
          <div class="wt-hint">平稳段标注壁厚和长度，过渡段标注长度。数值与总参数表一致。</div>
          <div class="modified-wt-chart">
            <canvas ref="modifiedWtChartRef"></canvas>
          </div>
        </div>

        <!-- 保存设计：车种号 / 管件号 / 设计名称 / Taper 壁厚 toggle -->
        <h3 class="block-title" style="margin-top: 24px">保存设计</h3>
        <div class="save-meta">
          <div class="save-meta-row">
            <div class="meta-item">
              <div class="meta-label">车种号</div>
              <el-input v-model="saveForm.vehicle_spec" placeholder="请输入车种号" size="default" style="width: 200px" />
            </div>
            <div class="meta-item">
              <div class="meta-label">管件号</div>
              <el-input v-model="saveForm.tube_number" placeholder="请输入管件号" size="default" style="width: 200px" />
            </div>
            <div class="meta-item">
              <div class="meta-label">设计名称</div>
              <el-input v-model="saveForm.display_name" placeholder="请输入设计名称（必填）" size="default" style="width: 320px" />
            </div>
            <div class="meta-item">
              <div class="meta-label">是否将 Taper 段壁厚处理成过渡段</div>
              <el-switch v-model="taperThick" @change="refreshPreview" />
            </div>
          </div>
        </div>

        <div class="save-actions">
          <el-button type="success" :loading="saving" size="large" @click="confirmSaveDesign">
            保存设计到工艺设计数据库
          </el-button>
          <el-button :loading="previewing" @click="refreshPreview">刷新表格</el-button>
        </div>
      </div>
    </el-card>

  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { Chart, registerables } from 'chart.js'
import { ppApi } from '@/api/pp'
import PlotlyChart from '@/components/PlotlyChart.vue'

Chart.register(...registerables)
import { InfoFilled, Lock, Unlock } from '@element-plus/icons-vue'

const props = defineProps({
  sessionId: { type: String, default: '' },
})

// 工艺方法定义（按算法分组，与后端 _PP_METHODS 对应）
const ALL_METHODS = [
  // ---- GA 遗传算法组 ----
  {
    key: 'tp+taper+flaring',
    label: 'Taper+TP抽+扩口',
    algo: 'ga',
    steps: ['扩口', 'TP抽', 'Taper'],
    condition: '形线长度 ≥ 300mm（次选）',
  },
  {
    key: 'normal+tp+taper+flaring',
    label: '普通抽+TP抽+Taper+扩口',
    algo: 'ga',
    steps: ['普通抽', 'TP抽', 'Taper', '扩口'],
    condition: '通用兜底方案',
  },
  {
    key: 'tp+taper+flaring',   // 加长Taper 与基本型 key 相同但参数不同，这里合并展示
    label: '加长Taper+TP抽+扩口',
    algo: 'ga',
    steps: ['扩口', 'TP抽', 'Taper'],
    condition: 'Taper 段加长（可调）',
  },
  // ---- 暴力枚举组 ----
  {
    key: 'tp',
    label: '仅TP抽',
    algo: 'brute',
    steps: ['TP抽'],
    condition: '形线 < 150mm，变化率 < 30%',
  },
  {
    key: 'normal+tp',
    label: '普通抽+TP抽',
    algo: 'brute',
    steps: ['普通抽', 'TP抽'],
    condition: '形线 < 150mm，变化率 < 30%',
  },
  {
    key: 'only-flaring',
    label: '仅扩口',
    algo: 'brute',
    steps: ['扩口'],
    condition: '形线 < 150mm，变化率 < 30%',
  },
  {
    key: 'normal+flaring',
    label: '普通抽+扩口',
    algo: 'brute',
    steps: ['普通抽', '扩口'],
    condition: '形线 < 150mm，变化率 ≥ 30%',
  },
  {
    key: 'tp+flaring',
    label: 'TP抽+扩口',
    algo: 'brute',
    steps: ['TP抽', '扩口'],
    condition: '150mm ≤ 形线 < 300mm',
  },
  {
    key: 'normal+taper+flaring',
    label: '普通抽+Taper+扩口',
    algo: 'brute',
    steps: ['普通抽', 'Taper', '扩口'],
    condition: '形线长度 ≥ 300mm（优先）',
  },
  {
    key: 'normal+taper',
    label: '普通抽+Taper',
    algo: 'brute',
    steps: ['普通抽', 'Taper'],
    condition: '特殊定制',
  },
]

// 工艺方法元数据
const METHOD_META = {
  'tp+taper+flaring': { ga: true, useAdd1Add2: true, useAdd3: false, threshold: 0.74, editable: ['D0', 'T0', 'D2'] },
  'normal+tp+taper+flaring': { ga: true, useAdd1Add2: true, useAdd3: false, threshold: 0.74, editable: ['D0', 'T0', 'D2'] },
  'tp': { ga: false, useAdd1Add2: false, useAdd3: false, threshold: 1.0, editable: ['D0', 'T0'] },
  'normal+tp': { ga: false, useAdd1Add2: false, useAdd3: false, threshold: 1.0, editable: ['D0', 'T0'] },
  'only-flaring': { ga: false, useAdd1Add2: false, useAdd3: false, threshold: 1.0, editable: ['D0', 'T0'] },
  'normal+flaring': { ga: false, useAdd1Add2: false, useAdd3: true, threshold: 1.0, editable: ['D0', 'T0'] },
  'tp+flaring': { ga: false, useAdd1Add2: false, useAdd3: true, threshold: 1.0, editable: ['D0', 'T0'] },
  'normal+taper+flaring': { ga: false, useAdd1Add2: false, useAdd3: true, threshold: 1.0, editable: ['D0', 'T0'] },
  'normal+taper': { ga: false, useAdd1Add2: false, useAdd3: true, threshold: 1.0, editable: ['D0', 'T0'] },
}

const PROCESS_KEY_TO_METHOD = {
  'tp': '仅TP抽',
  'normal+tp': '普通抽+TP抽',
  'only-flaring': '仅扩口',
  'normal+flaring': '普通抽+扩口',
  'tp+flaring': 'TP抽+扩口',
  'normal+taper': '普通抽+Taper',
  'normal+taper+flaring': '普通抽+Taper+扩口',
  'tp+taper+flaring': 'Taper+TP抽+扩口',
  'normal+tp+taper+flaring': '普通抽+TP抽+Taper+扩口',
}

const METHOD_TO_PROCESS = {
  'tp': ['tp'],
  'normal+tp': ['normal', 'tp'],
  'only-flaring': ['flaring'],
  'normal+flaring': ['normal', 'flaring'],
  'tp+flaring': ['tp', 'flaring'],
  'normal+taper': ['normal', 'taper'],
  'normal+taper+flaring': ['normal', 'taper', 'flaring'],
  'tp+taper+flaring': ['tp', 'taper', 'flaring'],
  'normal+tp+taper+flaring': ['normal', 'tp', 'taper', 'flaring'],
}

// 后端期望中文标签，前端统一用英文 key 存储
const ENGLISH_TO_CHINESE = {
  'tp': '仅TP抽',
  'normal+tp': '普通抽+TP抽',
  'only-flaring': '仅扩口',
  'normal+flaring': '普通抽+扩口',
  'tp+flaring': 'TP抽+扩口',
  'normal+taper': '普通抽+Taper',
  'normal+taper+flaring': '普通抽+Taper+扩口',
  'tp+taper+flaring': 'Taper+TP抽+扩口',
  'normal+tp+taper+flaring': '普通抽+TP抽+Taper+扩口',
}
function toBackendMethod(key) {
  return ENGLISH_TO_CHINESE[key] || key
}

// 去重（加长Taper 与基本型合并）
const METHOD_MAP = {}
for (const m of ALL_METHODS) {
  if (!METHOD_MAP[m.key]) {
    METHOD_MAP[m.key] = { ...m }
  }
}
const GA_METHOD_KEYS = new Set(['tp+taper+flaring', 'normal+tp+taper+flaring'])
const gaMethods = computed(() =>
  Object.values(METHOD_MAP).filter(m => m.algo === 'ga')
)
const bruteMethods = computed(() =>
  Object.values(METHOD_MAP).filter(m => m.algo === 'brute')
)

// 选中工艺方法
function selectMethod(key) {
  console.log('[selectMethod] ENTER key=', key)
  console.log('[selectMethod] METHOD_META keys=', Object.keys(METHOD_META))
  const meta = METHOD_META[key]
  console.log('[selectMethod] meta=', meta)
  if (!meta) {
    console.error('[selectMethod] 未找到 METHOD_META[key]:', key)
    ElMessage.warning(`未支持的工艺方法：${key}`)
    return
  }
  confirmedMethod.value = key
  confirmedSteps.value = METHOD_TO_PROCESS[key] || []
  resetOptimizationState()
  pushHistory({ key, label: PROCESS_KEY_TO_METHOD[key] || key })
  console.log('[selectMethod] confirmedMethod set to:', confirmedMethod.value)
  ElMessage.success(`已选择：${PROCESS_KEY_TO_METHOD[key] || key}`)
}

// 路线选择状态（draftSteps 仅保留，不再作为主要选择方式）
const freeSteps = ref([])
const draftSteps = ref([])
const confirmedSteps = ref([])
const confirmedMethod = ref('')
const showHistory = ref(false)
const history = ref([])

// 历史记录工具函数
function getHistoryAlgoLabel(h) {
  const meta = METHOD_META[h.key || h.label]
  return meta?.ga ? 'GA' : '枚举'
}
function getHistoryAlgoClass(h) {
  const meta = METHOD_META[h.key || h.label]
  return meta?.ga ? 'ga' : 'brute'
}

// 优化相关状态
const optimizing = ref(false)
const recalcing = ref(false)
const progressPct = ref(0)
const progressMessage = ref('')
const solutions = ref([])
const selectedSolutionIdx = ref(0)
const isFeasible = ref(false)
const status = ref('')
const warningText = ref('')
const taskId = ref(null)
const fitnessHistory = ref({ best: [], average: [] })
const editing = ref(false)
const editModel = reactive({})

const configForm = reactive({
  dPrecision: 1,
  tPrecision: 1,
  lPrecision: 0,
  add1: 10,
  add2: 10,
  add3: 0,
})
const allowanceDraft = ref(1.3)
const confirmedAllowance = ref(1.3)

function confirmAllowance() {
  const value = Number(allowanceDraft.value)
  if (!Number.isFinite(value) || value <= 0) {
    ElMessage.warning('加工余量必须为大于 0 的数值')
    return
  }
  confirmedAllowance.value = value
  ElMessage.success(`加工余量已确认为 ${value}`)
}

// ─────────────────── 优化目标权重 ───────────────────
// 每条工艺路线的子目标 schema（key 与后端一致，default 为 0~1 小数，
// 与各 fitness 函数中原硬编码值相同；前端不强制总和=100%，
// 各路线的"原始合计 S"由 schema 中所有 default 之和决定，联动时保持 S 不变）。
const WEIGHT_SCHEMA = {
  // GA 组
  'tp+taper+flaring': [
    { key: 'expansion',  label: '扩口段',  default: 0.25, hint: '扩口率约束（理想 < 80%）' },
    { key: 'tp_extract', label: 'TP抽段',  default: 0.25, hint: '截面积变化率（理想 20% ~ 50%）' },
    { key: 'taper',      label: 'Taper段', default: 0.25, hint: '外延伸率约束（理想 < 30%）' },
  ],
  'normal+tp+taper+flaring': [
    { key: 'expansion',  label: '扩口段',  default: 0.25, hint: '扩口率约束（理想 < 80%）' },
    { key: 'pt_extract', label: '普通抽段', default: 0.25, hint: '截面积变化率（理想 20% ~ 50%）' },
    { key: 'tp_extract', label: 'TP抽段',  default: 0.25, hint: '截面积变化率（理想 20% ~ 50%）' },
    { key: 'taper',      label: 'Taper段', default: 0.25, hint: '外延伸率约束（理想 < 30%）' },
  ],
  // 暴力枚举组
  'tp': [
    { key: 'tp_extract', label: 'TP抽段',  default: 1.0,  hint: '截面积变化率（理想 20% ~ 50%）' },
  ],
  'normal+tp': [
    { key: 'pt_extract', label: '普通抽段', default: 1 / 2, hint: '截面积变化率（理想 20% ~ 50%）' },
    { key: 'tp_extract', label: 'TP抽段',  default: 1 / 2, hint: '截面积变化率（理想 20% ~ 50%）' },
  ],
  'normal+flaring': [
    { key: 'expansion',  label: '扩口段',  default: 1 / 2, hint: '扩口率约束（理想 < 80%）' },
    { key: 'pt_extract', label: '普通抽段', default: 1 / 2, hint: '截面积变化率（理想 20% ~ 50%）' },
  ],
  'tp+flaring': [
    { key: 'expansion',  label: '扩口段',  default: 1 / 3, hint: '扩口率约束（理想 < 80%）' },
    { key: 'tp_extract', label: 'TP抽段',  default: 1 / 2, hint: '截面积变化率（理想 20% ~ 50%）' },
  ],
  'normal+taper+flaring': [
    { key: 'expansion',  label: '扩口段',  default: 1 / 3, hint: '扩口率约束（理想 < 80%）' },
    { key: 'pt_extract', label: '普通抽段', default: 1 / 3, hint: '截面积变化率（理想 20% ~ 50%）' },
    { key: 'taper',      label: 'Taper段', default: 1 / 3, hint: '外延伸率约束（理想 < 30%）' },
  ],
  'normal+taper': [
    { key: 'pt_extract', label: '普通抽段', default: 1 / 2, hint: '截面积变化率（理想 20% ~ 50%）' },
    { key: 'taper',      label: 'Taper段', default: 1 / 2, hint: '外延伸率约束（理想 < 30%）' },
  ],
}

// 内部以"百分比"形式（0~100）存储，便于 el-slider 直接显示；提交后端时再除以 100。
const weights = reactive({})

// 锁定状态（key → true 表示已锁定，调整其它滑块时不参与联动，且本项滑块/输入框禁用）
const lockedMap = reactive({})

const currentWeightSchema = computed(() => {
  return WEIGHT_SCHEMA[confirmedMethod.value] || []
})

// 该工艺的"原始合计 S"（百分比形式），联动后保持不变。
const weightTotalSum = computed(() => {
  return currentWeightSchema.value.reduce((s, it) => s + it.default * 100, 0)
})

// 1 项以下的工艺（如「仅TP抽」）调权重无意义，整个 section 隐藏。
const shouldShowWeights = computed(() => currentWeightSchema.value.length >= 2)

// 当前各项可调最大值 = S - 其它锁定项之和（保证总和恒为 S）
const weightMaxList = computed(() => {
  const schema = currentWeightSchema.value
  const S = weightTotalSum.value
  return schema.map((cur, idx) => {
    const otherLockedSum = schema
      .filter((it, i) => i !== idx && lockedMap[it.key])
      .reduce((s, it) => s + Number(weights[it.key] || 0), 0)
    return S - otherLockedSum
  })
})

function isLocked(key) {
  return !!lockedMap[key]
}

function toggleLock(key) {
  if (lockedMap[key]) {
    delete lockedMap[key]
  } else {
    lockedMap[key] = true
  }
}

function applyDefaultWeights() {
  Object.keys(weights).forEach(k => delete weights[k])
  Object.keys(lockedMap).forEach(k => delete lockedMap[k])
  currentWeightSchema.value.forEach(it => {
    weights[it.key] = it.default * 100
  })
}

// 拖动/输入第 idx 项至 newPercent，未锁定的其它项按比例联动，
// 锁定的项保持不变，总和始终 = S。
function onWeightChange(idx, newPercent) {
  const schema = currentWeightSchema.value
  if (!schema.length) return
  const cur = schema[idx]
  if (!cur) return
  // 当前项被锁定 → 不允许变化
  if (lockedMap[cur.key]) return

  const S = weightTotalSum.value
  // 其它锁定项之和（不含当前项）
  const otherLockedSum = schema
    .filter((it, i) => i !== idx && lockedMap[it.key])
    .reduce((s, it) => s + Number(weights[it.key] || 0), 0)

  const maxV = S - otherLockedSum
  newPercent = Math.max(0, Math.min(maxV, Number(newPercent) || 0))

  // 未锁定的其它项（参与联动）
  const unlockedOthers = schema.filter((it, i) => i !== idx && !lockedMap[it.key])
  const R = unlockedOthers.reduce((s, it) => s + Number(weights[it.key] || 0), 0)
  const Rprime = S - otherLockedSum - newPercent  // 联动项需要分摊的新总额

  if (unlockedOthers.length === 0) {
    // 其它项全锁定：当前项也只能保持原值
    return
  }

  if (R > 1e-6) {
    unlockedOthers.forEach(it => {
      const v = Number(weights[it.key] || 0)
      weights[it.key] = v * Rprime / R
    })
  } else {
    // 其它未锁定项原值都是 0：均分剩余额度
    const each = Rprime / unlockedOthers.length
    unlockedOthers.forEach(it => {
      weights[it.key] = each
    })
  }
  weights[cur.key] = newPercent
}

function resetWeights() {
  applyDefaultWeights()
}

// 提交给后端的权重 payload：以 0~1 小数形式（与原硬编码一致）。
function buildWeightsPayload() {
  const schema = currentWeightSchema.value
  if (!schema.length) return null
  const out = {}
  schema.forEach(it => {
    out[it.key] = Number(weights[it.key] || 0) / 100
  })
  return out
}

// 保存到工艺设计数据库
const saving = ref(false)
const previewing = ref(false)
const taperThick = ref(false)
const modifiedWtAreaCheckEnabled = ref(true)
const areaCheckToggleLoading = ref(false)
const previewSections = ref([])
const modifiedWtData = ref([])
const previewSegments2 = ref({})
const allDataRows = ref([])
const wtPage = ref(1)
const wtPageSize = ref(100)
const saveForm = reactive({
  display_name: '',
  vehicle_spec: '',
  tube_number: '',
})
const recommendInfo = ref(null)
const recommendedMethod = ref('')

const normalDrawDn = computed(() => {
  const fromAll = allDataNum('Dn', NaN)
  if (Number.isFinite(fromAll)) return fromAll
  const fromSol = Number(selectedSolution.value?.Dn)
  return Number.isFinite(fromSol) ? fromSol : '-'
})

const modifiedWtView = computed(() => {
  const start = (wtPage.value - 1) * wtPageSize.value
  return modifiedWtData.value.slice(start, start + wtPageSize.value)
})

function allDataNum(key, fallback = 0) {
  const row = allDataRows.value.find(r => r['参数'] === key)
  const v = row?.['值']
  if (v === '' || v == null) return fallback
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

const modifiedWtChartRef = ref(null)
let modifiedWtChart = null

const modifiedWtCurvePoints = computed(() => {
  return (modifiedWtData.value || [])
    .map(row => ({
      x: Number(row['中心线长度(mm)']),
      y: Number(row['修改后壁厚(mm)']),
    }))
    .filter(point => Number.isFinite(point.x) && Number.isFinite(point.y))
})

const modifiedWtSegments = computed(() => {
  const order = [
    { lengthKey: 'TL1', thicknessKey: 'T1', transition: false },
    { lengthKey: 'TLM1', thicknessKey: '', transition: true },
    { lengthKey: 'TL2', thicknessKey: 'T2', transition: false },
    { lengthKey: 'TLM2', thicknessKey: '', transition: true },
    { lengthKey: 'TL3', thicknessKey: 'T3', transition: false },
    { lengthKey: 'TLM3', thicknessKey: '', transition: true },
    { lengthKey: 'TL4', thicknessKey: 'T4', transition: false },
  ]
  const spans = []
  let cursor = modifiedWtCurvePoints.value[0]?.x || 0
  for (const item of order) {
    const length = allDataNum(item.lengthKey, NaN)
    if (!Number.isFinite(length) || length <= 0) continue
    const thickness = item.thicknessKey ? allDataNum(item.thicknessKey, NaN) : NaN
    spans.push({
      ...item,
      start: cursor,
      end: cursor + length,
      length,
      thickness: Number.isFinite(thickness) ? thickness : null,
    })
    cursor += length
  }
  return spans
})

function formatChartValue(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return String(Math.round(number * 100) / 100)
}

function modifiedWtAxisLimits(values) {
  const nums = values.filter(value => Number.isFinite(value))
  if (!nums.length) return null
  const low = Math.min(...nums)
  const high = Math.max(...nums)
  const span = high - low
  const pad = span > 1e-9 ? span / 3 : Math.max(Math.abs(low) * 0.2, 0.2)
  return { min: low - pad, max: high + pad }
}

function renderModifiedWtChart() {
  if (modifiedWtChart) {
    modifiedWtChart.destroy()
    modifiedWtChart = null
  }
  const points = modifiedWtCurvePoints.value
  const canvas = modifiedWtChartRef.value
  if (!canvas || !points.length) return
  const segments = modifiedWtSegments.value
  const yValues = points.map(point => point.y)
  segments.forEach(span => {
    if (span.thickness != null) yValues.push(span.thickness)
  })
  const yAxis = modifiedWtAxisLimits(yValues)
  const xStart = Math.min(points[0].x, ...segments.map(span => span.start))
  const xEnd = Math.max(points[points.length - 1].x, ...segments.map(span => span.end), xStart)
  const labelPlugin = {
    id: 'modifiedWtSegmentLabels',
    afterDatasetsDraw(chart) {
      const { ctx, chartArea, scales } = chart
      const xScale = scales.x
      const yScale = scales.y
      if (!xScale || !yScale) return
      ctx.save()
      ctx.textAlign = 'center'
      ctx.font = 'bold 14px sans-serif'
      segments.forEach(span => {
        const midX = xScale.getPixelForValue((span.start + span.end) / 2)
        if (midX < chartArea.left + 8 || midX > chartArea.right - 8) return
        const boundary = xScale.getPixelForValue(span.end)
        if (boundary >= chartArea.left && boundary <= chartArea.right) {
          ctx.strokeStyle = '#dcdfe6'
          ctx.beginPath()
          ctx.moveTo(boundary, chartArea.top)
          ctx.lineTo(boundary, chartArea.bottom)
          ctx.stroke()
        }
        const yValue = span.transition
          ? curveThicknessAt(points, (span.start + span.end) / 2)
          : (span.thickness ?? curveThicknessAt(points, (span.start + span.end) / 2))
        const y = Math.max(chartArea.top + 36, yScale.getPixelForValue(yValue) - 8)
        ctx.fillStyle = span.transition ? '#606266' : '#303133'
        if (span.transition) {
          ctx.fillText(`${span.lengthKey}=${formatChartValue(span.length)}`, midX, y)
          return
        }
        ctx.fillText(`${span.thicknessKey}=${formatChartValue(span.thickness)}`, midX, y - 18)
        ctx.fillText(`${span.lengthKey}=${formatChartValue(span.length)}`, midX, y)
      })
      ctx.restore()
    },
  }
  modifiedWtChart = new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [{
        data: points,
        borderColor: '#28b45a',
        backgroundColor: 'rgba(40,180,90,0.1)',
        borderWidth: 2,
        pointRadius: 0,
        tension: 0,
        fill: true,
      }],
    },
    plugins: [labelPlugin],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          type: 'linear',
          min: xStart,
          max: xEnd,
          title: { display: true, text: '中心线长度 (mm)', color: '#909399' },
          ticks: { color: '#909399' },
          grid: { color: '#e4e7ed' },
        },
        y: {
          min: yAxis?.min,
          max: yAxis?.max,
          title: { display: true, text: '修改后壁厚 (mm)', color: '#909399' },
          ticks: { color: '#909399', callback: value => Number(value).toFixed(1) },
          grid: { color: '#e4e7ed' },
        },
      },
    },
  })
}

function curveThicknessAt(points, x) {
  if (!points.length) return 0
  if (x <= points[0].x) return points[0].y
  const last = points[points.length - 1]
  if (x >= last.x) return last.y
  for (let index = 1; index < points.length; index += 1) {
    const right = points[index]
    const left = points[index - 1]
    if (x <= right.x) {
      const span = right.x - left.x
      const ratio = span > 0 ? (x - left.x) / span : 0
      return left.y + ratio * (right.y - left.y)
    }
  }
  return last.y
}

function ringCrossSectionArea(d, t) {
  if (d <= 0 || t <= 0) return 0
  const r = d / 2
  return Math.PI * (r * r - Math.max(r - t, 0) ** 2)
}

function resolveSolutionParam(key, sol = {}) {
  const fromAll = allDataNum(key, NaN)
  if (Number.isFinite(fromAll) && fromAll > 0) return fromAll
  const fromSol = Number(sol[key])
  return Number.isFinite(fromSol) && fromSol > 0 ? fromSol : 0
}

const areaCheckContext = computed(() => {
  const sol = selectedSolution.value || {}
  const dn = resolveSolutionParam('Dn', sol)
  const ln = resolveSolutionParam('Ln', sol)
  return { dn, ln, ready: dn > 0 && ln > 0 }
})

const showAreaCheckSection = computed(() => !!selectedSolution.value && areaCheckContext.value.ready)

const areaCheckBase = computed(() => {
  const sol = selectedSolution.value || {}
  return {
    d0: allDataNum('D0', Number(sol.D0 || 0)),
    t0: allDataNum('T0', Number(sol.T0 || 0)),
    dn: areaCheckContext.value.dn,
    ln: areaCheckContext.value.ln,
  }
})

const modifiedWtAreaChecks = computed(() => {
  if (!areaCheckContext.value.ready || !modifiedWtAreaCheckEnabled.value) return []
  const { d0, t0, dn } = areaCheckBase.value
  const seg = previewSegments2.value || {}
  const sol = selectedSolution.value || {}
  const area0 = ringCrossSectionArea(d0, t0)
  if (area0 <= 0) {
    return [{ level: 'info', text: '- 无法验算：素材截面积无效（请检查 D0、T0）' }]
  }
  const pickT = (key) => {
    const fromAll = allDataNum(key, NaN)
    if (Number.isFinite(fromAll) && fromAll > 0) return fromAll
    const fromSeg = Number(seg[key])
    if (Number.isFinite(fromSeg) && fromSeg > 0) return fromSeg
    const fromSol = Number(sol[key])
    if (Number.isFinite(fromSol) && fromSol > 0) return fromSol
    return 0
  }
  const checks = []
  ;['T1', 'T2', 'T3'].forEach((tk) => {
    const t = pickT(tk)
    if (t <= 0) {
      checks.push({ key: tk, level: 'info', text: `- ${tk}：无有效修改后壁厚，跳过验算` })
      return
    }
    const rate = ((area0 - ringCrossSectionArea(dn, t)) / area0) * 100
    let level = 'success'
    let text = `✅ ${tk} 修改后壁厚截面积变化率正常 (${rate.toFixed(2)}%，理想 20% ~ 50%)`
    if (rate < 20) {
      level = 'warning'
      text = `⚠️ ${tk} 修改后壁厚截面积变化率 (${rate.toFixed(2)}%) 小于20%，成型可能不稳定`
    } else if (rate > 50) {
      level = 'warning'
      text = `⚠️ ${tk} 修改后壁厚截面积变化率 (${rate.toFixed(2)}%) 大于50%，成型难度较大`
    }
    checks.push({ key: tk, level, text, rate: Number(rate.toFixed(2)) })
  })
  return checks
})

function evalType(level) {
  if (level === 'error') return 'error'
  if (level === 'warning') return 'warning'
  if (level === 'success') return 'success'
  return 'info'
}

const methodMeta = computed(() => (
  METHOD_META[confirmedMethod.value] || { ga: false, useAdd1Add2: false, useAdd3: false, threshold: 0.74, editable: [] }
))
const confirmedRouteLabel = computed(() => {
  if (!confirmedMethod.value) return '-'
  return PROCESS_KEY_TO_METHOD[confirmedMethod.value] || confirmedMethod.value
})
const feasibleThreshold = computed(() => Number(methodMeta.value.threshold || 0.74))
const displayIsFeasible = computed(() => solutions.value.some(s => Number(s?.fitness || 0) >= feasibleThreshold.value - 1e-6))
const editableKeys = computed(() => methodMeta.value.editable || [])

const selectedSolution = computed(() => {
  if (!solutions.value.length) return null
  return solutions.value[selectedSolutionIdx.value] || solutions.value[0]
})

const showFallback1 = computed(() => (
  confirmedMethod.value === 'tp+taper+flaring' && solutions.value.length > 0 && !displayIsFeasible.value
))
const showFallback2 = computed(() => (
  confirmedMethod.value === 'normal+tp+taper+flaring' && solutions.value.length > 0 && !displayIsFeasible.value
))
const showFallbackButtons = computed(() => showFallback1.value || showFallback2.value)

// 自由组合相关
const PROCESS_ORDER = ['normal', 'tp', 'taper', 'flaring']
const PROCESS_NAMES = { normal: '普通抽', tp: 'TP抽', taper: 'Taper', flaring: '扩口' }

const freeStepsLabel = computed(() => {
  return freeSteps.value.map(k => PROCESS_NAMES[k] || k).join(' + ')
})
const freeStepsKey = computed(() => freeSteps.value.join('+'))
const freeStepsAlgoLabel = computed(() => {
  const key = freeStepsKey.value
  const meta = METHOD_META[key]
  return meta?.ga ? 'GA 遗传算法' : '暴力枚举'
})
const freeStepsAlgoClass = computed(() => {
  const key = freeStepsKey.value
  const meta = METHOD_META[key]
  return meta?.ga ? 'ga' : 'brute'
})

function toggleFreeStep(key) {
  const idx = freeSteps.value.indexOf(key)
  if (idx >= 0) {
    freeSteps.value.splice(idx, 1)
  } else {
    // 按标准顺序插入
    const order = PROCESS_ORDER.indexOf(key)
    let insertBefore = freeSteps.value.find(k => PROCESS_ORDER.indexOf(k) > order)
    if (insertBefore) {
      freeSteps.value.splice(freeSteps.value.indexOf(insertBefore), 0, key)
    } else {
      freeSteps.value.push(key)
    }
  }
}

function confirmFreeRoute() {
  const key = freeStepsKey.value
  const meta = METHOD_META[key]
  if (!meta) {
    ElMessage.warning('当前工艺组合暂不支持')
    return
  }
  confirmedMethod.value = key
  confirmedSteps.value = [...freeSteps.value]
  resetOptimizationState()
  pushHistory({ key, label: PROCESS_KEY_TO_METHOD[key] || freeStepsLabel.value })
  ElMessage.success(`已确认工艺路线：${freeStepsLabel.value}`)
}

const fitnessChart = computed(() => {
  const best = fitnessHistory.value.best || []
  const avg = fitnessHistory.value.average || []
  if (!best.length) return null
  return {
    data: [
      { y: best, type: 'scatter', mode: 'lines', name: '最佳适应度', line: { color: '#409EFF' } },
      { y: avg, type: 'scatter', mode: 'lines', name: '平均适应度', line: { color: '#E6A23C' } },
    ],
    layout: {
      title: '适应度变化曲线',
      xaxis: { title: '迭代代数' },
      yaxis: { title: '适应度' },
      height: 280,
      margin: { t: 30, b: 40, l: 60, r: 20 },
    },
  }
})

function resetRoute() {
  draftSteps.value = []
  confirmedSteps.value = []
  confirmedMethod.value = ''
  resetOptimizationState()
}

function pushHistory(method) {
  const now = new Date()
  const time = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`
  const key = typeof method === 'string' ? method : method.key
  const label = typeof method === 'string' ? (PROCESS_KEY_TO_METHOD[method] || method) : method.label
  const item = { key, label, steps: METHOD_TO_PROCESS[key] || [], time }
  history.value = [item, ...history.value.filter(h => h.key !== key)].slice(0, 10)
}

function loadHistory(h) {
  confirmedMethod.value = h.key || h.label
  confirmedSteps.value = [...(h.steps || [])]
  resetOptimizationState()
  showHistory.value = false
}

function resetOptimizationState() {
  solutions.value = []
  selectedSolutionIdx.value = 0
  fitnessHistory.value = { best: [], average: [] }
  progressPct.value = 0
  progressMessage.value = ''
  warningText.value = ''
  isFeasible.value = false
  status.value = ''
  taskId.value = null
  editing.value = false
  applyDefaultWeights()
}

async function loadRecommend() {
  if (!props.sessionId) return
  try {
    const res = await ppApi.optimizeRecommend(props.sessionId)
    if (res.data.success) {
      recommendInfo.value = res.data
      recommendedMethod.value = res.data.recommended || ''
    } else {
      console.warn('[推荐] 接口返回失败:', res.data.message)
      recommendInfo.value = null
    }
  } catch (e) {
    console.error('[推荐] 请求失败:', e)
    recommendInfo.value = null
  }
}

// 保留旧函数兼容性（历史记录可能还在用）
function toggleStep(key) {
  const idx = draftSteps.value.indexOf(key)
  if (idx >= 0) draftSteps.value.splice(idx, 1)
  else draftSteps.value.push(key)
}
function normalizeProcessKey(steps) {
  const PROCESS_ORDER = ['normal', 'tp', 'taper', 'flaring']
  return PROCESS_ORDER.filter(k => (steps || []).includes(k)).join('+')
}
function confirmRoute() {
  const key = normalizeProcessKey(draftSteps.value)
  const label = PROCESS_KEY_TO_METHOD[key] || ''
  if (!label) {
    ElMessage.warning('当前组合暂不支持，请重新选择')
    return
  }
  confirmedSteps.value = [...draftSteps.value].sort(
    (a, b) => normalizeProcessKey([a, b]) ? 0 : 0
  )
  confirmedMethod.value = key
  resetOptimizationState()
  pushHistory({ key, label })
  ElMessage.success(`已确认工艺路线：${label}`)
}
function applyRecommend() {
  if (!recommendedMethod.value) return
  selectMethod(recommendedMethod.value)
}

function hasKey(obj, key) {
  return !!obj && Object.prototype.hasOwnProperty.call(obj, key)
}
function canEdit(key) {
  return editing.value && editableKeys.value.includes(key) && hasKey(selectedSolution.value, key)
}
function normalizeSolutions(items = []) {
  return [...items].sort((a, b) => Number(b?.fitness || -Infinity) - Number(a?.fitness || -Infinity))
}
function formatAreaChange(v) {
  if (v == null || v === '') return '-'
  if (typeof v === 'number') return `${Number(v).toFixed(2)}%`
  if (typeof v !== 'object') return String(v)
  const labelMap = {
    area_change: '变化率',
    area_change1: 'T1',
    area_change2: 'T2',
    area_change3: 'T3',
  }
  const parts = Object.entries(v)
    .filter(([, x]) => x != null && x !== '')
    .map(([k, x]) => `${labelMap[k] || k}: ${Number(x).toFixed(2)}%`)
  return parts.length ? parts.join(' / ') : '-'
}
function formatExt(v) {
  if (!v && v !== 0) return '-'
  if (typeof v === 'number') return `${v.toFixed(2)}%`
  return Object.entries(v).map(([k, x]) => `${k}:${Number(x).toFixed(2)}%`).join(' / ')
}

function sectionItemLabel(item) {
  if (item?.key === 'Ln') return '裁剪前长度Ln(mm)'
  return item?.label || ''
}

function sectionItems(sec) {
  const items = sec?.items || []
  if (sec?.key !== 'normal') return items
  const row = allDataRows.value.find(r => r['参数'] === 'LL')
  const value = row && row['值'] !== '' && row['值'] != null ? row['值'] : '-'
  return [...items, { key: 'LL', label: '裁剪后长度LL', value, editable: false }]
}

function syncEditModel() {
  Object.keys(editModel).forEach(k => delete editModel[k])
  if (!selectedSolution.value) return
  ;['D0', 'D2', 'D20', 'T0', 'L0', 'L1', 'L2', 'L3', 'L4', 'Ln'].forEach(k => {
    if (hasKey(selectedSolution.value, k) && editableKeys.value.includes(k)) {
      editModel[k] = Number(selectedSolution.value[k])
    }
  })
  // 同步分段详情中所有可编辑的字段
  ;(previewSections.value || []).forEach(sec => {
    (sec.items || []).forEach(it => {
      if (it.editable && hasKey(selectedSolution.value, it.key)) {
        editModel[it.key] = Number(selectedSolution.value[it.key])
      }
    })
  })
}

function toggleEdit() {
  editing.value = !editing.value
  if (editing.value) syncEditModel()
}
function resetEdit() {
  syncEditModel()
}

async function startOptimization(forceMethod = '') {
  if (!props.sessionId) {
    ElMessage.warning('请先加载数据库中的分析记录')
    return
  }
  const method = (typeof forceMethod === 'string' && forceMethod) ? forceMethod : confirmedMethod.value
  if (!method) {
    ElMessage.warning('请先确认工艺路线')
    return
  }
  const activeMethod = forceMethod || confirmedMethod.value
  if (forceMethod && forceMethod !== confirmedMethod.value) {
    confirmedMethod.value = forceMethod
    confirmedSteps.value = METHOD_TO_PROCESS[forceMethod] || confirmedSteps.value
    draftSteps.value = [...confirmedSteps.value]
  }
  optimizing.value = true
  progressPct.value = 0
  progressMessage.value = '启动优化...'
  warningText.value = ''
  try {
    const res = await ppApi.optimize({
      session_id: props.sessionId,
      pp_method: toBackendMethod(activeMethod),
      population_size: 300,
      generations: 200,
      repetitions: 1,
      d_precision: Number(configForm.dPrecision),
      t_precision: Number(configForm.tPrecision),
      l_precision: Number(configForm.lPrecision),
      add1: Number(configForm.add1),
      add2: Number(configForm.add2),
      add3: Number(configForm.add3),
      material_allowance: Number(confirmedAllowance.value),
      weights: buildWeightsPayload(),
    })
    if (!res.data.success) {
      ElMessage.error(res.data.message || '优化启动失败')
      optimizing.value = false
      return
    }
    taskId.value = res.data.task_id
    await pollOptimizeStatus()
  } catch (e) {
    optimizing.value = false
    ElMessage.error(e.response?.data?.message || '优化启动失败')
  }
}

async function pollOptimizeStatus() {
  if (!taskId.value) return
  const poll = async (retryLeft = 2) => {
    try {
      const res = await ppApi.optimizeStatus(taskId.value)
      if (!res.data.success) return
      const p = res.data.progress
      progressPct.value = p.pct || 0
      progressMessage.value = p.message || ''
      if (p.done) {
        optimizing.value = false
        if (p.error) {
          warningText.value = p.error
          ElMessage.error(`优化失败：${p.error}`)
          return
        }
        const rr = await ppApi.optimizeResult(taskId.value)
        if (rr.data.success && rr.data.result) {
          const r = rr.data.result
          solutions.value = normalizeSolutions(r.solutions || [])
          selectedSolutionIdx.value = 0
          status.value = r.status || ''
          isFeasible.value = !!r.is_feasible
          warningText.value = r.warning || r.error || ''
          fitnessHistory.value = r.fitness_history || { best: [], average: [] }
          editing.value = false
          if (solutions.value.length) {
            ElMessage.success(`优化完成：${solutions.value.length} 个方案`)
          } else if (r.error) {
            ElMessage.error(`优化失败：${r.error}`)
          } else {
            ElMessage.warning(r.warning || '未生成有效方案，请检查输入数据')
          }
        } else {
          ElMessage.error(rr.data.message || '获取优化结果失败')
        }
        return
      }
      setTimeout(() => poll(2), 1000)
    } catch (e) {
      // 常见：后端线程刚创建任务/短暂卡顿/网络抖动导致 status 404/timeout。
      // 给少量重试，并把真实错误信息展示出来，避免“偶发且无法定位”。
      const status = e?.response?.status
      const msg = e?.response?.data?.message || e?.message || '轮询优化状态失败'
      if (retryLeft > 0) {
        setTimeout(() => poll(retryLeft - 1), 800)
        return
      }
      optimizing.value = false
      ElMessage.error(status ? `轮询优化状态失败(${status})：${msg}` : `轮询优化状态失败：${msg}`)
    }
  }
  setTimeout(() => poll(2), 1000)
}

async function applyRecalc() {
  if (!selectedSolution.value || !editing.value) return
  recalcing.value = true
  try {
    const params = {}
    ;['D0', 'D2', 'D20', 'Dn', 'T0'].forEach(k => {
      if (hasKey(selectedSolution.value, k) && selectedSolution.value[k] != null && selectedSolution.value[k] !== '') {
        params[k] = Number(selectedSolution.value[k])
      }
    })
    Object.assign(params, editModel)
    const res = await ppApi.optimizeRecalc({
      session_id: props.sessionId,
      pp_method: toBackendMethod(confirmedMethod.value),
      params,
      d_precision: Number(configForm.dPrecision),
      t_precision: Number(configForm.tPrecision),
      l_precision: Number(configForm.lPrecision),
      add1: Number(configForm.add1),
      add2: Number(configForm.add2),
      add3: Number(configForm.add3),
      material_allowance: Number(confirmedAllowance.value),
      weights: buildWeightsPayload(),
    })
    if (!res.data.success) {
      ElMessage.error(res.data.message || '重算失败')
      return
    }
    const target = solutions.value[selectedSolutionIdx.value]
    if (target) {
      Object.assign(target, editModel)
      Object.assign(target, res.data.updates || {})
      target.fitness = res.data.fitness
      target.is_feasible = Number(res.data.fitness || 0) >= feasibleThreshold.value - 1e-6
      isFeasible.value = solutions.value.some(s => Number(s.fitness || 0) >= feasibleThreshold.value - 1e-6)
      if (displayIsFeasible.value) warningText.value = ''
    }
    editing.value = false
    ElMessage.success('参数已应用并重算')
    refreshPreview()
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '重算失败')
  } finally {
    recalcing.value = false
  }
}

async function loadDesignFeatureFlags() {
  try {
    const res = await ppApi.designFeatureFlagsGet()
    if (res.data.success && res.data.flags) {
      modifiedWtAreaCheckEnabled.value = res.data.flags.modified_wt_area_check !== false
    }
  } catch (_) {
    /* 保持默认开启 */
  }
}

async function onModifiedWtAreaCheckToggle(enabled) {
  areaCheckToggleLoading.value = true
  try {
    const res = await ppApi.designFeatureFlagsSet({ modified_wt_area_check: enabled })
    if (res.data.success && res.data.flags) {
      modifiedWtAreaCheckEnabled.value = !!res.data.flags.modified_wt_area_check
      if (enabled && selectedSolution.value && confirmedMethod.value) {
        await refreshPreview()
      }
    } else {
      modifiedWtAreaCheckEnabled.value = !enabled
      ElMessage.warning(res.data.message || '设置失败')
    }
  } catch (e) {
    modifiedWtAreaCheckEnabled.value = !enabled
    ElMessage.error(e.response?.data?.message || '设置失败')
  } finally {
    areaCheckToggleLoading.value = false
  }
}

async function refreshPreview() {
  if (!props.sessionId || !selectedSolution.value || !confirmedMethod.value) return
  previewing.value = true
  try {
    const merged = { ...selectedSolution.value }
    // 把当前编辑模式下的临时值带上
    if (editing.value) Object.assign(merged, editModel)
    const res = await ppApi.designPreview({
      session_id: props.sessionId,
      pp_method: toBackendMethod(confirmedMethod.value),
      taper_thick: taperThick.value,
      solution: merged,
    })
    if (res.data.success) {
      const updates = res.data.solution_updates
      if (updates && selectedSolution.value) {
        Object.assign(selectedSolution.value, updates)
        if (editing.value) Object.assign(editModel, updates)
      }
      previewSections.value = res.data.sections || []
      modifiedWtData.value = res.data.modified_wt_data || []
      previewSegments2.value = res.data.segments2 || {}
      allDataRows.value = res.data.all_data || []
      if (res.data.modified_wt_area_check_enabled !== undefined) {
        modifiedWtAreaCheckEnabled.value = !!res.data.modified_wt_area_check_enabled
      }
      wtPage.value = 1
    } else {
      ElMessage.warning(res.data.message || '刷新表格失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '刷新表格失败')
  } finally {
    previewing.value = false
  }
}

async function confirmSaveDesign() {
  if (!props.sessionId) {
    ElMessage.warning('当前没有有效会话')
    return
  }
  if (!selectedSolution.value) {
    ElMessage.warning('当前没有可保存的方案')
    return
  }
  if (!confirmedMethod.value) {
    ElMessage.warning('请先确认工艺路线')
    return
  }
  const displayName = saveForm.display_name.trim()
  const vehicleSpec = saveForm.vehicle_spec.trim()
  const tubeNumber = saveForm.tube_number.trim()
  if (!displayName) {
    ElMessage.warning('请先输入设计名称')
    return
  }
  if (!vehicleSpec) {
    ElMessage.warning('请先输入车种号')
    return
  }
  if (!tubeNumber) {
    ElMessage.warning('请先输入管件号')
    return
  }
  saving.value = true
  try {
    const res = await ppApi.saveDesign({
      session_id: props.sessionId,
      pp_method: toBackendMethod(confirmedMethod.value),
      display_name: displayName,
      vehicle_spec: vehicleSpec,
      tube_number: tubeNumber,
      taper_thick: taperThick.value,
      solution: { ...selectedSolution.value },
      all_data: allDataRows.value,
      modified_wt_data: modifiedWtData.value,
    })
    if (res.data.success) {
      ElMessage.success(res.data.message || '工艺设计已保存')
    } else {
      ElMessage.error(res.data.message || '保存失败')
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

watch(selectedSolutionIdx, () => {
  if (editing.value) syncEditModel()
  refreshPreview()
})

watch(confirmedMethod, () => {
  if (solutions.value.length > 0) refreshPreview()
})

watch(() => solutions.value.length, (n) => {
  if (n > 0) refreshPreview()
})

watch([modifiedWtCurvePoints, modifiedWtSegments], () => {
  nextTick(() => renderModifiedWtChart())
})

onMounted(() => {
  loadDesignFeatureFlags()
  if (props.sessionId) loadRecommend()
})

onBeforeUnmount(() => {
  if (modifiedWtChart) modifiedWtChart.destroy()
})

watch(() => props.sessionId, (sid) => {
  if (!sid) {
    resetOptimizationState()
    previewSections.value = []
    previewSegments2.value = {}
    modifiedWtData.value = []
    allDataRows.value = []
    recommendInfo.value = null
  } else {
    loadRecommend()
  }
})
</script>

<style scoped>
.tab3-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
}

.route-card, .mode-card, .progress-card, .results-card {
  max-width: 1200px;
}

.page-title {
  font-size: 28px;
  font-weight: 700;
  margin: 0 0 16px 0;
  color: #1f2329;
}
.mode-title {
  font-size: 22px;
  margin-bottom: 18px;
}

.section-subtitle {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 10px 0;
  color: #1f2329;
}

/* 工艺方法选择 - 卡片式 */
.method-group {
  margin-bottom: 20px;
}
.group-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.group-tag {
  font-size: 13px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}
.group-tag.ga {
  background: #e6f0ff;
  color: #1d5fd4;
}
.group-tag.brute {
  background: #fff3e0;
  color: #b35a00;
}
.group-tag.custom {
  background: #f0f9e8;
  color: #3d6b1e;
}
.group-desc {
  font-size: 13px;
  color: #909399;
}

.method-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  margin-bottom: 4px;
}

.method-card {
  border: 1.5px solid #e4e7ed;
  border-radius: 8px;
  padding: 12px 14px;
  cursor: pointer;
  transition: all 0.15s ease;
  background: #fff;
}
.method-card:hover {
  border-color: #409EFF;
  box-shadow: 0 2px 8px rgba(64,158,255,0.12);
}
.method-card.selected {
  border-color: #409EFF;
  background: #f0f7ff;
  box-shadow: 0 2px 8px rgba(64,158,255,0.2);
}
.method-card.recommended .card-title {
  color: #1d5fd4;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.rec-badge {
  font-size: 11px;
  background: #ff6b35;
  color: #fff;
  padding: 1px 6px;
  border-radius: 10px;
  font-weight: 700;
}

.card-steps {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.step-tag {
  font-size: 12px;
  background: #f0f2f5;
  color: #606266;
  padding: 2px 8px;
  border-radius: 4px;
}

.card-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.card-condition {
  font-size: 11px;
  color: #c0c4cc;
}

/* 选中栏 */
.selection-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: #f5f7fa;
  border-radius: 6px;
  margin-top: 12px;
  flex-wrap: wrap;
  gap: 8px;
}
.confirmed-display {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
}
.confirmed-label {
  font-weight: 700;
  color: #303133;
}
.algo-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 700;
}
.algo-tag.ga {
  background: #e6f0ff;
  color: #1d5fd4;
}
.algo-tag.brute {
  background: #fff3e0;
  color: #b35a00;
}
.selection-hint {
  color: #909399;
  font-size: 14px;
}
.selection-actions {
  display: flex;
  gap: 8px;
}

.history-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.history-algo {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 700;
}
.history-algo.ga {
  background: #e6f0ff;
  color: #1d5fd4;
}
.history-algo.brute {
  background: #fff3e0;
  color: #b35a00;
}

.route-actions {
  display: none;  /* 旧版按钮隐藏，改为卡片直接选择 */
}

.step-buttons {
  display: none;  /* 旧版按钮隐藏 */
}
.step-button {
  display: none;
}
.route-banner {
  display: none;
}
.route-section {
  margin: 0;
}
.section-subtitle {
  display: none;
}

.recommend-banner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  background: linear-gradient(135deg, #e6f7ff 0%, #bae0ff 100%);
  border: 1px solid #91caff;
  border-radius: 8px;
  margin-bottom: 16px;
}
.recommend-icon {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #1890ff;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  flex-shrink: 0;
}
.recommend-body {
  flex: 1;
}
.recommend-reason {
  font-size: 14px;
  color: #096dd9;
  line-height: 1.6;
  margin-bottom: 4px;
}
.recommend-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: #1890ff;
}

.param-block {
  margin-bottom: 22px;
}
.block-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 6px 0;
  color: #1f2329;
}
.block-desc {
  font-size: 13px;
  color: #606266;
  margin-bottom: 10px;
}
.param-row {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.param-item {
  min-width: 220px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.param-label {
  font-size: 13px;
  color: #606266;
}

/* 优化目标权重 */
.weight-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: #fafcff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px 16px;
}
.weight-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
}
.weight-row.is-locked {
  background: #fff8e6;
  border-radius: 4px;
  margin: 0 -8px;
  padding-left: 8px;
  padding-right: 8px;
}
.weight-lock-btn {
  flex-shrink: 0;
}
.weight-meta {
  width: 180px;
  flex-shrink: 0;
}
.weight-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.weight-row.is-locked .weight-name {
  color: #b35a00;
}
.weight-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.weight-slider {
  flex: 1;
  margin: 0 8px;
  min-width: 200px;
}
.weight-input {
  width: 110px;
  flex-shrink: 0;
}
.weight-percent-sign {
  font-size: 14px;
  color: #606266;
  width: 14px;
  text-align: left;
}
.weight-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  margin-top: 10px;
}
.weight-sum-tip {
  font-size: 13px;
  color: #606266;
  font-variant-numeric: tabular-nums;
}
.hint-icon {
  font-size: 13px;
  vertical-align: middle;
  color: #1d5fd4;
}

.run-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 8px;
}
.hint {
  color: #909399;
  font-size: 13px;
}

.warn-line {
  margin-top: 12px;
}
.fallback-buttons {
  margin-top: 12px;
  display: flex;
  gap: 8px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.solution-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.solution-detail {
  padding: 6px 0;
}
.chart-container {
  margin-top: 16px;
  background: #fafafa;
  border-radius: 4px;
  padding: 12px;
}
.modified-wt-chart-wrap {
  margin-top: 4px;
}
.modified-wt-chart {
  height: 360px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 8px 8px 4px;
}

/* 分段工艺参数详情 */
.seg-section {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafcff;
}
.seg-title {
  font-weight: 600;
  font-size: 15px;
  color: #1f2329;
  margin-bottom: 8px;
}
.seg-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 0;
}
.seg-label {
  font-size: 12px;
  color: #606266;
}
.seg-value {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}
.seg-evals {
  margin-top: 8px;
}

/* 保存设计元数据 */
.save-meta {
  padding: 8px 0;
}
.save-meta-row {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  align-items: flex-end;
}
.meta-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.meta-label {
  font-size: 13px;
  color: #606266;
}

/* 壁厚/总参数表 */
.wt-hint {
  font-size: 12px;
  color: #909399;
  margin: 4px 0 8px 0;
  line-height: 1.6;
}
.wt-pagination {
  margin-top: 6px;
  display: flex;
  justify-content: flex-end;
}
.wt-area-checks {
  margin-top: 16px;
}
.wt-area-checks-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.wt-area-checks-header .block-title {
  margin: 0;
}
.wt-area-check-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
}

.save-actions {
  margin-top: 20px;
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: flex-end;
}

/* 自由组合 */
.custom-steps-grid {
  background: #f5f9ff;
  border: 1px dashed #bfd9f8;
  border-radius: 8px;
  padding: 16px 20px;
}
.custom-steps-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.step-option {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 72px;
  padding: 6px 14px;
  border: 2px solid #d9d9d9;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  color: #606266;
  background: #fff;
  transition: all 0.15s;
  user-select: none;
}
.step-option:hover {
  border-color: #409EFF;
  color: #409EFF;
  background: #ecf5ff;
}
.step-option.active {
  border-color: #409EFF;
  color: #fff;
  background: #409EFF;
  font-weight: 600;
}
.step-connector {
  color: #c0c4cc;
  font-size: 16px;
  user-select: none;
}
.custom-preview {
  font-size: 13px;
  color: #606266;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.preview-text {
  font-weight: 600;
  color: #303133;
}
.custom-actions {
  display: flex;
  gap: 8px;
}
</style>
