# Giant-Unified — 捷安特抽管工艺软件（整合版）

> 在原 `Giant-main-new1`（抽管工艺软件）基础上，新增 **一体化流水线**：
> 从原始管件出发，自动导出 5 类派生物，再分别用两条**互不依赖**的算法把每一个派生物
> 重建成「1 个 Solid / 4 个面 / 2 条共享分型边 / 0 自由边」的 4 面实体。
>
> 原程序目录 `D:\AAAGNT-1\Giant-main-new1` 未做任何修改。

## 1. 这是什么

| 项目 | 说明 |
|---|---|
| 主服务 | Flask + Vue（原 Giant 全部功能：登录/许可证/工艺设计/成型·扩口·压扁·压弯/数据库） |
| 一体化流水线 | `backend/core/entity/`（新）+ `/api/entity/*`（新）+ 前端「一体化流水线」页（新） |
| 端口 | Web **5890**、dev **5060**、Vite **3001**、FreeCAD 桥 **18766**（与旧程序全部错开） |
| 数据起点 | `gnt管件\<件>.stp`（6 个原始管件，sha256 已核对） |
| 输出根 | `entity_out\`（默认，可用 `--out` 覆盖） |

**反切模具已下线**：本程序只产出 5 类模型，不含任何 `*_反切模具.stp`。

## 2. 两类产物

每件先导出 5 类派生物（`00_models\`，Giant 阶段，与网页「成型/扩口/压扁/压弯」页同源算法）：

| 模型键 | 工序 | 说明 |
|---|---|---|
| `成型_外径形腔` | shaping | 逐站位外轮廓放样的实心包络 |
| `扩口_扩口腔体` | belling | 全长回转体腔体 |
| `扩口_扩口芯轴` | belling | 200 mm 芯轴 |
| `压扁_压扁模型` | flattening | 椭圆截面沿轴放样 |
| `压弯_压弯模型` | bending | 沿弯曲中心线扫掠（不含压扁段） |

再对每个派生物跑重建。**默认每个模型只走一条路**，需要对照时显式 `--routes c1,pipe` 双跑：

| 模型 | 默认路线 | 算法 | 实现 |
|---|---|---|---|
| `成型_外径形腔` | `c1` | C1 外推（测量 JSON → 2 mm 重采样 → 端部外推 → 4 面）／2052-2 走口径保形 | 主进程 OCP |
| `扩口_扩口腔体`、`扩口_扩口芯轴`、`压扁_压扁模型`、`压弯_压弯模型` | `pipe` | FreeCAD 截面重蒙皮（闭合环排序 → 镜像定轴 → 周期 BSpline → uIso 切割 → 缝合） | `freecadcmd.exe` 子进程 |

> `c1` 路对上述四类也实现了自参考保形，`pipe` 路对形腔也能跑；两条路互为对照，默认分派只是「每类选更合适的一条」。

## 2.1 「硬模成型」四页已内置实体化（2026-11-04）

「硬模成型」下的**成型 / 扩口 / 压扁 / 压弯**四页点「生成」时，后端在生成完原模型后**同步**再跑一次实体化，直接把结果替换成 4 面实体：

| 页面 | 产物 | 默认路线 |
|---|---|---|
| 成型 | 外径形腔 | `c1`（C1 外推／2052-2 口径保形） |
| 扩口（芯轴 + 型腔两个产物都替换） | 扩口芯轴、扩口腔体 | `pipe` |
| 压扁 | 压扁模型 | `pipe` |
| 压弯 | 压弯模型 | `pipe` |

- 实现：`backend/core/entity/rfm_bridge.py` 的 `convert_shape()`；`backend/api/rfm.py` 在 `generate` / `generate-cavity` 里调用；返回体新增 `entity` 字段。
- 前端 `RfmShell.vue` 显示绿色提示「已实体化：路线 …；N 面 / M 实体 / 分型边 K 条 / 实体有效…；体积 …（原 …）」，预览 STL、体积、下载 STEP 全部换成 4 面件；失败则黄色提示「实体化未生效（保留原模型）：…」，原模型照常可用。
- 失败降级链：`c1` → `pipe` → 自参考保形；都失败才保留原模型。
- 反切模具（勾选模具时）不做 4 面化，仍输出整块模具。
- 生成器侧修复（2026-11-04，`core/rfm/processors.py`）：
  - **压扁**：椭圆长短轴来自工艺表、按 0.01 mm 取整，与实测周长本就会有零点几毫米的差；原实现直接判「轴长与周长不一致」并拒绝生成。现在改为**按周长自动匹配**——偏差超容差（0.5% 或 0.05 mm）时把长短轴同比例缩放到实测周长（保长短比），修正量记录在 `proc.axis_corrections` 并写日志；缩放超过 ±20% 才仍判参数错误。实测本批件修正 1.3%–4.0%，可正常出模型。
  - **压弯**：`arc`（中心线站位）与 `s`（周长表站位）两套网格叠加后会产生相距 0.003 mm 的近重合站位，放样出零宽带，带面法线翻转，被质量检查判成「180° 折角」并拒绝。现在放样前把间距 < 0.2 mm 的站位抽稀（新增 `_thin_stations()`），1263 站 → 636 站，侧面接缝最大夹角从 180.0° 降到 0.64°，`inspect_solid` 返回 `ok`。几何外形不变（只是去掉重复截面），导出体积差异在 0.03% 内。
  - 回归：`entity_out` 里 6 件 × 压扁/压弯 × pipe 共 12 格已用新生成器重算，全部 4 面 / 1 实体 / 0 自由边 / 2 分型边 / valid；30 格汇总 `counts {total 30, cached 30, error 0}`、`conformance` 全 0、`over_threshold` 空；快速单测 75 passed / 5 skipped。

## 2.2 下载文件名统一命名（2026-11-04）

「硬模成型」四页与一体化流水线的所有 STEP 下载都统一成 **`件号_工序_模型__路线.step`**：

| 场景 | 文件名示例 |
|---|---|
| 一体化流水线 · 外径形腔（c1） | `24TK_1324-1_成型_外径形腔__c1.step` |
| 一体化流水线 · 压弯（pipe） | `24TK_1324-1_压弯_压弯模型__pipe.step` |
| 一体化流水线 · 原模型（未实体化） | `24TK_1324-1_压弯_压弯模型.stp` |
| 成型页生成后下载 | `24TK_1324-1_成型_外径形腔__c1.step` |
| 扩口页 · 芯轴 / 型腔 | `24TK_1324-1_扩口_扩口芯轴__pipe.step` / `…_扩口_扩口腔体__pipe.step` |
| 反切模具 | `24TK_1324-1_成型_反切模具.step` |
| 件级报告 / 全部件汇总 | `24TK_1324-1_报告.json` / `全部件汇总.md` |

- 件号取自工艺设计的来源文件名（如 `24TK_1324-1.stp` → `24TK_1324-1`），不依赖会话里的临时 id。
- 文件名由服务端 `Content-Disposition`（RFC 5987）给出，前端下载按钮直接用服务端名字，不再写死「外径形腔.step」。
- 改名只影响下载显示名，磁盘路径与产物内容不变。

## 2.3 「一体化流水线」页改成单件流程页（2026-11-04）

页面结构按「一次看一件」重做：

1. **第一行选管件**（可搜索），旁边是「刷新」「运行（保留缓存，只补缺）」「停止」。
2. **输入与口径**：原始 STEP、端部口径（`c1`/`v1`，参考件标注口径保形）、测量缓存状态、输出目录、件级报告/汇总下载。
3. **默认工艺参数**：测量（算法 A / 1 mm 站位 / 25 mm 稀疏 step）、C1 路线（采样 65 / 平滑 2 / 2 mm 外推）、Pipe 路线（采样 96 / 截面 auto / 方向 auto），以及「模型 → 默认路线」映射。
4. **工艺路线（默认）**：与「工艺参数优化」页同一套路线目录——`GA 遗传算法`（Taper+TP抽+扩口、普通抽+TP抽+Taper+扩口、加长Taper+TP抽+扩口）、`暴力枚举`（仅TP抽、普通抽+TP抽、仅扩口、普通抽+扩口、TP抽+扩口、普通抽+Taper+扩口、普通抽+Taper）、`自由组合`（普通抽 / TP抽 / Taper / 扩口 任意组合）。推荐值用主程序同一套 `recommend_pp_method()` 按本件实测型线长度与外径变化率计算，标 `推荐`；该件已登记工艺设计时再标 `已登记`，与推荐不一致会提示。
5. **上游工艺设计**：显示该件是否已登记设计与「补算上游设计」按钮。未登记且推荐路线可行时，按推荐路线跑一次优化器，把设计写入 `pp_design.db`（`display_name=<件> 默认设计`、`vehicle_spec=自动补算`、`extra_params.自动补算=true`、带逐站「修改后壁厚」）；推荐路线不可行（适应度 < 0.74）时**不写库**，只提示去「工艺优化 → 工艺参数优化」人工调整。点「运行」时也会先补算缺失设计（`ensure_design=true`），失败不阻断流水线。
6. **流程阶段**：按「原始管件 → 测量/型线 → 成型 → 扩口 → 压扁 → 压弯 → 实体化」七步展示，每步标出是否已产出并附细节（上游工艺、缓存大小、默认路线）。
7. **设计输入与型线 / 壁厚**：读取 `pp_design.db` 里该件的 30 个工艺标量（`D0/T0/L0/D1/D2/T1…TL1`）与原始分段（`D1/D3/T1..T4/TL1..4/TLM1..3`），并把设计阶段逐站位的「壁厚数据」（761/636 站）画成两张曲线：型线外径周长、壁厚设计值与修改后值；下面再接「实测复核」（实测周长、实测壁厚、壁厚中位与范围）。无设计登记的件显示空态，不影响其它功能。
8. **5 类模型与产物**：每类一行，列出原模型下载、默认路线实体化件下载、面数 / 实体数 / 分型边 / 自由边 / 体积与偏差 / 耗时。
9. **运行状态**：进度、当前件、格数统计、上游设计补算结果与最近日志。

运行语义是 **resume**：已有产物按缓存复核，只补缺失的格子，不强制重算；要重算请用命令行 `run_entity.py --force`。单件运行的 `_report/summary.json` 会并入磁盘上其它件的 `report.json`，不会缩成一件。

## 3. 快速开始

### 3.1 双击启动（最省事）

在资源管理器里进入 `D:\AAAGNT-1\Giant-Unified`：

- **`start_unified.bat`** —— 启动服务（前台窗口，窗口里持续打印日志）；浏览器打开 <http://127.0.0.1:5890>
- **`stop_unified.bat`** —— 停止服务（按端口释放，含 venv shim 父进程）

`.bat` 只用 ASCII 并自带 `chcp 65001`，内部优先 `pwsh`、没有则退回系统自带的 `powershell.exe`（Windows PowerShell 5.1），不要求安装 PowerShell 7。

### 3.2 命令行

```powershell
cd D:\AAAGNT-1\Giant-Unified

# 前台启动（Ctrl+C 停止；关掉窗口也会停）
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_unified.ps1

# 后台启动（日志 logs\server.out.log，PID 写 logs\server.pid）
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_unified.ps1 -Background

# 停止（推荐，按端口；也可 .\stop_unified.bat）
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop_unified.ps1

# 改过前端源码后：重建 dist 再启动（esbuild 需要把 TEMP 指到仓库内）
.\start_unified.ps1 -Build

# 只跑流水线（不起 Web）；注意 -PipelineArgs 默认只有 --dry-run
.\start_unified.ps1 -Pipeline -PipelineArgs '--dry-run'
.\start_unified.ps1 -Pipeline -PipelineArgs '--out','D:\AAAGNT-1\Giant-Unified\entity_out'

# 或直接用 CLI
cd backend
D:\python\envs\occ\Scripts\python.exe run_entity.py --dry-run        # 30 条计划（默认按模型分派）
D:\python\envs\occ\Scripts\python.exe run_entity.py                  # 默认路线出件
D:\python\envs\occ\Scripts\python.exe run_entity.py --routes c1,pipe # 两路对照（60 条）
```

登录：默认管理员 `admin`，初始口令 `Admin@2027`（`backend/core/common/db.py` 首次建库时创建，
启动横幅也会打印）。**首次登录后请立即改密**；不要把默认口令留在共享或联网环境。

许可证门禁：`license\` 下没有 `license.dat` 时，需 `GTAPP_LICENSE_GATE=0`（`start_unified.ps1`
默认 `-LicenseGate 0` 并打印告警；正式部署用 `-LicenseGate 1` 开启门禁）。
`backend/core/common/build_info.py: BUILD_CHANNEL = "dev"` 使开发者可用该开关。

### 3.3 离线交付包（客户机免装 Python）

给客户时不建议交付源码仓库（需要装 Python 3.11 + `cadquery-ocp` 等依赖）。用打包脚本生成
**自包含交付包**，客户解压后双击即可运行：

```powershell
cd D:\AAAGNT-1\Giant-Unified
D:\python\envs\occ\Scripts\python.exe deploy\make_portable_package.py
# 产物：dist\GiantTubeApp\（约 2.3 GB）与 dist\GiantTubeApp_win64.zip（约 860 MB）
```

包内布局（`run_release.py` 自带该布局支持：`python\` 放在安装根，`GTAPP_INSTALL_ROOT`
指向包根，前端走 `frontend\dist`，许可证走 `license\license.dat`）：

```
GiantTubeApp\
  python\            便携 CPython 3.11 + 全部 site-packages（numpy / OCP / cadquery / Flask …）
  backend\           后端、数据库
  frontend\dist\     前端页面
  freecad_addon\     FreeCAD 插件（pipe 路线）
  gnt管件\           6 个原始 STEP
  entity_out\        实体化产物 + 测量缓存
  license\ tools\ docs\
  .env  启动.bat  停止.bat  交付说明.txt
```

- **无需安装 Python**：`python\python.exe` 直接跑（`python311._pth` 固定搜索路径，
  已补 `ffi-8.dll` / OpenSSL / sqlite 等原生 DLL）。
- **FreeCAD**：pipe 路线（扩口/压扁/压弯）仍需要 FreeCAD 1.1.4。把 FreeCAD 解压到
  `freecad\`（含 `freecad\bin\freecadcmd.exe`），或设 `FREECAD_HOME`；没有 FreeCAD 时
  仅 pipe 路线不可用，c1 路线（成型_外径形腔）照常。
- 路径全部相对包根解析：`backend/core/entity/settings.py` 的 `PY_OCC` 默认取
  `sys.executable`，`FREECAD_HOME` 依次尝试 `<包根>\freecad`、`<包根>\..\freecad`、`D:\Freecad`。
- 本机已验证的交付件：`D:\AAAGNT-1\_deliver\GiantTubeApp_win64.zip`（859.7 MB，解压即用，
  已测 `/api/version`、登录、`/api/entity/status`、`/plan`=30、`/parts/<件>/profile`=761 站）。

## 4. 输出布局

```
entity_out/
  _cache/<件>_measured.json             # 测量缓存（c1 口径；2052-2 另有 _v1）
  _report/summary.json|summary.md       # 全局汇总（6 件 × 5 类 × 默认路线 = 30 格）
  _scratch/pipe_jobs/job_<uuid8>.json   # FreeCADCmd 任务描述/结果
  <件>/
    00_models/<slug>.stp                # 5 类派生物
    10_c1/<模型键>__c1.step
    20_pipe/<模型键>__pipe.step          # 扩展名随 FreeCAD 导出
    20_pipe/_work/<slug>/               # FreeCAD 侧中间件（solid.step/report.json），可随时删
    report.json / report.md
```

> `20_pipe/_work` 与 `_scratch/` 都是中间件，只用于排障；默认交付是每件 `00_models` 5 个 + `10_c1` 1 个 + `20_pipe` 4 个 = 10 个 STEP 与两份 report（双跑时 `10_c1`/`20_pipe` 各 5 个）。`--resume` 断点续跑只看默认路线的交付件是否存在。

## 5. 端到端验收口径

每格记录：`status / volume_mm3 / volume_error_pct / faces / edges / solids / free_edges /
parting_edges / valid / bbox_dev_mm / direction / sections_used / seconds`。
**只记录，不因不合规中断流水线。**

- 4 面实体规范：`faces == 4`、`solids == 1`、`free_edges == 0`、分型边 2 条、`valid is True`
- 体积基准：截面棱柱法（`shape.Volume` 会把重叠带面重复计入，不能直接比）
- `bbox_dev_mm` 统一取 optimalBoundingBox 口径
- 硬/软问题分级：面数/实体数/自由边/有效性违规 → 该格 `error`；分型边 ≠ 2 条只进 `problems`（状态仍 `ok`/`cached`）

## 6. 首跑结果（2026-11-04，6 件 × 5 类 × 默认路线 = 30 格）

- 命令：`cd backend; python run_entity.py`（默认按模型分派：外径形腔 → c1，扩口/压扁/压弯 → pipe）
- 结果：**30/30，error 0**；`--resume` 复跑 **27.3 s**（30 格全 cached）
- 拓扑：30 格全部 `faces=4 / edges=12 / solids=1 / free_edges=0 / valid=True`
- 体积：偏差 ≤0.17%（最大 `1324-2 / 压弯_压弯模型 / pipe` 0.1701%），无超 1% 记录
- 分型边：29 格 2 条；`1324-2 / 扩口_扩口芯轴 / pipe` 为 4 条（回转体短件真实棱线多于 2 条，软问题，已记入 `problems`）
- `bbox_dev_mm`：外径形腔（c1）约 0.02–0.03 mm；扩口两件 ≤0.03 mm；压扁 pipe 9.8–31.4 mm；压弯 pipe 2.9–15.8 mm
  （压扁/压弯的 pipe 路会削掉薄凸起与端部游离段，是算法局限；要形准可用 `--routes c1` 出保形件对照）
- 金标准命中（c1，外径形腔）：1,881,257.5 / 2,449,483.3 / 690,851.2 / 1,562,196.0 / 135,077.5 / 1,108,104.4，偏差 ≤0.053%
- 报告：`entity_out\_report\summary.md|json`、`entity_out\<件>\report.json|md`
- 交付：6 件 ×（5 派生 + 1 c1 + 4 pipe）= **60 个 STEP**；双跑对照用 `--routes c1,pipe`（60 格 / 每件 15 个 STEP）

## 7. 测试

```powershell
cd D:\AAAGNT-1\Giant-Unified
D:\python\envs\occ\Scripts\python.exe -m pytest backend\tests\entity -q                   # 79 passed, 1 skipped，约 120 s
$env:ENTITY_SKIP_SLOW='1'; D:\python\envs\occ\Scripts\python.exe -m pytest backend\tests\entity -q   # 75 passed, 5 skipped，约 19 s
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_unified_e2e.ps1 -Out .tmp\s10\e2e   # 端到端冒烟
```

## 8. 详细文档

- 契约与实现细节：[ENTITY_PIPELINE.md](ENTITY_PIPELINE.md)
- 已知局限与度量陷阱：见 `ENTITY_PIPELINE.md` §8 注 与 §12
