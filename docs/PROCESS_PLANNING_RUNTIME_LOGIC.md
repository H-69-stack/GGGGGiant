# 工艺规划 / 工艺参数优化 — 当前运行逻辑

> 本文记录当前代码**实际如何运行**，不以需求文档、注释或模块介绍页为准。  
> 若代码与介绍文案不一致，以代码为准，并在文末「差异」中列出。  
> 本文不修改业务代码。

相关代码：

| 层 | 路径 |
|---|---|
| 后端入口 | `backend/api/pp.py` |
| 方法分发 | `backend/core/pp/optimization.py` |
| 各工艺算法 | `backend/core/pp/legacy_opt/` |
| 当前线上页 | `frontend/src/views/pp/Optimization.vue` + `Tab3Optimization.vue` |
| API client | `frontend/src/api/pp.js` |

壁厚改写的完整公式与分段规则见 `docs/WALL_THICKNESS_MODIFY_LOGIC.md`。

---

## 功能实现流程总览

工艺规划的目标：根据已分析管件的形线（壁厚、外径、长度、体积），选出一条工艺路线，求出素材尺寸（D0 / T0 / L0）和各工步长度，再保存为可交给模具设计的工艺方案。

当前线上主路径是两段，不是页面里的三 Tab 向导：

```text
上传分析（/upload/model）
  分析 STP/STL → 确认形线 → 保存到管件数据库
↓
工艺参数优化（/pp/optimization）
  从数据库加载记录 → 选工艺路线 → 优化求解 → 预览/改参 → 保存工艺设计
```

`ProcessPlanning.vue`（上传 → 手工参数 → 优化 三个 Tab）**没有挂到路由**，当前菜单点「工艺参数优化」不会进入该向导。

各功能当前实现方法如下。

### 加载分析数据

从管件库选一条记录，`POST /api/pp/tubes/load` 把它还原成内存会话。优化器读的是：

- `wall_thickness_data`：沿中心线的壁厚、外径周长
- `segments`：T1–T4、TL1–TL4、TLM*
- `actual_z_length` / `centerline_length`：成品总长 L。长度校验完成后，保存入库时写成**图纸总长 / 修正后形线弧长**，不再用原始 STP 中心线长
- `volume`：成品体积（≤0 时用薄壁公式估算）
- `L4`：直管段长（QA 确认后的 L4，或缺省 0）
- `formline_length`：上述总长 − L4（同样按修正后总长计算）

缺 T/D 时，`_build_segments_for_optimization` 从手工分段或壁厚曲线补齐。

### 推荐工艺路线

按形线长度（总长 − L4）和外径变化率 `(Dmax−Dmin)/Dmin` 推荐：

| 条件 | 推荐 |
|---|---|
| 形线 < 150 mm 且变化率 < 30% | 仅TP抽 |
| 形线 < 150 mm 且变化率 ≥ 30% | 普通抽+扩口 |
| 150–300 mm | TP抽+扩口 |
| ≥ 300 mm | 普通抽+Taper+扩口 |

工程师仍可改选其它已实现路线。

### 优化求解

前端点「开始优化工艺参数」→ `POST /api/pp/optimize`，后台线程按 `pp_method` 分发：

- **GA 遗传算法**（长管、多决策变量）：种群 300、代数 200、精英+锦标赛、交叉变异。
- **暴力枚举**（短管、少变量）：整数 D0（及 T0 步长 0.1）遍历，按适应度排序取前 3。

适应度看扩口率、抽制截面积变化率（目标约 20%–50%）、Taper 外延伸率，超限扣分。GA 可行阈值 0.74，暴力枚举前端按 1.0 显示。

素材长度公共公式（暴力枚举）：

```text
L0 = 管件体积 / 素材截面积 × 1.3
```

GA 三条路线再加封口余量 add1、Taper 余量 add2。

### 改参重算与预览

工程师可改 D0/T0 等，`POST /api/pp/optimize-recalc` 只重算适应度和派生量，不再搜一遍。  
`POST /api/pp/design/preview` 按方案改壁厚曲线、分段表、工艺段评估。

### 保存工艺设计

填写名称、车种号、管件号后写入 `pp_design.db`，供模具设计模块加载。

---

## 0. 前端入口与页面

侧边栏「工艺优化」：

| 项 | 实际值 |
|---|---|
| 模块介绍 | `/pp/introduction` → `Introduction.vue` |
| 工艺参数优化 | `/pp/optimization` → `Optimization.vue` |
| 角色 | `canAccess('工艺规划')` |

`Optimization.vue` 只做一件事：从数据库加载管件，把 `session_id` 交给 `Tab3Optimization.vue`。

同目录还有未接入路由的页面：

| 文件 | 现状 |
|---|---|
| `ProcessPlanning.vue` | 三 Tab 向导（Tab1 分析 / Tab2 手工 / Tab3 优化），**路由未注册** |
| `Tab1ModelLoader.vue` | 仅被 ProcessPlanning 引用 |
| `Tab2ManualInput.vue` | 仅被 ProcessPlanning 引用 |
| `upload/ManualInput.vue` | 曲线编辑壁厚分段，**路由未注册** |

因此：**当前用户能点到的工艺规划页 = 数据库加载 + Tab3 优化。**  
分析、壁厚确认、保存管件发生在「上传分析 → 模型文件上传」。

---

## 1. 前端功能实现流程

### 1.1 数据从哪来

```text
用户在「管件模型分析」完成分析并「保存到数据库」
↓
进入「工艺参数优化」
↓
GET /api/pp/tubes?search=
↓
点「加载」→ POST /api/pp/tubes/load { tube_id }
↓
前端保存 session_id 到 sessionStorage.pp_session_id
↓
渲染 Tab3Optimization
```

加载后会话 ID 形如 `db_<tube_id>`，不是上传分析时的文件名会话。

列表每条显示 `创建时间`（保存时间标签）和记录 `#ID`，同名记录靠保存时间区分。`created_at` 是 **北京时间（Asia/Shanghai，UTC+8）**，不是 SQLite UTC。删除调用已有接口 `POST /api/pp/tubes/delete { ids }`；若删的是当前已加载记录，前端会清空 `session_id`。

### 1.2 Tab3 页面结构

`Tab3Optimization.vue` 从上到下：

1. **工艺路线选择**（GA 组 / 暴力枚举组 / 自由组合）
2. **自动推荐条**（形线长度、外径变化率、推荐原因）
3. **参数精度、余量、权重**
4. **开始优化**
5. **进度条**（仅 GA 有有效进度）
6. **方案列表**（可编辑、重算）
7. **设计预览表**（分段评估、修改后壁厚、总参数表）
8. **保存工艺设计**

进入页面会调 `GET /api/pp/optimize/recommend?session_id=`。

### 1.3 选择工艺路线

前端内部用英文 key，发给后端时转成中文 `pp_method`：

| 前端 key | 后端 `pp_method` | 算法 |
|---|---|---|
| `tp+taper+flaring` | Taper+TP抽+扩口 | GA |
| `normal+tp+taper+flaring` | 普通抽+TP抽+Taper+扩口 | GA |
| `tp` | 仅TP抽 | 暴力 |
| `normal+tp` | 普通抽+TP抽 | 暴力 |
| `normal+flaring` | 普通抽+扩口 | 暴力 |
| `tp+flaring` | TP抽+扩口 | 暴力 |
| `normal+taper+flaring` | 普通抽+Taper+扩口 | 暴力 |
| `normal+taper` | 普通抽+Taper | 暴力 |

「自由组合」只能组出上表已有 key，否则提示「当前工艺组合暂不支持」。不是任意四步都能算。

「加长Taper+TP抽+扩口」在卡片数组里与基本型共用 key `tp+taper+flaring`，随后被去重丢掉，**卡片点不到加长型**。不可行时的「补充方案2」会发 `tp+taper+flaring-extended`，后端不识别该字符串。后端虽有加长型模块，当前 UI **实际调不到**。

### 1.4 点击「开始优化工艺参数」

函数：`startOptimization()`。

请求体（`POST /api/pp/optimize`）：

| 字段 | 来源 |
|---|---|
| `session_id` | 已加载会话 |
| `pp_method` | `toBackendMethod(confirmedMethod)` |
| `population_size` | 写死 300 |
| `generations` | 写死 200 |
| `repetitions` | 写死 1 |
| `d_precision` / `t_precision` / `l_precision` | 页面精度控件 |
| `add1` / `add2` | GA 余量（封口、Taper） |
| `add3` | 暴力枚举扩口补偿 |
| `weights` | 未锁定权重联动后的子目标权重 |

前端校验：无 `session_id` 或未选路线则不发请求。

返回 `{ success, task_id }`。`task_id` 形如 `opt_<session_id>_<unix>`。

### 1.5 轮询与完成

每 1 s：`GET /api/pp/optimize-status?task_id=`。

- `progress.done === true` 且无 error → `GET /api/pp/optimize-result?task_id=`
- 方案写入 `solutions`，默认选第 0 个，再 `designPreview`

暴力枚举几乎立刻完成，进度条往往直接 100%。GA 约每 20 代回一次进度。

### 1.6 编辑与重算

可编辑字段因路线而异（见 `METHOD_META.editable`）。点「应用更改」：

```text
POST /api/pp/optimize-recalc
{ session_id, pp_method, params: editModel, 精度, add1/2/3, weights }
```

用同一套适应度函数重算 L*/面积变化率，不重新搜索。应用后停留在当前方案，不按适应度重排、不跳回第一条。若请求里带了用户填写的 T0，重算使用该值；未填时才用 `T1×D1/D0`。

`recalc_solution_by_method` 必须按各路线 `_calculate_fitness_change` 的真实返回元组解包，否则改 D0/T0 会报错：

- `普通抽+TP抽+Taper+扩口`：12 元组 `(fit, T0, L1, L2, L3, L0, Ln, area1, area2, area3, area4, ext)`；适应度还要读 `Dn`（编辑框只有 D0/T0/D2）
- `普通抽+TP抽`：走 `_calculate_fitness_change2` 的 10 元组 `(fit, T0, Ln, L1, L2, L0, area1, area2, area3, area4)`
- `加长Taper+TP抽+扩口`：请求缺 `D20` 时默认 `(D0+D2)/2`（只有 D0 则用 D0）；还要 `D2`
- `普通抽+Taper+扩口`：适应度要读 `Dn`。页面可编辑只有 D0/T0；请求仍缺 Dn 时用 D2（该路线 D2=Dn）。只发 D0/T0 会 KeyError `'Dn'`

前端应用更改会把当前方案的 `D0/D2/D20/Dn/T0` 一并带上，再用编辑框覆盖。因此「普通抽+TP抽+Taper+扩口」与「普通抽+Taper+扩口」不会再因缺 Dn 报错。其余 UI 路线（仅TP抽、普通抽+TP抽、普通抽+扩口、TP抽+扩口、普通抽+Taper、Taper+TP抽+扩口）只改 D0/T0（及 D2）即可重算。

### 1.7 预览与保存

预览：`POST /api/pp/design/preview`（solution + Taper 是否改壁厚）。  
保存：`POST /api/pp/design/save`（须填设计名称、车种号、管件号）。

不可行时：

- 当前是「Taper+TP抽+扩口」→ 按钮改跑「普通抽+TP抽+Taper+扩口」
- 当前是「普通抽+TP抽+Taper+扩口」→ 按钮发 `tp+taper+flaring-extended`（后端会报不支持）

---

## 2. 后端入口：`pp.py` 工艺规划部分

几何上传/QA 接口见另一份 `PIPE_MODEL_ANALYSIS_RUNTIME_LOGIC.md`。下面只列工艺规划真正用到的接口。

### 2.1 进入优化前的数据接口

| URL | 作用 |
|---|---|
| `POST /tubes/load` | DB → 内存会话，供 Tab3 使用 |
| `POST /apply-to-optimization` | 确认形线后解锁 Tab2（仅未挂路由的向导会用） |
| `GET /thickness-segments` | 自动识别壁厚分段（未挂路由的手工页） |
| `POST /save-manual-params` | 保存 T/TL，生成 1 mm 壁厚曲线 |
| `POST /upload-manual-file` | CSV/XLSX 覆盖壁厚表 |
| `POST /apply-to-tab3` | 标记 `ready_for_tab3`（**optimize 并不检查该标记**） |

线上主路径只依赖 `tubes/load`。`optimize` 只要会话里有 `results` 即可跑。

### 2.2 优化任务

```text
POST /optimize
↓
_build_segments_for_optimization(results)
↓
体积 ≤0 则 _estimate_volume_from_wt
↓
analysis_input = { wall_thickness_data, segments, actual_z_length, volume, L4 }
↓
daemon 线程 optimize_process_parameters_by_method(...)
↓
写入 _opt_tasks[task_id] 与 sess['tab3_result']
```

`GET /optimize-status`、`GET /optimize-result` 读内存 `_opt_tasks`，进程重启即丢。

### 2.3 方法分发（`optimization.py`）

```text
pp_method
├─ GA
│    Taper+TP抽+扩口              → ProcessOptimizer（optimization.py 内，不用 legacy_opt 类）
│    普通抽+TP抽+Taper+扩口       → opt_normal_tp.opt_normal_parameters
│    加长Taper+TP抽+扩口          → opt_replenish.opt_replenish_parameters
└─ 暴力枚举
     仅TP抽                      → opt_tp.OPT_TP.brute_force_optimize
     普通抽+TP抽                  → opt_tp.OPT_TP.brute_force_optimize2
     普通抽+扩口                  → opt_nor_kuo.OPT_Nor_Kuo.brute_force_optimize
     TP抽+扩口                    → opt_tp_kuo.OPT_TP_Kuo.brute_force_optimize
     普通抽+Taper+扩口            → opt_nor_taper_kuo.OPT_Nor_Taper_Kuo.brute_force_optimize
     普通抽+Taper                 → opt_nor_taper.OPT_Nor_Taper.brute_force_optimize
```

`legacy_opt/ga_opt.py` 只被 `opt_normal_tp.py`、`opt_replenish.py` 引用。  
「Taper+TP抽+扩口」用的是 `optimization.py` 里另一份 `GeneticAlgorithm`。

### 2.4 优化输入如何构造

`_build_segments_for_optimization`：

1. D1 / D3：优先 `segments`，否则周长最大/最小 ÷ π。
2. T1–T4：优先已有 segments；否则 `manual_segments.thicknesses`；再否则从壁厚曲线取去重量级（多档时取最大、最小、中位）。
3. TL / TLM：优先已有；否则用 `manual_segments.boundaries` 切平台/过渡。

壁厚表必须有列：`中心线长度(mm)`、`壁厚(mm)`、`外径周长(mm)`。  
总长 L 一律取 `actual_z_length`（优化器注释仍写「真实 Z 轴长度」，当前语义是中心线端点直线距；`pp.py` 缺省时回退 `centerline_length`）。

T1/T2/T3 在优化器内部会**重定义**：T1=各段壁厚最大值，T2=最小值，T3=中间值（没有则等于 T2）。不是图纸字段原样。

---

## 3. 各工艺模块实现方法

公共几何量：

| 符号 | 含义（优化器内部） |
|---|---|
| D1 | 成品最大外径（大径端 / 扩口目标） |
| D3 | 成品最小外径 |
| D0 | 素材外径（决策变量） |
| T0 | 素材壁厚 |
| L0 | 素材长度 |
| L | 成品总长 `actual_z_length` |
| L4 | 直管段，多来自 QA 的 L4 或 `segments.TL4` |
| L1 | 扩口段长（多数路线） |
| L2 | TP 抽段长 |
| L3 | Taper 段长 |
| Ln | 普通抽段长 |
| Dn | 普通抽后外径 |

截面积一律按圆环：`π((D/2)² − (D/2−T)²)`。  
抽制变化率：`(A素材 − A抽后) / A素材`，期望 0.20–0.50。  
扩口率：`(D大 − D小) / D小`，超过 0.80 重罚。  
Taper 外延伸率：`(D大 − D小) / D大`，超过 0.30 重罚。

---

### 3.1 Taper+TP抽+扩口（GA）

**文件：** `optimization.py` 的 `ProcessOptimizer`  
**前端 key：** `tp+taper+flaring`

决策变量：`D0`、`D2`。  
T0 不搜，按 `T0 = T1 × D1 / D0`。

种群：D0 ∈ (max(D3+0.5, D1/1.8), D1−0.5)，D2 ∈ (D3+0.5, min(D3/0.7, D0−0.5))。

沿形线（跳过前 5 点）找直径落到 D0、D2 的弧长：

```text
L1 = 直径降到 D0 处的弧长          → 扩口段
L2 = 降到 D2 处的弧长 − L1         → TP抽
L3 = max(0, L − L1 − L2 − L4)      → Taper
L0 = V/A0 × 1.3 + Taper余量/A0 + add1
```

适应度三项（默认各 0.25）：扩口、TP 抽面积变化、Taper 外延伸。  
惩罚：L1/L2 偏离 100 mm 过多或 >150；D0/D2 越界；T0 不在 [T1, 2×T2]；L1>TL1；L3>700。

输出方案字段：D0 T0 L0 D1 D2 D3 L1 L2 L3 L4 T1 T2 T3 fitness area_change_rate outer_extension_ratio。

---

### 3.2 普通抽+TP抽+Taper+扩口（GA）

**文件：** `legacy_opt/opt_normal_tp.py` → `OPT_Normal_TP` / `opt_normal_parameters`

决策变量：`D0`、`Dn`、`D2`、`T0`。

```text
Dn  ∈ (D3+0.5, D1−0.5)     普通抽目标径
D0  ∈ (Dn+0.5, D1−0.5)
D2  ∈ (D3+0.5, min(D3/0.7, Dn−0.5))
T0  ∈ (T1, min(2×T2, 10))
```

形线：直径到 Dn → L1（扩口），再落到 D2 → L2（TP），`L3 = L − L1 − L2 − L4`。  
普通抽长度 Ln 由体积平衡：素材体积减去扩口段体积后，按 Dn、T2 截面反算。

适应度四项（各 0.25）：扩口、TP 抽、普通抽、Taper。  
`opt_normal_parameters` 构造优化器时 **add1/add2 写死 10**，函数参数未传入构造。

手工重算解包 12 元组（含 Ln 与四组面积变化），见 §1.6。  
适应度还要 `Dn`；页面不编 Dn，应用时沿用当前方案。

---

### 3.3 加长Taper+TP抽+扩口（GA）

**文件：** `legacy_opt/opt_replenish.py` → `OPT_Replenish` / `opt_replenish_parameters`

决策变量：`D0`、`D2`、`D20`（加长 Taper 中间径）。

```text
L1 = 形线落到 D0
L2 = 落到 D2 − L1
L3 = L − L1 − L4     （与 L2 区间重叠，这是加长型特有语义）
```

TP 抽面积用 D20 处壁厚。外延伸拆成 outer1 / outer2。  
同样，构造时 add1/add2 写死 10。

手工重算若请求未带 `D20`，默认 `(D0+D2)/2`。

**当前前端选不到该 `pp_method`。** 后端分发仍保留。

---

### 3.4 仅TP抽（暴力）

**文件：** `legacy_opt/opt_tp.py` → `brute_force_optimize`

```text
D0 固定为 round(D1)+1
T0 从 T1+0.1 扫到 5.0，步长 0.1
D2 = D3
L1 = 0
L2 = L − L4
L0 = V/A0 × 1.3
```

适应度只评 TP 抽面积变化（默认权重 1.0）。L2>150 或 T0>2×T2 扣分。

**返回全部 T0 方案**（不是只取 3 个），按适应度排序。

---

### 3.5 普通抽+TP抽（暴力）

**同一文件** `OPT_TP.brute_force_optimize2`

```text
Dn = D1
D0 从 int(D1)+1 扫到 150
T0 从 T1+0.1 扫到 5.0，步长 0.1
```

取适应度前 3。输出含 Ln、两组面积变化率。

手工重算走 `_calculate_fitness_change2`（10 元组），不是仅 TP 抽那条 5 元组。

---

### 3.6 普通抽+扩口（暴力）

**文件：** `legacy_opt/opt_nor_kuo.py`

```text
Dn = D3
D0 从 int(D3)+1 扫到 150
T0 从 T1×D1/Dn+0.1 扫到 5.0，步长 0.1
L1 = L − L4     （整段当扩口长度）
Ln 由体积平衡
```

适应度：扩口率 + 普通抽对 T1/T2/T3 三组面积变化。取前 3。

---

### 3.7 TP抽+扩口（暴力）

**文件：** `legacy_opt/opt_tp_kuo.py`

```text
D0 从 max(int(D3)+1, int(D1/1.8)) 扫到 int(D1)−1
T0 = T1×D1/D0（不扫 T0）
D2 = D3
L1 = 形线落到 D0 的弧长
L2 = max(L − L1 − L4 + add3, 0)
构造时 L4 被设为 传入L4 + add3
```

只扫整数 D0，取前 3。

---

### 3.8 普通抽+Taper+扩口（暴力）

**文件：** `legacy_opt/opt_nor_taper_kuo.py`

T0 搜索方式与 `opt_nor_kuo.py` 相同（步长 0.1）；D0 上限 150，允许大于 D1。

```text
三层：Dn 从 int(D3)+1 到 int(D1)−1
      D0 从 Dn+1 扫到 150
      T0 从 T1×D1/Dn+0.1 扫到 5.0，步长 0.1
L1 = 形线落到 Dn（跳过前 5 点）
L3 = L − L1
Ln 体积平衡
D2 = Dn
```

手工重算若未带 T0，回退 `T0 = T1×D1/D0`。  
页面应用更改只改 D0/T0，须同时带上当前方案的 Dn；缺 Dn 时用 D2。

适应度：扩口率 + 普通抽对 T1/T2/T3 三组面积变化 + Taper 外延伸。取前 3。  
惩罚：L1 偏离 100 mm 过多或 >150；D0≤D3；T0 不在 [T1, 2×T2]；L1>TL1；L3>700；D3>D0−1。  
**不再**因 D0≥D1 扣分（否则上限 150 无意义）。

这是形线 ≥300 mm 时推荐的路线。

---

### 3.9 普通抽+Taper（暴力）

**文件：** `legacy_opt/opt_nor_taper.py`

```text
Dn 固定 = D1（无扩口）
D0 从 int(D1)+1 扫到 150
T0 从 T1+0.1 扫到 5.0
L1 = 0（D0≥D1）或形线落到 D0
L3 = L − L1
```

适应度：普通抽 + Taper。无扩口项。取前 3。

---

### 3.10 遗传算法引擎

两份实现，逻辑相近：

| 文件 | 谁用 |
|---|---|
| `optimization.py` 的 `GeneticAlgorithm` | Taper+TP抽+扩口 |
| `legacy_opt/ga_opt.py` | 普通抽+TP抽+Taper+扩口、加长型 |

共性：种群 300（类默认 500，接口传入 300）、代数 200、变异率 0.2、精英 10、锦标赛 5。  
选择：精英保留 + 锦标赛；交叉：按键混合数值；变异：数值 × 0.9–1.1。  
多次运行后合并，取历史最佳个体前 3；过相似则扰动第二名。  
`is_feasible = best_fitness ≥ 0.74`。

暴力枚举无进化曲线，`fitness_history` 为空。

---

## 4. 核心数据流

```text
管件 DB 记录
↓
session.results
  wall_thickness_data[]
  segments { T1..T4, TL1..TL4, TLM1..3, D1, D3 }
  actual_z_length, volume, L4
↓
_build_segments_for_optimization  （补 T/D/TL）
↓
optimize_process_parameters_by_method
↓
solutions[]   （D0 T0 L0 及各段 L、fitness、面积变化率）
↓
design/preview
  apply_modify_thick → modified_wt_data, segments2
  _evaluate_segments_for_method → 分段评估
  _build_alldata_table → 总参数表
↓
design/save → pp_design.db
```

各方案不改原始 `sections_full`。预览里的修改壁厚是工艺后的设计曲线。  
扩口段壁厚按 `T1×D1/Dn` 缩放；方案无 Dn 时才用 D0。  
Taper 段按 `D3/D2` 缩放；方案无 D2 时才用 D0。GA 三条路线本身已用 D2；暴力路线里「普通抽+Taper+扩口」和「普通抽+Taper」（方案 D2=D1）现同样走 D2。  
勾选「将 Taper 段壁厚处理成过渡段」后，Taper 改为线性过渡；总表 `TLM1` 优先写方案 **L3**（平台检测单独拿不到这段长度）。未勾选仍是扩口后约 50 mm 短过渡。细则见 `docs/WALL_THICKNESS_MODIFY_LOGIC.md`。  
「仅TP抽 / 普通抽+TP抽」预览强制修改后壁厚与原始相同。

---

## 5. 推荐逻辑与前端展示的对应

`recommend_pp_method` 返回的 `recommended` 是英文短码（`tp`、`normal+flaring` 等），与 Tab3 卡片 key 一致，用于打「推荐」角标。

`all_options` 里部分 `feasible` 标志**不参与推荐选择**，只作列表。  
「普通抽+TP抽+Taper+扩口」在推荐函数里 `feasible: True`，但 ≥300 mm 时实际推荐的是「普通抽+Taper+扩口」，不是 GA 兜底方案。

模块介绍页只写了 5 种工艺，后端实际 9 种（含加长型与普通抽+Taper）。

---

## 6. 与注释 / 页面文案的差异（以代码为准）

1. 侧边栏「工艺参数优化」不是三 Tab 向导，而是 **DB 加载 + Tab3**。`ProcessPlanning.vue` 未注册路由。
2. `optimize` **不检查** `ready_for_tab2` / `ready_for_tab3`。
3. 「加长Taper+TP抽+扩口」后端有、前端卡片被去重；补充方案2 的 key 后端不认。
4. 优化器注释要求 `actual_z_length` 为「真实 Z 轴」；当前分析语义是中心线端点直线距。`pp.py` 会在缺失时用 `centerline_length`。
5. 优化器内部的 T1/T2/T3 是壁厚最大/最小/中间值，不是图纸 T 字段原样。
6. `opt_normal_parameters` / `opt_replenish_parameters` 构造时 add1/add2 写死 10，页面余量对这两条 GA 路线可能不生效。
7. 「仅TP抽」返回全部 T0 网格，其它暴力枚举只返回前 3。
8. 介绍页写「输入 STL」；当前优化输入是已保存的分析结果，STL/STP 在上传分析阶段已处理完。
9. 两套 GA 类并存（`optimization.py` 与 `legacy_opt/ga_opt.py`）。
10. 自由组合不能创造新工艺，只能点选已实现组合。
11. 「普通抽+Taper+扩口」已改为三层暴力（Dn×D0×T0），D0 上限 150；与「普通抽+扩口」一样搜 T0，不再用 `T0=T1×D1/D0` 作为搜索变量。
12. 列表「创建时间」是北京时间；SQLite 默认 UTC 已不再用于新写入。
13. 重算解包与各路线 `_calculate_fitness_change` 返回长度对齐；加长型缺 D20 时补 `(D0+D2)/2`。
14. 「普通抽+Taper+扩口」与「普通抽+TP抽+Taper+扩口」重算都要当前方案 Dn。应用更改统一带上 D0/D2/D20/Dn/T0。
15. 勾选 Taper 作过渡后，总表过渡段长度写 L3，不再依赖常值平台空隙检测。

---

## 7. 关键文件索引

| 角色 | 路径 |
|---|---|
| 当前优化页 | `frontend/src/views/pp/Optimization.vue` |
| 路线选择与求解 UI | `frontend/src/views/pp/Tab3Optimization.vue` |
| 未挂路由的三 Tab 壳 | `frontend/src/views/pp/ProcessPlanning.vue` |
| API | `backend/api/pp.py`（optimize / recalc / recommend / design/*） |
| 分发与基本型 GA | `backend/core/pp/optimization.py` |
| 仅TP / 普通抽+TP | `backend/core/pp/legacy_opt/opt_tp.py` |
| 普通抽+扩口 | `backend/core/pp/legacy_opt/opt_nor_kuo.py` |
| TP抽+扩口 | `backend/core/pp/legacy_opt/opt_tp_kuo.py` |
| 普通抽+Taper | `backend/core/pp/legacy_opt/opt_nor_taper.py` |
| 普通抽+Taper+扩口 | `backend/core/pp/legacy_opt/opt_nor_taper_kuo.py` |
| 普通抽+TP+Taper+扩口 | `backend/core/pp/legacy_opt/opt_normal_tp.py` |
| 加长Taper+TP+扩口 | `backend/core/pp/legacy_opt/opt_replenish.py` |
| GA 引擎（后两条） | `backend/core/pp/legacy_opt/ga_opt.py` |
| 预览改壁厚 | `backend/core/pp/modify_thick.py` |
| 壁厚改写说明 | `docs/WALL_THICKNESS_MODIFY_LOGIC.md` |
| 工艺设计库 | `backend/core/pp/design_db.py` |
