<template>
  <div class="model-upload-app">
    <!-- ═══════════ LEFT SIDEBAR ═══════════ -->
    <aside class="sidebar">
      <!-- Header -->
      <div class="sidebar-header">
        <h1>管件模型分析</h1>
        <div class="subtitle">STL/STP 文件中心线与壁厚分析</div>
      </div>

      <!-- Upload section -->
      <div class="group">
        <!-- Load mode selector -->
        <el-radio-group v-model="loadMode" size="small" class="load-mode-group">
          <el-radio-button value="upload">上传文件</el-radio-button>
          <el-radio-button value="db">数据库加载</el-radio-button>
        </el-radio-group>

        <template v-if="loadMode === 'upload'">
          <!-- Algorithm selection -->
          <div class="algo-select">
            <el-radio-group v-model="algorithm" size="small">
              <el-radio-button value="A">A算法</el-radio-button>
              <el-radio-button value="B">B算法</el-radio-button>
            </el-radio-group>
          </div>

          <!-- Upload box -->
          <div
            class="upload-box"
            :class="{ dragover: isDragOver }"
            @click="fileInputRef?.click()"
            @dragover.prevent="isDragOver = true"
            @dragleave="isDragOver = false"
            @drop.prevent="handleDrop"
          >
            <div class="upload-icon">&#128193;</div>
            <div class="upload-label" v-if="!selectedFile">拖拽 STL/STP 文件到这里</div>
            <div class="upload-label" v-else>{{ selectedFile.name }}</div>
            <div class="upload-hint" v-if="!selectedFile">.stl / .stp / .step</div>
            <div class="upload-hint" v-else>{{ (selectedFile.size / 1024 / 1024).toFixed(2) }} MB</div>
            <input ref="fileInputRef" type="file" accept=".stl,.STL,.stp,.step,.STEP" @change="handleFileSelect" style="display:none">
          </div>

          <!-- Analysis settings -->
          <div class="row-2">
            <label class="label-group">
              采样间隔(mm)
              <input type="number" v-model.number="sampleInterval" min="0.5" max="5" step="0.5">
            </label>
          </div>

          <div class="row-2">
            <label class="label-group">起点舍弃(mm)<input type="number" v-model.number="endTrimStart" min="0" step="1" placeholder="自动识别"></label>
            <label class="label-group">终点舍弃(mm)<input type="number" v-model.number="endTrimEnd" min="0" step="1" placeholder="自动识别"></label>
          </div>
          <div class="upload-hint" style="margin:6px 0 10px;line-height:1.6">STP 端部舍弃后按实测形状梯度 C1 外推轮廓，同时线性延拓中心点、壁厚，保留完整长度坐标。留空自动识别；起点位于大周长端。修改后重新分析。</div>

          <button class="analyze-btn" :disabled="!selectedFile || analyzing" @click="handleAnalyze">
            {{ analyzing ? '分析中...' : '开始分析' }}
          </button>
        </template>

        <template v-if="loadMode === 'db'">
          <div class="db-panel">
            <div class="db-toolbar">
              <input v-model="dbSearch" placeholder="搜索文件名 / 显示名称" class="db-search-input" @input="loadTubes">
              <select v-model="dbSortKey" class="db-sort-select" @change="loadTubes">
                <option value="created_desc">最新优先</option>
                <option value="created_asc">最早优先</option>
                <option value="name_asc">名称 A→Z</option>
                <option value="name_desc">名称 Z→A</option>
                <option value="length_desc">中心线长度 ↓</option>
                <option value="length_asc">中心线长度 ↑</option>
              </select>
            </div>
            <div class="db-list-head" v-if="sortedTubes.length">
              <span class="db-list-title">历史记录</span>
              <button class="db-list-toggle" @click="dbListExpanded = !dbListExpanded">
                {{ dbListExpanded ? '收起' : '展开' }}
              </button>
            </div>
            <div class="db-list" :class="{ expanded: dbListExpanded }" v-if="sortedTubes.length">
              <div
                v-for="tube in visibleSortedTubes"
                :key="tube.ID"
                class="db-row"
                :class="{ loading: loadingDbId === tube.ID }"
                :title="`${tube.文件名} · 算法 ${tube.算法 || '-'}`"
                @click="handleLoadTube(tube)"
              >
                <div class="db-row-main">
                  <span class="db-row-name">{{ tube.显示名称 || tube.文件名 }}</span>
                  <span v-if="tube['来源'] === 'entity'" class="db-row-tag" title="一体化流水线「导入主流程」写入的记录">流水线</span>
                  <span class="db-row-meta">
                    <span>L {{ Number(tube['中心线长度(mm)'] || 0).toFixed(1) }} mm</span>
                    <span>{{ tube.截面数 || 0 }} 截面</span>
                    <span v-if="tube.算法">算法 {{ tube.算法 }}</span>
                    <span v-if="tube['文件类型']">{{ String(tube['文件类型']).toUpperCase() }}</span>
                  </span>
                  <span class="db-row-time">{{ tube.创建时间 || '' }}</span>
                </div>
                <div class="db-row-actions" @click.stop>
                  <button class="db-row-btn" title="重命名" @click="handleRenameTube(tube)">✎</button>
                  <button class="db-row-btn danger" title="删除" @click="handleDeleteTube(tube)">✕</button>
                </div>
              </div>
            </div>
            <el-empty v-else description="暂无历史记录" :image-size="48" />
          </div>
        </template>

        <PpDevQaPanel
          v-if="isDevBuild"
          class="dev-qa-slot"
          @restore="onDevRestore"
          @refreshed="onDevRefreshed"
        />
      </div>

      <el-alert v-if="analysisQuality" title="STP 原生截面测量" :closable="false" type="warning" style="flex-shrink: 0">
        可靠区间采用法向截面外轮廓面积质心与截面内壁厚中位数；端部按实测形状梯度 C1 外推，并标记为外推值。
        <div v-for="message in analysisQuality.warnings" :key="message">{{ message }}</div>
      </el-alert>
      <!-- Metrics -->
      <div class="metrics" v-if="infoReady">
        <div class="stats-card">
          <h3>中心线</h3>
          <div class="metric-row"><span class="metric-label">{{ analysisQuality?.end_treatment ? '完整长度坐标' : analysisQuality ? '有效测量长（旧数据）' : '长度' }}</span><span class="metric-value">{{ centerlineLength.toFixed(1) }} mm</span></div>
          <div class="metric-row"><span class="metric-label">截面数</span><span class="metric-value">{{ sectionsFull.length }}<template v-if="interpolatedCount">（实测 {{ measuredCount }} / 插值 {{ interpolatedCount }}）</template></span></div>
          <div class="metric-row" v-if="analysisQuality?.stage_timings?.total_seconds"><span class="metric-label">分析耗时</span><span class="metric-value">{{ Number(analysisQuality.stage_timings.total_seconds).toFixed(1) }} s<template v-if="analysisQuality?.measurement_station_step_mm">（步距 {{ analysisQuality.measurement_station_step_mm }} mm / {{ cutCount }} 次切面）</template></span></div>
        </div>
        <div class="stats-card">
          <h3>截面周长</h3>
          <div class="metric-row"><span class="metric-label">平均值</span><span class="metric-value">{{ perimAvg }} mm</span></div>
          <div class="metric-row"><span class="metric-label">最小值</span><span class="metric-value">{{ perimMin }} mm</span></div>
          <div class="metric-row"><span class="metric-label">最大值</span><span class="metric-value">{{ perimMax }} mm</span></div>
        </div>
        <div class="stats-card" v-if="thicknessStats">
          <h3>壁厚</h3>
          <div class="metric-row"><span class="metric-label">中位数</span><span class="metric-value">{{ thicknessStats.median_mm?.toFixed(2) }} mm</span></div>
          <div class="metric-row"><span class="metric-label">平均值</span><span class="metric-value">{{ thicknessStats.mean_mm?.toFixed(2) }} mm</span></div>
          <div class="metric-row"><span class="metric-label">最小 / 最大</span><span class="metric-value">{{ thicknessStats.min_mm?.toFixed(2) }} / {{ thicknessStats.max_mm?.toFixed(2) }} mm</span></div>
          <div class="metric-row"><span class="metric-label">采样数</span><span class="metric-value">{{ thicknessStats.count }}</span></div>
        </div>
      </div>

      <!-- Section data table -->
      <div class="table-section" v-if="infoReady && sectionsFull.length">
        <div class="table-title-row">
          <div class="table-title">截面数据</div>
          <button class="table-toggle-btn" @click="sectionTableExpanded = !sectionTableExpanded">
            {{ sectionTableExpanded ? '收起' : `展开 (${sectionsFull.length})` }}
          </button>
        </div>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr><th>序号</th><th>累计长度<br>(mm)</th><th>周长(mm)</th><th>壁厚(mm)</th><th>来源</th></tr>
            </thead>
            <tbody>
              <tr
                v-for="(sec, i) in (sectionTableExpanded ? sectionsFull : sectionsFull.slice(0, 10))"
                :key="i"
                :class="{ hovered: hoveredSectionIdx === i }"
                @mouseenter="onSectionHover(i)"
                @mouseleave="onSectionHover(-1)"
                @click="onSectionClick(i)"
              >
                <td>{{ i }}</td>
                <td>{{ sec.cumulative_length?.toFixed(1) ?? '-' }}</td>
                <td>{{ sec.perimeter?.toFixed(3) ?? '-' }}</td>
                <td>{{ sec.wall_thickness?.toFixed(2) ?? '-' }}</td>
                <td
                  :title="sectionSourceHint(sec)"
                  :style="{ color: sec.measurement_status === 'extrapolated' ? '#b56b08' : sec.measurement_status === 'needs_review' ? '#c12d2d' : (sec.station_source === 'interpolated' || sec.measurement_status === 'interpolated') ? '#6b7280' : '' }"
                >{{ sectionSourceLabel(sec) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="table-hint" v-if="!sectionTableExpanded && sectionsFull.length > 10">
          已收纳，当前显示前 10 条，点击“展开”查看全部截面数据
        </div>
      </div>

    </aside>

    <!-- ═══════════ MAIN AREA ═══════════ -->
    <main class="main-area">
      <!-- 3D Viewport + display settings -->
      <section class="viewport-panel">
        <div class="viewport-wrap">
        <div class="viewport-toolbar" v-if="infoReady">
          <span class="viewport-toolbar-title">3D 预览</span>
          <label class="toggle"><span>原始网格</span><input type="checkbox" v-model="showMesh" @change="updateVisibility"></label>
          <label class="toggle"><span>中心线</span><input type="checkbox" v-model="showCenterline" @change="updateVisibility"></label>
          <label class="toggle"><span>截面轮廓 (25mm)</span><input type="checkbox" v-model="showSections" @change="updateVisibility"></label>
          <label class="opacity-inline">
            网格透明度
            <input type="range" v-model.number="meshOpacity" min="0.05" max="1" step="0.05" @input="updateVisibility">
          </label>
          <span class="legend">
            <span><i class="swatch" style="background:#1478ff"></i>中心线</span>
            <span v-if="analysisQuality?.end_treatment"><i class="swatch" style="background:#ed901a"></i>{{ analysisQuality.end_treatment.method === 'c1_shape_extrapolation_v1' ? '端部 C1 形状外推' : '端部线性外推' }}</span>
            <span><i class="swatch" style="background:#ff2222"></i>截面外壁曲线</span>
          </span>
          <span class="viewport-scroll-hint">滚轮上滑页面，Ctrl+滚轮缩放模型</span>
        </div>

        <div class="viewport" ref="viewportRef">
          <div class="loading-overlay" v-if="analyzing">
            <div class="spinner"></div>
            <div class="progress-container">
              <div class="progress-message">{{ progressMsg || '准备中...' }}</div>
              <div class="progress-bar-bg">
                <div class="progress-bar-fill" :style="{ width: progressPct + '%' }"></div>
              </div>
            </div>
          </div>

          <div class="status-text">{{ statusText }}{{ softwareRendering ? ' · 矢量三维预览' : '' }}</div>

          <div class="cross-section-panel" v-if="csPanelVisible">
            <div class="cs-header">
              <span class="cs-title">截面详情 #{{ csIdx }}</span>
              <button class="cs-close" @click="csPanelVisible = false">&times;</button>
            </div>
            <canvas ref="csCanvasRef" class="cs-canvas" width="300" height="260"></canvas>
            <div class="cs-info">{{ csInfoText }}</div>
          </div>
        </div>
        </div>
      </section>

      <div class="workbench">
        <!-- 壁厚确认 + 壁厚曲线 -->
        <section class="confirm-row" v-if="infoReady">
          <div class="manual-panel">
            <div class="manual-title">壁厚分段确认</div>
            <p class="manual-hint">按工程分段手填壁厚与图纸长度。单段时 TL1 即为图纸总长，必须填写；多段时图纸总长为各填写长度之和。不再用 STP 读取长度自动填充。</p>

            <div class="manual-row">
              <span class="manual-label">壁厚段数</span>
              <div class="seg-choice">
                <button
                  v-for="n in [1, 2, 3, 4]"
                  :key="n"
                  class="seg-btn"
                  :class="{ active: manualSegmentCount === n }"
                  @click="setSegmentCount(n)"
                >{{ n }} 段</button>
              </div>
            </div>

            <div class="manual-row">
              <span class="manual-label">各段壁厚 (mm)</span>
              <div class="thk-inputs with-rec">
                <div class="thk-input-block" v-for="(t, i) in manualThicknesses" :key="`thk-${i}`">
                  <span class="thk-tag" :style="{ background: segColor(i) }">T{{ i + 1 }}</span>
                  <input
                    type="number"
                    class="thk-input"
                    :value="t"
                    step="0.1"
                    min="0"
                    @input="updateThickness(i, $event.target.value)"
                  >
                  <span class="thk-rec">推荐 {{ recommendationText(`T${i + 1}`) }}</span>
                </div>
              </div>
            </div>

            <div class="manual-row">
              <span class="manual-label">图纸长度 (mm)</span>
              <div class="thk-inputs with-rec">
                <div class="thk-input-block" v-for="key in drawingLengthKeys" :key="`draw-${key}`">
                  <span class="thk-tag" :class="{ transition: key.startsWith('TLM') }">{{ key }}</span>
                  <input
                    type="number"
                    class="thk-input"
                    :value="drawingLengths[key] ?? ''"
                    step="0.1"
                    min="0"
                    :placeholder="drawingLengthPlaceholder(key)"
                    @input="updateDrawingLength(key, $event.target.value)"
                  >
                  <span class="thk-rec">推荐 {{ recommendationText(key) }}</span>
                </div>
              </div>
              <div class="drawing-total">
                <template v-if="manualSegmentCount === 1">TL1 即为图纸总长，必须填写。</template>
                <template v-else>图纸总长：{{ fmtLen(drawingLengthTotal) }} mm（各填写长度之和）</template>
              </div>
            </div>

            <div class="manual-row">
              <button class="action-btn primary" :disabled="lengthBusy" @click="handleConfirmWallLength()">
                {{ lengthBusy ? '校验中...' : '确认壁厚并校验长度' }}
              </button>
            </div>
            <p v-if="lengthHint" class="manual-hint">{{ lengthHint }}</p>
            <div v-if="needsTrimSide" class="side-choice">
              <p class="manual-hint">两端截面尺寸接近，请确认截短/补长方向：</p>
              <button class="seg-btn" @click="handleConfirmWallLength('start')">起点端</button>
              <button class="seg-btn" @click="handleConfirmWallLength('end')">终点端</button>
            </div>
          </div>

          <div class="chart-stack">
            <div class="chart-box">
              <div class="chart-title">壁厚 (mm)</div>
              <div class="chart-canvas-wrap">
                <canvas v-if="sectionsFull.length" ref="thicknessChartRef"></canvas>
                <div v-else class="chart-empty-text">请上传模型文件并开始分析</div>
              </div>
            </div>
            <div class="chart-box">
              <div class="chart-title-row">
                <div>
                  <div class="chart-title">截面周长 (mm)</div>
                  <div class="chart-subtitle">{{ perimeterChartSubtitle }}</div>
                </div>
              </div>
              <div class="chart-canvas-wrap">
                <canvas
                  v-if="sectionsFull.length"
                  ref="perimeterChartRef"
                  class="perimeter-chart-canvas"
                ></canvas>
                <div v-else class="chart-empty-text">请上传模型文件并开始分析</div>
              </div>
            </div>
          </div>
        </section>

        <!-- 长度校验：位于形线平滑之前 -->
        <section class="length-row" v-if="infoReady">
          <div class="length-panel">
            <div class="manual-title">长度校验</div>
            <div v-if="lengthValidation" class="length-metrics">
              <div>图纸总长：{{ fmtLen(lengthValidation.drawing_length) }} mm</div>
              <div>STP读取管件长度：{{ fmtLen(lengthValidation.stp_length) }} mm</div>
              <div>长度差异（STP − 图纸）：{{ fmtSigned(lengthValidation.difference) }} mm</div>
              <div>校验状态：{{ lengthStatusLabel }}</div>
              <div v-if="smallEndLabel">小径端：{{ smallEndLabel }}</div>
            </div>
            <p v-else class="manual-hint">确认壁厚分段后，将按图纸总长与 STP 轴向长度进行校验。</p>
            <div v-if="lengthReady && lengthNeedsCorrection" class="split-correction">
              <p class="manual-hint">
                轴向坐标 s 从当前形线起点 0 起算，与关键截面预计/实际位置同一参考端。
                只填小径端修正量；大径端由长度守恒自动计算。正数=该端补长，负数=该端截短。
                留空则保持默认：全部差异由小径端承担。
              </p>
              <div class="thk-inputs">
                <div class="thk-input-block">
                  <span class="thk-tag">小径端修正</span>
                  <input
                    type="number"
                    class="thk-input"
                    v-model.number="smallEndCorrection"
                    step="0.1"
                    placeholder="mm"
                  >
                </div>
                <div class="thk-input-block">
                  <span class="thk-tag">大径端修正</span>
                  <input type="text" class="thk-input" :value="fmtSigned(largeEndCorrection)" disabled>
                </div>
                <button class="seg-btn active" :disabled="lengthBusy" @click="handleApplySplitCorrection">
                  按分配修正形线
                </button>
              </div>
            </div>
            <el-alert
              v-if="lengthAlertText"
              :title="lengthAlertText"
              :type="lengthAlertType"
              :closable="false"
              style="margin-top: 10px"
            />
          </div>
        </section>

        <section class="key-row" v-if="infoReady">
          <div class="key-panel">
            <div class="manual-title">关键截面校验</div>
            <p class="manual-hint">
              请先完成长度校验。在当前形线上查找图纸截面（容差 ±1 mm）。
              预计位置与实际匹配位置使用同一轴向坐标：中心线弧长 s，从形线起点 0 起算。
              位置偏差 = 实际位置 − 预计位置（保留正负号）。
            </p>
            <div class="thk-inputs" style="margin-bottom: 10px">
              <div class="thk-input-block">
                <span class="thk-tag">截面长度</span>
                <input type="number" class="thk-input" v-model.number="keyLength" min="0" step="0.1" placeholder="mm" :disabled="!lengthReady">
              </div>
              <div class="thk-input-block">
                <span class="thk-tag">截面宽度</span>
                <input type="number" class="thk-input" v-model.number="keyWidth" min="0" step="0.1" placeholder="mm" :disabled="!lengthReady">
              </div>
              <div class="thk-input-block">
                <span class="thk-tag">预计位置</span>
                <input type="number" class="thk-input" v-model.number="keyExpectedS" min="0" step="0.1" placeholder="mm" :disabled="!lengthReady">
              </div>
              <button class="seg-btn active" :disabled="keyBusy || !lengthReady" @click="handleSearchKeySection">查找可能位置</button>
              <button class="seg-btn" :disabled="!lengthReady" @click="resetKeyInputs">新增关键截面</button>
            </div>
            <el-alert
              v-if="!lengthReady"
              title="请先完成长度校验，再进行关键截面查找。"
              type="info"
              :closable="false"
              style="margin-bottom: 10px"
            />
            <el-alert
              v-if="qaState?.key_section_stale_message"
              :title="qaState.key_section_stale_message"
              type="warning"
              :closable="false"
              style="margin-bottom: 10px"
            />
            <p v-if="keySearchMessage" class="manual-hint">{{ keySearchMessage }}</p>
            <el-table
              v-if="keyCandidates.length"
              :data="keyCandidates"
              border
              stripe
              max-height="280"
              highlight-current-row
              row-key="range_index"
              @current-change="onKeyCandidateChange"
              style="margin-bottom: 10px"
            >
              <el-table-column label="轴向范围" min-width="160">
                <template #default="{ row }">{{ formatKeyRange(row) }}</template>
              </el-table-column>
              <el-table-column label="点数" width="70">
                <template #default="{ row }">{{ row.point_count || 1 }}</template>
              </el-table-column>
              <el-table-column label="实际长度" min-width="100">
                <template #default="{ row }">{{ Number(row.actual_length).toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="实际宽度" min-width="100">
                <template #default="{ row }">{{ Number(row.actual_width).toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="长度偏差" min-width="100">
                <template #default="{ row }">{{ Number(row.length_delta).toFixed(3) }}</template>
              </el-table-column>
              <el-table-column label="宽度偏差" min-width="100">
                <template #default="{ row }">{{ fmtSigned(row.width_delta) }}</template>
              </el-table-column>
              <el-table-column v-if="hasKeyExpected" label="预计位置" min-width="110">
                <template #default="{ row }">{{ fmtLen(row.expected_s_mm ?? keyExpectedS) }}</template>
              </el-table-column>
              <el-table-column label="实际位置" min-width="110">
                <template #default="{ row }">{{ fmtLen(row.s_mm) }}</template>
              </el-table-column>
              <el-table-column v-if="hasKeyExpected" label="位置偏差" min-width="110">
                <template #default="{ row }">{{ fmtSigned(row.position_delta) }}</template>
              </el-table-column>
            </el-table>
            <p v-if="keyCandidates.length > 1" class="manual-hint">点选一行只保存该段；不点选则保存全部段。</p>
            <button
              v-if="keyCandidates.length"
              class="action-btn primary"
              :disabled="keyBusy"
              @click="handleSaveKeySection"
            >保存校验记录</button>

            <div class="manual-title" style="margin-top: 16px">已保存关键截面校验记录</div>
            <el-empty v-if="!keyRecords.length" description="尚未保存记录" :image-size="48" />
            <el-table v-else :data="keyRecords" border stripe max-height="280">
              <el-table-column label="截面" width="70">
                <template #default="{ row }">#{{ row.id }}</template>
              </el-table-column>
              <el-table-column label="长×宽" min-width="140">
                <template #default="{ row }">
                  {{ Number(row.target_length).toFixed(2) }} × {{ Number(row.target_width).toFixed(2) }}
                </template>
              </el-table-column>
              <el-table-column label="预计位置" min-width="110">
                <template #default="{ row }">{{ row.expected_s_mm == null ? '—' : fmtLen(row.expected_s_mm) + ' mm' }}</template>
              </el-table-column>
              <el-table-column label="实际位置" min-width="110">
                <template #default="{ row }">{{ fmtLen(row.s_mm) }} mm</template>
              </el-table-column>
              <el-table-column label="位置偏差" min-width="110">
                <template #default="{ row }">
                  <span :class="{ stale: row.stale }">{{ row.position_delta == null ? '—' : fmtSigned(row.position_delta) + ' mm' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="" width="70" align="right">
                <template #default="{ row }">
                  <button class="db-row-btn danger" @click="handleDeleteKeySection(row.id)">✕</button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </section>

        <!-- 形线质量检查：位于长度校验与关键截面校验之后 -->
        <section class="qa-row">
          <PpQaPanel
            v-if="sessionId && infoReady"
            ref="qaPanelRef"
            class="qa-in-main"
            :session-id="sessionId"
            @update="onQaUpdate"
            @display="onQaDisplay"
          />
          <div class="qa-placeholder" v-else>
            <div class="manual-title">形线质量检查</div>
            <p class="manual-hint">完成长度校验与关键截面校验后，在此确认直管段、选择采用的形线，并上传人工测量数据。</p>
          </div>
        </section>
      </div>

      <div class="save-bar" v-if="infoReady">
        <div class="grind-allowance">
          <span class="grind-label">加研磨余量</span>
          <el-switch v-model="grindAllowanceOn" />
          <template v-if="grindAllowanceOn">
            <el-input-number
              v-model="grindAllowanceMm"
              :min="0"
              :step="0.1"
              :precision="3"
              :controls="false"
              placeholder="余量"
            />
            <span class="grind-unit">mm</span>
            <span class="save-bar-hint">壁厚增加该余量，外径周长按直径增加两倍余量，内径不变</span>
          </template>
          <span v-else class="save-bar-hint">确认壁厚分段与形线来源后保存</span>
        </div>
        <button class="action-btn primary" @click="handleSaveToDb">保存到数据库</button>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ppApi } from '../../api/pp'
import PpDevQaPanel from '../../components/PpDevQaPanel.vue'
import PpQaPanel from '../../components/PpQaPanel.vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { SVGRenderer } from 'three/examples/jsm/renderers/SVGRenderer.js'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { Chart, registerables } from 'chart.js'
Chart.register(...registerables)

// ── State ──
const isDevBuild = import.meta.env.DEV
const qaPanelRef = ref(null)
const qaState = ref(null)
const qaShowRaw = ref(true)
const qaShowPreview = ref(false)
const loadMode = ref('upload')
const algorithm = ref('A')
const endTrimStart = ref('')
const endTrimEnd = ref('')
const subdivisionLevel = ref(2)
const sampleInterval = ref(1.0)
const selectedFile = ref(null)
const isDragOver = ref(false)
const analyzing = ref(false)
const grindAllowanceOn = ref(false)
const grindAllowanceMm = ref(0.2)
const sessionId = ref('')
const progressPct = ref(0)
const progressMsg = ref('')
const statusText = ref('就绪')

// DB mode
const dbSearch = ref('')
const dbTubes = ref([])
const selectedDbId = ref(null)
const dbSortKey = ref('created_desc')
const loadingDbId = ref(null)
const dbListExpanded = ref(false)

const sortedTubes = computed(() => {
  const arr = [...dbTubes.value]
  const k = dbSortKey.value
  const num = (v) => Number(v || 0)
  const name = (t) => String(t.显示名称 || t.文件名 || '')
  const time = (t) => String(t.创建时间 || '')
  switch (k) {
    case 'created_asc': arr.sort((a, b) => time(a).localeCompare(time(b))); break
    case 'name_asc': arr.sort((a, b) => name(a).localeCompare(name(b))); break
    case 'name_desc': arr.sort((a, b) => name(b).localeCompare(name(a))); break
    case 'length_desc': arr.sort((a, b) => num(b['中心线长度(mm)']) - num(a['中心线长度(mm)'])); break
    case 'length_asc': arr.sort((a, b) => num(a['中心线长度(mm)']) - num(b['中心线长度(mm)'])); break
    default: arr.sort((a, b) => time(b).localeCompare(time(a)))
  }
  return arr
})
const visibleSortedTubes = computed(() => {
  const full = sortedTubes.value || []
  return dbListExpanded.value ? full : full.slice(0, 2)
})

// Results
const infoReady = ref(false)
const fileName = ref('')
const centerlineLength = ref(0)
const analysisQuality = ref(null)
const softwareRendering = ref(false)
const sectionsFull = ref([])
const thicknessStats = ref(null)
const hoveredSectionIdx = ref(-1)
const sectionTableExpanded = ref(false)

// Visibility
const showMesh = ref(true)
const showCenterline = ref(true)
const showSections = ref(true)
const meshOpacity = ref(0.28)

// Cross-section panel
const csPanelVisible = ref(false)
const csIdx = ref(-1)
const csInfoText = ref('')
const csCanvasRef = ref(null)

// Chart refs
const perimeterChartRef = ref(null)
const thicknessChartRef = ref(null)
let perimChart = null
let thickChart = null

// ── Manual wall-thickness segmentation ──
// We use N segments (1..4) with 2*(N-1) movable boundaries between
// stable thickness zones; transitions linearly interpolate.
const manualSegmentCount = ref(2)
const manualThicknesses = ref([])
const drawingLengths = ref({ TL1: null, TLM1: null, TL2: null, TLM2: null, TL3: null, TLM3: null, TL4: null })
const wallRecommendations = ref({
  T1: null, T2: null, T3: null, T4: null,
  TL1: null, TLM1: null, TL2: null, TLM2: null, TL3: null, TLM3: null, TL4: null,
})
const lengthBusy = ref(false)
const lengthHint = ref('')
const needsTrimSide = ref(false)
const keyLength = ref(null)
const keyWidth = ref(null)
const keyExpectedS = ref(null)
const smallEndCorrection = ref(null)
const keyBusy = ref(false)
const keyCandidates = ref([])
const keySearchMessage = ref('')
const selectedKeyCandidate = ref(null)
const segPalette = ['#1478ff', '#5fb8b0', '#ffa726', '#ef5350']

// File input ref
const fileInputRef = ref(null)

// Three.js
const viewportRef = ref(null)
let animationFrame
let scene, camera, renderer, controls
let meshObj = null, centerObj = null, sectionsObj = null, highlightObj = null
let sectionLineMap = new Map() // sparse index → Three.js LineLoop, used for hover-highlighting
let resizeObserver = null

// Timers
let pollTimer = null

// Computed metrics
const perimAvg = computed(() => {
  if (!sectionsFull.value.length) return '--'
  const sum = sectionsFull.value.reduce((a, s) => a + (s.perimeter || 0), 0)
  return (sum / sectionsFull.value.length).toFixed(1)
})
const perimMin = computed(() => {
  if (!sectionsFull.value.length) return '--'
  return Math.min(...sectionsFull.value.map(s => s.perimeter || 0)).toFixed(1)
})
const perimMax = computed(() => {
  if (!sectionsFull.value.length) return '--'
  return Math.max(...sectionsFull.value.map(s => s.perimeter || 0)).toFixed(1)
})

// 站位来源：实测 / 过渡段加密实测 / 1 mm 插值 / 端部外推
const interpolatedCount = computed(() => sectionsFull.value.filter(
  s => s.measurement_status === 'interpolated' || s.station_source === 'interpolated',
).length)
const measuredCount = computed(() => sectionsFull.value.length - interpolatedCount.value)
// 切面总数（种子 + 3 轮校正 + 精测 + 过渡段加密），看速度时比站数直观
const cutCount = computed(() => Object.values(analysisQuality.value?.cuts || {})
  .reduce((sum, v) => sum + (Number(v) || 0), 0))

function sectionSourceLabel(sec) {
  if (sec.measurement_status === 'extrapolated') return '外推'
  if (sec.measurement_status === 'needs_review') return '待复核'
  if (sec.measurement_status === 'interpolated' || sec.station_source === 'interpolated') return '插值'
  if (sec.station_source === 'refined') return '加密实测'
  return '实测'
}

function sectionSourceHint(sec) {
  if (sec.measurement_status === 'extrapolated') return '端部按实测形状梯度 C1 外推，长度坐标完整但数值非实测'
  if (sec.measurement_status === 'needs_review') return '截面法向偏差 >2°、最薄壁 <10% 平均壁厚或形心残差 >0.2 mm，需人工复核'
  if (sec.contour_inherited_from_mm != null) {
    return `周长/壁厚由 PCHIP 插值；截面轮廓沿用 ${Number(sec.contour_inherited_from_mm).toFixed(1)} mm 处实测站`
  }
  return 'CAD 原生截面实测'
}

const perimeterChartSubtitle = computed(() => {
  const status = qaState.value?.status
  if (status === 'QA_PASS') {
    return '绿带为已确认直管段；实线为平滑并确认后的形线；灰虚线=原始 STP 形线。'
  }
  if (status === 'QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION') {
    return '绿带为候选直管段；实线为当前工作形线（含已应用的平滑）。'
  }
  if (status === 'QA_LOCAL_ANOMALY' || status === 'QA_MANUAL_REQUIRED' || status === 'QA_RECHECK_FAILED') {
    return '红带为可疑段；灰虚线=原始形线，橙虚线=建议平滑。'
  }
  return '截面周长沿中心线变化。'
})

// ── Manual segmentation: helpers + computed ──
function segColor(i) { return segPalette[i % segPalette.length] }

function drawingKeysForCount(n) {
  const keys = ['TL1']
  for (let k = 1; k < n; k++) {
    keys.push(`TLM${k}`)
    keys.push(`TL${k + 1}`)
  }
  return keys
}
const drawingLengthKeys = computed(() => drawingKeysForCount(manualSegmentCount.value))
const drawingLengthTotal = computed(() => {
  let sum = 0
  drawingLengthKeys.value.forEach((key) => {
    const n = Number(drawingLengths.value[key])
    if (Number.isFinite(n) && n > 0) sum += n
  })
  return sum
})
function roundWall2(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  return Math.round(n * 100) / 100
}

function wallThicknessAt(curve, position) {
  const points = []
  for (const point of curve || []) {
    const x = Number(point?.cumulative_length)
    const y = Number(point?.wall_thickness)
    if (Number.isFinite(x) && Number.isFinite(y)) points.push({ x, y })
  }
  const target = Number(position)
  if (!points.length || !Number.isFinite(target)) return null
  if (target <= points[0].x) return points[0].y
  const last = points[points.length - 1]
  if (target >= last.x) return last.y
  for (let index = 1; index < points.length; index += 1) {
    const right = points[index]
    const left = points[index - 1]
    if (target <= right.x) {
      const span = right.x - left.x
      const ratio = span > 0 ? (target - left.x) / span : 0
      return left.y + ratio * (right.y - left.y)
    }
  }
  return last.y
}

function applyPreparedWallCurve(sections, curve) {
  const next = (sections || []).map((sec) => {
    if (!sec || sec.wall_thickness == null) return sec
    const thickness = roundWall2(wallThicknessAt(curve, sec.cumulative_length))
    if (thickness == null) return sec
    return { ...sec, wall_thickness: thickness }
  })
  sectionsFull.value = next
  const values = next.map(sec => sec?.wall_thickness).filter(value => Number.isFinite(value))
  if (values.length && thicknessStats.value) {
    const sorted = [...values].sort((a, b) => a - b)
    const mid = Math.floor(sorted.length / 2)
    const median = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
    const mean = values.reduce((sum, value) => sum + value, 0) / values.length
    thicknessStats.value = {
      ...thicknessStats.value,
      median_mm: roundWall2(median),
      mean_mm: roundWall2(mean),
      min_mm: roundWall2(Math.min(...values)),
      max_mm: roundWall2(Math.max(...values)),
    }
  }
}

function applyWallRecommendations(values) {
  const next = {
    T1: null, T2: null, T3: null, T4: null,
    TL1: null, TLM1: null, TL2: null, TLM2: null, TL3: null, TLM3: null, TL4: null,
  }
  if (values && typeof values === 'object') {
    Object.keys(next).forEach((key) => {
      const n = Number(values[key])
      next[key] = Number.isFinite(n) && n > 0 ? n : null
    })
  }
  wallRecommendations.value = next
}

function recommendationText(key) {
  const value = wallRecommendations.value?.[key]
  if (value == null || !Number.isFinite(Number(value)) || Number(value) <= 0) return '—'
  if (String(key).startsWith('TL')) return String(Math.round(Number(value)))
  return Number(value).toFixed(1)
}

function drawingLengthPlaceholder(key) {
  if (manualSegmentCount.value === 1 && key === 'TL1') return '总长度，必填'
  if (key === 'TL1') return '必填'
  return ''
}
const lengthValidation = computed(() => qaState.value?.length_validation || null)
const lengthReady = computed(() => {
  const s = lengthValidation.value?.status
  return s === 'matched' || s === 'trimmed' || s === 'extended'
})
const keyRecords = computed(() => qaState.value?.key_section_records || [])
const lengthStatusLabel = computed(() => {
  const s = lengthValidation.value?.status
  if (s === 'matched') return '一致'
  if (s === 'trimmed') return 'STP过长'
  if (s === 'extended') return 'STP不足'
  if (s === 'ambiguous_end') return '待确认截短方向'
  return '未校验'
})
const lengthAlertType = computed(() => {
  const s = lengthValidation.value?.status
  if (s === 'matched') return 'success'
  if (s === 'trimmed' || s === 'extended' || s === 'ambiguous_end') return 'warning'
  return 'info'
})
const lengthAlertText = computed(() => lengthValidation.value?.message || '')
const lengthNeedsCorrection = computed(() => {
  const s = lengthValidation.value?.status
  return s === 'trimmed' || s === 'extended'
})
const smallEndLabel = computed(() => {
  const side = lengthValidation.value?.small_end?.side || lengthValidation.value?.correction_side
  if (side === 'start') return '形线起点端（s = 0）'
  if (side === 'end') return '形线终点端'
  return ''
})
function parseOptionalMm(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

const largeEndCorrection = computed(() => {
  const lv = lengthValidation.value
  if (!lv) return null
  const needed = Number(lv.drawing_length) - Number(lv.stp_length)
  if (!Number.isFinite(needed)) return null
  const small = parseOptionalMm(smallEndCorrection.value)
  if (small == null) return 0
  return needed - small
})
const hasKeyExpected = computed(() => parseOptionalMm(keyExpectedS.value) != null)

function fmtLen(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(3) : '—'
}

function fmtSigned(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  if (Object.is(n, -0) || Math.abs(n) < 5e-12) return '0.000'
  return (n > 0 ? '+' : '') + n.toFixed(3)
}

function updateDrawingLength(key, v) {
  const num = parseFloat(v)
  drawingLengths.value = {
    ...drawingLengths.value,
    [key]: Number.isFinite(num) ? num : null,
  }
}

function buildWallValues() {
  const values = {
    T1: null, T2: null, T3: null, T4: null,
    TL1: null, TLM1: null, TL2: null, TLM2: null, TL3: null, TLM3: null, TL4: null,
  }
  manualThicknesses.value.forEach((t, i) => {
    values[`T${i + 1}`] = t
  })
  drawingLengthKeys.value.forEach((key) => {
    values[key] = drawingLengths.value[key]
  })
  return values
}

function profileSpans() {
  const spans = []
  let cursor = 0
  for (const key of drawingKeysForCount(manualSegmentCount.value)) {
    const length = Number(drawingLengths.value[key])
    if (!Number.isFinite(length) || length <= 0) break
    const transition = key.startsWith('TLM')
    const index = Number(key.replace(/\D/g, '')) - 1
    const tStart = Number(manualThicknesses.value[index])
    const tEnd = transition ? Number(manualThicknesses.value[index + 1]) : tStart
    if (!Number.isFinite(tStart) || (transition && !Number.isFinite(tEnd))) break
    spans.push({ start: cursor, end: cursor + length, tStart, tEnd, transition })
    cursor += length
  }
  return spans
}

function manualThicknessAt(x) {
  const pos = Number(x)
  if (!Number.isFinite(pos)) return null
  for (const span of profileSpans()) {
    if (pos + 1e-9 < span.start || pos - 1e-9 > span.end) continue
    if (!span.transition || span.end <= span.start) return span.tStart
    const ratio = (pos - span.start) / (span.end - span.start)
    return span.tStart + (span.tEnd - span.tStart) * ratio
  }
  return null
}

function hasManualConfirmedCurve() {
  const n = manualSegmentCount.value
  const t1 = Number(manualThicknesses.value[0])
  const tl1 = Number(drawingLengths.value.TL1)
  return (
    n >= 1 &&
    manualThicknesses.value.length === n &&
    manualThicknesses.value.every(v => Number.isFinite(Number(v))) &&
    Number.isFinite(t1) && t1 > 0 &&
    Number.isFinite(tl1) && tl1 > 0
  )
}

function sectionThicknessForDisplay(sec) {
  if (!sec) return null
  if (!hasManualConfirmedCurve()) return sec.wall_thickness ?? null
  const t = manualThicknessAt(sec.cumulative_length || 0)
  return Number.isFinite(t) ? t : (sec.wall_thickness ?? null)
}

function setSegmentCount(n) {
  manualSegmentCount.value = n

  // Pre-fill thicknesses from analysis median, preserving any
  // user-edited values from a previous segment count.
  const fallback = Number(((thicknessStats.value?.median_mm) ?? 1.0).toFixed(2))
  const old = manualThicknesses.value
  manualThicknesses.value = Array.from({ length: n }, (_, i) =>
    (old && old[i] != null && Number.isFinite(old[i])) ? old[i] : fallback
  )
  const nextDraw = { TL1: null, TLM1: null, TL2: null, TLM2: null, TL3: null, TLM3: null, TL4: null }
  drawingKeysForCount(n).forEach((key) => {
    nextDraw[key] = drawingLengths.value[key]
  })
  drawingLengths.value = nextDraw
}

function updateThickness(i, v) {
  const num = parseFloat(v)
  if (!Number.isFinite(num) || num < 0) return
  const arr = [...manualThicknesses.value]
  arr[i] = num
  manualThicknesses.value = arr
}

function thicknessAxisLimits(values) {
  const nums = values.map(Number).filter(value => Number.isFinite(value))
  if (!nums.length) return null
  const low = Math.min(...nums)
  const high = Math.max(...nums)
  const span = high - low
  const pad = span > 1e-9 ? span / 3 : Math.max(Math.abs(low) * 0.2, 0.2)
  return { min: low - pad, max: high + pad }
}

function applyThicknessAxis(chart) {
  if (!chart?.options?.scales?.y) return
  const values = []
  for (const dataset of chart.data.datasets || []) {
    for (const value of dataset.data || []) values.push(value)
  }
  const limits = thicknessAxisLimits(values)
  if (!limits) return
  chart.options.scales.y.min = limits.min
  chart.options.scales.y.max = limits.max
}

function updateManualOverlay() {
  if (!thickChart || !sectionsFull.value.length) return
  const valid = sectionsFull.value.filter(s => s.wall_thickness != null)
  if (!valid.length) return
  const overlay = valid.map((s) => {
    const thickness = manualThicknessAt(s.cumulative_length || 0)
    return Number.isFinite(thickness) ? thickness : null
  })
  const existing = thickChart.data.datasets.findIndex(d => d._role === 'manual')
  const meta = {
    _role: 'manual',
    label: '用户分段',
    data: overlay,
    borderColor: '#3366ff',
    backgroundColor: 'rgba(51, 102, 255, 0.06)',
    borderWidth: 2.2,
    borderDash: [6, 4],
    pointRadius: 0,
    fill: false,
    tension: 0,
    spanGaps: false,
  }
  if (existing >= 0) {
    thickChart.data.datasets[existing] = meta
  } else {
    thickChart.data.datasets.push(meta)
  }
  applyThicknessAxis(thickChart)
  thickChart.update('none')
}

function buildManualSegmentsPayload() {
  const final_curve = (sectionsFull.value || []).map(sec => ({
    s: Number(sec.cumulative_length || 0),
    wall_thickness: sectionThicknessForDisplay(sec),
  }))
  const perimeter_curve = (sectionsFull.value || []).map(sec => ({
    s: Number(sec.cumulative_length || 0),
    perimeter: Number(sec.perimeter || 0),
    wall_thickness: sectionThicknessForDisplay(sec),
  }))
  return {
    count: manualSegmentCount.value,
    thicknesses: [...manualThicknesses.value],
    boundaries: profileSpans().slice(0, -1).map(span => span.end),
    drawing_lengths: { ...drawingLengths.value },
    centerline_length_mm: centerlineLength.value,
    final_curve,
    perimeter_curve,
  }
}

function applyQaPayload(qa) {
  qaState.value = qa || null
  qaPanelRef.value?.applyQa?.(qa)
  keyCandidates.value = qa?.key_section_search?.candidates || keyCandidates.value
  if (qa?.key_section_search?.message) keySearchMessage.value = qa.key_section_search.message
  renderPerimeterChart()
}

async function handleConfirmWallLength(correctionSide = null, applySplit = false) {
  if (!sessionId.value) {
    ElMessage.warning('请先完成 STP 读取')
    return
  }
  const values = buildWallValues()
  const t1 = Number(values.T1)
  const tl1 = Number(values.TL1)
  if (Number.isFinite(t1) && t1 > 0 && !(Number.isFinite(tl1) && tl1 > 0)) {
    ElMessage.error('请填写壁厚 T1 对应的长度 TL1。')
    return
  }
  lengthBusy.value = true
  lengthHint.value = ''
  try {
    const extra = {}
    const small = parseOptionalMm(smallEndCorrection.value)
    if (applySplit && small != null) {
      extra.small_end_correction_mm = small
    }
    const res = await ppApi.qaConfirmWallLength(sessionId.value, values, correctionSide, '', extra)
    if (!res.data?.success) {
      ElMessage.error(res.data?.message || '壁厚确认失败')
      return
    }
    needsTrimSide.value = !!res.data.needs_side
    applyQaPayload(res.data.qa)
    const drawn = Number(
      res.data.centerline_length
      || res.data.qa?.centerline_length_mm
      || res.data.qa?.length_validation?.drawing_length
      || 0
    )
    if (drawn > 0) centerlineLength.value = drawn
    try {
      const info = await ppApi.sessionInfo(sessionId.value)
      if (info.data?.success) {
        if (info.data.sections_full?.length) applyPreparedWallCurve(info.data.sections_full, info.data.wall_curve)
        if (info.data.wall_recommendations) applyWallRecommendations(info.data.wall_recommendations)
        if (Number(info.data.centerline_length) > 0) {
          centerlineLength.value = Number(info.data.centerline_length)
        }
      }
    } catch (_) { /* keep QA drawing length already applied */ }
    const lv = res.data.length_validation || res.data.qa?.length_validation
    if (lv?.small_end_correction_mm != null && lv.status !== 'matched') {
      smallEndCorrection.value = Number(lv.small_end_correction_mm)
    }
    if (lv?.message) lengthHint.value = lv.message
    if (res.data.qa?.key_section_stale_message) {
      ElMessage.warning(res.data.qa.key_section_stale_message)
    }
    if (lv?.status === 'matched') ElMessage.success('长度校验通过。')
    else if (lv?.status === 'trimmed' || lv?.status === 'extended') ElMessage.warning(lv.message)
    else if (lv?.status === 'ambiguous_end') ElMessage.warning(lv.message)
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '壁厚确认失败')
  } finally {
    lengthBusy.value = false
  }
}

async function handleApplySplitCorrection() {
  const small = parseOptionalMm(smallEndCorrection.value)
  if (small == null) {
    ElMessage.warning('请输入小径端修正量；留空请使用「确认壁厚并校验长度」的默认全部由小径端承担。')
    return
  }
  await handleConfirmWallLength(lengthValidation.value?.correction_side || null, true)
}

function resetKeyInputs() {
  keyLength.value = null
  keyWidth.value = null
  keyExpectedS.value = null
  keyCandidates.value = []
  keySearchMessage.value = ''
  selectedKeyCandidate.value = null
}

function onKeyCandidateChange(row) {
  selectedKeyCandidate.value = row || null
}

function formatKeyRange(row) {
  if (!row) return ''
  const start = Number(row.s_start_mm ?? row.s_mm)
  const end = Number(row.s_end_mm ?? row.s_mm)
  const count = Number(row.point_count || 1)
  if (!Number.isFinite(start) || !Number.isFinite(end)) return ''
  const span = Math.abs(end - start) < 0.05
    ? `${start.toFixed(2)} mm`
    : `${start.toFixed(2)} – ${end.toFixed(2)} mm`
  return count > 1 ? `${span}（${count} 点）` : span
}

async function handleSearchKeySection() {
  if (!sessionId.value) return
  if (!lengthReady.value) {
    ElMessage.warning('请先完成长度校验，再进行关键截面查找。')
    return
  }
  const L = Number(keyLength.value)
  const W = Number(keyWidth.value)
  if (!(L > 0) || !(W > 0)) {
    ElMessage.warning('请输入截面长度和截面宽度')
    return
  }
  keyBusy.value = true
  selectedKeyCandidate.value = null
  try {
    const res = await ppApi.qaSearchKeySection(sessionId.value, L, W, keyExpectedS.value)
    applyQaPayload(res.data.qa)
    keyCandidates.value = res.data.candidates || []
    keySearchMessage.value = res.data.message || ''
    selectedKeyCandidate.value = keyCandidates.value.length === 1 ? keyCandidates.value[0] : null
    if (!keyCandidates.value.length) {
      ElMessage.warning(res.data.message || '在当前尺寸容差范围内未找到匹配截面。')
    } else if (res.data.message) {
      ElMessage.success(res.data.message)
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '关键截面搜索失败')
  } finally {
    keyBusy.value = false
  }
}

async function handleSaveKeySection() {
  if (!sessionId.value) return
  if (!keyCandidates.value.length) {
    ElMessage.warning('请先查找关键截面')
    return
  }
  keyBusy.value = true
  try {
    const payload = selectedKeyCandidate.value
      ? { candidate: selectedKeyCandidate.value }
      : { save_all: true }
    const expected = parseOptionalMm(keyExpectedS.value)
    if (expected != null) {
      payload.expected_s_mm = expected
    }
    const res = await ppApi.qaSaveKeySection(sessionId.value, payload)
    applyQaPayload(res.data.qa)
    resetKeyInputs()
    ElMessage.success('已保存关键截面校验记录')
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '保存失败')
  } finally {
    keyBusy.value = false
  }
}

async function handleDeleteKeySection(recordId) {
  if (!sessionId.value) return
  try {
    const res = await ppApi.qaDeleteKeySection(sessionId.value, recordId)
    applyQaPayload(res.data.qa)
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '删除失败')
  }
}

// ── Three.js Init ──
function initScene() {
  if (!viewportRef.value) return

  scene = new THREE.Scene()
  scene.background = new THREE.Color(0xf0f2f5)

  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000)
  camera.position.set(200, 150, 200)

  try { renderer = new THREE.WebGLRenderer({ antialias: true }) }
  catch { softwareRendering.value = true; renderer = new SVGRenderer() }
  renderer.setPixelRatio?.(Math.min(window.devicePixelRatio, 2))
  viewportRef.value.appendChild(renderer.domElement)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = !softwareRendering.value
  if (softwareRendering.value) controls.addEventListener('change', renderSoftware)
  controls.dampingFactor = 0.08
  // Wheel over the canvas should scroll the page; Ctrl+wheel still zooms the model.
  controls.enableZoom = false
  renderer.domElement.addEventListener('wheel', (event) => {
    if (event.ctrlKey) {
      controls.enableZoom = true
      event.preventDefault()
    } else {
      controls.enableZoom = false
    }
  }, { capture: true, passive: false })

  // Lights
  scene.add(new THREE.AmbientLight(0xffffff, 0.8))
  const dl1 = new THREE.DirectionalLight(0xffffff, 1.0)
  dl1.position.set(1, 2, 1.5)
  scene.add(dl1)
  const dl2 = new THREE.DirectionalLight(0xd0d8e8, 0.4)
  dl2.position.set(-1, 0.5, -1)
  scene.add(dl2)

  // Grid
  const grid = new THREE.GridHelper(500, 50, 0xc0c4cc, 0xe0e3e8)
  grid.name = 'baseGrid'
  if (!softwareRendering.value) scene.add(grid)

  onResize()
  window.addEventListener('resize', onResize)

  // Use ResizeObserver for reliable resize when layout changes (sidebar toggle, etc.)
  resizeObserver = new ResizeObserver(() => {
    if (!viewportRef.value || !renderer) return
    const w = viewportRef.value.clientWidth
    const h = viewportRef.value.clientHeight
    if (w > 0 && h > 0) {
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
      renderSoftware()
    }
  })
  resizeObserver.observe(viewportRef.value)

  if (softwareRendering.value) renderSoftware()
  else animate()
}

function onResize() {
  if (!viewportRef.value || !renderer) return
  const w = viewportRef.value.clientWidth
  const h = viewportRef.value.clientHeight
  if (w > 0 && h > 0) {
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
    renderSoftware()
  }
}

function renderSoftware() {
  if (!softwareRendering.value) return
  cancelAnimationFrame(animationFrame)
  animationFrame = requestAnimationFrame(() => renderer?.render(scene, camera))
}

function animate() {
  animationFrame = requestAnimationFrame(animate)
  controls?.update()
  renderer?.render(scene, camera)
}

function clearScene({ keepCenterline = false, keepSections = false } = {}) {
  const names = ['tubeMesh', 'highlightSection']
  if (!keepCenterline) names.push('centerLine')
  if (!keepSections) names.push('sectionOutlines')
  names.forEach(name => {
    const obj = scene?.getObjectByName(name)
    if (obj) {
      obj.traverse(c => { c.geometry?.dispose(); c.material?.dispose() })
      scene.remove(obj)
    }
  })
  meshObj = null
  highlightObj = null
  if (!keepCenterline) centerObj = null
  if (!keepSections) { sectionsObj = null; sectionLineMap.clear() }
}

// Wipe every piece of session state so the workspace looks like a
// freshly-mounted page (no 3D, no charts, no cross-section panel).
function resetWorkspace() {
  clearScene()
  infoReady.value = false
  sessionId.value = ''
  fileName.value = ''
  centerlineLength.value = 0
  sectionsFull.value = []
  thicknessStats.value = null
  analysisQuality.value = null
  hoveredSectionIdx.value = -1
  sectionTableExpanded.value = false
  manualSegmentCount.value = 2
  manualThicknesses.value = []
  drawingLengths.value = { TL1: null, TLM1: null, TL2: null, TLM2: null, TL3: null, TLM3: null, TL4: null }
  applyWallRecommendations(null)
  lengthHint.value = ''
  needsTrimSide.value = false
  keyLength.value = null
  keyWidth.value = null
  keyExpectedS.value = null
  smallEndCorrection.value = null
  keyCandidates.value = []
  keySearchMessage.value = ''
  selectedKeyCandidate.value = null
  csPanelVisible.value = false
  csIdx.value = -1
  csInfoText.value = ''
  selectedFile.value = null
  statusText.value = '就绪'
  progressPct.value = 0
  progressMsg.value = ''
  selectedDbId.value = null
  dbListExpanded.value = false
  if (fileInputRef.value) fileInputRef.value.value = ''
  try { perimChart?.destroy?.(); perimChart = null } catch (_) {}
  try { thickChart?.destroy?.(); thickChart = null } catch (_) {}
}

function displayMesh(meshData, { fitCamera = true } = {}) {
  // Remove only the existing mesh; preserve centerline/sections.
  const existing = scene.getObjectByName('tubeMesh')
  if (existing) {
    existing.traverse(c => { c.geometry?.dispose(); c.material?.dispose() })
    scene.remove(existing)
    meshObj = null
  }

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(meshData.vertices, 3))
  if (meshData.faces && meshData.faces.length > 0) {
    geometry.setIndex(new THREE.Uint32BufferAttribute(meshData.faces, 1))
  }
  geometry.computeVertexNormals()

  const Material = softwareRendering.value ? THREE.MeshBasicMaterial : THREE.MeshStandardMaterial
  const material = new Material({
    color: 0x9da7b2, ...(softwareRendering.value ? {} : { roughness: 0.72, metalness: 0.1 }),
    transparent: true, opacity: meshOpacity.value, side: THREE.DoubleSide,
  })
  meshObj = new THREE.Mesh(geometry, material)
  meshObj.name = 'tubeMesh'
  meshObj.renderOrder = 0
  scene.add(meshObj)

  if (fitCamera) {
    geometry.computeBoundingSphere()
    const bs = geometry.boundingSphere
    const maxDim = bs.radius * 3
    camera.position.set(bs.center.x + maxDim * 0.5, bs.center.y - maxDim * 1.25, bs.center.z + maxDim * 0.45)
    camera.near = Math.max(0.1, maxDim / 200)
    camera.far = maxDim * 20
    camera.updateProjectionMatrix()
    controls.target.copy(bs.center)
    controls.update()
  }
}

function displayCenterLine(points) {
  // Remove previous centerline
  const old = scene.getObjectByName('centerLine')
  if (old) {
    old.traverse(c => { c.geometry?.dispose(); c.material?.dispose() })
    scene.remove(old)
    centerObj = null
  }
  if (!points || points.length < 2) return

  const pos = points.flatMap(p => [p[0], p[1], p[2]])
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))

  centerObj = new THREE.Group()
  centerObj.name = 'centerLine'
  centerObj.renderOrder = 5

  const lineMat = new THREE.LineBasicMaterial({ color: 0x1478ff, depthTest: false })
  const line = new THREE.Line(g, lineMat)
  centerObj.add(line)
  if (sectionsFull.value.length === points.length) {
    for (const side of ['start', 'end']) {
      const indices = sectionsFull.value.map((sec, i) => sec.extrapolation_side === side ? i : -1).filter(i => i >= 0)
      if (!indices.length) continue
      const a = Math.max(0, indices[0] - 1), b = Math.min(points.length - 1, indices[indices.length - 1] + 1)
      const eg = new THREE.BufferGeometry().setFromPoints(points.slice(a, b + 1).map(p => new THREE.Vector3(...p)))
      const el = new THREE.Line(eg, new THREE.LineBasicMaterial({ color: 0xed901a, depthTest: false, linewidth: 3 }))
      centerObj.add(el)
    }
  }

  const ptsMat = new THREE.PointsMaterial({ color: 0x1478ff, size: 2, sizeAttenuation: false, depthTest: false })
  centerObj.add(new THREE.Points(g.clone(), ptsMat))

  centerObj.children.forEach(c => { c.renderOrder = 100 })
  scene.add(centerObj)
}

function displaySections(sections) {
  // Remove previous sections + cached map
  const old = scene.getObjectByName('sectionOutlines')
  if (old) {
    old.traverse(c => { c.geometry?.dispose(); c.material?.dispose() })
    scene.remove(old)
    sectionsObj = null
  }
  sectionLineMap.clear()
  const list = sections || []
  if (!list.length) return

  sectionsObj = new THREE.Group()
  sectionsObj.name = 'sectionOutlines'
  sectionsObj.renderOrder = 4

  for (let si = 0; si < list.length; si++) {
    const sec = list[si]
    const outline = sectionOutlinePoints(sec, si, list)
    if (!outline || outline.length < 3) continue
    const edgePos = outline.flatMap(p => [p[0], p[1], p[2]])
    const edgeG = new THREE.BufferGeometry()
    edgeG.setAttribute('position', new THREE.Float32BufferAttribute(edgePos, 3))
    const lineMat = new THREE.LineBasicMaterial({
      color: 0xff2244,
      transparent: true,
      opacity: 0.9,
      depthTest: false,
    })
    const lineLoop = new THREE.LineLoop(edgeG, lineMat)
    lineLoop.userData = { sectionIndex: sec.index ?? si, baseColor: 0xff2244 }
    sectionsObj.add(lineLoop)

    const idxKey = sec.index ?? si
    sectionLineMap.set(idxKey, lineLoop)
  }
  if (sectionsObj.children.length) scene.add(sectionsObj)
  else {
    sectionsObj = null
  }
}

function sectionOutlinePoints(sec, si, list) {
  const raw = sec?.outline || sec?.outline_3d
  if (Array.isArray(raw) && raw.length >= 3) return raw
  return synthesizeCircleOutline(sec, si, list)
}

function pickSparseSections(sparse, full, stepMm = 25) {
  const hasGeom = (sec) => {
    const raw = sec?.outline || sec?.outline_3d
    return (Array.isArray(raw) && raw.length >= 3)
      || (Array.isArray(sec?.position) && Number(sec?.perimeter) > 0)
  }
  const fromSparse = (sparse || []).filter(hasGeom)
  if (fromSparse.length) return fromSparse
  const src = full || []
  if (!src.length) return []
  const picked = []
  let next = 0
  for (let i = 0; i < src.length; i++) {
    const sec = src[i]
    if (!hasGeom(sec)) continue
    const s = Number(sec.cumulative_length || 0)
    if (s + 1e-6 >= next) {
      picked.push({ ...sec, index: sec.index ?? i })
      next = s + stepMm
    }
  }
  const last = src[src.length - 1]
  if (last && hasGeom(last)) {
    const lastS = Number(last.cumulative_length || 0)
    const prevS = Number(picked[picked.length - 1]?.cumulative_length)
    if (!picked.length || Math.abs(lastS - prevS) > 0.51) {
      picked.push({ ...last, index: last.index ?? src.length - 1 })
    }
  }
  return picked
}

function synthesizeCircleOutline(sec, si, list) {
  const pos = sec?.position
  const peri = Number(sec?.perimeter || 0)
  if (!Array.isArray(pos) || pos.length < 3 || !(peri > 0)) return null
  const r = peri / (2 * Math.PI)
  const src = (list && list.length > 1) ? list : sectionsFull.value
  const idx = Math.max(0, Math.min((src || []).length - 1, si))
  const before = (src && src[Math.max(0, idx - 1)]) || sec
  const after = (src && src[Math.min((src || []).length - 1, idx + 1)]) || sec
  let tx = (after.position?.[0] ?? pos[0]) - (before.position?.[0] ?? pos[0])
  let ty = (after.position?.[1] ?? pos[1]) - (before.position?.[1] ?? pos[1])
  let tz = (after.position?.[2] ?? pos[2]) - (before.position?.[2] ?? pos[2])
  const tn = Math.hypot(tx, ty, tz) || 1
  tx /= tn; ty /= tn; tz /= tn
  const hx = Math.abs(tx) < 0.9 ? 1 : 0
  const hy = Math.abs(tx) < 0.9 ? 0 : 1
  let ux = ty * 0 - tz * hy
  let uy = tz * hx - tx * 0
  let uz = tx * hy - ty * hx
  const un = Math.hypot(ux, uy, uz) || 1
  ux /= un; uy /= un; uz /= un
  const vx = ty * uz - tz * uy
  const vy = tz * ux - tx * uz
  const vz = tx * uy - ty * ux
  const pts = []
  const n = 48
  for (let k = 0; k <= n; k++) {
    const a = (k / n) * Math.PI * 2
    const c = Math.cos(a) * r
    const s = Math.sin(a) * r
    pts.push([
      pos[0] + c * ux + s * vx,
      pos[1] + c * uy + s * vy,
      pos[2] + c * uz + s * vz,
    ])
  }
  return pts
}

function updateVisibility() {
  if (meshObj) { meshObj.visible = showMesh.value; meshObj.material.opacity = meshOpacity.value }
  if (centerObj) centerObj.visible = showCenterline.value
  if (sectionsObj) sectionsObj.visible = showSections.value
  renderSoftware()
}

function highlightSection(idx) {
  hideHighlight()
  const full = sectionsFull.value
  if (!full || !full[idx]) return
  const sec = full[idx]

  // Reset all sparse outline colors first
  sectionLineMap.forEach(line => {
    if (line.material && line.userData?.baseColor != null) {
      line.material.color.setHex(line.userData.baseColor)
      line.material.opacity = 0.85
    }
  })

  // If this index is a sparse section, light it up brightly in yellow
  const sparseLine = sectionLineMap.get(idx)
  if (sparseLine && sparseLine.material) {
    sparseLine.material.color.setHex(0xffdd00)
    sparseLine.material.opacity = 1.0
  }

  // Always render the full hi-res outline at the hovered position (uses sec.outer_2d → 3D).
  highlightObj = buildHighlightSection(sec)
  if (highlightObj) scene.add(highlightObj)
}

function buildHighlightSection(sec) {
  const group = new THREE.Group()
  group.name = 'highlightSection'
  group.renderOrder = 999

  const cx = sec.position[0], cy = sec.position[1], cz = sec.position[2]

  // Preferred path: backend already gave us an absolute-frame outline.
  let outerWorld = null
  if (sec.outline_3d && sec.outline_3d.length > 2) {
    outerWorld = sec.outline_3d
  } else if (sec.outer_2d && sec.outer_2d.length > 2) {
    // Fallback: project the 2D outline using a local tangent frame built
    // from neighbouring centerline points. Orientation may not be exact
    // for A algorithm, but it gives a reasonable visualisation.
    const full = sectionsFull.value
    const myIdx = full.indexOf(sec)
    const before = full[Math.max(0, myIdx - 1)] || sec
    const after = full[Math.min(full.length - 1, myIdx + 1)] || sec
    let tx = after.position[0] - before.position[0]
    let ty = after.position[1] - before.position[1]
    let tz = after.position[2] - before.position[2]
    const tn = Math.hypot(tx, ty, tz) || 1
    tx /= tn; ty /= tn; tz /= tn
    const hx = Math.abs(tx) < 0.9 ? 1 : 0
    const hy = Math.abs(tx) < 0.9 ? 0 : 1
    let ux = ty * 0 - tz * hy
    let uy = tz * hx - tx * 0
    let uz = tx * hy - ty * hx
    const un = Math.hypot(ux, uy, uz) || 1
    ux /= un; uy /= un; uz /= un
    const vx = ty * uz - tz * uy
    const vy = tz * ux - tx * uz
    const vz = tx * uy - ty * ux
    outerWorld = sec.outer_2d.map(p => ([
      cx + p[0] * ux + p[1] * vx,
      cy + p[0] * uy + p[1] * vy,
      cz + p[0] * uz + p[1] * vz,
    ]))
  }

  if (outerWorld && outerWorld.length > 2) {
    const flat = outerWorld.flat()
    const og = new THREE.BufferGeometry()
    og.setAttribute('position', new THREE.Float32BufferAttribute(flat, 3))
    const om = new THREE.LineBasicMaterial({ color: 0xffdd00, depthTest: false, transparent: true, opacity: 1 })
    const loop = new THREE.LineLoop(og, om)
    loop.renderOrder = 999
    group.add(loop)

    // Translucent yellow disc
    const fanVerts = []
    for (let i = 0; i < outerWorld.length; i++) {
      const a = outerWorld[i]
      const b = outerWorld[(i + 1) % outerWorld.length]
      fanVerts.push(cx, cy, cz, a[0], a[1], a[2], b[0], b[1], b[2])
    }
    const fg = new THREE.BufferGeometry()
    fg.setAttribute('position', new THREE.Float32BufferAttribute(fanVerts, 3))
    const fm = new THREE.MeshBasicMaterial({ color: 0xffdd00, transparent: true, opacity: 0.22, side: THREE.DoubleSide, depthTest: false })
    const fmesh = new THREE.Mesh(fg, fm)
    fmesh.renderOrder = 998
    group.add(fmesh)
  }

  // Centerline marker
  const sg = new THREE.SphereGeometry(1.2, 12, 12)
  const sm = new THREE.MeshBasicMaterial({ color: 0xffdd00, depthTest: false })
  const sphere = new THREE.Mesh(sg, sm)
  sphere.position.set(cx, cy, cz)
  sphere.renderOrder = 999
  group.add(sphere)

  return group
}

function hideHighlight() {
  if (highlightObj) {
    highlightObj.traverse(c => { c.geometry?.dispose(); c.material?.dispose() })
    scene.remove(highlightObj)
    highlightObj = null
  }
  // Reset sparse colors
  sectionLineMap.forEach(line => {
    if (line.material && line.userData?.baseColor != null) {
      line.material.color.setHex(line.userData.baseColor)
      line.material.opacity = 0.85
    }
  })
}

// ── File handling ──
function handleFileSelect(e) {
  const f = e.target.files?.[0]
  if (f) { selectedFile.value = f; statusText.value = `已选择: ${f.name}` }
}
function handleDrop(e) {
  isDragOver.value = false
  const f = e.dataTransfer?.files?.[0]
  if (f && f.name.toLowerCase().match(/\.(stl|stp|step)$/i)) {
    selectedFile.value = f
    statusText.value = `已选择: ${f.name}`
  }
}

// ── Analyze ──
async function handleAnalyze() {
  if (!selectedFile.value) return
  analyzing.value = true
  progressPct.value = 0
  progressMsg.value = '开始分析...'
  clearScene()
  infoReady.value = false

  const formData = new FormData()
  formData.append('file', selectedFile.value)
  formData.append('algorithm', algorithm.value)
  formData.append('end_treatment', JSON.stringify({ start_trim_mm: endTrimStart.value, end_trim_mm: endTrimEnd.value }))
  formData.append('subdivision_level', subdivisionLevel.value)
  formData.append('sample_interval', sampleInterval.value)

  try {
    const res = await ppApi.upload(formData)
    if (!res.data.success) { ElMessage.error(res.data.message || '上传失败'); analyzing.value = false; return }
    sessionId.value = res.data.session_id
    startPolling()
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '上传失败')
    analyzing.value = false
    progressPct.value = 0
    progressMsg.value = ''
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const res = await ppApi.uploadStatus(sessionId.value)
      const p = res.data.progress
      if (!p) return
      progressPct.value = Math.round(p.pct || 0)
      progressMsg.value = p.message || ''
      if (p.done) {
        clearInterval(pollTimer); pollTimer = null; analyzing.value = false
        if (p.error) { ElMessage.error(p.error); statusText.value = '错误' }
        else { await loadSessionInfo() }
      }
    } catch (e) {}
  }, 500)
}

async function loadSessionInfo() {
  try {
    const res = await ppApi.sessionInfo(sessionId.value)
    if (!res.data.success) {
      ElMessage.error('加载会话信息失败')
      return
    }
    const d = res.data
    fileName.value = d.filename || ''
    centerlineLength.value = d.centerline_length || d.actual_z_length || 0
    thicknessStats.value = d.thickness_stats || null
    applyPreparedWallCurve(d.sections_full || [], d.wall_curve)
    applyWallRecommendations(d.wall_recommendations)
    analysisQuality.value = d.analysis_quality || null

    // Stash any previously-saved manual segmentation so the watcher on
    // ``infoReady`` re-applies it after the chart is built.
    const ms = d.manual_segments
    if (ms && Array.isArray(ms.thicknesses)) {
      manualSegmentCount.value = Number(ms.count) || ms.thicknesses.length || 2
      manualThicknesses.value = ms.thicknesses.map(Number)
    }
    if (ms && ms.drawing_lengths && typeof ms.drawing_lengths === 'object') {
      drawingLengths.value = {
        TL1: null, TLM1: null, TL2: null, TLM2: null, TL3: null, TLM3: null, TL4: null,
        ...ms.drawing_lengths,
      }
    }

    infoReady.value = true
    statusText.value = `完成: ${sectionsFull.value.length} 个截面`
    await nextTick()
    renderPerimeterChart()
    renderThicknessChart()
    await nextTick()
    perimChart?.resize()
    thickChart?.resize()
    updateManualOverlay()

    // 3D: Load mesh first (does NOT wipe centerline/sections), then add overlays.
    statusText.value = '渲染3D模型...'
    let meshPreviewFailed = false
    try {
      if (d.mesh_data) {
        displayMesh(d.mesh_data, { fitCamera: true })
      } else {
        // DEV caches omit the large preview mesh. Rebuild it from the saved
        // source through the authenticated API, preserving the measured CAD
        // centerline and contours as independent overlays.
        const response = await ppApi.previewData(sessionId.value)
        const geometry = new STLLoader().parse(response.data)
        try {
          displayMesh({ vertices: geometry.attributes.position.array,
            faces: geometry.index?.array }, { fitCamera: true })
        } finally { geometry.dispose() }
      }
    } catch (e) {
      meshPreviewFailed = true
      console.error('Mesh load error:', e)
    }

    if (d.center_line?.points?.length > 1) {
      displayCenterLine(d.center_line.points)
    }

    const sections = pickSparseSections(
      d.sections,
      d.sections_full,
      Number(d.sparse_step_mm) || 25,
    )
    displaySections(sections)

    updateVisibility()
    statusText.value = `完成: ${sectionsFull.value.length} 个截面 (算法${d.algorithm || 'A'})${meshPreviewFailed ? ' · 原模型预览不可用' : ''}`
    await qaPanelRef.value?.loadQa()
  } catch (e) {
    ElMessage.error('加载会话信息失败: ' + (e.message || '未知错误'))
  }
}

async function onDevRestore(payload) {
  if (!payload?.session_id) return
  sessionId.value = payload.session_id
  clearScene()
  infoReady.value = false
  statusText.value = '加载 DEV 缓存...'
  await loadSessionInfo()
}

async function onDevRefreshed(payload) {
  if (payload?.qa) {
    qaPanelRef.value?.applyQa(payload.qa)
  } else if (sessionId.value) {
    await qaPanelRef.value?.loadQa()
  }
}

function onQaUpdate(next) {
  qaState.value = next || null
  if (next?.status === 'QA_PASS') {
    qaShowPreview.value = false
    qaShowRaw.value = true
  } else if (next?.processed_profile && (next?.status === 'QA_LOCAL_ANOMALY' || next?.status === 'QA_MANUAL_REQUIRED')) {
    qaShowPreview.value = true
  }
  renderPerimeterChart()
}

function onQaDisplay({ showRaw, showPreview } = {}) {
  if (showRaw != null) qaShowRaw.value = showRaw
  if (showPreview != null) qaShowPreview.value = showPreview
  renderPerimeterChart()
}

// ── Charts ──
const chartDefaults = {
  responsive: true,
  maintainAspectRatio: false,
  resizeDelay: 0,
  animation: { duration: 400 },
  plugins: { legend: { display: false }, tooltip: { backgroundColor: 'rgba(255,255,255,0.95)', titleColor: '#303133', bodyColor: '#606266', borderColor: '#dcdfe6', borderWidth: 1, cornerRadius: 4 } },
  scales: { x: { grid: { color: '#e4e7ed' }, ticks: { color: '#909399', font: { size: 10 } } }, y: { grid: { color: '#e4e7ed' }, ticks: { color: '#909399', font: { size: 10 } } } },
}

function lerpSorted(xs, ys, x) {
  const n = xs.length
  if (!n) return null
  if (x <= xs[0]) return ys[0]
  if (x >= xs[n - 1]) return ys[n - 1]
  let lo = 0
  let hi = n - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (xs[mid] <= x) lo = mid
    else hi = mid
  }
  const x0 = xs[lo]
  const x1 = xs[hi]
  const t = (x - x0) / Math.max(1e-12, x1 - x0)
  return ys[lo] + t * (ys[hi] - ys[lo])
}

function chartXValues() {
  const p = activeQaProfile()
  if (p?.s_mm?.length >= 2) return p.s_mm.map(Number)
  return sectionsFull.value.map((sec) => Number(sec.cumulative_length || 0))
}

function profileCircumference(profile) {
  if (!profile) return null
  let circ = profile.outer_circumference_mm
  const d = profile.outer_diameter_mm
  if ((!circ || !circ.length) && d?.length) {
    circ = d.map((v) => (Number(v) || 0) * Math.PI)
  }
  if (!circ?.length) return null
  return circ.map(Number)
}

function circumferenceFromProfile(profile, xValues) {
  const targets = xValues || chartXValues()
  if (!profile || !targets.length) return null
  const ys = profileCircumference(profile)
  const s = profile.s_mm
  if (!ys || !s?.length || ys.length !== s.length) return null
  const xs = s.map(Number)
  const aligned = xs.length === targets.length
    && xs.every((v, i) => Math.abs(v - targets[i]) < 0.51)
  if (aligned) return ys
  return targets.map((x) => lerpSorted(xs, ys, x))
}

function activeQaProfile() {
  const qa = qaState.value
  if (!qa) return null
  if (qa.profile_choice === 'manual') return qa.manual_profile
  if (qa.profile_choice === 'auto') return qa.auto_profile || qa.final_profile || qa.working_profile
  if (qa.status === 'QA_PASS') return qa.final_profile || qa.working_profile
  return qa.smoothed_profile || qa.working_profile || qa.corrected_profile
}

function mainPerimeterLabel() {
  const qa = qaState.value
  if (qa?.profile_choice === 'manual') return '人工形线'
  if (qa?.profile_choice === 'auto' || qa?.status === 'QA_PASS') return '确认后形线'
  if (qa?.profile_source === 'stp_smoothed') return '平滑后形线'
  if (qa?.profile_source === 'stp_length_corrected') return '长度修正后形线'
  if (qa?.profile_source === 'manual_xlsx') return '人工形线'
  return '当前周长'
}

function mainPerimeterData() {
  const p = activeQaProfile()
  const own = profileCircumference(p)
  if (own && p?.s_mm?.length === own.length) return own
  return circumferenceFromProfile(p, chartXValues())
    || sectionsFull.value.map((s) => s.perimeter || 0)
}

function sectionIndexForS(sVal) {
  const arr = sectionsFull.value
  if (!arr.length) return 0
  let lo = 0
  let hi = arr.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (Number(arr[mid].cumulative_length || 0) < sVal) lo = mid + 1
    else hi = mid
  }
  if (lo > 0) {
    const a = Number(arr[lo - 1].cumulative_length || 0)
    const b = Number(arr[lo].cumulative_length || 0)
    return Math.abs(sVal - a) <= Math.abs(sVal - b) ? lo - 1 : lo
  }
  return lo
}

function bandIndexRange(seg) {
  const xs = chartXValues()
  if (seg.start_index != null && seg.end_index != null && activeQaProfile()) {
    return [seg.start_index, seg.end_index]
  }
  if (seg.start_s != null && seg.end_s != null && xs.length) {
    const i0 = xs.reduce((best, v, i) => Math.abs(v - Number(seg.start_s)) < Math.abs(xs[best] - Number(seg.start_s)) ? i : best, 0)
    const i1 = xs.reduce((best, v, i) => Math.abs(v - Number(seg.end_s)) < Math.abs(xs[best] - Number(seg.end_s)) ? i : best, 0)
    return [i0, i1]
  }
  return [seg.start_index ?? 0, seg.end_index ?? 0]
}

function visibleStraightSegments() {
  const qa = qaState.value
  const segs = qa?.straight_segments || []
  if (qa?.status === 'QA_PASS') return segs.filter((s) => s.status === 'confirmed')
  return segs.filter((s) => s.status !== 'cancelled')
}

function buildQaOverlayDatasets() {
  const qa = qaState.value
  if (!qa) return []
  const xValues = chartXValues()
  const out = []
  if (qaShowRaw.value) {
    const raw = circumferenceFromProfile(qa.raw_profile, xValues)
    if (raw) {
      out.push({
        _role: 'qaRaw',
        label: '原始形线',
        data: raw,
        borderColor: '#909399',
        borderWidth: 1.5,
        borderDash: [4, 3],
        pointRadius: 0,
        fill: false,
        tension: 0,
      })
    }
  }
  if (qaShowPreview.value && (qa.status === 'QA_LOCAL_ANOMALY' || qa.status === 'QA_MANUAL_REQUIRED')) {
    const preview = circumferenceFromProfile(qa.processed_profile, xValues)
    if (preview) {
      out.push({
        _role: 'qaPreview',
        label: '建议平滑',
        data: preview,
        borderColor: '#e6a23c',
        borderWidth: 2,
        borderDash: [6, 3],
        pointRadius: 0,
        fill: false,
        tension: 0,
      })
    }
  }
  return out
}

function qaBandPlugin() {
  return {
    id: 'qaBands',
    beforeDraw(chart) {
      const qa = qaState.value
      const { ctx, chartArea, scales } = chart
      if (!chartArea || !scales?.x) return
      const xOf = (idx) => {
        const v = scales.x.getPixelForValue(idx)
        return Math.max(chartArea.left, Math.min(chartArea.right, v))
      }
      const paint = (seg, fill) => {
        const [i0, i1] = bandIndexRange(seg)
        const x0 = xOf(i0)
        const x1 = xOf(i1)
        ctx.fillStyle = fill
        ctx.fillRect(Math.min(x0, x1), chartArea.top, Math.abs(x1 - x0) || 2, chartArea.bottom - chartArea.top)
      }
      ctx.save()
      for (const seg of visibleStraightSegments()) {
        paint(seg, 'rgba(103, 194, 58, 0.22)')
      }
      if (qa?.status === 'QA_LOCAL_ANOMALY' || qa?.status === 'QA_MANUAL_REQUIRED' || qa?.status === 'QA_RECHECK_FAILED') {
        for (const seg of qa.suspect_segments || []) {
          paint(seg, 'rgba(245, 108, 108, 0.20)')
        }
      }
      ctx.restore()
    },
  }
}

function renderPerimeterChart() {
  if (perimChart) perimChart.destroy()
  if (!perimeterChartRef.value) return
  const xValues = chartXValues()
  if (!xValues.length) return

  const labels = xValues.map((s) => Number(s).toFixed(0))
  const passed = qaState.value?.status === 'QA_PASS'
  const datasets = [{
    _role: 'current',
    label: mainPerimeterLabel(),
    data: mainPerimeterData(),
    borderColor: passed ? '#409eff' : '#1478ff',
    backgroundColor: passed ? 'rgba(64,158,255,0.12)' : 'rgba(20,120,255,0.1)',
    borderWidth: passed ? 2.4 : 1.5,
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHoverBackgroundColor: '#ffdd00',
    fill: true,
    tension: passed ? 0 : 0.2,
  }]
  datasets.push(...buildQaOverlayDatasets())

  perimChart = new Chart(perimeterChartRef.value, {
    type: 'line',
    data: { labels, datasets },
    plugins: [qaBandPlugin()],
    options: {
      ...chartDefaults, interaction: { mode: 'index', intersect: false },
      onHover: (event, elements) => {
        if (elements.length > 0) {
          const sVal = xValues[elements[0].index]
          const realIdx = sectionIndexForS(sVal)
          highlightSection(realIdx)
          onSectionHover(realIdx)
        } else { hideHighlight(); onSectionHover(-1) }
      },
      onClick: (event, elements) => {
        if (elements.length > 0) onSectionClick(sectionIndexForS(xValues[elements[0].index]))
      },
      scales: { ...chartDefaults.scales, x: { ...chartDefaults.scales.x, title: { display: true, text: '修正后的轴向位置 / mm', color: '#909399' } }, y: { ...chartDefaults.scales.y, title: { display: true, text: '周长 (mm)', color: '#909399' } } },
      plugins: {
        ...chartDefaults.plugins,
        legend: {
          display: datasets.length > 1 || passed,
          labels: { color: '#606266', font: { size: 10 }, boxWidth: 12 },
        },
      },
    },
  })
}

function renderThicknessChart() {
  if (thickChart) thickChart.destroy()
  if (!thicknessChartRef.value) return

  const valid = sectionsFull.value.filter(s => s.wall_thickness != null)
  if (!valid.length) return

  const labels = valid.map(s => s.cumulative_length?.toFixed(0) || '')
  const dataVals = valid.map(s => s.wall_thickness)
  const medianVal = thicknessStats.value?.median_mm
  const axis = thicknessAxisLimits(medianVal == null ? dataVals : [...dataVals, medianVal])

  const datasets = [{ data: dataVals, borderColor: '#28b45a', backgroundColor: 'rgba(40,180,90,0.1)', borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: '#ffdd00', fill: true, tension: 0 }]
  if (medianVal != null) {
    datasets.push({ data: Array(labels.length).fill(medianVal), borderColor: '#f5be2d', borderWidth: 1.5, borderDash: [6, 3], pointRadius: 0, fill: false })
  }

  thickChart = new Chart(thicknessChartRef.value, {
    type: 'line',
    data: { labels, datasets },
    options: {
      ...chartDefaults, interaction: { mode: 'index', intersect: false },
      onHover: (event, elements) => {
        if (elements.length > 0) {
          const chartIdx = elements[0].index
          const section = valid[chartIdx]
          const realIdx = sectionsFull.value.indexOf(section)
          if (realIdx >= 0) { highlightSection(realIdx); onSectionHover(realIdx) }
        } else { hideHighlight(); onSectionHover(-1) }
      },
      onClick: (event, elements) => {
        if (elements.length > 0) {
          const chartIdx = elements[0].index
          const section = valid[chartIdx]
          const realIdx = sectionsFull.value.indexOf(section)
          if (realIdx >= 0) onSectionClick(realIdx)
        }
      },
      scales: { ...chartDefaults.scales, x: { ...chartDefaults.scales.x, title: { display: true, text: '累计长度 (mm)', color: '#909399' } }, y: { ...chartDefaults.scales.y, min: axis?.min, max: axis?.max, title: { display: true, text: '壁厚 (mm)', color: '#909399' }, ticks: { ...chartDefaults.scales.y.ticks, callback: (value) => Number(value).toFixed(1) } } },
      plugins: {
        ...chartDefaults.plugins,
        legend: { display: false },
        tooltip: {
          ...chartDefaults.plugins.tooltip,
          callbacks: {
            label: (item) => `${Number(item.parsed.y).toFixed(2)} mm`,
          },
        },
      },
    },
  })
  // Re-paint the user-confirmed overlay on top of the freshly built chart.
  nextTick(() => updateManualOverlay())
}

// ── Cross-section panel ──
function onSectionHover(idx) {
  renderSoftware()
  hoveredSectionIdx.value = idx
  if (idx >= 0) {
    highlightSection(idx)
    syncChartCursors(idx)
  } else {
    hideHighlight()
    syncChartCursors(-1)
  }
}

function onSectionClick(idx) {
  renderSoftware()
  const sec = sectionsFull.value[idx]
  if (!sec) return
  csIdx.value = idx
  const t = sec.wall_thickness
  csInfoText.value = `周长: ${sec.perimeter?.toFixed(3) || 0} mm${t != null ? ' | 壁厚: ' + t.toFixed(2) + ' mm' : ''} | D=P/π: ${(sec.perimeter / Math.PI).toFixed(3)} mm | 累计长度: ${sec.cumulative_length?.toFixed(1) || 0} mm`
  csPanelVisible.value = true
  highlightSection(idx)
  syncChartCursors(idx)
  nextTick(() => drawCrossSection(idx))
}

// Activate the hover state on both charts so a click in one place is reflected
// in the other (perimeter↔thickness↔3D). Pass -1 to clear.
function syncChartCursors(fullIdx) {
  try {
    if (perimChart) {
      if (fullIdx >= 0 && fullIdx < sectionsFull.value.length) {
        perimChart.setActiveElements([{ datasetIndex: 0, index: fullIdx }])
        perimChart.tooltip?.setActiveElements?.([{ datasetIndex: 0, index: fullIdx }], { x: 0, y: 0 })
      } else {
        perimChart.setActiveElements([])
        perimChart.tooltip?.setActiveElements?.([], { x: 0, y: 0 })
      }
      perimChart.update('none')
    }
    if (thickChart) {
      const validList = sectionsFull.value.filter(s => s.wall_thickness != null)
      const targetSec = sectionsFull.value[fullIdx]
      const chartIdx = targetSec ? validList.indexOf(targetSec) : -1
      if (chartIdx >= 0) {
        thickChart.setActiveElements([{ datasetIndex: 0, index: chartIdx }])
        thickChart.tooltip?.setActiveElements?.([{ datasetIndex: 0, index: chartIdx }], { x: 0, y: 0 })
      } else {
        thickChart.setActiveElements([])
        thickChart.tooltip?.setActiveElements?.([], { x: 0, y: 0 })
      }
      thickChart.update('none')
    }
  } catch (e) { /* chart not yet ready */ }
}

function drawCrossSection(idx) {
  const sec = sectionsFull.value[idx]
  if (!sec || !csCanvasRef.value) return

  const canvas = csCanvasRef.value
  const dpr = window.devicePixelRatio || 1
  canvas.width = canvas.clientWidth * dpr
  canvas.height = canvas.clientHeight * dpr
  const ctx = canvas.getContext('2d')
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.scale(dpr, dpr)
  const W = canvas.clientWidth, H = canvas.clientHeight
  ctx.clearRect(0, 0, W, H)
  ctx.fillStyle = '#fafafa'
  ctx.fillRect(0, 0, W, H)

  const outer = sec.outer_2d || []
  const inner = sec.inner_2d || []

  if (outer.length < 3) {
    ctx.fillStyle = '#c0c4cc'
    ctx.font = '13px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('无截面轮廓数据', W / 2, H / 2)
    return
  }

  // outer / inner are already centred on (0, 0) by the backend.
  const allPts = [...outer, ...(inner || [])]
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (const p of allPts) {
    if (p[0] < minX) minX = p[0]
    if (p[0] > maxX) maxX = p[0]
    if (p[1] < minY) minY = p[1]
    if (p[1] > maxY) maxY = p[1]
  }
  const rangeX = (maxX - minX) || 1
  const rangeY = (maxY - minY) || 1
  const margin = 28
  const scale = Math.min((W - margin * 2) / rangeX, (H - margin * 2) / rangeY)
  const ox = W / 2
  const oy = H / 2
  function toScreen(p) {
    return [ox + p[0] * scale, oy - p[1] * scale]
  }

  // Grid (faint)
  ctx.strokeStyle = '#e8eaee'
  ctx.lineWidth = 1
  const gridStep = scale > 8 ? 5 : 10  // mm per gridline
  for (let g = Math.ceil(minX / gridStep) * gridStep; g <= maxX; g += gridStep) {
    const x = ox + g * scale
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke()
  }
  for (let g = Math.ceil(minY / gridStep) * gridStep; g <= maxY; g += gridStep) {
    const y = oy - g * scale
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke()
  }

  // Wall-fill: between outer (filled) and inner (cut-out)
  ctx.beginPath()
  ctx.moveTo(...toScreen(outer[0]))
  for (let i = 1; i < outer.length; i++) ctx.lineTo(...toScreen(outer[i]))
  ctx.closePath()
  if (inner && inner.length >= 3) {
    // Counter-clockwise inner = cut-out via even-odd fill
    ctx.moveTo(...toScreen(inner[0]))
    for (let i = inner.length - 1; i >= 0; i--) ctx.lineTo(...toScreen(inner[i]))
    ctx.closePath()
  }
  ctx.fillStyle = 'rgba(255, 68, 68, 0.18)'
  ctx.fill('evenodd')

  // Outer ring
  ctx.beginPath()
  ctx.moveTo(...toScreen(outer[0]))
  for (let i = 1; i < outer.length; i++) ctx.lineTo(...toScreen(outer[i]))
  ctx.closePath()
  ctx.strokeStyle = '#ff4444'
  ctx.lineWidth = 2
  ctx.stroke()

  // Inner ring
  if (inner && inner.length >= 3) {
    ctx.beginPath()
    ctx.moveTo(...toScreen(inner[0]))
    for (let i = 1; i < inner.length; i++) ctx.lineTo(...toScreen(inner[i]))
    ctx.closePath()
    ctx.strokeStyle = '#00b8d4'
    ctx.lineWidth = 2
    ctx.stroke()
  }

  // Centroid
  ctx.beginPath()
  ctx.arc(ox, oy, 3, 0, Math.PI * 2)
  ctx.fillStyle = '#ffdd00'
  ctx.strokeStyle = '#b89100'
  ctx.fill(); ctx.stroke()

  // Wall thickness annotation (draw a single radial gauge at angle ~ 30°)
  if (inner && inner.length >= 3 && sec.wall_thickness != null) {
    const angle = -Math.PI / 6
    const dirX = Math.cos(angle), dirY = Math.sin(angle)
    const outerPoint = outer.reduce((best, p) => p[0]*dirX+p[1]*dirY > best[0]*dirX+best[1]*dirY ? p : best)
    let nearest = null, distance = Infinity
    inner.forEach((a, i) => {
      const b = inner[(i + 1) % inner.length], dx = b[0]-a[0], dy = b[1]-a[1]
      const ratio = Math.max(0, Math.min(1, ((outerPoint[0]-a[0])*dx+(outerPoint[1]-a[1])*dy) / (dx*dx+dy*dy || 1)))
      const p = [a[0]+ratio*dx, a[1]+ratio*dy], d = Math.hypot(p[0]-outerPoint[0], p[1]-outerPoint[1])
      if (d < distance) { distance = d; nearest = p }
    })
    if (nearest) {
      const [sx1, sy1] = toScreen(nearest)
      const [sx2, sy2] = toScreen(outerPoint)
      ctx.strokeStyle = '#1478ff'
      ctx.lineWidth = 1.5
      ctx.setLineDash([4, 3])
      ctx.beginPath(); ctx.moveTo(sx1, sy1); ctx.lineTo(sx2, sy2); ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = '#1478ff'
      ctx.font = '11px sans-serif'
      const label = `局部壁厚 ${distance.toFixed(3)} mm`
      const labelW = ctx.measureText(label).width
      // Anchor the label at the midpoint of the gauge, then push it
      // toward the centroid by a fixed pixel offset. This keeps the
      // text well inside the canvas no matter where the gauge lands.
      const mx = (sx1 + sx2) / 2
      const my = (sy1 + sy2) / 2
      const toCx = ox - mx
      const toCy = oy - my
      const dist = Math.hypot(toCx, toCy) || 1
      const inset = 16
      let tx = mx + (toCx / dist) * inset
      let ty = my + (toCy / dist) * inset
      const pad = 4
      tx = Math.min(Math.max(tx, pad + labelW / 2), W - pad - labelW / 2)
      ty = Math.min(Math.max(ty, pad + 6), H - pad - 6)
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(label, tx, ty)
      ctx.textBaseline = 'alphabetic'
    }
  }

  // Legend
  ctx.font = '11px sans-serif'
  ctx.textAlign = 'left'
  ctx.fillStyle = '#ff4444'
  ctx.fillText('● 外壁', 8, 14)
  if (inner && inner.length >= 3) {
    ctx.fillStyle = '#00b8d4'
    ctx.fillText('● 内壁', 56, 14)
  }
  ctx.fillStyle = '#909399'
  ctx.fillText(`1 格 = ${gridStep} mm`, 8, H - 8)
}

function projectMax(pts, dirX, dirY) {
  let best = -Infinity
  for (const p of pts) {
    const v = p[0] * dirX + p[1] * dirY
    if (v > best) best = v
  }
  return best
}

// ── DB operations ──
async function loadTubes() {
  try {
    const res = await ppApi.listTubes(dbSearch.value)
    if (res.data.success) dbTubes.value = res.data.tubes || []
  } catch (e) {}
}

async function handleLoadTube(tube) {
  const tubeId = (tube && (tube.ID ?? tube.id)) || selectedDbId.value
  if (!tubeId) return
  if (loadingDbId.value) return
  loadingDbId.value = tubeId
  selectedDbId.value = tubeId
  try {
    const res = await ppApi.loadTube(tubeId)
    if (!res.data.success) {
      ElMessage.error(res.data.message || '加载失败')
      return
    }
    sessionId.value = res.data.session_id
    fileName.value = res.data.display_name || res.data.file_name || '从数据库加载'
    centerlineLength.value = res.data.centerline_length || res.data.actual_z_length || 0
    clearScene()
    infoReady.value = false
    statusText.value = '加载数据...'
    // Re-use the exact same post-analysis flow upload uses, so UX
    // (3D + charts + stats + interactive highlight) is identical.
    await loadSessionInfo()
    ElMessage.success(`已加载：${fileName.value}`)
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '加载失败')
  } finally {
    loadingDbId.value = null
  }
}

async function handleRenameTube(tube) {
  if (!tube) return
  try {
    const { value } = await ElMessageBox.prompt('新名称', '重命名', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: tube.显示名称 || tube.文件名 || '',
      inputValidator: (v) => (v && v.trim().length > 0) || '名称不能为空',
    })
    const res = await ppApi.renameTube(tube.ID, value.trim())
    if (res.data.success) {
      ElMessage.success('已重命名')
      await loadTubes()
    } else {
      ElMessage.error(res.data.message || '重命名失败')
    }
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('重命名失败')
  }
}

async function handleDeleteTube(tube) {
  if (!tube) return
  try {
    await ElMessageBox.confirm(
      `确定删除「${tube.显示名称 || tube.文件名}」？此操作不可撤销。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
    const res = await ppApi.deleteTubes([tube.ID])
    if (res.data.success) {
      ElMessage.success('已删除')
      await loadTubes()
      if (selectedDbId.value === tube.ID) selectedDbId.value = null
    } else {
      ElMessage.error(res.data.message || '删除失败')
    }
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('删除失败')
  }
}

// ── Actions ──
async function handleSaveToDb() {
  if (!sessionId.value) { ElMessage.warning('无分析会话数据'); return }
  try {
    const defaultName = fileName.value || (selectedFile.value && selectedFile.value.name) || ''
    let displayName = defaultName
    try {
      const { value } = await ElMessageBox.prompt('为本次分析记录起一个名字（用于数据库加载列表）', '保存到数据库', {
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputValue: defaultName,
        inputPlaceholder: '例如：H0A2Z3-DT 演示',
        inputValidator: (v) => (v && v.trim().length > 0) || '名称不能为空',
      })
      displayName = (value || '').trim()
    } catch (e) {
      return // 用户取消
    }

    const choice = qaState.value?.profile_choice
    if (!choice) {
      ElMessage.warning('请先在形线质量检查中选择采用自动读取形线或人工测量形线')
      return
    }
    let grindAllowance = null
    if (grindAllowanceOn.value) {
      grindAllowance = Number(grindAllowanceMm.value)
      if (!Number.isFinite(grindAllowance) || grindAllowance <= 0) {
        ElMessage.warning('请填写大于 0 的研磨余量，单位 mm')
        return
      }
    }
    const res = await ppApi.saveTube(sessionId.value, displayName, buildManualSegmentsPayload(), choice, grindAllowance)
    if (res.data && res.data.success) {
      ElMessage.success('保存成功')
      await loadTubes()
    } else {
      ElMessage.error((res.data && res.data.message) || '保存失败')
    }
  } catch (e) {
    const serverMsg = e?.response?.data?.message
    console.error('[saveTube] failed', e?.response?.status, e?.response?.data, e)
    ElMessage.error(serverMsg ? `保存失败：${serverMsg}` : `保存失败：${e?.message || '网络错误'}`)
  }
}

// ── Lifecycle ──
onMounted(() => {
  initScene()
  loadTubes()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null }
  cancelAnimationFrame(animationFrame)
  if (renderer) { renderer.dispose?.(); renderer.forceContextLoss?.() }
  if (controls) controls.dispose()
  window.removeEventListener('resize', onResize)
  perimChart?.destroy()
  thickChart?.destroy()
})

watch(meshOpacity, (v) => {
  if (meshObj?.material) meshObj.material.opacity = v
})

// Switching between "上传分析" and "数据库加载" panels: always start
// from a clean slate so neither view shows leftover state from the
// other. For DB mode we still refresh the list right after the reset.
watch(loadMode, (mode) => {
  resetWorkspace()
  if (mode === 'db') {
    loadTubes()
  }
})

// Whenever analysis becomes available (fresh upload OR DB load), seed
// the manual panel. If we already restored manual_segments from the DB
// they are preserved; otherwise we fall back to an even 2-segment split
// using the analyzed median thickness.
watch(infoReady, (ready) => {
  if (ready) {
    const n = manualSegmentCount.value || 2
    if (manualThicknesses.value.length !== n) {
      setSegmentCount(n)
    }
    nextTick(() => updateManualOverlay())
  } else {
    manualSegmentCount.value = 2
    manualThicknesses.value = []
  }
})

// Live overlay: refresh the dashed line from the entered thicknesses and lengths.
watch(
  [manualSegmentCount, manualThicknesses, drawingLengths],
  () => updateManualOverlay(),
  { deep: true }
)
</script>

<style scoped>
/* ═══════════ LAYOUT ═══════════ */
.model-upload-app {
  position: absolute;
  inset: 0;
  height: 100%;
  display: flex;
  background: #f5f7fa;
  color: #303133;
  color-scheme: light;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  overflow: hidden;
}
.model-upload-app :deep(input),
.model-upload-app :deep(textarea),
.model-upload-app :deep(select),
.model-upload-app :deep(.el-input__inner),
.model-upload-app :deep(.el-textarea__inner),
.model-upload-app :deep(.el-select__selected-item) {
  color: #000 !important;
  -webkit-text-fill-color: #000 !important;
}
.model-upload-app :deep(input::placeholder),
.model-upload-app :deep(textarea::placeholder),
.model-upload-app :deep(.el-input__inner::placeholder),
.model-upload-app :deep(.el-select__placeholder) {
  color: #909399;
  -webkit-text-fill-color: #909399;
}

/* ═══════════ SIDEBAR ═══════════ */
.sidebar {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  background: #ffffff;
  border-right: 1px solid #e4e7ed;
  overflow-y: auto;
}
.sidebar::-webkit-scrollbar { width: 6px; }
.sidebar::-webkit-scrollbar-thumb { background: #c0c4cc; border-radius: 3px; }
.sidebar::-webkit-scrollbar-track { background: transparent; }

.sidebar-header h1 { margin: 0; font-size: 17px; font-weight: 700; color: #303133; }
.sidebar-header .subtitle { font-size: 11px; color: #909399; margin-top: 2px; }
.dev-qa-slot { margin-top: 4px; }
.dev-qa-slot :deep(.dev-qa-card) { margin-bottom: 0; }

.group { display: grid; gap: 8px; }
.label-group { display: grid; gap: 4px; font-size: 12px; color: #606266; }
.label-group input[type="number"],
.label-group input[type="range"] {
  width: 100%;
  padding: 4px 6px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #f5f7fa;
  color: #000;
  -webkit-text-fill-color: #000;
  font-size: 12px;
}
.label-group input[type="range"] {
  -webkit-appearance: none;
  height: 5px;
  border-radius: 3px;
  background: #dcdfe6;
  border: none;
  padding: 0;
}
.label-group input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #409eff;
  cursor: pointer;
}

.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }

.load-mode-group { width: 100%; }
.algo-select { margin-top: 4px; }

/* Upload box */
.upload-box {
  border: 2px dashed #dcdfe6;
  border-radius: 8px;
  padding: 14px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
}
.upload-box:hover, .upload-box.dragover { border-color: #409eff; background: rgba(64,158,255,0.05); }
.upload-icon { font-size: 22px; color: #409eff; }
.upload-label { margin-top: 4px; font-size: 12px; color: #303133; }
.upload-hint { font-size: 10px; color: #909399; margin-top: 2px; }

.analyze-btn {
  width: 100%;
  min-height: 34px;
  border: 1px solid #409eff;
  border-radius: 6px;
  background: #409eff;
  color: #fff;
  font: inherit;
  font-weight: 600;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}
.analyze-btn:hover:not(:disabled) { background: #337ecc; }
.analyze-btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* DB list */
.db-panel {
  margin-top: 8px;
  padding: 10px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fafbfc;
  display: grid;
  gap: 8px;
}
.db-toolbar { display: flex; gap: 6px; align-items: center; }
.db-search-input {
  flex: 1;
  padding: 6px 8px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #f5f7fa;
  color: #303133;
  font-size: 12px;
}
.db-sort-select {
  padding: 6px 6px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #fff;
  color: #303133;
  font-size: 12px;
}
.db-list-head {
  margin-top: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.db-list-title {
  font-size: 12px;
  font-weight: 700;
  color: #606266;
}
.db-list-toggle {
  height: 24px;
  padding: 0 8px;
  border: 1px solid #dcdfe6;
  border-radius: 5px;
  background: #fff;
  color: #606266;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}
.db-list-toggle:hover { background: #f5f7fa; }
.db-list {
  margin-top: 4px;
  max-height: 158px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fff;
}
.db-list.expanded { max-height: 390px; }
.db-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  cursor: pointer;
  border-bottom: 1px solid #f0f2f5;
  font-size: 12px;
  transition: background 0.15s;
}
.db-row:last-child { border-bottom: none; }
.db-row:hover { background: rgba(64, 158, 255, 0.06); }
.db-row.loading { background: rgba(64, 158, 255, 0.12); opacity: 0.75; }
.db-row-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.db-row-name {
  color: #303133;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.db-row-tag {
  flex: none;
  padding: 0 6px;
  border-radius: 8px;
  background: #ecf5ff;
  border: 1px solid #b3d8ff;
  color: #409eff;
  font-size: 11px;
  line-height: 16px;
  margin-left: 6px;
}
.db-row-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: #909399;
  font-size: 11px;
}
.db-row-time { color: #c0c4cc; font-size: 11px; }
.db-row-actions { display: flex; gap: 4px; }
.db-row-btn {
  width: 24px; height: 24px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #fff;
  color: #606266;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s;
}
.db-row-btn:hover { background: #f5f7fa; color: #1478ff; border-color: #1478ff; }
.db-row-btn.danger:hover { color: #f56c6c; border-color: #f56c6c; }

/* Toggle */
.toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 8px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fafafa;
  font-size: 12px;
  cursor: pointer;
}
.toggle:hover { background: #f0f2f5; }
.toggle input { width: 16px; height: 16px; margin: 0; accent-color: #409eff; }

.legend { display: grid; gap: 6px; font-size: 12px; color: #606266; }
.legend span { display: flex; align-items: center; gap: 6px; }
.swatch { width: 10px; height: 10px; border-radius: 2px; flex: 0 0 auto; }

/* Metrics */
.metrics { display: grid; gap: 8px; font-size: 12px; color: #606266; }
.stats-card {
  background: rgba(64,158,255,0.06);
  border: 1px solid rgba(64,158,255,0.15);
  border-radius: 6px;
  padding: 8px 10px;
}
.stats-card h3 { font-size: 11px; color: #409eff; font-weight: 600; margin: 0 0 6px 0; }
.metric-row { display: flex; justify-content: space-between; padding: 3px 0; border-bottom: 1px solid #f0f2f5; }
.metric-label { color: #909399; }
.metric-value { color: #303133; font-weight: 600; }

/* Table */
.table-section {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  display: grid;
  gap: 6px;
  margin-top: 4px;
  padding: 8px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fafbfc;
}
.table-title-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.table-title { font-size: 12px; color: #606266; font-weight: 700; padding: 2px 0; }
.table-toggle-btn {
  flex-shrink: 0;
  white-space: nowrap;
  height: 24px;
  padding: 0 8px;
  border: 1px solid #dcdfe6;
  border-radius: 5px;
  background: #fff;
  color: #606266;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}
.table-toggle-btn:hover { background: #f0f2f5; }
.table-scroll {
  width: 100%;
  min-width: 0;
  max-height: 300px;
  overflow-y: auto;
  overflow-x: auto;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fff;
}
.data-table { width: 100%; border-collapse: collapse; font-size: 11px; color: #606266; table-layout: fixed; }
.data-table th { text-align: right; padding: 5px 6px; color: #909399; font-weight: 500; border-bottom: 1px solid #e4e7ed; position: sticky; top: 0; background: #fff; z-index: 1; }
.data-table td { text-align: right; padding: 5px 6px; }
.data-table th,
.data-table td { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.data-table th:nth-child(1),
.data-table td:nth-child(1) {
  width: 18%;
  text-align: center;
  overflow: visible;
  text-overflow: clip;
}
.data-table th:nth-child(2),
.data-table td:nth-child(2) { width: 29%; }
.data-table th:nth-child(2) {
  white-space: normal;
  line-height: 1.15;
  overflow: visible;
  text-overflow: clip;
}
.data-table th:nth-child(3),
.data-table td:nth-child(3) { width: 29%; }
.data-table th:nth-child(4),
.data-table td:nth-child(4) { width: 24%; }
.data-table tbody tr { border-bottom: 1px solid #f0f2f5; cursor: pointer; }
.data-table tbody tr:hover { background: rgba(64,158,255,0.05); }
.data-table tbody tr.hovered { background: rgba(230,162,60,0.12); }
.table-hint { font-size: 10px; color: #909399; text-align: center; padding: 2px 0; }

/* Bottom actions */
.bottom-actions { display: grid; gap: 6px; margin-top: 8px; }
.action-btn {
  width: 100%;
  min-height: 32px;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  background: #ffffff;
  color: #606266;
  font: inherit;
  font-weight: 600;
  font-size: 12px;
  cursor: pointer;
}
.action-btn:hover { background: #f0f2f5; border-color: #c0c4cc; }
.action-btn.primary { background: #409eff; border-color: #409eff; color: #fff; }
.action-btn.primary:hover { background: #337ecc; }

/* ═══════════ 壁厚分段确认 ═══════════ */
.manual-panel {
  min-width: 0;
  padding: 18px 20px 20px;
  border: 1px solid #dce3ee;
  border-radius: 10px;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 16px;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.04);
}
.manual-title {
  font-size: 16px;
  font-weight: 700;
  color: #303133;
  letter-spacing: 0.2px;
}
.manual-hint {
  margin: -8px 0 0;
  font-size: 13px;
  color: #606266;
  line-height: 1.5;
}
.manual-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.manual-label {
  font-size: 13px;
  color: #606266;
  font-weight: 600;
}
.seg-choice { display: flex; gap: 8px; }
.seg-btn {
  flex: 1;
  height: 40px;
  font-size: 14px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  background: #fff;
  color: #606266;
  cursor: pointer;
  font-weight: 600;
}
.seg-btn:hover { background: #f0f2f5; }
.seg-btn.active {
  background: #409eff;
  border-color: #409eff;
  color: #fff;
}

.thk-inputs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
}
.thk-inputs.with-rec {
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
}
.thk-input-block {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f8fafc;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  padding: 6px 8px;
}
.thk-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 36px;
  height: 32px;
  border-radius: 6px;
  color: #000;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.4px;
}
.thk-input {
  flex: 1;
  min-width: 0;
  height: 36px;
  border: none;
  outline: none;
  font-size: 18px;
  font-weight: 600;
  text-align: right;
  padding-right: 6px;
  background: transparent;
  color: #000;
  -webkit-text-fill-color: #000;
}
.thk-rec {
  flex: none;
  font-size: 12px;
  line-height: 1.2;
  font-weight: 600;
  color: #909399;
  white-space: nowrap;
}

.boundary-inputs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px;
}
.boundary-input-block {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f8fafc;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  padding: 6px 10px;
}
.bd-label { font-size: 13px; color: #303133; font-weight: 700; min-width: 52px; }
.bd-label.transition { color: #909399; font-weight: 600; }
.bd-input {
  flex: 1;
  min-width: 0;
  height: 32px;
  border: none;
  outline: none;
  font-size: 16px;
  font-weight: 600;
  text-align: right;
  background: transparent;
  color: #303133;
}
.drawing-total {
  margin-top: 8px;
  font-size: 13px;
  color: #606266;
}
/* ═══════════ MAIN AREA ═══════════ */
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.main-area::-webkit-scrollbar { width: 8px; }
.main-area::-webkit-scrollbar-thumb { background: #c0c4cc; border-radius: 4px; }
.viewport-panel {
  flex: none;
  padding: 12px 16px 0;
  background: #f5f7fa;
}
.viewport-wrap {
  position: relative;
  height: min(320px, 38vh);
  min-height: 220px;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 10px;
  overflow: hidden;
}
.viewport-toolbar {
  position: absolute;
  top: 10px;
  left: 10px;
  right: 10px;
  z-index: 8;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
  pointer-events: auto;
}
.viewport-toolbar-title {
  font-size: 12px;
  font-weight: 700;
  color: #303133;
  margin-right: 4px;
}
.viewport-toolbar .toggle {
  padding: 4px 10px;
  justify-content: flex-start;
  white-space: nowrap;
  background: #fff;
}
.opacity-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #606266;
  white-space: nowrap;
}
.opacity-inline input[type="range"] {
  width: 110px;
  accent-color: #409eff;
}
.viewport-toolbar .legend {
  display: flex;
  flex-direction: row;
  gap: 12px;
  margin-left: 4px;
}
.viewport-scroll-hint {
  margin-left: auto;
  font-size: 11px;
  color: #909399;
  white-space: nowrap;
}
.viewport {
  flex: 1;
  position: relative;
  min-height: 0;
  overflow: hidden;
}
.viewport canvas { display: block; }

.workbench {
  flex: none;
  min-width: 0;
  overflow: visible;
  padding: 14px 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  background: #f5f7fa;
}
.confirm-row {
  width: 100%;
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 560px;
  gap: 14px;
  align-items: start;
}
.chart-stack {
  width: 560px;
  display: flex;
  flex-direction: column;
  gap: 0;
}
.qa-placeholder {
  min-width: 0;
  padding: 18px 20px;
  border: 1px dashed #dcdfe6;
  border-radius: 10px;
  background: #fff;
}
.qa-row {
  width: 100%;
  min-width: 0;
}
.length-row, .key-row {
  width: 100%;
  min-width: 0;
}
.length-panel, .key-panel {
  padding: 16px 18px;
  border: 1px solid #dce3ee;
  border-radius: 10px;
  background: #fff;
}
.length-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 16px;
  font-size: 14px;
  color: #303133;
  line-height: 1.7;
}
.split-correction { margin-top: 10px; }
.side-choice { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 8px; }
.key-record-list { display: grid; gap: 6px; }
.key-record {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #f8fafc;
  font-size: 13px;
}
.key-record.stale { border-color: #e6a23c; background: #fdf6ec; }
.key-record em { color: #e6a23c; font-style: normal; margin-left: 6px; }
.thk-tag.transition { background: #dcdfe6; color: #000; }
.qa-in-main {
  min-width: 0;
  width: 100%;
}
.qa-in-main :deep(.qa-card) {
  margin-bottom: 0;
  height: auto;
  border: 1px solid #dce3ee;
  border-radius: 10px;
}
.qa-in-main :deep(.el-card__header) {
  padding: 14px 20px;
  font-size: 16px;
  font-weight: 700;
}
.qa-in-main :deep(.el-card__body) {
  padding: 16px 20px 20px;
}
.qa-in-main :deep(.el-button) {
  min-height: 36px;
  padding: 8px 16px;
  font-size: 14px;
}

.chart-box {
  position: relative;
  box-sizing: border-box;
  width: 560px;
  height: 280px;
  flex: none;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  padding: 8px;
  border: 1px solid #e4e7ed;
}
.chart-stack .chart-box + .chart-box {
  border-top-left-radius: 0;
  border-top-right-radius: 0;
  border-top: none;
}
.chart-stack .chart-box:first-child {
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
}
.chart-canvas-wrap {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
}
.chart-canvas-wrap canvas {
  position: absolute;
  inset: 0;
  display: block;
  max-width: 100%;
  max-height: 100%;
}
.chart-title-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 4px;
}
.chart-title { font-size: 13px; color: #303133; font-weight: 700; }
.chart-subtitle {
  margin-top: 2px;
  font-size: 11px;
  color: #909399;
  line-height: 1.2;
}
.chart-tool-btn {
  flex-shrink: 0;
  height: 24px;
  padding: 0 8px;
  border: 1px solid #dcdfe6;
  border-radius: 5px;
  background: #fff;
  color: #606266;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}
.chart-tool-btn:hover {
  color: #409eff;
  border-color: #409eff;
  background: rgba(64, 158, 255, 0.06);
}
.perimeter-chart-canvas { cursor: crosshair; }
.chart-empty-text {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: #c0c4cc;
}

.save-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 16px;
  background: #fff;
  border-top: 1px solid #e4e7ed;
}
.grind-allowance {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}
.grind-label,
.grind-unit {
  font-size: 13px;
  color: #303133;
}
.grind-allowance :deep(.el-input-number) {
  width: 110px;
}
.save-bar-hint {
  font-size: 13px;
  color: #909399;
}
.save-bar .action-btn {
  width: auto;
  min-width: 160px;
  min-height: 40px;
  font-size: 14px;
}

/* Loading overlay */
.loading-overlay {
  position: absolute;
  inset: 0;
  background: rgba(245,247,250,0.88);
  display: flex;
  justify-content: center;
  align-items: center;
  flex-direction: column;
  gap: 12px;
  z-index: 10;
}
.spinner { width: 32px; height: 32px; border: 3px solid #e4e7ed; border-top-color: #409eff; border-radius: 50%; animation: spin 0.8s linear infinite; }
.progress-container { width: 240px; text-align: center; }
.progress-bar-bg { width: 100%; height: 5px; background: #e4e7ed; border-radius: 3px; overflow: hidden; margin-top: 6px; }
.progress-bar-fill { height: 100%; width: 0%; background: linear-gradient(90deg, #409eff, #67c23a); border-radius: 3px; transition: width 0.2s ease; }
.progress-message { font-size: 12px; color: #606266; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Status text */
.status-text {
  position: absolute;
  left: 12px;
  bottom: 10px;
  padding: 6px 8px;
  border-radius: 4px;
  background: rgba(255,255,255,0.9);
  border: 1px solid #e4e7ed;
  color: #606266;
  font-size: 12px;
  pointer-events: none;
  z-index: 5;
}

/* Cross-section panel */
.cross-section-panel {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 280px;
  background: rgba(255,255,255,0.96);
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 10px;
  z-index: 30;
  box-shadow: 0 4px 16px rgba(0,0,0,0.1);
}
.cs-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.cs-title { font-size: 12px; font-weight: 600; color: #303133; }
.cs-close { background: none; border: none; color: #909399; font-size: 18px; cursor: pointer; padding: 0 4px; min-height: auto; }
.cs-close:hover { color: #f56c6c; }
.cs-canvas { width: 100%; height: 220px; background: #fafafa; border-radius: 4px; border: 1px solid #e4e7ed; }
.cs-info { margin-top: 6px; font-size: 11px; color: #606266; line-height: 1.5; }

@media (max-width: 1280px) {
  .confirm-row { grid-template-columns: minmax(0, 1fr); }
  .chart-stack, .chart-box { width: min(560px, 100%); }
  .viewport-toolbar { right: 12px; }
}
@media (max-width: 760px) {
  .model-upload-app { flex-direction: column; }
  .sidebar { width: 100%; max-height: 44vh; flex-shrink: 0; border-right: none; border-bottom: 1px solid #e4e7ed; }
  .viewport-wrap { height: 280px; }
}
</style>
