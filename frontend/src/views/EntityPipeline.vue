<template>
  <div class="page-container entity-pipeline">
    <div class="page-head">
      <div>
        <div class="page-title">一体化流水线</div>
        <p class="page-desc">
          选一个或多个管件，从头到尾看一遍默认流程：实测型线 → 工艺路线 → 派生物 → 4 面实体。
          多件会排进同一个批量任务逐件跑；运行只补缺失的格子（已有产物按缓存复核，不强制重算）。
        </p>
      </div>
      <div class="head-actions">
        <el-select
          v-model="selectedParts"
          placeholder="选择管件（可多选，一次批量跑）"
          filterable
          multiple
          collapse-tags
          collapse-tags-tooltip
          clearable
          style="width: 300px"
          :disabled="running"
          @change="onPartsChange"
        >
          <el-option
            v-for="p in parts"
            :key="p.name"
            :label="partOptionLabel(p)"
            :value="p.name"
          />
        </el-select>
        <el-button :loading="loading" :disabled="running" @click="loadFlow">刷新</el-button>
        <el-button
          type="primary"
          :loading="starting"
          :disabled="!runParts.length || running"
          @click="startRun"
        >
          {{ runParts.length > 1 ? `运行（${runParts.length} 件批量）` : '运行（保留缓存，只补缺）' }}
        </el-button>
        <el-button
          v-if="running"
          type="danger"
          plain
          :loading="stopping"
          :disabled="isStopping"
          @click="stopRun('graceful')"
        >
          {{ isStopping ? '正在停止…' : '停止' }}
        </el-button>
        <el-button v-if="running" type="danger" :disabled="isStopping" @click="stopRun('force')">
          强制停止
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="loadError"
      type="error"
      :title="loadError"
      :closable="false"
      show-icon
      class="block-alert"
    />

    <el-card shadow="never" class="page-card upload-card">
      <template #header>
        <span class="panel-title">新增管件</span>
        <span class="hint">（可一次选多个 .stp/.step；同名文件覆盖旧件、件名保持一致；后台子进程测量，按本机核数自动分配、当前 {{ uploadParallel }} 路并行）</span>
      </template>
      <div class="upload-row">
        <el-upload
          ref="uploaderRef"
          class="upload-picker"
          multiple
          :auto-upload="false"
          :show-file-list="false"
          accept=".stp,.step"
          :disabled="uploading"
          :on-change="onUploadChange"
        >
          <el-button :disabled="uploading">选择 STEP 文件</el-button>
        </el-upload>
        <span class="upload-name">{{ uploadPickText }}</span>
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="!pickedFiles.length"
          @click="doUpload"
        >
          上传并测量{{ pickedFiles.length ? `（${pickedFiles.length}）` : '' }}
        </el-button>
        <el-button
          :loading="importing"
          :disabled="!doneUploadParts.length || running"
          @click="doImport"
        >
          导入主流程{{ doneUploadParts.length ? `（${doneUploadParts.length}）` : '' }}
        </el-button>
        <el-button
          :disabled="!clearableUploadCount"
          @click="clearUploadRecords"
        >
          清空记录
        </el-button>
        <span v-if="uploading" class="hint">上传中 {{ uploadPercent }}%</span>
      </div>
      <div class="hint upload-parallel-hint">
        当前并行 {{ uploadParallel }} 个（按本机核数自动分配：物理核数 ÷ 单件进程数；
        可用环境变量 GTAPP_UPLOAD_PARALLEL 覆盖，单件进程数见 GTAPP_MEASURE_WORKERS）；
        测量完点「导入主流程」写入「管件模型分析」的数据库（origin=entity）并登记主流程队列，
        运行时会直接复用缓存（「工艺优化」的加载列表不显示这一类）
        <span v-if="measurement.station_step_mm !== undefined"> · 本实例测量口径：实测 {{ measurementLabel }}</span>
        <span v-if="importedParts.length"> · 队列已有 {{ importedParts.length }} 件</span>
      </div>
      <el-table
        v-if="uploadTasks.length"
        :data="uploadTasks"
        :row-class-name="uploadRowClass"
        size="small"
        border
        stripe
        class="upload-tasks"
      >
        <el-table-column label="文件" min-width="160">
          <template #default="{ row }">
            <span class="mono">{{ row.file || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="任务" min-width="140">
          <template #default="{ row }">
            <span class="mono">{{ row.task_id || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="管件" min-width="150">
          <template #default="{ row }">{{ row.part || '—' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="uploadTagType(row.status)">{{ uploadStatusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="140">
          <template #default="{ row }">
            <el-progress
              :percentage="uploadProgressOf(row)"
              :stroke-width="8"
              :status="isUploadFailed(row.status) ? 'exception' : undefined"
            />
          </template>
        </el-table-column>
        <el-table-column label="信息" min-width="200">
          <template #default="{ row }">
            <span class="upload-task-msg" :class="{ 'is-error': isUploadFailed(row.status) }">
              {{ row.error || row.message || uploadStatusHint(row.status) || '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="center">
          <template #default="{ row }">
            <el-button size="small" text type="primary" :disabled="!row.part" @click="selectPart(row.part)">
              查看
            </el-button>
            <el-button
              v-if="row.cancellable || isUploadActive(row.status)"
              size="small"
              text
              type="danger"
              :loading="cancellingTasks.has(row.task_id)"
              @click="cancelUploadTask(row)"
            >
              {{ row.status === 'cancelling' ? '停止中' : '取消' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="hint upload-empty">还没有上传任务；上传后这里会显示测量进度。</div>
    </el-card>

    <el-row :gutter="12" class="top-row">
      <el-col :xs="24" :md="9">
        <el-card shadow="never" class="page-card">
          <template #header><span class="panel-title">输入与口径</span></template>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="管件">{{ currentPart || '—' }}</el-descriptions-item>
            <el-descriptions-item label="原始 STEP">{{ sourceName }}</el-descriptions-item>
            <el-descriptions-item label="端部口径">
              <el-tag size="small" :type="flow?.end_treatment === 'v1' ? 'warning' : 'success'">
                {{ flow?.end_treatment || '—' }}
              </el-tag>
              <span v-if="flow?.reference" class="hint">（参考件：外径形腔走口径保形）</span>
            </el-descriptions-item>
            <el-descriptions-item label="测量缓存">
              <span :class="{ missing: !flow?.measured?.exists }">{{ measuredText }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="输出目录">
              <span class="mono">{{ flow?.out_dir || '—' }}</span>
            </el-descriptions-item>
          </el-descriptions>
          <div class="report-links">
            <el-button size="small" :disabled="!flow?.report?.exists" @click="downloadEntry(flow.report)">
              下载件级报告
            </el-button>
            <el-button size="small" :disabled="!flow?.summary?.exists" @click="downloadEntry(flow.summary)">
              下载全部件汇总
            </el-button>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :md="15">
        <el-card shadow="never" class="page-card">
          <template #header>
            <span class="panel-title">默认工艺参数</span>
            <span class="hint">（页面不提供改参，按这些默认值产出）</span>
          </template>
          <div class="param-grid">
            <div class="param-block">
              <div class="param-head">测量</div>
              <div class="param-line">算法 <b>{{ defaults.measurement?.algorithm || 'A' }}</b></div>
              <div class="param-line">站位 <b>{{ defaults.measurement?.station_step_mm }} mm</b></div>
              <div class="param-line">稀疏 step <b>{{ defaults.measurement?.sparse_step_mm }} mm</b></div>
              <div class="param-line">端部口径 <b>{{ defaults.measurement?.end_treatment }}</b></div>
            </div>
            <div class="param-block">
              <div class="param-head">C1 路线（外径形腔默认）</div>
              <div class="param-line">截面采样 <b>{{ defaults.c1?.half_samples }}</b></div>
              <div class="param-line">平滑次数 <b>{{ defaults.c1?.smooth_passes }}</b></div>
              <div class="param-line">外推站位 <b>{{ defaults.c1?.station_step_mm }} mm</b></div>
              <div class="param-line">轮廓 <b>{{ defaults.c1?.contour }}</b></div>
            </div>
            <div class="param-block">
              <div class="param-head">Pipe 路线（其余四类默认）</div>
              <div class="param-line">截面采样 <b>{{ defaults.pipe?.half_points }}</b></div>
              <div class="param-line">拟合截面 <b>{{ defaults.pipe?.max_sections || 'auto' }}</b></div>
              <div class="param-line">分型方向 <b>{{ defaults.pipe?.direction }}</b></div>
              <div class="param-line">导出 STL <b>{{ defaults.pipe?.export_stl ? '是' : '否' }}</b></div>
            </div>
          </div>
          <div class="route-map">
            <span class="hint">默认路线：</span>
            <el-tag
              v-for="row in routeRows"
              :key="row.key"
              size="small"
              :type="row.route === 'c1' ? 'success' : 'primary'"
            >
              {{ row.key }} → {{ row.route }}
            </el-tag>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="page-card">
      <template #header>
        <span class="panel-title">工艺路线（默认）</span>
        <span class="hint">（与「工艺参数优化」页同一套路线目录；推荐值按本件几何自动计算）</span>
      </template>
      <template v-if="routePlan">
        <div class="route-rec">
          <el-tag type="success" effect="dark">推荐</el-tag>
          <b>{{ routePlan.recommended_label }}</b>
          <span class="hint">{{ routePlan.reason }}</span>
        </div>
        <div class="design-meta">
          <span>型线长度 {{ routePlan.line_length_mm }} mm</span>
          <span>外径变化率 {{ routePlan.d_change_pct }}%</span>
          <span>最大外径 {{ routePlan.max_diameter_mm }} mm</span>
          <span>最小外径 {{ routePlan.min_diameter_mm }} mm</span>
          <span v-if="routePlan.chosen">
            已登记：{{ routePlan.chosen }}<template v-if="!routePlan.chosen_is_recommended">（与推荐不同）</template>
          </span>
          <span v-else>尚未登记工艺设计（按推荐默认）</span>
        </div>
        <div class="design-status">
          <el-tag
            size="small"
            :type="designState.state === 'registered' ? 'success' : (designState.state === 'infeasible' ? 'warning' : 'info')"
          >
            {{ designStateText }}
          </el-tag>
          <span class="hint">{{ flow?.design_state?.message }}</span>
          <span class="hint">设计由人工判断后导入数据库；流水线只给推荐路线，不再自动补算。</span>
        </div>
        <div v-for="group in routePlan.groups" :key="group.id" class="route-group">
          <div class="route-group-title">
            {{ group.title }}<span class="hint"> · {{ group.subtitle }}</span>
          </div>
          <div class="route-group-items">
            <div
              v-for="item in group.items"
              :key="item.label"
              class="route-item"
              :class="{ 'is-recommended': item.recommended, 'is-chosen': item.chosen }"
            >
              <div class="route-item-label">{{ item.label }}</div>
              <div class="route-item-tags">
                <el-tag v-if="item.recommended" size="small" type="success">推荐</el-tag>
                <el-tag v-if="item.chosen" size="small" type="warning">已登记</el-tag>
                <el-tag v-if="item.feasible === false" size="small" type="info">默认不建议</el-tag>
              </div>
            </div>
          </div>
        </div>
        <div class="design-seg">
          <span class="hint">自由组合：</span>
          <el-tag
            v-for="stepName in routePlan.free_combo"
            :key="stepName"
            size="small"
            type="info"
            class="seg-tag"
          >
            {{ stepName }}
          </el-tag>
          <span class="hint">任意组合，可在「工艺参数优化」页手动选择</span>
        </div>
      </template>
      <el-empty v-else description="无法读取测量数据，未计算推荐路线" :image-size="60" />
    </el-card>

    <el-card shadow="never" class="page-card">
      <template #header>
        <span class="panel-title">流程阶段</span>
        <span class="hint">（上游抽管 / Taper / 扩口来自工艺设计；成型与三类工序按需走）</span>
      </template>
      <el-steps :active="routeActive" align-center finish-status="success">
        <el-step
          v-for="step in routeSteps"
          :key="step.id"
          :title="step.title"
          :description="step.subtitle"
        />
      </el-steps>
      <div class="route-detail">
        <el-tag
          v-for="step in routeSteps"
          :key="step.id"
          size="small"
          :type="step.done ? 'success' : 'info'"
        >
          {{ step.title }}{{ step.done ? ' ✓' : ' · 待生成' }} · {{ step.detail }}
        </el-tag>
      </div>
    </el-card>

    <el-card shadow="never" class="page-card">
      <template #header>
        <span class="panel-title">工艺摘要</span>
        <span class="hint">（推荐工艺路线为主 + 实测中心线 / 壁厚 / 外径 + 三类抽管默认结果）</span>
      </template>
      <el-empty v-if="!summary" description="该件尚未测量" :image-size="60" />
      <template v-else>
        <div class="summary-route-hero">
          <div class="summary-hero-head">
            <span class="summary-label">推荐工艺路线（主信息）</span>
            <el-tag v-if="summary.design_missing" size="small" type="info">未登记设计</el-tag>
          </div>
          <div class="summary-route">
            <b>{{ summary.route?.recommended || routePlan?.recommended_label || '—' }}</b>
            <el-tag v-if="summary.route?.fitness != null" size="small" type="info">
              适应度 {{ summary.route.fitness }}
            </el-tag>
          </div>
          <div class="summary-reason">{{ summary.route?.reason || '—' }}</div>
          <div class="summary-basis">
            依据：形线长度 {{ routePlan?.line_length_mm ?? '—' }} mm · 外径变化率
            {{ routePlan?.d_change_pct ?? '—' }}% · L4 {{ routePlan?.l4_mm ?? '—' }} mm
          </div>
        </div>
        <div class="summary-grid">
          <div class="summary-block">
            <div class="summary-label">中心线长度</div>
            <div class="summary-value">{{ mmText(summary.centerline?.length_mm) }}<span class="unit"> mm</span></div>
            <div class="summary-sub">{{ summary.centerline?.point_count || 0 }} 点</div>
          </div>
          <div class="summary-block">
            <div class="summary-label">壁厚 min / mean / max</div>
            <div class="summary-value">
              {{ mmText(summary.wall?.min_mm) }} / {{ mmText(summary.wall?.mean_mm) }} /
              {{ mmText(summary.wall?.max_mm) }}
            </div>
            <div class="summary-sub">mm · 中位 {{ mmText(summary.wall?.median_mm) }}</div>
          </div>
          <div class="summary-block">
            <div class="summary-label">外径 max / min</div>
            <div class="summary-value">
              {{ mmText(summary.diameter?.max_mm) }} / {{ mmText(summary.diameter?.min_mm) }}
            </div>
            <div class="summary-sub">mm · 等效最大 {{ mmText(summary.diameter?.equivalent_max_mm) }}</div>
          </div>
          <div class="summary-block">
            <div class="summary-label">D0 / T0 / L0</div>
            <div class="summary-value">
              {{ summaryParam('D0') }} / {{ summaryParam('T0') }} / {{ summaryParam('L0') }}
            </div>
            <div class="summary-sub">
              mm · 工艺设计 {{ summary.design?.id ? '#' + summary.design.id : '未登记' }}
            </div>
          </div>
          <div class="summary-block">
            <div class="summary-label">三类抽管（默认参数道次数）</div>
            <template v-if="summary.design_missing">
              <div class="summary-missing">
                未导入工艺设计：请人工判断后导入数据库，流水线只给推荐路线；本区暂无道次。
              </div>
              <div v-if="summary.drawing?.note" class="summary-sub">{{ summary.drawing.note }}</div>
            </template>
            <template v-else>
              <div class="summary-draw">
                <span v-for="row in drawingRows" :key="row.key" class="draw-item">
                  {{ row.label }} <b>{{ row.count }}</b> 道
                </span>
              </div>
              <div v-if="summary.drawing?.note" class="summary-sub">{{ summary.drawing.note }}</div>
            </template>
          </div>
        </div>

        <div class="summary-charts">
          <div class="summary-chart">
            <div class="summary-chart-head">
              <span>中心线投影 · {{ plane === 'yz' ? 'Y-Z' : 'X-Z' }}（mm）</span>
              <span class="plane-switch">
                <el-button size="small" text :type="plane === 'xz' ? 'primary' : ''" @click="plane = 'xz'">
                  X-Z
                </el-button>
                <el-button size="small" text :type="plane === 'yz' ? 'primary' : ''" @click="plane = 'yz'">
                  Y-Z
                </el-button>
              </span>
            </div>
            <svg
              v-if="centerPlot"
              class="chart-svg"
              :viewBox="centerPlot.viewBox"
              preserveAspectRatio="xMidYMid meet"
            >
              <line
                v-for="tick in centerPlot.yTicks"
                :key="'gy' + tick.pos"
                :x1="centerPlot.plot.left"
                :x2="centerPlot.plot.right"
                :y1="tick.pos"
                :y2="tick.pos"
                class="chart-grid-line"
              />
              <line
                v-for="tick in centerPlot.xTicks"
                :key="'gx' + tick.pos"
                :y1="centerPlot.plot.top"
                :y2="centerPlot.plot.bottom"
                :x1="tick.pos"
                :x2="tick.pos"
                class="chart-grid-line"
              />
              <polyline :points="centerPlot.points" class="chart-line-main" fill="none" />
              <circle :cx="centerPlot.start.x" :cy="centerPlot.start.y" r="3" class="chart-dot-start" />
              <circle :cx="centerPlot.end.x" :cy="centerPlot.end.y" r="3" class="chart-dot-end" />
              <text
                v-for="tick in centerPlot.xTicks"
                :key="'tx' + tick.pos"
                :x="tick.pos"
                :y="centerPlot.plot.bottom + 14"
                class="chart-axis-text"
                text-anchor="middle"
              >
                {{ tick.label }}
              </text>
              <text
                v-for="tick in centerPlot.yTicks"
                :key="'ty' + tick.pos"
                :x="centerPlot.plot.left - 6"
                :y="tick.pos + 4"
                class="chart-axis-text"
                text-anchor="end"
              >
                {{ tick.label }}
              </text>
              <text
                :x="(centerPlot.plot.left + centerPlot.plot.right) / 2"
                :y="centerPlot.height - 6"
                class="chart-axis-name"
                text-anchor="middle"
              >
                {{ centerPlot.xLabel }}
              </text>
              <text
                :x="16"
                :y="(centerPlot.plot.top + centerPlot.plot.bottom) / 2"
                class="chart-axis-name"
                text-anchor="middle"
                :transform="'rotate(-90 16 ' + ((centerPlot.plot.top + centerPlot.plot.bottom) / 2) + ')'"
              >
                {{ centerPlot.yLabel }}
              </text>
            </svg>
            <div v-else class="hint chart-empty">该件没有中心线点。</div>
            <div v-if="centerPlot" class="chart-legend">
              <span><i class="dot" /> {{ summary.centerline?.method || '中心线' }}</span>
              <span><i class="dot dot-start" /> 起点</span>
              <span><i class="dot dot-end" /> 终点</span>
              <span>共 {{ summary.centerline?.point_count || centerlinePoints.length }} 点</span>
            </div>
          </div>

          <div class="summary-chart">
            <div class="summary-chart-head">
              <span>壁厚沿轴线（mm）</span>
            </div>
            <svg
              v-if="wallPlot"
              class="chart-svg"
              :viewBox="wallPlot.viewBox"
              preserveAspectRatio="xMidYMid meet"
            >
              <line
                v-for="tick in wallPlot.yTicks"
                :key="'gy' + tick.pos"
                :x1="wallPlot.plot.left"
                :x2="wallPlot.plot.right"
                :y1="tick.pos"
                :y2="tick.pos"
                class="chart-grid-line"
              />
              <line
                v-for="tick in wallPlot.xTicks"
                :key="'gx' + tick.pos"
                :y1="wallPlot.plot.top"
                :y2="wallPlot.plot.bottom"
                :x1="tick.pos"
                :x2="tick.pos"
                class="chart-grid-line"
              />
              <polyline :points="wallPlot.points" class="chart-line-wall" fill="none" />
              <text
                v-for="tick in wallPlot.xTicks"
                :key="'tx' + tick.pos"
                :x="tick.pos"
                :y="wallPlot.plot.bottom + 14"
                class="chart-axis-text"
                text-anchor="middle"
              >
                {{ tick.label }}
              </text>
              <text
                v-for="tick in wallPlot.yTicks"
                :key="'ty' + tick.pos"
                :x="wallPlot.plot.left - 6"
                :y="tick.pos + 4"
                class="chart-axis-text"
                text-anchor="end"
              >
                {{ tick.label }}
              </text>
              <text
                :x="(wallPlot.plot.left + wallPlot.plot.right) / 2"
                :y="wallPlot.height - 6"
                class="chart-axis-name"
                text-anchor="middle"
              >
                {{ wallPlot.xLabel }}
              </text>
              <text
                :x="16"
                :y="(wallPlot.plot.top + wallPlot.plot.bottom) / 2"
                class="chart-axis-name"
                text-anchor="middle"
                :transform="'rotate(-90 16 ' + ((wallPlot.plot.top + wallPlot.plot.bottom) / 2) + ')'"
              >
                {{ wallPlot.yLabel }}
              </text>
            </svg>
            <div v-else class="hint chart-empty">该件没有壁厚分段。</div>
            <div v-if="wallPlot" class="chart-legend">
              <span><i class="dot dot-wall" /> 壁厚</span>
              <span>{{ summary.wall?.segments?.length || 0 }} 段</span>
            </div>
          </div>
        </div>
      </template>
    </el-card>

    <el-card shadow="never" class="page-card">
      <template #header>
        <span class="panel-title">设计输入与型线 / 壁厚</span>
        <span class="hint">（工艺设计库里的参数、原始分段，以及按 1 mm 站位的型线周长与壁厚曲线）</span>
      </template>
      <el-empty v-if="!design" description="该管件没有登记工艺设计" :image-size="60" />
      <template v-else>
        <div class="design-meta">
          <span>工艺设计 #{{ design.id }}</span>
          <span>{{ design.pp_method || '—' }}</span>
          <span>车种 {{ design.vehicle_spec || '—' }}</span>
          <span>管件号 {{ design.extra?.['管件号'] ?? '—' }}</span>
          <span>站位 {{ design.stations }}</span>
        </div>
        <el-descriptions :column="4" size="small" border class="design-params">
          <el-descriptions-item v-for="row in design.scalars" :key="row.key" :label="row.key">
            {{ row.value }}
          </el-descriptions-item>
        </el-descriptions>
        <div class="chart-row">
          <div class="chart-box">
            <div class="chart-title">
              型线 · 外径周长（mm）
              <span class="hint">长度 {{ designLengthText }}</span>
            </div>
            <svg class="chart" viewBox="0 0 560 180" preserveAspectRatio="none">
              <polyline
                :points="chartPoints(design.curves.s_mm, design.curves.perimeter_mm)"
                fill="none"
                stroke="#409eff"
                stroke-width="1.5"
              />
            </svg>
            <div class="chart-scale">
              <span>min {{ minOf(design.curves.perimeter_mm) }}</span>
              <span>max {{ maxOf(design.curves.perimeter_mm) }}</span>
            </div>
          </div>
          <div class="chart-box">
            <div class="chart-title">
              壁厚（mm）· 设计值 / 修改后
              <span class="hint">min {{ minOf(design.curves.wall_mm) }} / max {{ maxOf(design.curves.wall_mm) }}</span>
            </div>
            <svg class="chart" viewBox="0 0 560 180" preserveAspectRatio="none">
              <polyline
                :points="chartPoints(design.curves.s_mm, design.curves.wall_mm)"
                fill="none"
                stroke="#67c23a"
                stroke-width="1.5"
              />
              <polyline
                :points="chartPoints(design.curves.s_mm, design.curves.wall_modified_mm)"
                fill="none"
                stroke="#e6a23c"
                stroke-width="1.2"
                stroke-dasharray="4 3"
              />
            </svg>
            <div class="chart-scale">
              <span>— 设计壁厚</span>
              <span>- - 修改后壁厚</span>
            </div>
          </div>
        </div>
        <div class="design-seg">
          <span class="hint">原始分段：</span>
          <el-tag
            v-for="(value, key) in design.segments"
            :key="key"
            size="small"
            type="info"
            class="seg-tag"
          >
            {{ key }} = {{ value }}
          </el-tag>
        </div>
      </template>

      <template v-if="profile">
        <el-divider />
        <div class="design-meta">
          <span>实测复核</span>
          <span>{{ profile.station_count }} 站</span>
          <span>中心线 {{ profile.length_mm }} mm</span>
          <span>壁厚 {{ profile.wall.min_mm }}–{{ profile.wall.max_mm }} mm（中位 {{ profile.wall.median_mm }}）</span>
          <span>周长 {{ profile.perimeter.min_mm }}–{{ profile.perimeter.max_mm }} mm</span>
        </div>
        <div class="chart-row">
          <div class="chart-box">
            <div class="chart-title">实测外径周长（mm）</div>
            <svg class="chart" viewBox="0 0 560 180" preserveAspectRatio="none">
              <polyline
                :points="chartPoints(profile.series.s_mm, profile.series.perimeter_mm)"
                fill="none"
                stroke="#909399"
                stroke-width="1.5"
              />
            </svg>
          </div>
          <div class="chart-box">
            <div class="chart-title">实测壁厚（mm）</div>
            <svg class="chart" viewBox="0 0 560 180" preserveAspectRatio="none">
              <polyline
                :points="chartPoints(profile.series.s_mm, profile.series.wall_mm)"
                fill="none"
                stroke="#606266"
                stroke-width="1.5"
              />
            </svg>
          </div>
        </div>
      </template>
    </el-card>

    <el-card shadow="never" class="page-card">
      <template #header>
        <span class="panel-title">外径形腔路线</span>
        <span class="hint">（每件可单独覆盖默认路线；影响下一次「运行」）</span>
      </template>
      <div class="route-pref-row">
        <div class="route-pref-field">
          <span class="route-pref-label">路线</span>
          <el-select
            v-model="routePref.impl"
            :disabled="!currentPart || routePrefSaving"
            style="width: 132px"
            @change="(value) => changeRoutePref('impl', value)"
          >
            <el-option v-for="impl in routePref.impls" :key="impl" :label="implLabel(impl)" :value="impl" />
          </el-select>
          <span class="hint">121 = v1 口径 + 参考保形，更慢但分型更稳</span>
        </div>
        <div class="route-pref-field">
          <span class="route-pref-label">方向</span>
          <el-select
            v-model="directionSelect"
            :disabled="!currentPart || routePrefSaving"
            style="width: 132px"
          >
            <el-option
              v-for="dir in routePref.directions"
              :key="dir"
              :label="directionLabel(dir)"
              :value="dir"
            />
            <el-option label="手动角度（绕 Z）" value="angle" />
          </el-select>
          <span class="hint">auto = 平面弯取弯曲平面法向（直管回退对称轴）</span>
        </div>
        <div class="route-pref-field">
          <span class="route-pref-label">角度</span>
          <el-input-number
            v-model="manualAngle"
            :min="-180"
            :max="180"
            :step="1"
            :precision="2"
            size="small"
            :disabled="!currentPart || routePrefSaving"
            style="width: 120px"
          />
          <el-button size="small" :loading="routePrefSaving" :disabled="!currentPart" @click="applyAngle">
            应用
          </el-button>
          <span class="hint">° 绕 Z 轴、从 +X 逆时针为正（−27 ≈ 弯平面法向）</span>
        </div>
      </div>
      <div class="route-pref-meta">
        <el-tag v-if="routePref.effective" size="small" type="success">
          生效：{{ routePref.effective.impl || routePref.impl }} · 端部口径
          {{ routePref.effective.end_treatment || (routePref.impl === '121' ? 'v1' : 'c1') }}
        </el-tag>
        <el-tag v-if="routePrefSaving" size="small" type="info">保存中…</el-tag>
        <span v-if="routePrefLoading" class="hint">读取中…</span>
      </div>
      <div class="hint route-pref-note">
        c1 = 按实测外径（默认）；121 = 与 2052 同路径的参考保形，分型更稳但耗时更长。
        切换后下次「运行」生效，已存在的产物仍按缓存复用。
      </div>
    </el-card>

    <el-card shadow="never" class="page-card">
      <template #header>
        <span class="panel-title">5 类模型与产物</span>
        <span class="hint">（每类给出原模型 + 默认路线实体化件；缺失可点「运行」补齐）</span>
      </template>
      <el-table :data="modelRows" size="small" border stripe>
        <el-table-column label="工序 · 模型" min-width="150">
          <template #default="{ row }">
            <div class="model-name">{{ row.op_label }} · {{ row.name }}</div>
            <div class="hint mono">{{ row.key }}</div>
          </template>
        </el-table-column>
        <el-table-column label="默认路线" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="row.default_route === 'c1' ? 'success' : 'primary'">
              {{ row.route_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="关键参数" min-width="200">
          <template #default="{ row }">
            <div class="param-mini">{{ paramText(row) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="原模型" width="120" align="center">
          <template #default="{ row }">
            <el-button
              size="small"
              text
              type="primary"
              :disabled="!row.original?.exists"
              @click="downloadEntry(row.original)"
            >
              {{ row.original?.exists ? '下载 .stp' : '未生成' }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="实体化件" width="120" align="center">
          <template #default="{ row }">
            <el-button
              size="small"
              type="success"
              text
              :disabled="!primaryOutput(row)"
              @click="downloadEntry(primaryOutput(row))"
            >
              {{ primaryOutput(row) ? '下载 .step' : '未生成' }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="指标" min-width="230">
          <template #default="{ row }">
            <template v-if="primaryOutput(row)">
              <div class="metric-line">
                面 {{ primaryOutput(row).faces ?? '—' }} ·
                实体 {{ primaryOutput(row).solids ?? '—' }} ·
                分型边 {{ primaryOutput(row).parting_edges ?? '—' }} ·
                自由边 {{ primaryOutput(row).free_edges ?? '—' }}
              </div>
              <div class="metric-line">
                体积 {{ formatVolume(primaryOutput(row).volume_mm3) }}
                <span v-if="primaryOutput(row).volume_error_pct != null">
                  （{{ formatPct(primaryOutput(row).volume_error_pct) }}）
                </span>
                · {{ primaryOutput(row).valid ? '有效' : '无效' }}
                <span v-if="primaryOutput(row).seconds"> · {{ primaryOutput(row).seconds }}s</span>
              </div>
            </template>
            <span v-else class="hint">暂无产物</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="page-card">
      <template #header>
        <span class="panel-title">运行状态</span>
        <span v-if="job" class="hint">任务 {{ job.id }} · {{ jobStatusText }}</span>
      </template>
      <el-empty v-if="!job" description="还没有运行任务" :image-size="60" />
      <template v-else>
        <el-progress :percentage="Math.round(job.progress_pct || 0)" :status="progressStatus" />
        <div class="job-meta">
          <span>状态：{{ jobStatusText }}</span>
          <span v-if="job.current_part">当前件：{{ job.current_part }}</span>
          <span v-if="job.counts">
            共 {{ job.counts.total ?? '—' }} 格 · ok {{ job.counts.ok ?? 0 }} · 缓存
            {{ job.counts.cached ?? 0 }} · 失败 {{ job.counts.error ?? 0 }}
          </span>
          <span v-if="job.seconds">{{ job.seconds }}s</span>
        </div>
        <div v-if="jobDesigns.length" class="job-meta">
          <span v-for="item in jobDesigns" :key="item.part">
            上游设计 {{ item.part }}：
            <el-tag size="small" :type="item.state === 'created' ? 'success' : (item.state === 'registered' ? 'info' : 'warning')">
              {{ item.text }}
            </el-tag>
          </span>
        </div>
        <pre v-if="logsText" class="log-box">{{ logsText }}</pre>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  downloadFile,
  getImportQueue,
  getJob,
  getPartFlow,
  getPartProfile,
  getParts,
  getRoutePref,
  getStatus,
  getUploads,
  cancelUpload,
  clearUploads,
  importToPipeline,
  runPipeline,
  setRoutePref,
  stopPipeline,
  uploadParts,
} from '@/api/entity'

const loading = ref(false)
const starting = ref(false)
const stopping = ref(false)
const importing = ref(false)
const loadError = ref('')
const parts = ref([])
const currentPart = ref('')
/** 批量运行的目标件；空则回落 currentPart（兼容只看不选的情况）。 */
const selectedParts = ref([])
/** 已导入主流程队列的件名，仅用于提示。 */
const importedParts = ref([])
const flow = ref(null)
const profile = ref(null)
const job = ref(null)
const status = ref({})

const plane = ref('xz')
const pickedFiles = ref([])
const uploaderRef = ref(null)
const uploading = ref(false)
const uploadPercent = ref(0)
const uploadTasks = ref([])
/** 上传并行路数：默认 1，页面加载后由 /api/entity/status 的 upload_parallel 覆盖。 */
const uploadParallel = ref(1)
/** 当前实例的测量口径（来自 /api/entity/status 的 measurement 字段，只读展示）。 */
const measurement = reactive({ station_step_mm: undefined, station_grid_mm: undefined })
const measurementLabel = computed(() => {
  const step = Number(measurement.station_step_mm)
  const grid = Number(measurement.station_grid_mm)
  if (!Number.isFinite(step)) return ''
  const parts = [`${step} mm 布点`]
  if (Number.isFinite(grid) && grid > step + 1e-9) parts.push(`归档 ${grid} mm`)
  return parts.join(' / ')
})
const routePref = reactive({
  impl: 'c1',
  direction: 'auto',
  effective: null,
  impls: ['c1', '121'],
  directions: ['auto', 'X', 'Y', 'Z'],
})
const routePrefLoading = ref(false)
const routePrefSaving = ref(false)
const manualAngle = ref(-27)
const directionSelect = computed({
  get() {
    const value = String(routePref.direction || 'auto')
    return value.startsWith('angle:') ? 'angle' : value
  },
  set(value) {
    if (value === 'angle') {
      applyAngle()
    } else {
      changeRoutePref('direction', value)
    }
  },
})

const RUNNING_STATES = ['running', 'stopping', 'queued', 'pending']
const uploadHandled = new Set()

let pollTimer = null
let uploadPollTimer = null

const running = computed(
  () =>
    Boolean(status.value?.running) ||
    Boolean(status.value?.stopping) ||
    RUNNING_STATES.includes(job.value?.status),
)
const isStopping = computed(
  () => job.value?.status === 'stopping' || Boolean(status.value?.stopping),
)
const defaults = computed(() => flow.value?.defaults || {})
const design = computed(() => flow.value?.design || null)
const designState = computed(() => flow.value?.design_state || { state: 'missing' })
const designStateText = computed(() => {
  const state = designState.value.state
  if (state === 'registered') return '设计已登记'
  if (state === 'infeasible') return '推荐路线不可行'
  if (state === 'error') return '补算失败'
  return '未登记设计'
})
const routeSteps = computed(() => flow.value?.route_steps || [])
const routePlan = computed(() => flow.value?.route_plan || null)
const routeActive = computed(() => {
  const steps = routeSteps.value
  if (!steps.length) return 0
  const done = steps.filter((step) => step.done).length
  return Math.min(done, steps.length)
})
const designLengthText = computed(() => {
  const s = design.value?.curves?.s_mm || []
  if (!s.length) return '—'
  return `${s[0]}–${s[s.length - 1]} mm`
})

const summary = computed(() => flow.value?.process_summary || null)
const uploadPickText = computed(() => {
  const files = pickedFiles.value
  if (!files.length) return '未选择文件（支持多选 .stp / .step）'
  if (files.length === 1) return files[0].name
  return `${files.length} 个文件：${files.map((file) => file.name).join('、')}`
})
/** 上传区里已经测量完成、可以导入主流程的任务。 */
const doneUploadParts = computed(() =>
  uploadTasks.value.filter((task) => {
    const state = String(task?.status || '').toLowerCase()
    return ['done', 'completed', 'finished'].includes(state) && Boolean(task?.part)
  }),
)
/** 可以清掉的上传记录条数（进行中的任务不算）。 */
const clearableUploadCount = computed(() =>
  uploadTasks.value.filter((task) => {
    const state = String(task?.status || '').toLowerCase()
    return !['queued', 'measuring', 'saving'].includes(state)
  }).length,
)
/** 「运行」实际要跑的件：优先多选，其次当前查看件。 */
const runParts = computed(() => {
  const picked = selectedParts.value.filter(Boolean)
  if (picked.length) return picked
  return currentPart.value ? [currentPart.value] : []
})
function partOptionLabel(p) {
  if (!p) return ''
  return p.measured ? `${p.name}（已测量）` : p.name
}
const centerlinePoints = computed(() => {
  const list = summary.value?.centerline?.points || []
  return list
    .filter((point) => Array.isArray(point) && point.length >= 3)
    .map((point) => [Number(point[0]), Number(point[1]), Number(point[2])])
    .filter((point) => point.every(Number.isFinite))
})
const wallPoints = computed(() => {
  const segments = summary.value?.wall?.segments || []
  const out = []
  segments.forEach((seg) => {
    const start = Number(seg?.start_mm)
    const end = Number(seg?.end_mm)
    const thickness = Number(seg?.thickness_mm)
    if (![start, end, thickness].every(Number.isFinite)) return
    out.push([start, thickness])
    out.push([end, thickness])
  })
  return out
})
const centerPlot = computed(() => {
  const useYz = plane.value === 'yz'
  const raw = centerlinePoints.value.map((point) => (useYz ? [point[1], point[2]] : [point[0], point[2]]))
  return buildPlot(raw, { xLabel: useYz ? 'Y (mm)' : 'X (mm)', yLabel: 'Z (mm)' })
})
const wallPlot = computed(() =>
  buildPlot(wallPoints.value, { xLabel: '沿轴线 (mm)', yLabel: '壁厚 (mm)' }),
)
const drawingRows = computed(() => {
  const drawing = summary.value?.drawing || {}
  return [
    ['normal', '普通抽'],
    ['tp', 'TP抽'],
    ['taper', 'Taper'],
  ].map(([key, fallback]) => {
    const entry = drawing[key] || {}
    const count = Array.isArray(entry.passes) ? entry.passes.length : Number(entry.count)
    return {
      key,
      label: entry.label || fallback,
      count: Number.isFinite(count) ? count : 0,
    }
  })
})

function chartPoints(xs, ys) {
  const width = 560
  const height = 180
  const pad = 8
  const x = xs || []
  const y = ys || []
  const count = Math.min(x.length, y.length)
  if (!count) return ''
  const x0 = x[0]
  const x1 = x[count - 1]
  const spanX = x1 - x0 || 1
  let minY = Infinity
  let maxY = -Infinity
  for (let i = 0; i < count; i += 1) {
    const value = Number(y[i])
    if (!Number.isFinite(value)) continue
    if (value < minY) minY = value
    if (value > maxY) maxY = value
  }
  if (!Number.isFinite(minY) || !Number.isFinite(maxY)) return ''
  const spanY = maxY - minY || 1
  const points = []
  for (let i = 0; i < count; i += 1) {
    const value = Number(y[i])
    if (!Number.isFinite(value)) continue
    const px = pad + ((x[i] - x0) / spanX) * (width - pad * 2)
    const py = height - pad - ((value - minY) / spanY) * (height - pad * 2)
    points.push(`${px.toFixed(1)},${py.toFixed(1)}`)
  }
  return points.join(' ')
}

function minOf(list) {
  const values = (list || []).map(Number).filter(Number.isFinite)
  return values.length ? Math.min(...values).toFixed(2) : '—'
}

function maxOf(list) {
  const values = (list || []).map(Number).filter(Number.isFinite)
  return values.length ? Math.max(...values).toFixed(2) : '—'
}

function fmtTick(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  if (Math.abs(n) < 1e-9) return '0'
  if (Math.abs(n) >= 100) return n.toFixed(0)
  if (Math.abs(n) >= 10) return n.toFixed(1)
  return n.toFixed(2)
}

function niceRange(min, max) {
  let lo = Number(min)
  let hi = Number(max)
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null
  if (lo === hi) {
    lo -= 0.5
    hi += 0.5
  }
  const raw = (hi - lo) / 4
  const power = 10 ** Math.floor(Math.log10(raw))
  const base = raw / power
  let step = power
  if (base > 5) step = 10 * power
  else if (base > 2) step = 5 * power
  else if (base > 1) step = 2 * power
  return { lo, hi, step }
}

function buildPlot(rawPoints, labels = {}) {
  const points = (rawPoints || [])
    .filter(
      (point) => Array.isArray(point) && Number.isFinite(Number(point[0])) && Number.isFinite(Number(point[1])),
    )
    .map((point) => [Number(point[0]), Number(point[1])])
  if (points.length < 2) return null
  const plot = { left: 64, right: 536, top: 30, bottom: 188 }
  const width = plot.right - plot.left
  const height = plot.bottom - plot.top
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  points.forEach(([x, y]) => {
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  })
  const yRange = niceRange(minY, maxY) || { lo: minY, hi: maxY, step: 1 }
  const spanX = maxX - minX || 1
  const spanY = yRange.hi - yRange.lo || 1
  const px = (x) => plot.left + ((x - minX) / spanX) * width
  const py = (y) => plot.bottom - ((y - yRange.lo) / spanY) * height
  const pointText = points
    .map(([x, y]) => `${px(x).toFixed(1)},${py(y).toFixed(1)}`)
    .join(' ')
  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
    pos: plot.left + ratio * width,
    label: fmtTick(minX + ratio * spanX),
  }))
  const yTicks = []
  const count = Math.round((yRange.hi - yRange.lo) / yRange.step) || 1
  for (let i = 0; i <= count; i += 1) {
    const value = yRange.lo + i * yRange.step
    yTicks.push({ pos: py(value), label: fmtTick(value) })
  }
  return {
    viewBox: '0 0 560 232',
    points: pointText,
    start: { x: px(points[0][0]), y: py(points[0][1]) },
    end: { x: px(points[points.length - 1][0]), y: py(points[points.length - 1][1]) },
    xTicks,
    yTicks,
    plot,
    height: 232,
    xLabel: labels.xLabel || 'X (mm)',
    yLabel: labels.yLabel || 'Y (mm)',
  }
}

function mmText(value) {
  if (value === null || value === undefined || value === '') return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return n.toFixed(2)
}

function summaryParam(key) {
  const params = summary.value?.design?.params
  if (!params) return '—'
  if (Array.isArray(params)) {
    const hit = params.find((item) => item?.name === key || item?.key === key)
    const value = hit?.value ?? hit?.v
    return value === null || value === undefined || value === '' ? '—' : value
  }
  const value = params[key]
  return value === null || value === undefined || value === '' ? '—' : value
}

async function loadProfile() {
  if (!currentPart.value) return
  try {
    profile.value = await getPartProfile(currentPart.value)
  } catch {
    profile.value = null
  }
}

const modelRows = computed(() => flow.value?.models || [])
const routeRows = computed(() =>
  Object.entries(defaults.value?.routes || {}).map(([key, route]) => ({ key, route })),
)
const sourceName = computed(() => flow.value?.source?.file || flow.value?.source?.name || '—')
const measuredText = computed(() => {
  const m = flow.value?.measured
  if (!m) return '—'
  if (!m.exists) return '未生成（运行时会先测量）'
  return `${m.download_name} · ${formatSize(m.size)}`
})
const logsText = computed(() => (job.value?.logs || []).slice(-8).join('\n'))
const jobDesigns = computed(() => {
  const designs = job.value?.designs || {}
  const label = {
    created: '已补算',
    registered: '已登记',
    infeasible: '推荐路线不可行',
    no_measured: '缺测量数据',
    error: '补算失败',
  }
  return Object.entries(designs).map(([part, info]) => ({
    part,
    state: info?.state || '',
    text: `${label[info?.state] || info?.state || '未知'}${info?.route ? ' · ' + info.route : ''}`,
  }))
})
const jobStatusText = computed(() => {
  const map = {
    running: '运行中',
    queued: '排队中',
    stopping: '正在停止…',
    stopped: '已停止',
    forced: '已强制停止',
    cancelled: '已取消',
    finished: '已完成',
    failed: '失败',
    pending: '排队中',
  }
  return map[job.value?.status] || job.value?.status || '—'
})
const progressStatus = computed(() => {
  if (job.value?.status === 'failed') return 'exception'
  if (job.value?.status === 'finished') return 'success'
  return ''
})

function formatSize(bytes) {
  const n = Number(bytes || 0)
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function formatVolume(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || !n) return '—'
  return `${n.toLocaleString('zh-CN', { maximumFractionDigits: 1 })} mm³`
}

function formatPct(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(3)}%`
}

function paramText(row) {
  const p = row.params || {}
  if (row.default_route === 'c1') {
    return `采样 ${p.half_samples} · 平滑 ${p.smooth_passes} · 站位 ${p.station_step_mm}mm`
  }
  return `采样 ${p.half_points} · 截面 ${p.max_sections || 'auto'} · 方向 ${p.direction_rule}`
}

function primaryOutput(row) {
  const list = row?.outputs || []
  return list.find((item) => item.default) || list[0] || null
}

async function loadStatus() {
  try {
    status.value = await getStatus()
    if (Number(status.value?.upload_parallel) > 0) {
      uploadParallel.value = Number(status.value.upload_parallel)
    }
    if (status.value?.measurement) Object.assign(measurement, status.value.measurement)
    parts.value = (await getParts()).parts || []
    if (!selectedParts.value.length && parts.value.length) {
      selectedParts.value = [parts.value[0].name]
    }
    if (!currentPart.value && parts.value.length) currentPart.value = parts.value[0].name
  } catch (err) {
    loadError.value = err?.response?.data?.message || err?.message || '读取状态失败'
  }
}

/** 已导入主流程队列的件名（只用于提示，失败不影响页面）。 */
async function loadImportQueue() {
  try {
    const data = await getImportQueue()
    importedParts.value = (data?.parts || []).map((item) => item.part).filter(Boolean)
  } catch (err) {
    importedParts.value = []
  }
}

async function loadFlow() {
  if (!currentPart.value) return
  loading.value = true
  loadError.value = ''
  try {
    flow.value = await getPartFlow(currentPart.value)
    if (flow.value?.route_pref) applyRoutePref({ route_pref: flow.value.route_pref })
    loadProfile()
    maybeAutoGenerate()
  } catch (err) {
    flow.value = null
    loadError.value = err?.response?.data?.message || err?.message || '读取该件流程失败'
  } finally {
    loading.value = false
  }
}

// 一体化流水线：该件已经测量过、但 5 类实体还没生成时，自动补跑一次。
// 每个管件在本次页面会话里只自动触发一次，避免失败后无限重试；
// 手动「运行」或上传完成后走同一套 loadFlow 逻辑，因此也会自动接上。
const autoGenerated = new Set()
/** 导入主流程后的一小段时间内不自动补跑单件（时间窗比布尔量稳，能挡住轮询竞态）。 */
let suppressAutoUntil = 0

function hasModelOutput() {
  return modelRows.value.some((row) => {
    const out = primaryOutput(row)
    return Boolean(out) && out.exists !== false
  })
}

async function maybeAutoGenerate() {
  if (Date.now() < suppressAutoUntil) return
  if (runParts.value.length > 1) return   // 批量任务由用户点「运行」触发
  const part = currentPart.value
  if (!part || autoGenerated.has(part)) return
  if (running.value || starting.value) return
  const s = summary.value
  if (!s || s.error) return          // 没有测量结果，先不自动跑
  if (hasModelOutput()) return       // 已经有实体产物
  ElMessage.info('检测到该件已测量、但还没有实体，自动开始生成 5 类模型…')
  const ok = await startRun()
  // 只有真的启动成功才记下；若是 409（正在上传测量）等失败，等上传结束后
  // loadFlow 会再进来重试，不会因为一次失败就永久跳过。
  if (ok) autoGenerated.add(part)
}

async function downloadEntry(entry) {
  if (!entry?.exists || !entry?.rel_path) {
    ElMessage.warning('该文件尚未生成')
    return
  }
  try {
    await downloadFile(entry.rel_path, entry.download_name)
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '下载失败')
  }
}

async function startRun() {
  const targets = runParts.value
  if (!targets.length) return false
  starting.value = true
  try {
    const data = await runPipeline({
      parts: targets,
      models: [],
      routes: null,
      force: false,
      ensure_design: false,
    })
    if (!data.success) {
      ElMessage.error(data.message || '启动失败')
      return false
    }
    if (targets.length > 1) {
      ElMessage.success(`已启动 ${targets.length} 件批量运行：逐件复用测量缓存，只补缺的格子`)
    } else {
      ElMessage.success('已启动：不补算设计，只输出推荐工艺路线（保留缓存只补缺）')
    }
    const missing = Array.isArray(data?.missing_measured) ? data.missing_measured : []
    if (missing.length) {
      ElMessage.warning(
        `${missing.length} 件没有测量缓存，会在本任务里串行测量（想并行请先上传并点「导入主流程」）：` +
          missing.slice(0, 5).join('、') +
          (missing.length > 5 ? ' …' : ''),
      )
    }
    job.value = data.job ? { ...data.job, status: data.job.status || 'running' } : null
    startPolling()
    return true
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '启动失败')
    return false
  } finally {
    starting.value = false
  }
}

async function stopRun(mode = 'graceful') {
  if (mode === 'force') {
    try {
      await ElMessageBox.confirm(
        '强制停止会立即结束正在运行的子进程，写到一半的产物可能不完整。确定强制停止？',
        '强制停止',
        { confirmButtonText: '强制停止', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
  }
  stopping.value = true
  try {
    const data = await stopPipeline(mode)
    if (mode === 'force') {
      const killed = data?.killed || []
      ElMessage.warning(killed.length ? `已强制停止：${killed.join('、')}` : '已请求强制停止')
    } else {
      ElMessage.info('已请求停止（当前单元结束后退出）')
    }
    job.value = { ...(job.value || {}), status: 'stopping' }
    startPolling()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '停止失败')
  } finally {
    stopping.value = false
  }
}

async function pollOnce() {
  try {
    const data = await getStatus()
    status.value = data
    const jobId = job.value?.id || data.job_id
    if (!jobId) {
      stopPolling()
      return
    }
    const detail = await getJob(jobId)
    job.value = detail.job || job.value
    const state = job.value?.status
    if (state === 'stopping' && !data.running && !data.stopping) {
      job.value = { ...job.value, status: 'stopped' }
      stopPolling()
      await loadFlow()
      return
    }
    if (state && RUNNING_STATES.includes(state)) return
    if (!state && (data.running || data.stopping)) return
    stopPolling()
    await loadFlow()
  } catch (err) {
    /* 轮询失败忽略，下一轮再试 */
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(pollOnce, 2000)
}

function stopPolling() {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}:${pad(date.getSeconds())}`
}

function onUploadChange(file, fileList) {
  const list = Array.isArray(fileList) && fileList.length ? fileList : [file]
  const raws = list.map((item) => item?.raw).filter(Boolean)
  const accepted = raws.filter((raw) => /\.(stp|step)$/i.test(raw.name || ''))
  const rejected = raws.length - accepted.length
  pickedFiles.value = accepted
  uploadPercent.value = 0
  if (rejected) ElMessage.warning(`已忽略 ${rejected} 个非 .stp/.step 文件`)
}

async function refreshParts() {
  try {
    parts.value = (await getParts()).parts || []
  } catch (err) {
    /* 旧后端或网络错误：保留现有列表 */
  }
}

function isUploadActive(state) {
  return ['measuring', 'queued', 'pending', 'running', 'processing', 'cancelling'].includes(
    String(state || '').toLowerCase(),
  )
}

function isUploadFailed(state) {
  return ['failed', 'error'].includes(String(state || '').toLowerCase())
}

function uploadStatusText(state) {
  const map = {
    measuring: '测量中',
    queued: '排队中',
    pending: '排队中',
    running: '测量中',
    processing: '测量中',
    cancelling: '停止中',
    cancelled: '已取消',
    done: '完成',
    completed: '完成',
    finished: '完成',
    failed: '失败',
    error: '失败',
  }
  return map[String(state || '').toLowerCase()] || state || '—'
}

function uploadTagType(state) {
  const key = String(state || '').toLowerCase()
  if (['done', 'completed', 'finished'].includes(key)) return 'success'
  if (isUploadFailed(key)) return 'danger'
  if (key === 'cancelled') return 'warning'
  if (['measuring', 'running', 'processing', 'cancelling'].includes(key)) return 'primary'
  return 'info'
}

function uploadStatusHint(state) {
  const key = String(state || '').toLowerCase()
  if (key === 'cancelling') return '正在停止测量子进程，通常 1 s 内结束'
  if (key === 'cancelled') return '已取消；重新上传会从头测量'
  if (isUploadActive(key)) return '正在测量并入库，完成后自动刷新'
  if (isUploadFailed(key)) return '测量失败，请查看错误信息'
  if (['done', 'completed', 'finished'].includes(key)) return '已完成，可选中该件查看'
  return ''
}

function uploadRowClass({ row }) {
  if (isUploadActive(row?.status)) return 'upload-row-active'
  if (isUploadFailed(row?.status)) return 'upload-row-error'
  return ''
}

function uploadProgressOf(row) {
  const n = Number(row?.progress)
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(100, Math.round(n > 1 ? n : n * 100)))
}

function startUploadPolling() {
  stopUploadPolling()
  uploadPollTimer = window.setInterval(loadUploads, 2000)
}

function stopUploadPolling() {
  if (uploadPollTimer) {
    window.clearInterval(uploadPollTimer)
    uploadPollTimer = null
  }
}

async function loadUploads() {
  try {
    const data = await getUploads()
    uploadTasks.value = data.tasks || []
    if (Number(data.parallel) > 0) uploadParallel.value = Number(data.parallel)
  } catch (err) {
    return
  }
  let hasActive = false
  let hasDone = false
  uploadTasks.value.forEach((task) => {
    const state = String(task?.status || '').toLowerCase()
    if (isUploadActive(state)) hasActive = true
    if (!['done', 'completed', 'finished'].includes(state)) return
    const key = task?.task_id || `${task?.part}-${task?.created_at}`
    if (uploadHandled.has(key)) return
    uploadHandled.add(key)
    hasDone = true
  })
  if (hasDone) {
    await refreshParts()
    await loadFlow()
  }
  if (!hasActive) stopUploadPolling()
}

async function doUpload() {
  if (!pickedFiles.value.length) {
    ElMessage.warning('请先选择 STEP 文件')
    return
  }
  uploading.value = true
  uploadPercent.value = 0
  try {
    const data = await uploadParts(pickedFiles.value, (percent) => {
      uploadPercent.value = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)))
    })
    const submitted = Array.isArray(data?.tasks) ? data.tasks : []
    const count = Number(data?.count) || submitted.length || pickedFiles.value.length
    const parallel = Number(data?.parallel) || uploadParallel.value
    uploadParallel.value = parallel
    if (count === 1 && data?.unchanged) {
      ElMessage.success('文件内容未变，已复用原件的测量缓存')
    } else if (count === 1 && data?.overwritten) {
      ElMessage.success('同名件已覆盖更新（件名不变），正在重新测量')
    } else {
      ElMessage.success(`已提交 ${count} 个测量任务（最多 ${parallel} 路并行）`)
    }
    const cleared = (Array.isArray(data?.cache_cleared) ? data.cache_cleared : []).length
    if (cleared) {
      ElMessage.info(`同名覆盖已清掉 ${cleared} 份旧测量缓存，将重新测量`)
    }
    if (Array.isArray(data?.errors) && data.errors.length) {
      const detail = data.errors
        .map((item) => `${item.file || '文件'}: ${item.error || '未接收'}`)
        .join('；')
      ElMessage.warning(`有 ${data.errors.length} 个文件未接收：${detail}`)
    }
    pickedFiles.value = []
    uploadPercent.value = 0
    uploaderRef.value?.clearFiles?.()
    await refreshParts()
    const firstPart = submitted[0]?.part || data?.part
    if (firstPart) selectPart(firstPart)
    await loadUploads()
    startUploadPolling()
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || err?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

/** 清掉上传任务记录（只在内存里，重启本来就没了；这里给个手动清空入口）。 */
async function clearUploadRecords() {
  try {
    const data = await clearUploads({ keep_active: true })
    ElMessage.success(data?.message || '已清空上传任务记录')
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '清空上传任务记录失败')
    return
  }
  uploadHandled.clear()
  uploadTasks.value = []
  stopUploadPolling()
  await loadUploads()
}

/** 停止一个上传测量任务（排队中=移出队列，测量中=杀子进程树）。 */
const cancellingTasks = reactive(new Set())

async function cancelUploadTask(row) {
  const taskId = row?.task_id
  if (!taskId) return
  cancellingTasks.add(taskId)
  try {
    const data = await cancelUpload(taskId)
    ElMessage.success(data?.message || '已请求取消')
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || '取消失败')
  } finally {
    cancellingTasks.delete(taskId)
    await loadUploads()
  }
}

/**
 * 把上传区已并行测量完的件写入「管件模型分析」数据库并登记进主流程队列。
 *
 * 后端写 tube_analysis 时盖 origin='entity'（「工艺优化」的加载列表按 origin=pp 过滤，
 * 看不到这一类），同时登记队列；前端把登记成功的件选进多选框，
 * 运行时会直接复用测量缓存。真正的几何计算仍走「运行」——5 类导出与
 * c1/pipe 必须串行（OCP/FreeCAD 非线程安全）。
 */
async function doImport() {
  const tasks = doneUploadParts.value.map((task) => task.task_id).filter(Boolean)
  if (!tasks.length) {
    ElMessage.warning('还没有测量完成的上传件')
    return
  }
  importing.value = true
  try {
    const data = await importToPipeline({ tasks, replace: false })
    const imported = (data?.imported || []).map((item) => item.part).filter(Boolean)
    const existing = (data?.existing || []).map((item) => item.part).filter(Boolean)
    const removed = data?.duplicates_removed || []
    const names = [...new Set([...imported, ...existing])]
    if (names.length) {
      const merged = [...selectedParts.value]
      names.forEach((name) => {
        if (!merged.includes(name)) merged.push(name)
      })
      selectedParts.value = merged
      currentPart.value = selectedParts.value[0] || ''
      suppressAutoUntil = Date.now() + 6000   // 别让紧接着的 loadFlow 抢跑单件
      await refreshParts()
      await loadFlow()
      await loadRoutePref()
      const pieces = []
      if (imported.length) pieces.push(`已写入分析库并登记 ${imported.length} 件`)
      if (existing.length) pieces.push(`分析库已有 ${existing.length} 件`)
      if (removed.length) pieces.push(`清理重复记录 ${removed.length} 条`)
      ElMessage.success(
        pieces.length
          ? `${pieces.join('，')}${imported.length > 1 ? `；点「运行（${imported.length} 件批量）」开始` : ''}`
          : data?.message || '已导入',
      )
    } else {
      ElMessage.warning(data?.message || '没有可导入的件')
    }
    const skipped = data?.skipped || []
    if (skipped.length) {
      ElMessage.warning(
        `跳过 ${skipped.length} 件：${skipped
          .map((item) => `${item.part}（${item.reason || '未就绪'}）`)
          .join('；')}`,
      )
    }
    importedParts.value = data?.queue?.parts || importedParts.value
  } catch (err) {
    ElMessage.error(err?.response?.data?.message || err?.message || '导入失败')
  } finally {
    importing.value = false
  }
}

function selectPart(part) {
  if (!part) return
  currentPart.value = part
  selectedParts.value = [part]
  loadFlow()
  loadRoutePref()
}

function onPartsChange(value) {
  const picked = Array.isArray(value) ? value.filter(Boolean) : []
  if (picked.length) currentPart.value = picked[0]
  loadFlow()
  loadRoutePref()
}

function onPartChange() {
  onPartsChange(selectedParts.value)
}

function applyRoutePref(data) {
  const payload = data?.route_pref || data || {}
  if (payload.impl) routePref.impl = String(payload.impl)
  if (payload.direction) {
    routePref.direction = String(payload.direction)
    if (routePref.direction.startsWith('angle:')) {
      const value = Number.parseFloat(routePref.direction.slice(6))
      if (Number.isFinite(value)) manualAngle.value = value
    }
  }
  if (Array.isArray(payload.impls) && payload.impls.length) routePref.impls = payload.impls
  if (Array.isArray(payload.directions) && payload.directions.length) {
    routePref.directions = payload.directions
  }
  if (payload.effective) routePref.effective = payload.effective
}

async function loadRoutePref() {
  if (!currentPart.value) return
  routePrefLoading.value = true
  try {
    applyRoutePref(await getRoutePref(currentPart.value))
  } catch (err) {
    routePref.effective = null
  } finally {
    routePrefLoading.value = false
  }
}

function applyAngle() {
  const value = Number(manualAngle.value)
  if (!Number.isFinite(value)) {
    ElMessage.warning('请输入 −180～180 之间的角度')
    return
  }
  changeRoutePref('direction', `angle:${value.toFixed(2)}`)
}

async function changeRoutePref(field, value) {
  if (!currentPart.value) return
  const previous = { impl: routePref.impl, direction: routePref.direction }
  routePrefSaving.value = true
  try {
    const payload = {
      model: '成型_外径形腔',
      impl: routePref.impl,
      direction: routePref.direction,
      [field]: value,
    }
    applyRoutePref(await setRoutePref(currentPart.value, payload))
    ElMessage.success('路线偏好已保存，下次运行生效')
  } catch (err) {
    routePref.impl = previous.impl
    routePref.direction = previous.direction
    ElMessage.error(err?.response?.data?.message || '路线偏好保存失败，已回滚')
  } finally {
    routePrefSaving.value = false
  }
}

function implLabel(key) {
  if (key === '121') return '121（保形）'
  if (key === 'c1') return 'c1（默认）'
  return key || '—'
}

function directionLabel(key) {
  if (key === 'auto') return 'auto（自动）'
  if (key === 'angle') return '手动角度（绕 Z）'
  if (!key) return '—'
  return String(key).toUpperCase()
}

onMounted(async () => {
  await loadStatus()
  await loadFlow()
  await loadRoutePref()
  await loadUploads()
  await loadImportQueue()
  if (uploadTasks.value.some((task) => isUploadActive(task?.status))) {
    startUploadPolling()
  }
  if ((status.value?.running || status.value?.stopping) && status.value?.job_id) {
    try {
      const detail = await getJob(status.value.job_id)
      job.value = detail.job || null
      startPolling()
    } catch (err) {
      /* 忽略 */
    }
  }
})

onBeforeUnmount(() => {
  stopPolling()
  stopUploadPolling()
})
</script>

<style scoped>
.entity-pipeline {
  padding-bottom: 32px;
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.page-desc {
  margin: 4px 0 0;
  color: #6b7280;
  font-size: 13px;
  max-width: 720px;
}

.head-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.block-alert {
  margin-bottom: 12px;
}

.top-row {
  margin-bottom: 0;
}

.page-card {
  border-radius: 10px;
  margin-bottom: 12px;
}

.panel-title {
  font-weight: 600;
}

.hint {
  color: #9ca3af;
  font-size: 12px;
  margin-left: 4px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.missing {
  color: #ef6c00;
}

.report-links {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.param-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.param-block {
  background: #f8fafc;
  border: 1px solid #eef2f7;
  border-radius: 8px;
  padding: 10px 12px;
}

.param-head {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 6px;
  color: #111827;
}

.param-line {
  font-size: 12px;
  color: #4b5563;
  line-height: 1.7;
}

.route-map {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.model-name {
  font-weight: 600;
  font-size: 13px;
}

.param-mini {
  font-size: 12px;
  color: #4b5563;
  line-height: 1.6;
}

.metric-line {
  font-size: 12px;
  color: #374151;
  line-height: 1.6;
}

.job-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin: 10px 0 6px;
  font-size: 12px;
  color: #4b5563;
}

.log-box {
  margin: 0;
  max-height: 160px;
  overflow: auto;
  background: #0f172a;
  color: #d1fae5;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.design-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  font-size: 12px;
  color: #606266;
  margin-bottom: 8px;
}

.design-status {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.design-status .hint {
  flex: 1;
  min-width: 240px;
}

.design-params {
  margin-bottom: 10px;
}

.chart-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.chart-box {
  flex: 1 1 320px;
  min-width: 280px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 6px 8px 2px;
  background: #fafcff;
}

.chart-title {
  font-size: 12px;
  color: #303133;
  margin-bottom: 2px;
}

.chart {
  width: 100%;
  height: 150px;
  display: block;
}

.chart-scale {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #909399;
  padding: 2px 0 4px;
}

.design-seg {
  margin-top: 8px;
}

.seg-tag {
  margin: 0 6px 6px 0;
}

.route-detail {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}


.route-rec {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  margin-bottom: 8px;
}

.route-group {
  margin-top: 10px;
}

.route-group-title {
  font-size: 12px;
  color: #303133;
  margin-bottom: 6px;
}

.route-group-items {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.route-item {
  min-width: 150px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 6px 10px;
  background: #fafcff;
}

.route-item.is-recommended {
  border-color: #67c23a;
  background: #f0f9eb;
}

.route-item.is-chosen {
  border-color: #e6a23c;
  background: #fdf6ec;
}

.route-item-label {
  font-size: 13px;
  color: #303133;
}

.route-item-tags {
  margin-top: 4px;
  display: flex;
  gap: 4px;
  min-height: 20px;
}

/* 上传区 */
.upload-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.upload-name {
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-tasks {
  margin-top: 4px;
}

.upload-task-msg {
  font-size: 12px;
  color: var(--el-text-color-regular, #606266);
  word-break: break-all;
}

.upload-task-msg.is-error {
  color: var(--el-color-danger, #f56c6c);
}

.upload-empty {
  padding: 6px 0;
}

.upload-parallel-hint {
  margin: -4px 0 10px;
}

:deep(.el-table .upload-row-active > td) {
  background: var(--el-color-primary-light-9, #ecf5ff);
}

:deep(.el-table .upload-row-error > td) {
  background: var(--el-color-danger-light-9, #fef0f0);
}

/* 工艺摘要 */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}

.summary-block {
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--el-fill-color-lighter, #fafafa);
}

.summary-block-wide {
  grid-column: span 2;
}

@media (max-width: 720px) {
  .summary-block-wide {
    grid-column: auto;
  }
}

.summary-label {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 4px;
}

.summary-value {
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary, #303133);
  font-variant-numeric: tabular-nums;
}

.summary-value .unit {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary, #909399);
}

.summary-sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}

.summary-route {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 14px;
  color: var(--el-text-color-primary, #303133);
}

.summary-reason {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-regular, #606266);
  line-height: 1.5;
}

.summary-route-hero {
  margin-bottom: 12px;
  padding: 12px 14px;
  border: 1px solid var(--el-color-primary-light-7, #a0cfff);
  border-left: 4px solid var(--el-color-primary, #409eff);
  border-radius: 8px;
  background: var(--el-color-primary-light-9, #ecf5ff);
}

.summary-hero-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.summary-route-hero .summary-route {
  font-size: 18px;
}

.summary-basis {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  font-variant-numeric: tabular-nums;
}

.summary-missing {
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-color-warning-dark-2, #b88230);
}

.summary-draw {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
}

.draw-item b {
  color: var(--el-color-primary, #409eff);
  font-size: 15px;
}

.summary-charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.summary-chart {
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px;
  padding: 8px 10px 4px;
}

.summary-chart-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 2px;
}

.plane-switch {
  display: inline-flex;
  align-items: center;
}

.chart-svg {
  width: 100%;
  height: auto;
  display: block;
}

.chart-grid-line {
  stroke: var(--el-border-color-lighter, #ebeef5);
  stroke-width: 1;
}

.chart-axis-text {
  fill: var(--el-text-color-secondary, #909399);
  font-size: 10px;
}

.chart-axis-name {
  fill: var(--el-text-color-secondary, #909399);
  font-size: 10px;
}

.chart-line-main {
  stroke: var(--el-color-primary, #409eff);
  stroke-width: 1.6;
  stroke-linejoin: round;
}

.chart-line-wall {
  stroke: var(--el-color-success, #67c23a);
  stroke-width: 1.4;
  stroke-linejoin: round;
}

.chart-dot-start {
  fill: var(--el-color-success, #67c23a);
}

.chart-dot-end {
  fill: var(--el-color-danger, #f56c6c);
}

.chart-legend {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
  padding: 2px 0 4px;
}

.chart-legend .dot,
.chart-legend .dot-start,
.chart-legend .dot-end {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
  background: var(--el-color-primary, #409eff);
}

.chart-legend .dot-start {
  background: var(--el-color-success, #67c23a);
}

.chart-legend .dot-end {
  background: var(--el-color-danger, #f56c6c);
}

.chart-legend .dot-wall {
  background: var(--el-color-success, #67c23a);
}

.chart-empty {
  padding: 24px 0;
  text-align: center;
}

/* 外径形腔路线 */
.route-pref-row {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  align-items: center;
}

.route-pref-field {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.route-pref-label {
  font-size: 13px;
  color: var(--el-text-color-regular, #606266);
}

.route-pref-meta {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.route-pref-note {
  margin-top: 8px;
  line-height: 1.6;
  margin-left: 0;
}

</style>
