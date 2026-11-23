# 一体化流水线（Entity Pipeline）使用与接口说明

> 适用范围：`backend/api/entity.py`、`backend/core/entity/`、`frontend/src/views/EntityPipeline.vue`。
> 本文记录 2026-11-06 的行为调整：**设计由人工判断后导入数据库，流水线只输出推荐工艺路线**；
> **上传支持多文件 + 子进程并行测量（默认 2 路）**。

## 1. 目标与边界

- 流水线负责：测量缓存、按推荐路线生成各模型 STEP、输出报告。
- 工艺设计（抽管道次、Taper、扩口等参数）**不由流水线自动补算**。
- 没有登记设计时，流水线仍可运行：
  - `route_plan.recommended_label / reason / line_length_mm / l4_mm / d_change_pct` 始终可用；
  - `process_summary.drawing` 留空，并带 `missing_design=True` 与明确提示；
  - 报告中的「推荐工艺路线」置于工艺摘要前部，缺设计不算错误。

## 2. 只输出推荐路线（任务 A）

### 2.1 默认行为

`POST /api/entity/run` 的 `ensure_design` 默认值已由 `True` 改为 `False`
（`backend/api/entity.py:2736`）：

```jsonc
// 不传 ensure_design：不补算设计，只输出推荐路线
{ "parts": ["H0A3Z2-DT"], "models": ["成型_外径形腔"], "routes": ["c1"] }
```

任务摘要中新增：

| 字段 | 含义 |
| --- | --- |
| `job.ensure_design` | 是否补算设计（默认 `false`） |
| `job.design_mode` | `manual`（默认）/ `auto`（显式 `ensure_design=true`） |
| `job.design_note` | 「不自动补算设计：设计由人工判断后导入数据库，流水线只输出推荐路线」 |
| `job.designs` | 每件设计状态（仅自动模式会填充 created/registered/infeasible/…） |

启动日志会明确写出：

```text
设计：不自动补算缺失设计；设计请人工判断后导入数据库，本任务只输出推荐工艺路线
```

### 2.2 兼容入口

- 显式传 `ensure_design: true` 仍会调用 `_ensure_designs_for_job` →
  `design_gen.create_default_design`，行为与旧版一致；日志会写「补算上游设计=true」。
- 手工补算接口保持不变：`POST /api/entity/parts/<part>/design`。
- 前端已移除「补算上游设计」按钮，工艺路线卡改为提示「设计由人工判断后导入数据库」。

### 2.3 摘要与报告

`GET /api/entity/parts/<part>/flow` 的 `process_summary`：

- 有设计：`drawing.normal/tp/taper` 正常返回道次。
- 无设计：
  - `summary.design_missing = true`
  - `summary.drawing.missing_design = true`
  - `summary.drawing.note = "未导入工艺设计；请人工判断后导入数据库，流水线只给推荐路线（本区暂无道次）"`
  - `route_plan` 仍由 `_route_plan_for(part, design=None, ...)` 计算，使用
    `_end_treatment_for(part, ROUTE_PREF_MODEL)` 命中 121→v1 缓存。
- `flow.design_state.message` 同步改为「未登记工艺设计；设计请人工判断后导入数据库（流水线只给推荐路线，不再自动补算）」。

`report.md` / `report.json`：工艺摘要由 `process_summary.summary_markdown` 生成，
「- 推荐路线：…」位于几何摘要之后、设计记录之前；缺设计不再作为错误写入。

## 3. 多文件上传与并行测量（任务 B）

### 3.1 上传接口

`POST /api/entity/upload`（multipart）：

- 字段 `files`：可重复，一次提交多个 `.stp/.step`；
- 字段 `file`：单文件旧字段，继续兼容；
- 同名文件自动加 `_2 / _3 …` 后缀（`_unique_upload_path`）；
- 每个文件独立 task，接口**立即返回**，测量在后台线程池 + 独立子进程执行。

响应（节选）：

```jsonc
{
  "success": true,
  "task_id": "up_xxx",          // 兼容旧前端：第一个任务
  "part": "DemoPart",
  "status": "queued",
  "file": "DemoPart.stp",
  "size": 123456,
  "task": { "...": "第一个任务摘要" },
  "tasks": [ { "task_id": "...", "part": "...", "status": "queued", "progress": 2.0 } ],
  "count": 2,
  "errors": [ { "file": "bad.txt", "error": "只支持 .stp / .step 文件…" } ],
  "parallel": 2,
  "message": "已接收 2 个文件，后台并行测量（最多 2 路）"
}
```

单个文件失败（后缀/空文件/保存失败）进入 `errors`，不拖累其它文件；全部失败返回 400。

### 3.2 并行度

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| 环境变量 `GTAPP_UPLOAD_PARALLEL` | `2` | 线程池大小，夹在 `1..8` |

取舍：OCP 测量单进程内存/CPU 占用高，默认 2 路可在「同时上传多个件」与
「不把工作站打满、不与流水线抢 OCP」之间折中；不建议默认调高，确认机器余量后
再设 `3`–`4`。上传任务活跃（`queued/measuring/saving`）时 `POST /api/entity/run`
返回 409，避免上传测量与流水线同时进 OCP。

### 3.3 任务状态机

```text
queued → measuring → saving → done
   └──────────────┴──────────→ error
```

- `queued`：已落盘，等待线程池/等当前流水线任务结束；
- `measuring`：子进程 `prepare_proc.run_prepare(measure_only=True)` 测量，
  进度从子进程日志里的 `NN%` 解析（映射到 5–88%）；
- `saving`：读取测量缓存 JSON，调用 `thickness_db.save_full_analysis` 入库（88–100%）；
- `error`：错误前缀区分「测量失败 / 测量结果读取失败 / 入库异常」，可直接展示。

### 3.4 子进程 measure_only

- `run_prepare(..., measure_only=True)`：job json 增加 `"measure_only": true`；
- `prepare_worker.run_job` 在测量完成后直接返回
  `{"models": {}, "measured_path": <cache>, "seconds": …, "cached": …, "measure_only": true}`，
  **跳过 5 类模型导出**；
- 默认 `measure_only=False`，原有导出行为不变。

## 4. 上传任务查询

- `GET /api/entity/uploads?limit=50`：按创建时间倒序返回全部任务
  （`task_id / part / file / status / phase / progress / message / error / warning / tube_id / created_at`），
  以及 `count`、`active`、`parallel`、`uploads_dir`。
- `GET /api/entity/uploads/<task_id>`：单任务详情 + `logs[-50]` + `cache_path`（保持兼容）。

## 5. 前端

`frontend/src/views/EntityPipeline.vue`：

- 上传控件 `multiple`，一次选多个 `.stp/.step`；按钮显示已选数量；
- 「上传并测量」提交全部文件，提示「最多并行 N 个（可配置）」；
- 任务表新增「文件」列，显示 文件 / 状态 / 进度条 / 信息；活跃行高亮，失败行浅红；
- 上传/轮询完成后自动 `refreshParts()` 并刷新当前件；
- 摘要卡把「推荐工艺路线 + 依据（形线长度 / 外径变化率 / L4）」置于顶部主信息；
- 无设计时「三类抽管」区显示人工导入提示，不再显示 0 道空表。

`frontend/src/api/entity.js` 新增 `uploadParts(files, onProgress)`（字段 `files`）；
`uploadPart` 保留兼容。

## 6. 测试

```powershell
cd backend
& D:\python\envs\occ\Scripts\python.exe -m pytest tests\entity -q
```

覆盖点：

- `/run` 默认不触发 `design_gen.create_default_design`，显式 `ensure_design=true` 仍触发；
- 多文件上传（`files`）创建多个 task、并行执行、全部 done；
- 单文件旧字段 `file` 兼容；同名文件生成 `_2`；
- `/uploads` 列表与单任务详情；
- 无设计摘要标记 `design_missing` 与 drawing 提示；
- 上传测量统一 monkeypatch `prepare_proc.run_prepare`，不跑真实 OCP。

## 7. 接口表（§13）

| 方法 | 路径 | 关键入参 | 返回/说明 |
| --- | --- | --- | --- |
| POST | `/api/entity/run` | `parts[]`、`models[]`、`routes[]?`、`force?`、`ensure_design?`（默认 `false`） | `job_id`、`job`（含 `design_mode/design_note/designs`）；上传活跃时 409 |
| GET | `/api/entity/status` | — | 当前运行状态、活跃 job |
| POST | `/api/entity/stop` | `mode`: `graceful`/`force` | 停止当前流水线 |
| GET | `/api/entity/parts` | — | 管件列表（含上传进来的件） |
| GET | `/api/entity/parts/<part>/flow` | — | 单件视图：`route_plan`、`process_summary`、`design_state` |
| POST | `/api/entity/parts/<part>/design` | — | 手工补算设计（保留，不由流水线默认调用） |
| POST | `/api/entity/upload` | multipart `files`（可重复）/ `file` | `tasks[]`、`count`、`errors[]`、`parallel`；立即返回 |
| GET | `/api/entity/uploads` | `limit?` | 任务列表 + `active`/`parallel` |
| GET | `/api/entity/uploads/<task_id>` | — | 任务详情 + `logs` + `cache_path` |
| GET | `/api/entity/jobs` | `limit?` | 流水线任务列表 |
| GET | `/api/entity/jobs/<job_id>` | — | 任务详情/日志/进度 |
| GET | `/api/entity/download` | `path`、`name?` | 下载 out_root 内产物 |

## 8. 变更文件

| 文件 | 变更 |
| --- | --- |
| `backend/api/entity.py` | `ensure_design` 默认 false；`design_mode/design_note`；缺设计摘要标记；多文件上传、线程池、`measure_only` 测量、`/uploads` 并行信息 |
| `backend/core/entity/prepare_proc.py` | `run_prepare(measure_only=False)`，job json 透传 |
| `backend/core/entity/prepare_worker.py` | `measure_only` 时跳过模型导出，返回 `measured_path` |
| `frontend/src/views/EntityPipeline.vue` | 多选上传、任务表文件/状态/进度、摘要推荐路线主信息、无设计提示、移除补算按钮、`ensure_design:false` |
| `frontend/src/api/entity.js` | 新增 `uploadParts` |
| `backend/tests/entity/test_api_entity.py`、`conftest.py` | 多文件/并行/默认不补算/缺设计摘要测试 |
| `docs/ENTITY_PIPELINE.md` | 本文 |
