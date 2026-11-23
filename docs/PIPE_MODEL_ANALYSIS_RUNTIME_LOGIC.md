# 管件模型分析 — 当前运行逻辑

> 本文记录当前代码**实际如何运行**，不以需求文档、注释或 README 为准。  
> 若代码与注释不一致，以代码为准，并在「与注释/历史描述的差异」中列出。  
> 梳理日期以仓库当前工作区代码为准。本文不描述未实现的理想设计。

---

## 功能实现流程总览

本模块把 STP/STL 管件模型变成可校核的轴向形线，再按图纸壁厚长度做长度校验，最后产出工艺可用的形线。整条链路分两段：

```text
开始分析（自动）
  读模型 → 定轴向 → 切截面 → 生成原始形线 → 页面出图
↓
工程师确认（交互）
  填壁厚与图纸长 → 长度校验 → 关键截面 → 形线平滑/直管确认 → 选出最终形线
```

「开始分析」只做到原始形线和图表。壁厚、图纸总长、长度截短/补长、关键截面、局部平滑都不在该按钮里自动跑。

各功能当前实现方法如下。

### 模型读取

用户上传 `.stl` / `.stp` / `.step`。STL 直接进网格引擎。STP 先用 CadQuery/OCP 按容差 0.3 网格化成临时 STL，再走同一套 STL 分析。系统内部一律按 **mm** 使用，不做单位换算。

### 轴向与中心线

算法 A：对网格顶点做 PCA，把主轴转到 +Z 后再切。算法 B：不旋转，假定模型已大致沿 Z。没有 A 失败改 B 的自动回退。

一次切割用 Z 平面、步长 1 mm。每片取外轮廓质心连成折线，两端钉住、修剪外推、Savitzky–Golay 平滑，得到工作中心线。管件总长以该折线弧长为准。

### 截面读取

沿中心线每 1 mm 用垂直于切向的平面再切一次，得到密截面。外环周长、径向射线中位数壁厚、SVD 主轴长×宽写入截面表。多环时面积最大者为外壁，内部闭合环作内壁。失败切片跳过，缺口用相邻片回填。3D 预览另抽一份默认 25 mm 的稀疏截面。

### 原始形线

分析结束后，用截面表的「中心线长度」和「外径周长」生成 `raw_profile`（弧长 s、周长 C、直径 D=C/π）。这是后续长度校验和平滑的输入，分析过程不改它。

### 壁厚确认

工程师**手填** T1–T4 和对应图纸长度 TL1–TL4，过渡段可填 TLM*。有厚度的段必须有长度；**单段时 TL1 即为图纸总长，必须填写**。多段时图纸总长为各填写长度之和。不使用 STP 自动读取的中心线长度或滑条区间去填充图纸长度。壁厚数值只提供图纸分段信息，不覆盖 STP 测得的截面壁厚。

### 图纸总长

有效长度项求和：

```text
L_drawing = TL1 + TLM1 + TL2 + TLM2 + TL3 + TLM3 + TL4
```

只加 `> 0` 的项。单段时就是 TL1。空值跳过，负值失败。界面上的「图纸总长」与此同一套求和，不读取 STP 长度。

### 长度校验

以图纸长度为准，比较 STP 形线弧长 `L_stp`（容差 1e-6 mm）：

- **大致相等**：原样通过，形线不改。
- **有差异**：先定小径端，再把 `L_drawing − L_stp` 分配到两端。工程师只输入**小径端修正量**（正数=该端补长，负数=该端截短）；大径端修正量由长度守恒自动算出。未输入时保持旧默认：全部差异由小径端承担。
- 截短：删去该端长度，保留区间端点并插值，再把弧长平移到从 0 起。
- 补长：该端已是直管则按常值外延；否则按端头约 40 mm 的线性斜率外推。不调用后面的形线平滑算法。两端可同时截/补，只要代数和等于图纸差。

小径端取两端各 5 mm 窗口内外径中位数的较小端。两端差不超过 0.50 mm 时不自动判断，由工程师指定哪一端是小径端。修正只改 QA 形线拷贝，不改底层 `sections_full`。长度修正发生在形线平滑之前。

### 关键截面

在长度修正后的截面长宽上按图纸尺寸搜索，容差 ±1 mm，长宽可对调。工程师可另填**预计位置**（mm）：与实际匹配位置使用同一轴向定义——中心线弧长 `s`，从当前形线起点 0 起算。保存时记录预计位置、实际位置，以及有符号位置偏差 `ΔP = P_actual − P_expected`。连续命中间隙 ≤ 2 mm 合并成段后保存。只记校验记录，不改形线，也不进入能力包字段。

### 形线平滑与直管段

外径应沿轴向单调不增。局部异常由工程师预览/确认后，只在嫌疑区间做窗外趋势单调插值。单调通过后检测直管候选，工程师确认后的段长之和作为 L4。

前端平滑按钮在 `QA_LOCAL_ANOMALY` 出现；`QA_MANUAL_REQUIRED` 若已识别出可疑段，同样给出红带与建议平滑，并仍可上传人工 XLSX。`oscillatory_profile` 不把 0.01 mm 量化台阶计入符号翻转。凸起起点是突然升高处，不是升高前的基线；因此凸起后略高的直管平台不会被吞进可疑段。

### 人工形线

工程师上传稀疏 XLSX 控制点后，由独立模块 `core/pp/manual_profile` 生成连续形线：同尺寸段常值，渐变段 PCHIP，方向统一为大径→小径单调不增。不走 STP 形线平滑。

### 结果使用

页面用密截面画周长/壁厚图和 3D。QA 通过并选定 auto/manual 形线后，才能取能力包 `get_final_profile`。写入工艺规划数据库是另一次「保存到数据库」，分析本身只存在进程内会话。

后文按入口、数据流、各步骤源码逐项展开。

---

## 0. 范围与入口页

侧边栏菜单文案是「上传分析 → 模型文件上传」，路由：

| 项 | 实际值 |
|---|---|
| 路由 path | `/upload/model` |
| 路由 name | `ModelUpload` |
| 前端文件 | `frontend/src/views/upload/ModelUpload.vue` |
| 页面标题（页面内 h1） | **管件模型分析** |
| 分析按钮文案 | **开始分析**（不是「运行分析」） |

同一套后端上传接口还被另一页调用：

| 项 | 实际值 |
|---|---|
| 路由 | `/pp/optimization` 内嵌 Tab1 |
| 前端文件 | `frontend/src/views/pp/Tab1ModelLoader.vue` |
| 按钮 | 同样调用 `handleAnalyze()` → `POST /api/pp/upload` |

下文以 **ModelUpload.vue（管件模型分析）** 为主路径。Tab1 的差异在对应小节注明。

---

## 1. 「开始分析」入口（对应任务中的「运行分析」）

### 1.1 按钮与处理函数

- 组件：`ModelUpload.vue` 中 class 为 `analyze-btn` 的 `<button>`。
- 点击：`@click="handleAnalyze"`。
- 禁用条件：`!selectedFile || analyzing`。

### 1.2 点击后的函数

`handleAnalyze()`（同文件）：

1. 若 `selectedFile` 为空则直接 `return`（无提示）。
2. `analyzing = true`，清空 3D 场景，`infoReady = false`。
3. 构造 `FormData` 并调用 `ppApi.upload(formData)`。

### 1.3 API Client

`frontend/src/api/pp.js`：

```js
ppApi.upload(formData)
→ api.post('/api/pp/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
```

Axios 实例：`frontend/src/api/index.js`，`baseURL: ''`，请求头可带 `X-Auth-Token`。

### 1.4 HTTP

| 项 | 实际值 |
|---|---|
| URL | `POST /api/pp/upload` |
| Blueprint | `backend/api/pp.py` 的 `pp_bp`，在 `backend/app.py` 以 `/api/pp` 注册 |
| 处理函数 | `upload()` |

### 1.5 请求体（FormData）

ModelUpload 实际 append 的字段：

| 字段 | 来源 | 后端是否使用 |
|---|---|---|
| `file` | 用户选择的文件 | **使用** |
| `algorithm` | 页面单选 A/B，默认 `A` | **使用** |
| `subdivision_level` | 前端 `subdivisionLevel`，默认 `2` | **未读取** |
| `sample_interval` | 页面「采样间隔(mm)」，默认 `1.0`，范围 0.5–5 | **未读取** |

后端读取的是 `sparse_step_mm`（缺省 `25.0`）。前端**没有**发送 `sparse_step_mm`。  
因此 3D 稀疏截面步长始终是后端默认 **25 mm**，与页面「采样间隔」无关。截面密采样步长在引擎内写死为 **1.0 mm**。

Tab1ModelLoader 只发送 `file` 和 `algorithm`。

### 1.6 前端校验（请求前）

实际存在：

- 按钮禁用：未选文件或正在分析。
- `<input accept=".stl,.STL,.stp,.step,.STEP">`。
- 拖拽：文件名需匹配 `/\.(stl|stp|step)$/i`，否则不写入 `selectedFile`。
- 点击选择：`handleFileSelect` **不检查扩展名**，任意文件都可被选中；扩展名由后端再拦。

不存在：文件大小上限、算法合法性校验、采样间隔是否传给后端的校验。

### 1.7 任务 ID

上传成功后：

```js
sessionId.value = res.data.session_id
```

这是**进程内会话 ID**，不是数据库 job ID。生成规则：`secure_filename(文件名)` 去掉扩展名，冲突则加 `_1`、`_2`。

分析过程**不写 tube_analysis 数据库**。入库只发生在用户后续点「保存到数据库」(`POST /api/pp/tubes/save`)。

`tube_analysis.created_at`（列表上的「创建时间」）按 **Asia/Shanghai（UTC+8，北京时间）** 写入，不用 SQLite `CURRENT_TIMESTAMP`（那是 UTC，会比北京时间慢 8 小时）。新记录用 `_now_local_sql()`。旧库若仍是 UTC，启动时按 `pp_schema_meta.tube_created_at_local_plus8` 一次性把已有 `created_at` 加 8 小时。

### 1.8 轮询与完成判定

存在轮询。`startPolling()` 每 **500 ms** 请求：

```
GET /api/pp/upload-status?id=<session_id>
```

完成条件：`progress.done === true`。

- `progress.error` 有值 → 弹错误，不拉结果。
- 否则 → `loadSessionInfo()`。

后端分析在 **daemon 线程** `threading.Thread(target=run_analysis)` 中执行。`POST /api/pp/upload` 立即返回 `{ success: true, session_id }`，不等待几何分析结束。

### 1.9 如何取最终结果

`GET /api/pp/session-info?id=<session_id>` 返回会话摘要（含 `sections_full`、`center_line`、`mesh_data` 等）。

随后：

1. 填图表 / 3D。
2. `qaPanelRef.value?.loadQa()` → `GET /api/pp/qa?id=<session_id>` 拉完整 QA bundle。

`session-info` **不返回**完整 `qa` 对象，只返回 `qa_summary`。

---

## 2. 从点击到页面展示的实际链路

下面只写代码里真实发生的步骤。

```text
用户点击「开始分析」
↓
handleAnalyze()
↓
POST /api/pp/upload  （保存文件 + 建 session + 启动线程）
↓
前端保存 session_id，每 500ms GET /api/pp/upload-status
↓
后台线程 run_analysis()
    ↓ analyze_tube(file_path, file_type, algorithm, sparse_step_mm=25)
        ↓ STP: tessellate → 临时 STL → analyze_stl_tube
        ↓ STL: 直接 analyze_stl_tube
    ↓ 可选：STP 再 tessellate 一份 mesh_data 给 3D
    ↓ 组装 session_results
    ↓ 弯曲检测 is_tube_bended / calculate_bend_angle
    ↓ 由 sections_full 生成 wall_thickness_data
    ↓ initialize_qa_from_records（单调检查 + 可能的直管候选）
    ↓ 调用 analyze_formline_from_data，但返回值未写入结果（见差异）
    ↓ 标记 progress.done=true
↓
GET /api/pp/session-info
↓
前端：周长/壁厚图、3D、截面表
↓
GET /api/pp/qa  （形线 QA 面板）
```

### 2.1 点击「开始分析」时**不会**自动做的步骤

以下逻辑存在于同一页面，但**不是**该按钮触发的：

| 步骤 | 实际触发 |
|---|---|
| 壁厚 T/TL 确认 | 用户点「确认壁厚并校验长度」→ `POST /api/pp/qa/confirm-wall-length` |
| 图纸总长计算 | 同上，后端 `calculate_drawing_length` |
| 长度校验/截短/补长 | 同上，`validate_profile_length` |
| 关键截面搜索/保存 | 用户查找/保存 → `/qa/search-key-section`、`/qa/save-key-section` |
| 形线局部平滑确认 | QA 面板预览/确认平滑 |
| 直管段工程师确认 | QA 面板确认/拒绝/finalize |
| 选择 auto/manual 形线 | `POST /api/pp/qa/select-profile` |
| 能力包 `get_final_profile` | 仅当 QA 状态为 `QA_PASS` 时 `GET /api/pp/qa/final-profile` |
| 写入工艺规划数据库 | `POST /api/pp/tubes/save` |

### 2.2 候选环节是否存在

| 候选环节 | 结论 |
|---|---|
| 前端按钮 / 事件 / API | 存在 |
| 后端 Router | 存在：`upload()` |
| 独立 Service 层 | **未发现**。几何在 `core.pp.tube`，会话逻辑写在 `api/pp.py` |
| 任务创建 | 存在：内存 `_pp_sessions` + 后台线程。无队列、无持久 job 表 |
| 文件处理 | 存在：存到 `PP_UPLOADS_DIR` |
| STP/STEP 读取 | 存在：先网格化为 STL |
| 几何分析 / 中心线 / 截面 / 壁厚测量 | 存在：`stl_engine` |
| 长度校验 | **分析线程中未运行**；壁厚确认后才运行 |
| 形线生成（raw_profile） | 存在：`initialize_qa_from_records` |
| 形线长度修正（corrected_profile） | 分析线程中未运行 |
| 形线平滑（smoothed_profile） | 分析线程中未运行；单调通过且无局部异常时，后续 QA 可能把 working 拷到 smoothed |
| 直管段识别 | 分析后 QA 初始化时，若单调通过会跑 `detect_straight_segments` |
| 关键截面 | 分析线程中未运行 |
| 结果组装 / 返回 / 前端展示 | 存在 |

---

## 3. 核心数据流（真实对象名）

```text
上传文件（磁盘路径 save_path）
↓
STP：临时 STL（tessellation_tolerance=0.3）
↓
trimesh.Trimesh（work_mesh；算法 A 会 PCA 旋转）
↓
一次切割 primary[]（Z 平面，步长 1 mm）
↓
centerline_work（切片质心折线，修剪 + Savitzky–Golay）
↓
二次切割 secondary[]（垂直中心线）
↓
逆变换回世界系 → sections_full / sections / center_line_points
↓
wall_thickness_data（中心线长度(mm) / 外径周长(mm) / 壁厚(mm) / 可能有 x_length,y_length）
↓
QA bundle.raw_profile / working_profile
↓
（用户确认壁厚后）corrected_profile
↓
（用户确认平滑后）smoothed_profile
↓
（finalize + 选形线后）final_profile
```

### 3.1 重要中间对象

#### `session`（`_pp_sessions[session_id]`）

- 定义：`backend/api/pp.py` 内存字典。
- 类型：`dict`。
- 主要字段：`file_path`, `file_type`, `algorithm`, `sparse_step_mm`, `original_filename`, `progress`, `results`, `ready_for_tab2`。
- 生成：`upload()`。
- 使用：轮询、session-info、全部后续 QA/保存接口。
- 进程重启即丢失（除非 DEV 缓存 hydrate）。

#### `analysis`（`analyze_tube` 返回值）

- 定义：`stl_engine._format_output` / `analyze_stl_tube`。
- 主要字段：`sections`, `sections_full`, `center_line_points`, `center_line_length`, `actual_z_length`, `centerline_endpoint_length`, `wall_thickness_stats`, `bounding_box`, `volume`, `algorithm`；STP 另加 `source_format`, `tessellation_tolerance`。
- 失败形态：`{"error": "..."}`。
- 不进入能力包；进入 `session.results`。

#### `sections_full[]`

- 每条约：`position`, `perimeter`, `cumulative_length`, `wall_thickness?`, `outer_2d?`, `inner_2d?`, `outline_3d?`, `x_length?`, `y_length?`。
- 生成：二次切割 + 1 mm 弧长重采样；`x_length/y_length` 由 `principal_extents_2d(outer_2d)` 写入（`stl_engine`）。
- 使用：图表、壁厚记录、QA 初始化、关键截面补尺寸、3D 高亮。
- 分析过程中会被重采样替换，不是用户上传的原始 CAD 截面。
- **不直接**进入 `get_final_profile`。

#### `wall_thickness_data` / `centerline_perimeter_data`

- 同一份 list[dict]，键名保持工艺规划旧字段：`中心线长度(mm)`, `外径周长(mm)`, `壁厚(mm)`, 可选 `x_length`, `y_length`。
- 生成：`upload().run_analysis` 从 `sections_full` 拷贝。
- QA 初始化消费这份记录；**不改** `sections_full`。
- 用户选形线并 `tubes/save` 时，可能被 `working_to_wall_thickness_records` 覆盖周长列。

#### QA bundle（`results.qa`）

- 定义：`core/pp/profile_qa/models.py` 的 `empty_qa_bundle()` + pipeline。
- 关键字段：
  - `raw_profile` / `working_profile` / `corrected_profile` / `smoothed_profile` / `manual_profile` / `final_profile`
  - `raw_section_dims` / `corrected_section_dims`
  - `status`, `suspect_segments`, `straight_segments`
  - `wall_thickness`, `length_validation`
  - `key_section_records`, `key_section_search`
- 分析线程只调用 `initialize_qa_from_records`，此时 `corrected_profile` 为 `None`。
- 能力包只在 `get_final_profile` 取出其中一部分（见第 12 节）。

#### Profile 字典形态（`ProfileArrays.to_dict()`）

```text
s_mm
outer_circumference_mm
outer_diameter_mm
source
point_count
```

`source` 实际取值包括：`stp_raw`（STL 也会标这个）、`stp_length_corrected`、`stp_smoothed`、`manual_xlsx`。

---

## 4. STP / STEP 读取

模块：`backend/core/pp/tube/stp_engine.py`。  
调度：`backend/core/pp/tube/__init__.py` 的 `analyze_tube`。

**当前 STP 分析不走 `core.pp.step_analyzer.analyze_shape()`。**  
`step_analyzer.read_step` 仅在分析成功后给 3D 再 tessellate 一次网格。

### 4.1 输入

- 实际输入：磁盘路径 `save_path`（`PP_UPLOADS_DIR` + `secure_filename(filename)`）。
- 不是内存文件对象直接解析。

### 4.2 库

优先：

1. `OCP.STEPControl.STEPControl_Reader` + `cadquery.Shape.tessellate`
2. 失败则 `OCP.BRepMesh_IncrementalMesh` + `OCP.StlAPI.StlAPI_Writer`

然后一律交给 `trimesh` 的 STL 引擎。

若 `OCP_AVAILABLE` 为假，上传 STP 直接 400：提示安装 cadquery/OCP。

### 4.3 解析过程

`_tessellate_step(stp_path, tolerance=0.3)`：

1. 建临时 `.stl`。
2. `ReadFile` 必须 `IFSelect_RetDone`。
3. `TransferRoots()` → `OneShape()`，空 shape 失败。
4. CadQuery 路径：`tessellate(0.3, 0.2)` → trimesh 写二进制 STL。
5. OCP 路径：`BRepMesh_IncrementalMesh(shape, 0.3, False, 0.2, True)`，`IsDone()` 为假则失败。
6. 两边都失败：`{"error": "STP→STL 失败，请检查是否已安装 cadquery 或 OCP"}`。
7. 分析结束后删除临时 STL。

当前代码**没有**：拓扑检查、缝合、几何修复、实体遍历过滤（只取 `OneShape()`）。

成功判定：tessellate 得到 STL 且 `analyze_stl_tube` 不返回 `error`。

### 4.4 单位

- 全链路字段带 `(mm)` 或 `_mm`，容差常量也是 mm。
- **没有单位换算代码。** STEP 文件若不是 mm，系统仍当 mm 用。
- 网格容差 `0.3` 与 CAD 模型同一套数值单位。

---

## 5. 中心线 / 轴向

实现：`backend/core/pp/tube/stl_engine.py` 的 `analyze_stl_tube`。

### 5.1 算法选择

`algorithm` 仅允许 `A` / `B`，其它强制为 `A`。

```text
算法 A
  PCA 求顶点主轴 pca_long_axis
  ↓
  旋转到 +Z（rotation_between_vectors）
  ↓
  在旋转后坐标系做 Z 切片
  ↓
  结束后用 rotation.T 和原心把中心线/轮廓变回世界系

算法 B
  不旋转、不用 PCA
  ↓
  在原始坐标系用 Z 平面切片
  ↓
  要求模型主轴已大致沿 Z
```

没有 A 失败自动改 B 的 fallback。A/B 失败都直接 `{"error": ...}`。

### 5.2 一次切割后如何得到中心线

`_build_centerline_from_centroids`：

1. 丢掉周长 > 中位数 × 1.4 的切片（有效切片 < 4 则不过滤）。
2. 每片用 `hull_and_inner` 的 **shoelace 质心** `(cx, cy, z)`，不用外轮廓顶点均值。
3. 两端各约 20%（至少 8 片）的 (cx,cy) 被钉到中间 60% 的均值；太短则退化为 25%–75%。
4. `trim_and_extrapolate_centerline(centroids, trim=10, n_fit=5)`。
5. Savitzky–Golay：`window=31, poly=3`。

轴向位置：中心线折线弧长 `cumulative_length`。  
起点/终点：重采样后弧长轴从 0 到 `floor(折线总长)`；`center_line_length` 用**重采样前**折线长，避免被 1 mm 网格截短。

### 5.3 大径端 / 小径端

**几何分析阶段不判断大小径端。**

长度修正时才用 `identify_small_end`：两端各 `SMALL_END_WINDOW_MM = 5` mm 窗口内外径中位数，较小的一端为小径端。两端差 ≤ `SMALL_END_SIZE_DELTA_MM = 0.50` mm 则 `ambiguous`，不默认取数组头。

### 5.4 长度口径（多个并存）

| 字段 | 实际含义（当前代码） |
|---|---|
| `center_line_length` / `centerline_length` | 中心线折线弧长（重采样前） |
| `actual_z_length` | **已改为** 中心线两端点直线距离，不是 mesh Z 跨度 |
| `centerline_endpoint_length` | 与上面同一值 |
| 长度校验用的 `L_stp` | `raw_profile.s[-1] - s[0]`，即形线弧长跨度 |

前端 `loadSessionInfo` 显示的「中心线长度」优先 `centerline_length`，否则 `actual_z_length`。

弯曲：中心线点数 > 10 时 `is_tube_bended(..., angle_threshold=2.0)`。这不改变中心线算法。

---

## 6. 截面读取

### 6.1 位置如何生成

**一次切割（找轴）：**

- 平面：`z = const`，法向 `(0,0,1)`。
- `z` 从 `bounds.zmin+0.2` 到 `zmax-0.2`，步长 `_SLICE_STEP_MM = 1.0`。
- Z 跨度 < 5 mm → 报错。

**二次切割（正式截面）：**

- 在中心线每个点上，平面过该点、法向为中心线切向。
- 后再 `_resample_to_integer_mm`：弧长对齐到 `0,1,2,...,floor(L)`。周长/壁厚先平滑再插值；`outer_2d` 按最近源索引拷贝。

3D 稀疏截面 `sections`：按弧长每隔 `sparse_step_mm`（实际默认 25 mm）。

### 6.2 station 数量

密表：约 `floor(中心线弧长)+1` 个点（1 mm 一站）。  
不是用户指定 station 数。`n_sections` 参数在 `analyze_tube` 里标明 deprecated，当前 STL 引擎不用它。

### 6.3 长 / 宽 / 直径 / 壁厚

- **周长**：外环多边形周长。
- **外径（形线用）**：`D = C / π`（QA 里由周长推导）。
- **壁厚**：有内环时，144 条径向射线的外–内距离中位数（`_wall_thickness_radial`）；无内环则为 `None`。
- **截面长×宽**：分析输出时对 `outer_2d` 做 SVD 主轴跨度 `principal_extents_2d`，写入 `x_length`（较长）、`y_length`（较短）。不是轴对齐包围盒。

多轮廓：面积最大的闭合环为外，其余质心在外环内的作为内壁候选，再对其点集做 ConvexHull。

失败切片：`section is None`、点数不足、`hull_and_inner` 失败 → `continue` 跳过。二次切割缺口用一次切割结果回填端点，再沿索引前向/后向填。

并行超时或覆盖率 < 50%：改串行重跑。

一次切割离群：周长 > 1.4×中位数则丢。

### 6.4 容差（几何阶段）

| 常量 | 值 | 位置 |
|---|---|---|
| 切片步长 | 1.0 mm | `stl_engine._SLICE_STEP_MM` |
| Z 端内缩 | 0.2 mm | `analyze_stl_tube` |
| 最小 Z 跨度 | 5.0 mm | 同上 |
| 周长离群 | 中位数 × 1.4 | `_filter_outlier_sections` |
| 网格容差 | 0.3 | STP tessellate |
| 弧长去重 | `diff(cum) > 1e-9` | `_resample_to_integer_mm` |

关键截面匹配容差 **±1.0 mm** 和段合并间隙 **2.0 mm** 只用于后续 QA 搜索，不用于切割。

---

## 7. 壁厚确认（交互，非「开始分析」自动步骤）

代码：

- 前端：`ModelUpload.vue` 的 `buildWallValues` / `handleConfirmWallLength`
- 后端：`core/pp/profile_qa/wall_thickness_confirm.py`
- 配置：`WALL_THICKNESS_KEYS`, `WALL_LENGTH_KEYS`, `WALL_THICKNESS_LENGTH_PAIRS`

### 7.1 支持的字段

厚度：`T1 T2 T3 T4`  
长度：`TL1 TLM1 TL2 TLM2 TL3 TLM3 TL4`

没有 T5。`TLM*` 是过渡段长度，不要求对应单独的 T。

对应关系硬编码：`(T1,TL1) (T2,TL2) (T3,TL3) (T4,TL4)`。

### 7.2 规则（后端 `validate_wall_thickness`）

- `T_i` 出现且 `> 0` 时，对应 `TL_i` 必须为有限且 `> 0`。  
  **单段 T1 也必须有 TL1。** 缺/0/负 → `"请填写壁厚 T1 对应的长度 TL1。"`
- 未填的 T 视为没有该段；其 TL 不强制（但若填了非法 TL 仍报错）。
- 空字符串 / `None` 当缺失，不计入图纸长。

### 7.3 前端填写与校验

`handleConfirmWallLength` 只硬拦：T1>0 且 TL1 无效时本地报错。  
T2–T4 主要靠后端 409。

前端 `manualSegmentCount` 为 1–4。**图纸长度只来自「图纸长度」输入框**，空着不填，不从 STP 中心线或滑条区间自动填充。

- **一段**：只显示 TL1，占位「总长度，必填」。`L_drawing = TL1`。
- **多段**：显示 TL1、TLM1、TL2…，界面「图纸总长」= 各填写且 `> 0` 的项之和。

填写框与 TL/关键截面等浅色标签文字为黑色（`#000`），浅底上可见；不使用白字。

多段时滑条只用来在读取形线上划分壁厚区间（厚度曲线叠加），其长度**不**写入图纸 TL/TLM。

### 7.4 对后续分析的影响

确认成功后：

- `qa.wall_thickness` 保存 values / parsed / terms。
- 用 **原始** `raw_profile`（不是 working）做长度校验。
- 得到 `corrected_profile`，再 `_run_monotonic_and_followup`（平滑/直管检测在修正后的形线上重跑）。

壁厚 T 数值**不改** STP 测得的 `sections_full.wall_thickness`；T/TL 只提供图纸分段长度和图纸总长。页面上的壁厚曲线仍可按用户分段滑条显示确认厚度，那是另一套 `manual_segments` / `thickness_curve_from_tl_t`，与 QA 长度校验分开。

---

## 8. 图纸总长 `L_drawing`

**已实现。**

函数：`calculate_drawing_length`。

公式（当前代码）：

```text
L_drawing = Σ { TL1, TLM1, TL2, TLM2, TL3, TLM3, TL4 中每一个 > 0 的值 }
```

- `None` / 空：跳过。
- `< 0`：失败。
- 求和结果 `<= 0`：失败，消息仍是「请填写壁厚 T1 对应的长度 TL1。」
- 单段：只有 TL1，故 `L_drawing = TL1`。
- 多段：各手填 TL/TLM 相加；未填的项不计入。

写入：`qa.length_validation.drawing_length`，以及 `qa.wall_thickness.terms`。

`_store()` / `tubes/save` 把 QA 有效总长（图纸总长或修正后形线弧长）写回 `results.centerline_length`、`results.actual_z_length`，以及 `L4`、`formline_length = 总长 − L4`。调用方传入的原始 STP 中心线长不能覆盖已校验长度。

---

## 9. 长度校验（重点）

代码：`core/pp/profile_qa/length_correction.py` 的 `validate_profile_length`。  
触发：`confirm_wall_and_validate_length`。  
**「开始分析」线程不调用它。**

### 9.1 三个量

| 符号 | 代码字段 | 来源 |
|---|---|---|
| `L_drawing` | `drawing_length` | `Σ` 有效 TL/TLM |
| `L_stp` | `stp_length` | `raw_profile.s[-1]-s[0]`（`stp_length_of`） |
| `ΔL` | `difference` | `L_stp - L_drawing`（有符号） |

比较容差：`LENGTH_COMPARE_TOLERANCE_MM = 1e-6`（配置注释写明只吃浮点噪声，不把真 1 mm 差当相等）。

判定：

```text
abs(L_stp - L_drawing) <= 1e-6   → matched
否则先定小径端，再按两端修正量改形线：
  L_stp > L_drawing             → trimmed
  L_stp < L_drawing             → extended
```

`raw_profile` **不被原地修改**。修正结果是新的 `ProfileArrays`，`source="stp_length_corrected"`，存为 `corrected_profile`。再次确认壁厚/长度时仍从 `raw_profile` 重算，而不是叠在已修正形线上。

底层 `sections_full` **不会**被截短或补点。改的是 QA 形线数组（s, C, D），截面长宽随 s 线性插值到新轴。

### 9.2 双端修正量

需要补到图纸长的总量：

```text
needed = L_drawing − L_stp = −ΔL
```

工程师只传 `small_end_correction_mm`（该端**增加**的长度：正=补长，负=截短）。系统：

```text
large_end_correction_mm = needed − small_end_correction_mm
```

后端校验 `small + large == needed`（容差 1e-6）。未传或空字符串时：

```text
small = needed
large = 0
```

即旧默认「全部由小径端承担」。显式传 `0` 表示小径端不改，全部由大径端承担；二者语义不同。

小径端仍由两端 5 mm 窗口外径中位数决定；差 ≤ 0.50 mm 则 `ambiguous_end`，前端出现「起点端/终点端」，用户必须传 `correction_side`。`start` = 形线 s=0 端，`end` = 形线终点。预计位置/实际位置与此同一套 s 轴。

前端：首次「确认壁厚并校验长度」不传小径端修正量（走默认）。出现长度差后，可填小径端修正量并点「按分配修正形线」；大径端只读显示。

实现：`allocate_end_corrections` → `apply_split_length_correction`。先按两端负量截短（`trim_profile_ends`），再按正量分别在 start/end 调用 `extend_profile_smoothly`。截短后剩余长度必须大于 `MIN_CENTERLINE_LENGTH_MM`。

### Case A：`L_stp ≈ L_drawing`

- `status = matched`
- `profile = raw.copy()`，不改长度
- 消息：`长度校验通过。`
- `working_profile = corrected_profile`（仍是原长）
- `profile_source` 保持 `stp_raw`

### Case B：`L_stp > L_drawing`

- 以图纸长为准。默认从小径端删除 `abs(ΔL)`。
- 若工程师给出小径端修正量，则两端按各自负量截短（也可一端截、一端补，代数和仍为 `needed`）。
- `trim_profile_ends`：在原 s 轴上丢掉 start/end 指定长度，保留区间端点，周长/直径/`x_length` 等对保留轴 `np.interp`，再把 s 平移到从 0 开始。
- 删的是形线采样点，不是 CAD 实体，也不是 `sections_full` 行。
- 边界：保留精确端点 `s_keep0/s_keep1`，用插值得到该处 C/D。

### Case C：`L_stp < L_drawing`

- 默认从小径端补 `abs(ΔL)`；工程师分配后可两端补，或一端补、一端截。
- `extend_profile_smoothly`：新 s 按原采样中位间距（至少 0.25 mm）外延，最后一个点精确落在 `±delta`。
- 直径：
  - 若该端局部斜率 `|dD/ds| <= STRAIGHT_MAX_SLOPE(0.0015)`，或直管检测认为该端已在直管段上 → **常值外推**（直管延长）。
  - 否则用端头 `LENGTH_EXTEND_TREND_WINDOW_MM = 40` mm 的线性斜率外推（**不是**高阶多项式，也**不**调用形线平滑算法）。
- 新周长 `C = D * π`。
- 截面长宽：新段重复旧端点的 x/y，不从 CAD 再切。
- `D` clip 到 `[MIN_DIAMETER_MM, MAX_DIAMETER_MM]` = `[1, 2000]`。
- 返回的 `final_length` 为修正后 `s[-1]-s[0]`；实现上用原间距拼出精确端点，目标等于图纸长。

---

## 10. 形线

### 10.1 生成（分析完成后立刻发生）

`initialize_qa_from_records(wall_thickness_data, centerline_length=..., profile_source='stp_raw')`：

1. `records_to_arrays`：读 `中心线长度(mm)`、`外径周长(mm)`。
2. `validate_profile_input`：点数、间隙、直径范围等。
3. `raw_profile = working_profile`（拷贝）。
4. `raw_section_dims = extract_section_dims(...)`（有 x/y 或 outer_2d 才有）。
5. `_run_monotonic_and_followup`：
   - `validate_monotonic_profile`（外径应沿 s 单调不增，允许噪声）。
   - 通过 → `detect_straight_segments` → 状态 `QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION`。
   - **局部异常**（少数凸起，残差点比例 ≤10%、几何段长 ≤12%）→ `QA_LOCAL_ANOMALY`，前端给出平滑预览/确认。
   - **较大异常**（趋势不可靠、残差点 >10%、段长 >12%、或碎片过多）→ `QA_MANUAL_REQUIRED`。前端仍标出已识别的可疑段（红带 + 范围/高度），若有可疑段则给出建议平滑预览/应用，同时保留上传人工 XLSX。`oscillatory_profile` 只统计 `|ΔD| > 0.01 mm` 的符号翻转，0.01 mm 量化锯齿不计入。

局部凸起检测：先把外径收到小数点后两位（0.01 mm），C/π 带来的 0.003 mm 台阶不参与判定。在突然升高处记起点，后续上升趋势超过 3 个点才判为凸起；终点是第一个 **D < 起点 D** 的点，而不是第一个相对前一点下降的点。单调递减的大端锥面没有上升趋势，不判为凸起。

`analyze_formline_from_data`（`formline_conclude.py`）在 `run_analysis` 里被调用，但：

```python
fl_result = analyze_formline_from_data(...)
session_results['L4'] = 0.0
session_results['segments'] = {}
```

**返回值丢弃。** 当前运行链路中，这套旧周长窗口直管识别**不进入**前端结果。真正直管段来自 QA 的 `detect_straight_segments`（外径 ±1.0 mm、斜率、最短 50 mm 等，见 `config.py`）。

### 10.2 修正

即第 9 节长度校验（可双端分配）。之后 `working_profile = corrected_profile`，再重跑单调/直管。长度修正始终在形线平滑之前。

### 10.3 平滑

- 方法名：`SMOOTHING_METHOD = "local_constrained_isotonic"`。
- 仅用户在 QA 面板点预览/确认后执行 `preview_local_smooth`。
- 只改 `suspect_segments` 区间，锚点不升，用 isotonic decreasing。
- **不改** `raw_profile`。
- 确认后 `profile_source = stp_smoothed`，并再验证；失败 → `QA_RECHECK_FAILED`。
- 长度已修正且不需要局部平滑时，`_finish_auto_stage` 可能把 `working_profile` 拷到 `smoothed_profile`。

### 10.4 直管段 / 渐变段

- 直管候选：`straight_segment_detector.detect_straight_segments`。
- 工程师确认/拒绝/finalize 后，`status==confirmed` 的段长之和 = QA 的 `L4`。
- `formline_length`（QA 内）= **QA 有效总长**（图纸总长 / 修正后形线）− L4，不是原始 STP 中心线长 − L4。
- **渐变段**：没有单独检测器。壁厚 UI 的 `TLM*` 是用户画的过渡长度，不是几何识别结果。

### 10.5 关键截面

- 搜索：`search_key_sections`，容差 ±1 mm，长宽可对调。发生在长度修正之后、形线平滑之前的 `corrected_profile` 上。
- 输入：截面长度、截面宽度、可选**预计位置**（mm）。预计位置与实际匹配位置同一参考：中心线弧长 `s`，从当前形线起点 0 起算（不是「一端从下径、一端从大径」）。
- 连续命中间隙 ≤ 2 mm 合并为段（`s_start_mm`–`s_end_mm`）。候选上附带 `expected_s_mm` 与有符号 `position_delta = s_mm − expected_s_mm`。
- 保存记录至少含：输入长/宽、预计位置、实际匹配位置、实际长/宽、长度偏差、宽度偏差、位置偏差（保留正负号）。允许多条；后一条不覆盖前一条。
- 数据源：优先 `corrected_section_dims`，否则 `raw_section_dims`；缺省时 API 用 `sections_full.outer_2d` 补算。
- 不修改形线，只往 `key_section_records` 追加。工程师根据位置偏差决定小径端修正量后，再次长度校验会改形线，已存记录标 `stale`。
- **不进入** `get_final_profile` 字段集合。

### 10.6 人工形线生成（独立模块）

与 STP 形线平滑**完全分开**。代码：`backend/core/pp/manual_profile/`。  
**不调用** `profile_qa/profile_smoothing.py`。

上传仍走现有 XLSX 模板与列名（`centerline_mm` + `outer_circumference_mm`，或外径列别名）。解析器只取出控制点 `xlxs = [[x, y], ...]`，`y` 在模块内部用外径 mm。

生成链：

```text
raw_xlxs
  → 校验（≥2 点，有限数值）
  → normalized_control_points（按真实 x 排序、合并重复 x、平移到 0、若小径→大径则翻转）
  → constrained_control_points（同尺寸段常值化，强制 y 单调不增）
  → manual_profile（直管段常值填充 + 渐变段 PCHIP，按 1 mm 重采样，保留原控制点）
```

`raw_xlxs` 不原地修改。控制点不必等距。直管段 `y(x)=C`，不允许插值波动。渐变段 `scipy.interpolate.PchipInterpolator` 用真实 `x_i`。最终 `y[i+1] ≤ y[i]`（允许相等）。能力包 `get_final_profile` 字段集合不变；选人工形线时 `source` 仍是 `manual_xlsx`。

---

## 11. 结果组装、保存、前端展示

### 11.1 分析线程写入 `session.results` 的字段

见第 1.9 节 session-info 返回列表。另外内存中还有完整 `qa`、`wall_thickness_data`。

分析结束时：

- `L4 = 0.0`（写死）
- `formline_length = centerline_length`
- `segments = {}`

之后 QA 接口 `_store()` 以及「保存到数据库」会用 QA 有效总长覆盖 session 的 `centerline_length` / `actual_z_length` / `L4` / `formline_length`，供工艺规划读取。

DEV 模式下 `persist_from_session` 写 `backend/data/dev/current_stp.json`（压缩 `sections_full`，保留 x/y）。

### 11.2 前端解析（`loadSessionInfo`）

- `sectionsFull` → 左表、周长图、壁厚图。
- `center_line.points` → 3D 中心线。
- `mesh_data`（STP 分析后 tessellate 的网格）→ 3D 实体。没有 `mesh_data` 则只画中心线和稀疏截面，**不再**请求 `GET /api/pp/stl-file/<id>`（该请求不带 `X-Auth-Token`，会 401；STP 会话的 `file_path` 也是 `.stp`，即使用 token 也无法当 STL 解析）。
- `pickSparseSections(sections, sections_full, sparse_step_mm)` → 3D 截面环。
- 再 `loadQa()` 驱动 `PpQaPanel`。

### 11.3 能力包 `get_final_profile`

仅状态 `QA_PASS` 时允许。字段**只有**：

```text
status
profile_source
final_profile
straight_segments          # 仅 confirmed
qa_quantity                # outer_diameter_mm
storage_quantity           # outer_circumference_mm
algorithm_version
```

不包含：关键截面记录、T/TL、`sections_full`、`L_drawing`、mesh。

`final_profile` 来自用户 `select-profile`（auto 或 manual）之后写入的 bundle 字段。

---

## 12. 与注释 / 历史描述的差异（以代码为准）

1. 按钮文案是「开始分析」，侧边栏是「模型文件上传」，不是「运行分析」。
2. 前端 `sample_interval`、`subdivision_level` **不影响**分析；密截面步长恒为 1 mm；稀疏步长恒为 25 mm（前端未传 `sparse_step_mm`）。
3. STP 并不用 `step_analyzer.analyze_shape` 做截面；只 tessellate 成 STL 再走 STL 引擎。
4. `actual_z_length` 注释与旧语义是 mesh Z 跨度；当前是中心线端点直线距。
5. `analyze_formline_from_data` 仍被调用，结果丢弃；直管段以 QA 检测器为准。
6. 分析结束时 session 的 `L4`/`segments` 被写成 0/空，与 QA bundle 可能不一致，直到后续 `_store`。
7. 形线 `profile_source` 在 STL 文件上仍标 `stp_raw`。
8. 长度校验、图纸总长、关键截面**不是**「开始分析」自动流水线的一部分。
9. 长度修正不改 `sections_full`，只改 QA profile 拷贝。
10. 3D 实体只吃 `mesh_data`，前端不再拉 `/api/pp/stl-file/`。缺网格时仍显示中心线/截面，不弹 STL 401。

---

## 13. 关键文件索引

| 角色 | 路径 |
|---|---|
| 页面 | `frontend/src/views/upload/ModelUpload.vue` |
| API client | `frontend/src/api/pp.js` |
| 上传/会话 | `backend/api/pp.py` |
| QA HTTP | `backend/api/pp_qa.py` |
| 调度 | `backend/core/pp/tube/__init__.py` |
| STP→STL | `backend/core/pp/tube/stp_engine.py` |
| 切割/中心线 | `backend/core/pp/tube/stl_engine.py` |
| 切片 worker | `backend/core/pp/tube/workers.py` |
| 外/内环与壁厚 | `backend/core/pp/tube/geometry.py` |
| QA 编排 | `backend/core/pp/profile_qa/pipeline.py` |
| 壁厚/图纸长 | `backend/core/pp/profile_qa/wall_thickness_confirm.py` |
| 长度校验 | `backend/core/pp/profile_qa/length_correction.py` |
| 直管检测 | `backend/core/pp/profile_qa/straight_segment_detector.py` |
| 平滑 | `backend/core/pp/profile_qa/profile_smoothing.py` |
| 关键截面 | `backend/core/pp/profile_qa/key_section.py` |
| 人工形线生成（独立） | `backend/core/pp/manual_profile/` |
| 容差 | `backend/core/pp/profile_qa/config.py` |
| 旧形线分析（调用但未采用） | `backend/core/pp/formline_conclude.py` |

## 14. 实测站距（`GTAPP_STATION_MM`，2.0.1.5.1119 起）

- 默认 1 mm，与历史逐位一致。设 `GTAPP_STATION_MM=3`（允许 1–10）改走「粗测 + 过渡段加密 +
  插值回填」链路；`=1` 立即回退。
- 站位来源（`station_source`）：`measured`（定点精测）/ `refined`（曲率加密，1 mm 实测）/
  `interpolated`（PCHIP 回填，标量是插值、轮廓沿用最近实测站）/ `extrapolated`（端部外推）。
  `measurement_status` 仍是 `measured|needs_review|extrapolated`，`interpolated` 行记作
  `measurement_status='interpolated'`、`thickness_sample_count=0`。
- 判曲率：在粗测站距上算二阶差分，`|Δ²P| > 0.05 mm` 或 `|Δ²T| > 0.02 mm` 的两站之间补
  1 mm；两端各 15 mm 恒补 1 mm（端部形状梯度最陡）。粗测样点判曲率会别名化，所以不允许
  「只粗测 + 全场插值」。
- 网格：`station_plan.expand_rows()` 用实测站的「中位小数相位」铺 1 mm 网格（端部处理会把
  s 轴整体平移，实测站常落在 10.2439 这种非整毫米上），每个网格点吸附最近的真实行，同一
  网格点只留一行（优先级 measured > needs_review > extrapolated）；偏离超过 0.45 mm 的异常
  补测原样保留、作为额外站位追加，不丢读数。
- 精度（离线用 1 mm 真值验证）：3 mm + 加密的周长误差 ≤0.005 mm、壁厚 ≤0.0006 mm（1324-1），
  体积偏差 0.0000%；1 mm 基线的体积/拓扑不变。
- 限制：插值站没有独立轮廓（`outer_2d/inner_2d` 为空，`x_length/y_length` 缺失），关键截面
  ±1 mm 抽查对插值站不适用；过渡段加密是独立一批切面，批量很小时批次开销会偏高。

### 14.1 归档网格（`GTAPP_STATION_GRID`，2.0.1.5.1123 起）

- 与 `GTAPP_STATION_MM` 正交：前者决定「结果表 / 入库 payload 有多密」，后者决定「真的在哪些
  位置切截面」。默认 `1`（逐毫米归档，与 1122 及以前逐位一致），允许 `1`–`10`。
- 设 `3` 的效果（实测）：24TK_1324-1 `sections_full` 759→254 行、24TK_2052-2 493→165 行，
  前端「截面数」与截面表行数同比例下降，测量耗时不变（切面次数与实测步距没变）。
- 实现是**抽稀不是重采样**：先用 `station_plan.expand_rows(..., grid_step=1.0)` 把实测站铺满
  1 mm 计算网格，再由 `station_plan.decimate_rows()` 每 N 行取一行（目标行是插值行、而 1 mm
  邻域内有真实截面时优先归档真实截面），归档点上的数值与 1 mm 基线逐点相同。
- 计算层始终 1 mm：`analyze_tube` 额外返回 `sections_dense`，`api/pp.py` 用它（以及
  `core/pp/section_records.py` 的自动铺密）生成 `wall_thickness_data`；形线质检、壁厚分段、
  工艺优化、硬模成型读到的仍是 1 mm 记录。实测两件管的记录条数与 1 mm 基线一致、壁厚逐点
  最大差 0.0000 mm、QA 状态和直管段数一致。
- 质量字段：`analysis_quality.station_grid_step_mm`（归档步长）、
  `analysis_quality.station_grid.archive_step_mm / archive_rows`；warning 首条会写明「归档抽稀、
  计算层仍 1 mm」。
- 入库后加载：`/api/pp/tubes/save` 存的是归档层 `sections_full` + 1 mm 的
  `wall_thickness_data`，所以「数据库加载」不会因为归档变粗而改变下游结果；只有历史 payload
  里缺 `wall_thickness_data` 时才会从 `sections_full` 现场铺密（此时误差 ≤0.05 mm）。

### 14.2 逐阶段计时与定位旋钮（2.0.1.5.1124 起）

`analysis_quality` 现在带三组计时数据，用来回答「每一步花了多少时间」：

| 字段 | 内容 |
|---|---|
| `stage_timings` | `read` 读模型 / `seeds` 种子定位 / `corrections` 中心线校正（全部轮次合计）/ `probe` 端部探测 / `fine` 精测 / `refine` 过渡段加密 / `ends` 端部处理 / `grid` 网格重建（含归档抽稀）/ `assemble` 组装 / `total_seconds` |
| `cuts` | 各阶段的切面次数：`seeds` / `corrections` / `fine` / `refine` |
| `corrections[]` | 每轮：`round`、`grid_mm`、`sections`、`stations`、`seconds`、`max_shift_mm` |

三个定位旋钮，默认值等于 1123 的固定行为：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GTAPP_COARSE_STEP_MM` | `5` | 粗定位（找中心线）的轴向网格间距，夹 `[3, 30]` |
| `GTAPP_CORRECTION_ROUNDS` | `3` | 校正轮数上限，夹 `[1, 5]` |
| `GTAPP_CORRECTION_TOL_MM` | `0`（关闭） | 单轮最大位移小于它才跳过剩余轮；夹 `[0, 2]` |

为什么默认是 3 轮全切：24TK_1324-1 相对 1 mm 基线的周长最大差为
5 mm × 3 轮全切 **0.0052 mm**、5 mm × 2 轮 **4.39 mm**、只重切未收敛站 **1.36 mm**、
10 mm 种子 + 2 轮 **0.3677 mm**。斜切椭圆的质心偏置沿管面方向，样条残差看不见它，
所以「少切一轮」不是精度换时间的线性交易，而是直接丢掉收尾那一轮纠偏。
只有明确接受精度下降时才调这些变量；日常不要动。
