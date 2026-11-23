# Giant-Unified · 一体化流水线（V1）设计与契约

> 本文件是**唯一权威契约**。10 个子代理各自实现下面「模块契约」里属于自己的文件，
> 任何跨模块调用必须严格按签名来；实现细节自由，但输入输出结构不得改。

## 0. 定位与边界

- `Giant-Unified` = `Giant-main-new1` 的**完整副本**（后端 Flask + 前端 Vue + 许可证 + 工艺设计 + 数据库 + 工具），
  加上**新增的一体化流水线**（`backend/core/entity/`）。
- **不改动** `D:\AAAGNT-1\Giant-main-new1` 原仓库。所有改动只发生在本目录。
- 原程序端口：web 5880 / dev 5050 / vite 3000 / FreeCAD 桥 18765 —— **本程序全部换新端口**：
  web **5890**、dev flask **5060**、vite **3001**、FreeCAD 桥 **18766**。
- 反切模具（mold）**整类下线**：流水线只产出 5 类模型，不产出任何 `*_反切模具.stp`。

## 1. 输入

`D:\AAAGNT-1\Giant-Unified\gnt管件\<件>.stp`（原始管件的**副本**；内容与
`D:\AAAGNT-1\Giant-main-new1\gnt管件\` 逐字一致，见 `FILES`/sha256 核对）作为**唯一起点**。
不读任何历史形腔 / 历史全模型目录；`settings.SRC_PARTS_DIR` 指向本仓库内这份副本。

## 2. 目录布局（输出）

```
Giant-Unified/
  entity_out/                          # 默认输出根（可用 --out 覆盖）
    _cache/<件>_measured.json          # 测量缓存，c1 口径
    _cache/<件>_measured_v1.json       # 测量缓存，v1 口径（仅 REFERENCE_PARTS）
    _scratch/pipe_jobs/                # FreeCADCmd 任务描述与结果 json（job_<uuid8>.json）
    _report/summary.json|summary.md    # 全局汇总
    <件>/
      00_models/<slug>.stp             # 5 类模型（Giant 阶段产物，文件名用 slug：shaping_cavity /
                                       #   belling_cavity / belling_mandrel /
                                       #   flattening_model / bending_model）
      10_c1/<模型键>__c1.step          # 两条路的产物（10/20 两层用中文模型键，便于人眼对照）
      20_pipe/<模型键>__pipe.step
      20_pipe/_work/<slug>/            # FreeCADCmd 的 solid.step + report.json（中间件）
      report.json                      # 该件全部 RouteResult
      report.md
  freecad_addon/PipeSolidBridge/       # FreeCAD 端插件（端口 18766）
  frontend/                            # Vue 前端（新增 entity 页面）
```

## 3. 运行环境（不可合并）

| 角色 | 解释器 | 说明 |
|---|---|---|
| 主服务（Flask + c1 路） | `D:\python\envs\occ\Scripts\python.exe` | numpy 1.26.4 + OCP 7.7.2 + trimesh + Flask 3.1.3 |
| pipe 路几何 | `D:\Freecad\bin\freecadcmd.exe` | FreeCAD 1.1.4 自带 Python，只能用子进程调用 |
| 前端构建 | node + npm（`frontend/`） | `npm run build` → `frontend/dist` |

## 4. 阶段与数据流

```
gnt管件/<件>.stp
   │  S0 measure()            core.pp.tube.analyze_tube(algorithm="A", sparse_step_mm=25.0,
   │                          end_treatment={"shape_extrapolation": "c1"|"v1"})
   ▼
sections_full（1 mm 站位；含 position/outer_2d/inner_2d/basis_u/basis_v/perimeter）
   │  S1 export_models()      core.rfm.processors.get_processor(op).set_sections(...).generate_model()
   ▼
5 类模型 STEP  ──┬── S2a c1 路   → 10_c1/<键>__c1.step
                 └── S2b pipe 路 → 20_pipe/<键>__pipe.step（FreeCAD solidify 重蒙皮）
   │  S3 checks()             4 面 / 1 实体 / 2 分型边 / 0 自由边 / 体积偏差 / 包围盒偏差（只记录）
   ▼
S4 report()                   report.json|md + _report/summary.json|md
```

## 5. 模型注册表（5 类，键名即文件名）

| key | op | 中文 | **默认路线** | c1 路做法 | pipe 路做法 |
|---|---|---|---|---|---|
| `成型_外径形腔` | shaping | 外径形腔 | **c1** | `measured.json` → C1 外推；`REFERENCE_PARTS` 改走「口径形腔 → 保形」 | solidify 重蒙皮 |
| `扩口_扩口腔体` | belling | 扩口腔体 | **pipe** | STEP 自参考保形（逐截面左右极点） | 同上 |
| `扩口_扩口芯轴` | belling | 扩口芯轴 | **pipe** | 同上 | 同上 |
| `压扁_压扁模型` | flattening | 压扁模型 | **pipe** | 同上 | 同上 |
| `压弯_压弯模型` | bending | 压弯模型 | **pipe** | 同上 | 同上 |

- 默认分派由 `contracts.DEFAULT_ROUTE_BY_KEY` / `default_route(key)` 决定：外径形腔只走 c1，
  其余四类只走 pipe → 默认计划 = 6 件 × 5 类 × 1 路 = **30 格**。
- 显式 `--routes c1,pipe`（Web 端「两条都跑」）时每类都跑两路 → 60 格，用于对照/交叉验证。
- `PipelineContext.routes = None` 表示按模型默认；`normalize_routes(None|[]) → None`。

端部口径：默认 `c1`（形状外推）；`REFERENCE_PARTS = {"24TK_2052-2"}` 走 `v1`（线性外推），
且该件 5 类模型的 `_measured.json` 都用 v1（对齐 `run_20261103_新逻辑` 的做法）。

## 6. 两条路的算法定义

### 6.1 c1 路（`routes/c1.py`）

- **C1 外推**（`成型_外径形腔`）：读 `_measured.json` 的 `sections_full` → 只用 `measurement_status == "measured"`
  的站位作种子 → 2 mm 站位重采样 → 端部按局部趋势外推到全长 → 生成截面环 → `vendor/pipe_smoother`
  的 `geometry`/`assemble` 出「1 Solid / 4 面 / 2 条共享分型边 / 0 自由边」。
  沿用 c2 已验证参数：`HALF_SAMPLES = 65`、`smooth_passes=2`、`keep_all_extrapolated=False`。
- **口径保形**（`REFERENCE_PARTS` 的外径形腔，现仅 2052-2）：参考件就是**本件 v1 口径刚导出的形腔 STEP**
  （`spec.reference_step or spec.step`，与 `run_20261103_新逻辑/03_c2输出/references.json` 一致），
  逐截面找对称轴左右极点 → 保形重建 4 面；实现复用 `routes/selfref.py`，c1 路只做转调。
- **自参考保形**（其余 4 类）：把该件自己的模型 STEP 当参考件，逐截面左右极点 → 4 面（同样复用 `selfref.py`）。

### 6.2 pipe 路（`routes/pipe.py`）

- 调 FreeCAD 端 `PipeSolidBridge.solidify.process_file(step, out_dir, options)`：
  `closed 环排序 → 镜像评分定分型轴 → 黄金分割求 ±a → 周期 BSpline（U=0/+a, U=0.5/−a）
  → buildFromNSections → uIso(0.5) 切割 → 端盖 → makeShell/makeSolid`，
  自适应截面数 40/64/96/160（包围盒 tol=max(1, 0.0025×对角线)、体积偏差 ≤1%）。
- 调用方式：默认 **headless**（`freecadcmd.exe -c` + `PIPESOLID_JOB` 环境变量 + 结果 json），
  可选 GUI 桥（127.0.0.1:18766，QTimer 主线程执行）。
- `solidify.py` 里 `pick_symmetry_axis` 的自动轴替换为 `directions.resolve_for_category()` 的结果。

## 7. 分型方向规则（`directions.py`）

硬约束：**分型方向必须是对称方向**（镜像评分最小者，且评分必须在容差内），否则报错而不是硬选。

| 类别 | 规则 |
|---|---|
| 成型_外径形腔 | 三向镜像评分取最小（并列时 Z > Y > X） |
| 扩口_扩口腔体 / 扩口_扩口芯轴 | 任意（回转体），固定取「评分最小的轴，并列取 Y」 |
| 压扁_压扁模型 | **截面长轴**（逐截面长轴取平均方向，再投影到首截面） |
| 压弯_压弯模型 | **弯曲平面的镜像对称轴**（= 质心序列 PCA 得到的平面法向 n） |

> **压弯规则说明（2026-11-03 定，实现见 `directions.py:716-739` 的 `bend_axis`）**：
> 早期文档写「取首截面内与 n 垂直的方向」，实测对回转圆弧**天然退化**——真实压弯件第
> 488/760 个截面里，弯曲平面内的候选方向与截面法向几乎平行（sin≈0），投影长度 <0.15，
> 下游 `selfref` 的可用性门会直接判不可用；三路（c1/selfref/pipe）只能陆续退回 `auto`。
> 压弯件唯一的镜像对称轴就是 **n（弯曲平面法向）**：沿 n 劈开，两条分型线在弯曲平面两侧
> 对称铺开，与「压弯件分型线沿弯曲平面」的工艺要求一致。因此本流水线固定
> `d = n`（`rule="bend_plane"`，`direction_source` 记录来源），并保留
> `span_projection → plane_axis → in_plane_perp` 三个兜底候选，每个候选都过
> 「全截面投影 ≥ 0.15」校验；真要改回字面语义只需把 `("bend_axis", normal)` 挪到候选表末尾。

## 8. 验收（只记录，不判失败）

每件每个产物记录：`faces / edges / solids / free_edges / valid / volume_mm3 /
ref_volume_mm3 / volume_error_pct / bbox / bbox_dev_mm / direction / sections_used /
sections_total / seconds`。规范值：`faces == 4`、`solids == 1`、`free_edges == 0`、分型边 2 条、
`valid is True`。不合规只写 `problems`，**不中断流水线**。

**硬/软问题分级（2026-11-03 定，`checks.hard_issues()` / `checks.soft_issues()`）**：
- **硬问题**：面数 ≠ 4、实体数 ≠ 1、自由边 ≠ 0、`valid is False` → 该格 `status="error"`（产物本身不可用）。
- **软问题**：共享分型边 ≠ 2 条 → 只写 `problems`（并在 `--resume` 时打印「缓存产物有软问题（只记录）」），
  状态仍是 `ok`/`cached`。当前实例：无——2026-11-04 修正 `checks._parting_edges()` 后，端盖（平面面）上的边
  不再计入（短件的端盖圆弧也会超过「长度 > 0.6×对角线」阈值，但它们是端盖轮廓，不是纵向分型线），
  30 格全部 `parting_edges == 2`（`summary.json` 的 `conformance.parting_ne_2 = 0`）。

**`bbox_dev_mm` 统一口径（2026-11-03 定）**：一律取 **optimalBoundingBox**（精确最小包围盒，
`BRepBndLib.AddOptimal`）与源件最优盒的逐轴最大差；`BoundBox`（轴向对齐盒）口径的值只放进
`artifacts["bbox_dev_boundbox"]` 备查。原因：FreeCAD 的 `BoundBox` 对同一颗实体会给出 3.270 mm
的假偏差（同一 STEP 重新导入后只有 0.0032 mm），用它会把合格件判成超差；`geometry.build_solid`
内部的 `tol` 判定也受此影响（同一实体 4 个候选截面数全被判超差，白跑 8–9 s，结果仍取最小者，
不影响产物）。

## 8.1 金标准（历史已验值，回归用）

**C1 外推路（c1）**——`_measured.json` 直接外推出的 4 面件体积（mm³，容差 1 mm³）：

| 件 | 口径 | 成型_外径形腔 |
|---|---|---|
| 24TK_1324-1 | c1 | 1,881,257.533 |
| 24TK_1324-2 | c1 | 2,449,483.308 |
| 24TK_1324-31 | c1 | 690,851.228 |
| 24TK_1442-1 | c1 | 1,562,196.022 |
| 24TK_2052-2 | v1 | **不适用**（见下）——本流水线走「形腔 → 保形」，金标准 135,077.517 |
| H0A2Z3-DT | c1 | 1,108,104.392 |

> **2052-2 更正（2026-11-03 实测，S2）**：`135,019.050` 与 `135,077.517` 都是
> **「形腔 STEP → 保形 rebuild」** 的产物，不是 C1 外推值：
> - `外径形腔_c1\24TK_2052-2_外径形腔.stp`（120 面）→ 保形 → **135,019.050**
> - `外径形腔_v1\24TK_2052-2_外径形腔.stp`（121 面）→ 保形 → **135,077.517**（本轮采用）
>
> 真正的 raw C1 外推：v1 口径 measured → 131,288.262；c1 口径 measured → 132,103.554，
> 比正确件瘦约 2.2%，与 `c2/README §8` 的结论一致。故 2052-2 的外径形腔在本流水线里
> **必须走 `selfref`（金标准 135,077.517）**，`routes/c1.py` 不产出该件的外径形腔。

**pipe 路（FreeCAD 重蒙皮）**——对 Giant 形腔 STEP 的实测（历史）：

| 件 | 源 面/环 | 用处截面 | bbox 偏差 | 体积偏差 |
|---|---|---|---|---|
| 24TK_1324-1 | 139/138 | 36/138（Z） | 0.30 mm | −0.19% |
| 24TK_1324-31 | 89/88 | 31/89（Z） | 0.00 mm | +0.04% |
| 24TK_2052-2 | 121/120 | 33/128（Y） | 0.32 mm | −0.20% |
| 压扁_压扁模型 | 962/961 | 40/961（Y） | **17.69 mm**（已知算法局限） | −0.01% |
| 压弯_压弯模型 | 762/761 | 60/761（Z） | **14.55 mm**（已知算法局限） | +0.0005% |

> **两路保真度差异（2026-11-03 全量实测，24TK_1324-1）**：`bbox_dev_mm`（optimal 口径）
> ——成型_外径形腔 c1 **0.021** / pipe **0.026**；压扁 c1 **0.000** / pipe **19.013**；
> 压弯 c1 **0.0002** / pipe **14.671**。体积两路都 ≤0.33%。
> 即：**直管/扩口两类两路都准；压扁、压弯只有 c1（selfref 逐截面轮廓）保真**，
> pipe 路（FreeCAD 全局单分型轴 + NSections 抽稀 40 截面）会削掉薄凸起/端部游离段，
> 属算法局限而非实现 bug（`attempts` 里 4 个候选截面数都 ≥14.5 mm）。
> 需要「形准」时以 c1 路为准，pipe 路用于对照与交叉验证。

## 9. 模块契约（签名固定）

```python
# contracts.py —— 本文件所有类型
ModelDef(key, op, op_label, name, contour, slug, c1_kind)
ModelSpec(part, key, step, end_treatment, out_dir, measured_path?, reference_step?)
Measured(part, path, sections_full, analysis, end_treatment, seconds)
CheckRecord(faces, edges, solids, free_edges, valid, volume_mm3, bbox, parting_edges, extra)
RouteResult(part, key, route, status, step, stl, volume_mm3, ref_volume_mm3,
            volume_error_pct, faces, edges, solids, free_edges, parting_edges, valid,
            bbox, ref_bbox, bbox_dev_mm, direction, direction_label, rule,
            sections_used, sections_total, attempts, seconds, logs, error,
            artifacts, problems)  # .to_dict()；status ∈ ok/error/skipped/cached/failed(≡error)/stopped
PartReport(part, source_step, end_treatment, measured_path, results, started, finished, seconds, logs)
PipelineContext(src_dir, out_root, parts, routes=None, models, force, resume, options, log)
# routes=None  → 每个模型走 default_route(key)（外径形腔 c1、其余 pipe）
# routes=tuple → 每个模型都跑这些路线（如 ("c1","pipe") 双跑对照）
ROUTES = ("c1", "pipe")
DEFAULT_ROUTE_BY_KEY / default_route(key) / routes_for(keys=None) / normalize_routes(requested=None)

# attempts 形状（pipe 路）：成功轮 {sections, bbox_dev, volume}；异常轮 {max_sections, error}；
# max_sections 固定（非 auto）时该键缺失
```

```python
# giant_adapter.py
def measure(step_path=None, *legacy, part=None, end_treatment=None, cache_path=None,
            force=False, algorithm="A", log=None, src_step=None) -> MeasureResult
    # MeasureResult 既是 dict（analysis/meta/part/path/end_treatment/cached/seconds），
    # 也带 .sections_full/.analysis/.path 等属性；兼容 measure(part, src_step, end_treatment, cache_path)
def export_models(part, analysis, out_dir, models=None, *, log=None, naming="slug") -> dict[str, dict]
def step_paths(results) -> dict[str, str]                                  # {模型键: step}
def to_measured(result) -> contracts.Measured

# routes/c1.py
def run(spec: ModelSpec, ctx: PipelineContext) -> RouteResult
def available() -> bool

# routes/selfref.py
def run(spec: ModelSpec, ctx: PipelineContext) -> RouteResult
def sections_from_step(step_path, *, max_sections=None) -> list[dict]   # {points(N,3), centroid(3,), normal(3,), t, perimeter, index, side, up, axis}
def build_from_step(step_path, out_path, *, part="", key="", op="", direction=None, log=None,
                    category=None, half_samples=None, max_sections=None) -> dict
    # category 是 key 的兼容别名；direction 额外接受 "X"/"Y"/"Z"；返回字段是 §8 的超集
    # （另给 ref_volume_brep_mm3 / volume_error_vs_source_pct / warnings / check /
    #   parting_edges_measured / 分阶段 seconds）

# routes/pipe.py
def run(spec: ModelSpec, ctx: PipelineContext) -> RouteResult
def available() -> tuple[bool, str]                                        # (是否可用, freecadcmd 路径/原因)

# directions.py
def score_axes(rings) -> dict[str, float]                                  # {'X':..,'Y':..,'Z':..}
def resolve_for_category(category: str, rings, requested="auto") -> dict   # {'vector':[x,y,z],'label':'Z±','rule':'symmetry|major_axis|bend_plane|fixed'}
def per_section_axes(rings, vector) -> list[dict]                          # 自参考保形用：每截面 n/a/up

# checks.py
def inspect_step(step_path) -> CheckRecord
def safe_inspect_step(step_path) -> CheckRecord                            # 不抛
def merge_check(result: RouteResult, rec: CheckRecord, ref_volume=None, ref_bbox=None) -> RouteResult
compare = merge_check                                                      # 别名
def conformance_issues(obj) -> list[str]
def summarize(reports) -> dict

# report.py
def write_part_report(report=None, out_dir=None, **kwargs) -> dict          # 也接受 (out_dir, report) 反序
def write_summary(reports=None, out_root=None, *, tolerance_pct=1.0, **kwargs) -> dict
# 状态口径：ok / cached / skipped / error（failed 归 error）/ stopped；未知状态原样保留并计入 other

# orchestrator.py
def scan_parts(src_dir=None) -> list[str]
def build_plan(parts, models=None, routes=None) -> list[ModelSpec]          # 默认 30 行（每模型一条默认路线）
def run_part(spec: ModelSpec, ctx: PipelineContext) -> PartReport
def run(ctx: PipelineContext, on_progress=None) -> dict
# backend/run_entity.py —— argparse CLI：--src --out --parts --models --routes --force --resume/--no-resume --limit --dry-run
#   省略 --routes ⇒ 按模型默认（外径形腔 c1、其余 pipe，30 格）；显式 --routes c1,pipe ⇒ 60 格

# api/entity.py
entity_bp: Blueprint                     # /api/entity/status|parts|plan|run|stop|jobs|jobs/<id>|jobs/<id>/files|download
```

## 10. 子代理分工（文件所有权，互不重叠）

| # | 负责文件 | 说明 |
|---|---|---|
| 1 | `giant_adapter.py` | 测量 + 5 类导出（去 mold），复制 `tools/rfm_export_all/export_all_models.py` 的 `build_rows/perimeter_matched_axes/make_processor` |
| 2 | `routes/c1.py` | C1 外推（含内轮廓）；`c1_kind == selfref` / `REFERENCE_MODELS` 时转调 `selfref.run` |
| 3 | `routes/selfref.py` | STEP 自参考 4 面保形（`run` + `sections_from_step` + `build_from_step`），并适配 `vendor/pipe_smoother` |
| 4 | `routes/pipe.py` + `freecad_addon/` 适配 | FreeCAD headless 调用、结果归一化、18766 端口 |
| 5 | `directions.py` | 对称评分 + 4 条类别规则 |
| 6 | `checks.py` | 拓扑/体积/包围盒检查，只记录 |
| 7 | `orchestrator.py` + `backend/run_entity.py` | 扫描、计划、并发、断点续跑 |
| 8 | `api/entity.py` + `frontend/src/**`（entity 页面、路由、菜单） + `app.py` 注册 | Web 入口与页面 |
| 9 | `report.py` | 件级/全局报告 |
| 10 | `backend/tests/entity/**` + `run_unified_e2e.ps1` | 回归与端到端 |
| Claude | 只读评审 | 架构 + 代码审查 + 集成检查 |

## 10.1 RFM 四页内置实体化（`core/entity/rfm_bridge.py`）

「硬模成型」的成型/扩口/压扁/压弯四页（`frontend/src/views/rfm/*.vue` → `RfmShell.vue`）在点「生成」时，
`backend/api/rfm.py::_generate` 会在原模型生成完之后**同步**调用 `rfm_bridge.convert_shape()`，
把 shape 换成 4 面实体，再统一走 `step_bytes` / `mesh_from_shape` / `inspect_solid`：

```python
convert_shape(shape, operation, *, sections=None, kind='', keep_dir=None, log=None)
    -> {'shape': TopoDS_Shape, 'info': {...}, 'step': bytes}

# info 关键字段：applied / model_key / default_route / route / faces / edges / solids /
#   free_edges / parting_edges / valid / volume_before / volume_after / direction /
#   sections_used / sections_total / problems / fallback / seconds / error / reason
```

- 路线：`contracts.default_route(model_key)`；c1 且有 `sections_full` → `routes.c1.run`（写临时 measured.json），
  否则/失败 → `routes.pipe.run`，再失败 → `selfref.build_from_step`；都失败返回 `applied=False`，调用方保留原模型。
- 工作目录：`entity_out\_scratch\rfm_bridge\<uuid10>\`（源 STEP 懒导出，c1 路只写 measured.json）。
- 反切模具（`mold_info` 非空）不参与替换，`info = {'applied': False, 'reason': '反切模具不转 4 面实体'}`。
- 前端展示：绿色「已实体化…」或黄色「实体化未生效（保留原模型）…」；扩口两个 tab 各带一枚状态 tag。
- 已验证（2026-11-04，design 1 / tube 1）：成型 c1 → 4 面/1 实体/2 分型边/体积 1,108,104.392（+0.0013 s 级误差），
  扩口芯轴 pipe → 220,912.535，扩口腔体 pipe → 1,194,229.658，转换耗时 2.6–5.8 s/件（c1 约 30 s）。
  压扁/压弯此前被 Giant 原生成器的自检拦下，2026-11-04 修 `core/rfm/processors.py` 后已通过（见下）。
- **生成器侧修复（2026-11-04）**
  - 压扁 `FlatteningProcessor._axes`：工艺表轴长按 0.01 mm 取整，和实测周长天然有零点几毫米差；原实现只要超容差（0.5% 或 0.05 mm）就抛
    「椭圆轴长与前序周长不一致」。改为**按周长自动匹配**：`scale = perimeter / ellipse_perimeter(a, b)` 同比缩放长短轴（保长短比），
    偏差记录在 `self.axis_corrections`（含 station/before/after/scale）并写 INFO 日志；`scale ∉ [0.8, 1.25]` 才仍判参数错误。
    实测本批 786 站中 760 站需修正，缩放 0.987–1.040，压扁模型可正常生成（`inspect_solid` 返回 warning：0.487 mm² 薄片面）。
  - 压弯 `BendingProcessor.generate_model`：`arc` 与周长表 `s` 两套网格叠加会产生相距约 0.003 mm 的近重合站位，放样出零宽带，
    带面法线翻转 → `inspect_solid` 判「侧面接缝 180.0° 折角」拒绝。新增 `_thin_stations(grid, min_gap=0.2)` 在放样前抽稀
    （1263 站 → 636 站），接缝最大夹角 180.0° → 0.638°，`inspect_solid` 返回 ok；外形不变（只去掉重复截面）。
  - 回归：`entity_out` 6 件 × 压扁/压弯 × pipe = 12 格用新生成器重算，全部 4 面/1 实体/0 自由边/2 分型边/valid；
    30 格 `counts {total 30, cached 30, error 0}`、`conformance` 全 0、`over_threshold` 空。
  - 注意：这两处改的是与主程序共用的 `core/rfm/processors.py`（原程序 `Giant-main-new1` 未改；备份见 `_backup\Giant-Unified_20261104_172637`）。

## 10.2 单件流程页与下载命名（2026-11-04）

- 后端新增 `GET /api/entity/parts/<件>`（`api/entity.py::_part_flow`），返回：
  `part / source / end_treatment / reference / measured / defaults{measurement,c1,pipe,routes,route_labels} /
  models[5]{key,op,op_label,name,slug,default_route,route_label,params,original,outputs[]} / report / summary`。
  `outputs[]` 每项含 `route/route_label/default/status/faces/solids/parting_edges/free_edges/valid/volume_mm3/volume_error_pct/seconds`
  与 `download_name`；数值来自件级 `report.json` 的 `results`，缺报告时字段为 `null`。
- `GET /api/entity/parts/<件>` 的 `route_plan` 字段（「工艺路线（默认）」卡）：
  `line_length_mm / d_change_pct / max_diameter_mm / min_diameter_mm / recommended_label / reason /
  chosen / chosen_is_recommended / groups[{id,title,subtitle,items[{label,feasible,recommended,chosen}]}] / free_combo[]`；
  推荐值来自 `core.pp.optimization.recommend_pp_method(centerline_length, max_diameter, min_diameter, L4)`，
  长度与直径取自该件的测量缓存（`_profile_for`），`L4` 取自 `pp_design.params_json`。
- `GET /api/entity/parts/<件>` 另带两块上游信息：
  - `design`：来自 `backend/database/pp/pp_design.db` 的 `pp_design` 行（按 `source_file_name` 匹配，取 `id` 最大者），字段
    `id / display_name / vehicle_spec / tube_number / pp_method / source_file_name / fitness / created_at /
    scalars[30]{key,value} / extra{抽管方式,车种规格,管件号,型号,...} / segments{...} / stations /
    curves{s_mm,perimeter_mm,wall_mm,wall_modified_mm}`；
  - `route_steps[7]`：`source / measure / shaping / belling / flattening / bending / entity`，每项 `id/title/subtitle/done/detail`，
    `done` 依据源 STEP、测量缓存、`00_models/<slug>.stp` 与 `10_c1|20_pipe` 产物是否存在。
- 新增 `GET /api/entity/parts/<件>/profile`（`api/entity.py::_profile_for`）：解析测量缓存 `_cache/<件>_measured.json`，
  返回 `station_count / length_mm / bbox / wall{min,max,median,mean} / perimeter{min,max} / status_counts /
  centerline[[x,y,z]…] / series{s_mm,perimeter_mm,eq_diameter_mm,wall_mm,wall_min_mm,wall_max_mm,status}`，
  曲线抽样到 ~400 点；结果缓存到 `_cache/<件>_profile.json`（`mtime >= measured` 时直接复用）。
- `routes/c1.py` 里的「口径保形」参考件（`contracts.uses_reference_route`）在页面上的 `route_label` 标为「口径保形（c1 路线，参考件）」，
  `params.reference=True`，避免与普通 C1 外推混淆。
- `/download` 新增 `?name=` 覆盖，并按路径推导友好名（`_friendly_download_name`）：
  `10_c1/<键>__c1.step` → `<件>_<工序>_<模型>__c1.step`；`20_pipe/<键>__pipe.step` → `…__pipe.step`；
  `00_models/<slug>.stp` → `<件>_<工序>_<模型>.stp`；`report.*` → `<件>_报告.*`；其它保持原名。
- RFM 侧（`api/rfm.py`）：会话落 `part_label`（`pp_design.source_file_name` 去扩展名，回退 `display_name`/`design<id>`），
  `_download_name()` 生成 `件号_工序_模型__路线.step`；扩口主页主产物是**芯轴**（`kind=''` → 芯轴，`generate-cavity` → 型腔）；
  反切模具 → `件号_工序_反切模具.step`；`generate`/`generate-cavity` 响应带 `download_name`、`part_label`，
  `entity.download_name` 同步带出；`init*` 响应带 `part_label`。
- 前端：`RfmShell.vue` 下载用 `Content-Disposition`（RFC 5987）解析出的服务端文件名，回退到 `entity.download_name`，不再写死；
  `views/EntityPipeline.vue` 重写为单件流程页（选件 → 输入/口径 → 默认参数 → 5 类模型与产物 → 运行状态），运行按钮 = resume（只补缺）。

## 10.3 一键全流程：补算缺失的上游工艺设计（2026-11-04）

目标（用户选 A）：点一次「运行」时，如果该件还没登记工艺设计，先按推荐路线补算并写一条 `pp_design` 记录，
再生成 5 类派生模型、按默认路线实体化；已有产物仍按缓存只补缺。

- 新模块 `backend/core/entity/design_gen.py`：
  - 常量 `FEASIBLE_FITNESS = 0.74`、`ROUTE_ORDER`（7 个中文暴力枚举标签）。
  - `existing_design(part)`：只读 `pp_design.db`，按 `source_file_name`（`<件>.stp/.step`）取最新一行；**不写库**。
  - `build_analysis_input(part, out_root=None)`：从 `entity_out/_cache/<件>_measured.json`（找不到再试 `_measured_v1.json`）
    重建优化器输入：`wall_thickness_data`（逐站 `中心线长度(mm)/壁厚(mm)/外径周长(mm)`）、`segments`
    （`D1/D3/T1..T4/TL1..4/TLM1..3`，取自 `core.pp.wall_thickness_segments.analyze_uploaded_wall`）、
    `centerline_length`、`volume`（**管壁体积** ∫π(d·w−w²)ds，不是实体体积）、
    `L4`（`core.pp.formline_conclude.analyze_formline_from_data` 的直段长度）。
  - `create_default_design(part, out_root=None, *, method=None, force=False, log=None) -> dict`：
    已登记且非 force → `state='registered'`；缺测量 → `no_measured`；否则 `recommend_pp_method(...)` → 中文 label
    → `optimize_process_parameters_by_method(...)`（`d_precision=1, t_precision=1, l_precision=0, add3=0.0`）
    → 最优解 `fitness < 0.74` 判 `infeasible`（**不写库**，返回中文原因）；可行则用 `core.pp.modify_thick.apply_modify_thick`
    生成逐站「修改后壁厚」，`extra_params = {Taper段壁厚过渡: False, 原始segments, 自动补算: True, 壁厚数据}`，
    调 `core.pp.design_db.save_design(...)`（**返回 `(new_id, err)` 元组**）写库，返回 `state='created'` + `design_id`。
- API（`backend/api/entity.py`）：
  - `POST /api/entity/parts/<件>/design`（body `{method?, force?}`）→ 同上结果；件不存在 404。
  - `_part_flow` 增加 `design_state{state,design_id,route,fitness,message}`（`registered` / `missing`）。
  - `POST /api/entity/run` 支持 `ensure_design`（默认 `true`）；job 记录 `designs{件: {state,design_id,route,fitness,message}}`，
    在 `orchestrator.run` 之前逐件补算，失败只记日志、不阻断。
  - `_job_summary` 返回 `ensure_design` 与 `designs`，页面「运行状态」卡显示补算结果。
  - `orchestrator._write_summary` 改为合并**磁盘上所有件的 `report.json`**（本次运行优先），单件运行不再把
    `_report/summary.json` 缩成一件。
- 实测结论（需向用户说明）：
  - 可行并已写库：`24TK_1442-1`（`TP抽+扩口`，适应度 1.8159，设计 #4）。
  - 已登记（未动）：`24TK_1324-1`（#3，`仅TP抽`，1.9059）、`H0A2Z3-DT`（#2，`普通抽+Taper+扩口`）。
  - 默认参数下不可行：`24TK_1324-2`（推荐 `普通抽+扩口`，−999998.8）、`24TK_1324-31`（`普通抽+Taper+扩口`，−999997.8）、
    `24TK_2052-2`（`普通抽+Taper+扩口`，−999998.7）→ 不写库，页面提示去「工艺优化 → 工艺参数优化」调整。
    5 类模型生成不依赖设计，仍可正常出件。
- 测试：`backend/tests/entity/test_design_gen.py`（6 条：分析输入口径、已登记幂等短路、不可行不写库、
  可行解析 `(id, err)` 元组、缺测量）；快速组 **81 passed / 5 skipped**。

## 11. 运行方式

```powershell
# 默认全量（按模型分派路线：外径形腔 c1、扩口/压扁/压弯 pipe，6 件 × 5 类 = 30 格）
& D:\python\envs\occ\Scripts\python.exe backend\run_entity.py --src gnt管件 --out entity_out

# 双跑对照（每条路线都跑，60 格）
& D:\python\envs\occ\Scripts\python.exe backend\run_entity.py --src gnt管件 --out entity_out --routes c1,pipe

# 单件 / 单模型 / 单路
... --parts 24TK_1324-1 --models 成型_外径形腔 --routes pipe

# Web（默认/只走 c1/只走 pipe/两条都跑 四选一）
.\start_unified.ps1        # 5890 端口，前端 dist + /api/entity
```

## 12. 已知坑（务必遵守）

1. **OCP/FreeCAD 非线程安全**：所有几何调用必须串行（orchestrator 单 worker + 锁），
   pipe 路每次都是新起的 `freecadcmd` 子进程。
2. **`shape.Volume` 会把重叠带面重复计入**：体积基准一律用截面棱柱法
   `∫(A_i+A_{i+1})/2·d_i`，不要用源 `shape.Volume` 直接比。
3. **长中文路径 + 空格**：所有子进程参数用列表传，不要拼字符串 shell。
4. 测量一次 **1–58 min/件**（`24TK_1324-2` 最慢，约 3500 s；本机 2026-11-03 实测 `24TK_1324-1`
   单件 **324 s**），产出 60–154 MB JSON，**必须**复用 `_cache`（`--force` 才重测）。
   缓存命中约 **1.5 s**（瓶颈是 67 MB JSON 的 float 解析，环境里没有 orjson/ujson）。
5. FreeCAD 里的 numpy 与主进程 numpy 是两份，**不要**在父子进程间 pickle 数组。
6. 前端 `dist` 需要 `npm run build`；本程序构建产物在 `frontend/dist`，不要提交进 git。
   本机 esbuild 会因 `%TEMP%` 权限报 `Access is denied`，构建前必须把 `TEMP`/`TMP` 指到
   仓库内 `.tmp`（`start_unified.ps1 -Build` 已处理）。
7. **`bbox_dev_mm` 只看 optimalBoundingBox**（见 §8 注）：用 `BoundBox` 会得到 3.270 mm 的
   假偏差；`entity_out` 里历史产物若带 `bbox_dev_boundbox` 属正常。
8. **含中文的 `.ps1` 必须带 UTF-8 BOM**：Windows PowerShell 5.1 无 BOM 时按 ANSI(GBK) 解码，
   中文注释会吃掉字符串终止符，报「字符串缺少终止符」并连带「缺少右 }」。批量改脚本时
   一律用 `utf-8-sig` 读写；改完用 `[System.Management.Automation.Language.Parser]::ParseFile()`
   复核。`.bat` 里的中文同理（cmd 走 ANSI），要么保持 GBK，要么只写 ASCII。

## 13. HTTP 接口（2026-11-06）

全部挂在 `/api/entity` 前缀下；除上传是 `multipart/form-data` 外均为 JSON。写操作要登录
（`POST /api/auth/login`，body 用 `id` 不是 `username`），后续请求头带 `X-Auth-Token`。
错误一律 `{"success": false, "message": "..."}`，机器可读的状态码优先于文案。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/entity/parts` | 件列表；本轮新增 `source` / `builtin` / `uploaded` |
| GET | `/api/entity/parts/<件>` | 单件详情（含 design / route_steps / profile） |
| GET | `/api/entity/parts/<件>/flow` | 单件流程页数据；本轮新增 `route_pref*` / `c1_impl` / `process_summary` |
| GET/POST | `/api/entity/parts/<件>/route-pref` | 读 / 写「c1 vs 121 + 分型方向」偏好 |
| POST | `/api/entity/run` | 起流水线；上传测量未完成时 409（见 §13.2） |
| POST | `/api/entity/stop` | 两档停止：`graceful` / `force` |
| POST | `/api/entity/upload` | 上传 `.stp/.step`，后台串行测量入库 |
| GET | `/api/entity/uploads` | 上传任务列表 |
| GET | `/api/entity/uploads/<task_id>` | 单条上传任务（含末尾 50 行日志、缓存路径） |
| POST | `/api/entity/uploads/clear` | 清空已结束的上传任务记录（仅内存表；`{"keep_active": false}` 连进行中一起清） |
| GET | `/api/entity/import` | 主流程待跑队列 + 每件缓存是否就绪（见 §13.2.1） |
| POST | `/api/entity/import` | 把并行测量完的上传件一次登记进主流程队列（不跑几何） |
| GET | `/api/entity/jobs`、`/api/entity/jobs/<id>`、`/api/entity/jobs/<id>/files` | 流水线任务（原有） |

### 13.1 停止：graceful / force

```
POST /api/entity/stop
{"mode": "graceful"}          # 省略 mode 也按 graceful
```

两档语义完全不同：

- **graceful（默认）**：只置 `stop_event`，**不调** `terminate_all()`；当前任务 `pending/running` →
  `stopping`，**保留坑位**（`_job_running` 把 `pending/running/stopping` 都算占用，此时再 `/run`
  是 409）。测量/导出子进程每 0.2 s 轮询 `stop_event`，置位后自己 `terminate_tree` 并抛
  `PrepareCancelled`；已进入的路线单元在主进程里跑到单元结束，之后在编排器安全点（§4）收尾，
  未跑的单元记 `stopped`，产物尽力保留。
- **force**：先把任务置 `stopped` 并**立即释放坑位**，再调
  `core.entity.proc_control.terminate_all()` 终止 OCP / FreeCAD 子进程。为了补上「第一次扫描后
  仍有子进程刚注册」的窗口，`_terminate_all_procs()` 会**扫两次**（中间 `sleep 0.3`）并合并
  `killed` 列表；`proc_control` 不可用或调用异常时给 `note`，不因此报 500。

返回 `{success, ok, mode, job_id, status, killed?, note?, message}`。没有活动任务 → 409
`当前没有运行中的流水线任务`；`mode` 不在 `graceful/force` → 400
`未知停止模式：X（可选：graceful / force）`。

### 13.2 上传：只收 STEP，后台串行测量

```
POST /api/entity/upload        # multipart 字段名固定为 file
```

- 只收 `.stp` / `.step`，**后缀大小写不敏感**（`.STP` 可以，落盘时后缀统一成小写 `.stp`）；
  其余后缀 400 `只支持 .stp / .step 文件（收到：...）`；缺字段 / 空文件名 400
  `缺少上传文件（multipart 字段名 file）`；空文件也会拒绝。
- 落到 `gnt管件/uploads/`（`settings.SRC_PARTS_DIR/uploads`），件名 = 文件名 stem 经
  `_safe_name` 清洗；同名不覆盖，依次加 `_2` … `_999`。列表里顶层文件优先于 `uploads/` 同名件。
- 响应 `{success, ok, task_id, part, status, file, size, end_treatment, task, message}`，
  此时后台线程已启动，`status` 是 `queued` 或 `measuring`。端部口径按该件的路线偏好解析
  （显式 121 → `v1`，否则默认 `c1`），与 §13.3 同源。
- **串行**：`_upload_worker` 先等当前流水线任务结束，再抢 `_UPLOAD_MEASURE_LOCK` 调
  `giant_adapter.measure(...)` 写 `<out>/_cache/<件>_measured[_v1].json`，随后入库管件分析；
  入库失败记 `warning`，测量异常记 `error`，成功则 `status=done, progress=100`。OCP 不是线程安全的，
  所以测量同一时刻只允许一个。反向也拦：只要有任务处于 `queued/measuring/saving`，
  `POST /run` 直接 409 `...请等上传测量完成后再启动流水线（避免 OCP 并发）`。
- `GET /uploads` 返回 `{success, tasks[], count, uploads_dir}`，每条摘要字段：
  `task_id / part / file / status / phase / progress / message / created_at / updated_at /
  error / warning / tube_id / end_treatment / seconds`。`GET /uploads/<task_id>` 额外带
  `logs`（末尾 50 行）与 `cache_path`；不存在 → 404 `上传任务不存在：...`。
- **上传任务表只存在内存里**（`_UPLOAD_TASKS`，上限 50 条，超出按 `created_at` 淘汰已结束的）：
  进程重启即清空，不写数据库、也不落盘，所以它只是进度/历史显示，不是数据资产。
  要手动清：`POST /api/entity/uploads/clear`（默认保留 queued/measuring/saving 的任务）→
  `{success, removed, removed_ids, remaining, active, message}`；页面「新增管件」卡片的
  「清空记录」按钮走这个接口。清理只动内存表，`uploads/` 的 STEP、测量缓存、导入队列与
  `tube_analysis` 一律不动。
- **入库时要补齐逐站记录**：测量只产出 `sections_full`，而管件分析页、关键截面校验、
  Tab2/Tab3 读的是老字段 `wall_thickness_data` / `centerline_perimeter_data`。
  `_upload_db_payload` 因此调 `core.pp.section_records.ensure_results_records` 把它们
  从 `sections_full` 重建，并补 `centerline_length`（`center_line_length` 那一列名字不同，
  漏了会让列表显示 `L 0.0 mm`）。缺这一步，这条记录在「数据库加载」后会直接判
  `QA_INVALID_INPUT（empty_profile）`；`/api/pp/tubes/load` 与 `pp_qa._sized_records`
  也做了同样的兜底，历史记录不需要重新测量。

### 13.2.1 导入主流程：写「管件模型分析」数据库（origin=entity）+ 登记队列

上传区的测量是并行的（`GTAPP_UPLOAD_PARALLEL`，缺省按核数自动分配，见 §19），但流水线是**单任务串行**的：
一个 `POST /run` 只认一个 `parts` 数组，跑起来之后上传区反而要等它结束。所以「上传 5 件」
过去只能自动跑第 1 件，其余要逐件切换、逐次起任务。新增的导入接口把这一步省掉：

```
POST /api/entity/import
{"tasks": ["up_ab12cd34", ...]}     # 按上传任务 id
{"parts": ["24TK_1324-1", ...]}     # 按件名
{"source": "upload"}                # 所有 upload 来源的件
{"replace": true}                   # 可选：覆盖原队列（默认按件名合并）
{"write_db": false}                 # 可选：本次不写 tube_analysis（默认写）
```

- **两件事**（2026-11-21 与用户确认）：①把这些件写进「管件模型分析」的 `tube_analysis`
  （`_db_save_measured` → `save_full_analysis(..., origin='entity')`，入库前补老字段，见 §13.2）
  ——流水线测完的件要能在「管件模型分析 → 数据库加载」里直接打开；②登记主流程队列
  `entity_out/_config/import_queue.json`（原子写，结构 `{version, updated_at,
  parts:[{part, step, source, end_treatment, cache_path, cache_mb, tube_id, imported_at}]}`）。
  不启动任务、不碰几何。
- **「工艺优化」列表看不到这一类**：`tube_analysis` 新增 `origin` 列（`'pp'` = 在「管件模型分析」
  页自己保存的分析、`'entity'` = 流水线导入）；`GET /api/pp/tubes?origin=pp|entity` 过滤，
  「工艺优化 → 从数据库加载分析结果」固定带 `origin=pp`，「管件模型分析」的历史列表不带
  参数（全部），导入的行在列表里带一个「流水线」小标签。旧库由 `check_and_upgrade_database`
  自动 `ALTER TABLE` 补列，历史行默认 `'pp'`。
- **按件号（display_name）幂等**：库里已有同名记录 → `db_status="exists"`、不插入、返回已有
  `tube_id`；历史重复行顺手删掉、只留最新一条（`duplicates_removed` 报出删掉的 id）。
- **上游不上传即入库**：`_upload_worker` 只测量+体检缓存，`tube_id` 恒为 `None`，写库只发生在导入时。
- **就绪判定**：文件存在 + 大小 > 0 + 头部 64KB 内含 `"sections_full"`。只读头部，
  不解析 60–154MB 的 JSON —— 逐件解析会把页面卡死。没测量完的件不入库，进 `skipped`
  并给中文 `reason`（`尚未测量…` / `测量缓存不完整…` / `未知管件…`）。
- 响应 `{success, write_db, imported[], created[], existing[], duplicates_removed[],
  skipped[], imported_parts[], queue:{count, parts, file}, message}`；`imported[]` 每条带
  `db_status`（`imported` / `exists` / `queue_only`）。`GET /import` 回读队列并**重新体检**
  （`exists` / `ready` / `in_db` / `tube_id` / `cache_mb` / `reason`）。
- 页面行为（`EntityPipeline.vue`）：上传卡片有「导入主流程（N）」按钮，导入后这些件被
  自动勾进头部多选框，按钮变成「运行（N 件批量）」，一次 `POST /run` 把 N 件排进同一任务。
  因为 `force=false`，每件直接命中测量缓存（约 1.5 s/件），**不再串行重测**。
- **同名上传 = 覆盖**（同一轮确认）：`POST /upload` 落盘前先比对既有 `{件名}.stp/.step` 的
  sha256——内容相同 → `unchanged=true`、复用原文件与缓存（`_upload_worker` 直接 done）；
  内容不同 → 覆盖同名文件（件名保持不变）、删另一后缀旧件、`_clear_measured_cache` 清掉
  c1/v1/profile 缓存后重测。不再产生 `_2…_999` 副本；上传件与内置件同名时**上传件优先**
  （`_scan_parts` 按件名去重）。
- 边界：并行只对**测量**有效。5 类导出与 c1/pipe 仍必须串行（OCP/FreeCAD 非线程安全），
  所以 N 件批量是「同一任务里逐件跑」，不是同时起 N 个进程。上传仍占用 OCP 时
  `POST /run` 照旧 409（§13.2）。
- 清理历史副本：`tools/clean_duplicate_uploads.py`（同目录 `--root <安装目录> --apply`）
  把 `_N` 副本移进 `uploads/_removed_duplicates/<时间戳>/`、删对应缓存、清队列条目，
  分析数据库一条不动；`--dedupe-builtin-copies` 额外清掉与内置件逐字节相同的上传副本。

### 13.3 路线偏好：c1 / 121 与分型方向

```
GET  /api/entity/parts/<件>/route-pref?model=成型_外径形腔
POST /api/entity/parts/<件>/route-pref
{"model": "成型_外径形腔", "impl": "121", "direction": "Y"}
```

- `impl ∈ {c1, 121}`：`c1` = C1 外推（无参考件），`121` = 参考保形（参考件 = 本件刚导出的
  形腔 STEP）。`direction ∈ {auto, X, Y, Z}`，大小写不敏感（`x` 归一成 `X`）。
- 校验：未知 `model` → 400 `未知模型：...`；非法 `impl` → 400 `非法实现：...（可选：c1 / 121）`；
  非法方向 → 400 `非法方向：...（可选：auto / X / Y / Z）`；`impl` 与 `direction` 都不给 → 400
  `至少需要 impl 或 direction 之一`。`model` 省略时按 `成型_外径形腔`。
- **持久化**在 `entity_out/_config/route_prefs.json`（原子写；测试/换 `OUT_ROOT` 时跟着走）。
  结构示例如下，`parts` 下按件存，`models` 才是逐模型的 `impl/direction`：

```json
{"version": 1,
 "parts": {"H0A3Z2-DT": {"direction": "auto",
                          "models": {"成型_外径形腔": {"impl": "121", "direction": "Y"}}}}}
```

- 优先级：`POST /run` 请求体里的 `route_prefs` > 磁盘 `route_prefs.json` > `contracts` 默认
  （参考件 `24TK_2052-2` 的 `成型_外径形腔` 默认就是 121 → `v1`，其余默认 c1）。件级
  `end_treatment` 由同件**任一**模型解析结果决定：任一 121 → 整件 `v1`；任一显式 c1 → `c1`；
  否则回落到 `end_treatment_for`。返回体里 `effective` 给最终 `impl` 与 `end_treatment`，
  `models` 给 5 类的全量视图，POST 还附带重算的 `route_plan`（失败给 `plan_error`，不 500）。

### 13.4 单件流程页新增字段

`GET /parts/<件>/flow` 在原有 `route_plan` / `profile` 之外新增：

- `route_pref`：磁盘里该件的原始偏好；`route_pref_effective`：
  `{model, impl, direction, end_treatment, impls, directions}`，即**实际生效**的一套。
- `c1_impl`：`121保形` / `自参考保形` / `C1外推`（见 §15）。
- `process_summary`：工艺摘要 dict；失败或缓存缺失时为 `null` 或带 `error` 的 dict，另给
  `process_summary_error`（摘要模块自身抛错的兜底文案）。**无缓存时 `route_plan` 不再造假推荐**：
  `recommended_label == ""`，`reason` = `该件尚未测量（或测量缓存缺失），暂不推荐工艺路线`。
- `/parts` 每项新增 `source`（`builtin` / `upload`）、`builtin`、`uploaded`；扫描顺序是
  `gnt管件/` 顶层（内置）→ `uploads/` 递归（上传），同名 stem 顶层优先。

## 14. 工艺摘要（2026-11-06）

`core/entity/process_summary.py` 把「实测 + 设计 + 路线」压成一份可读摘要，供单件流程页、
`report.json` 与 `report.md` 共用：

```python
build_process_summary(*, part, measured_path, design=None, route_plan=None,
                      out_root=None, max_points=80) -> dict
```

它**绝不抛异常**：任何一步失败都写进 `summary["error"]`，其余字段给保守空值，调用方不用 try。
实测缓存按 `<out>/_cache/<件>_measured.json` → `_measured_v1.json`（端部口径非 c1 时）取；
都读不到时 `error` 形如 `测量缓存不存在或不可读：<候选1>、<候选2>、<候选3>`。

**端部口径互换不必重测。** c1 与 v1 共用同一批实测截面、同一条站点网格，差异只在
`end_treatment.complete_ends` 的 `shape_extrapolation` 分支。`giant_adapter.measure()`
在目标口径缓存缺失、而另一口径缓存存在时，会先调 `derive_analysis()` 只重算两端（约 10–20 s），
写出的 `_measured_v1.json` 与独立测出的缓存**逐站一致**：H0A3Z2-DT 633 站（608 measured /
24 extrapolated / 1 needs_review）对拍，`s`、周长、壁厚、轮廓、坐标全部 0 差异，
`contour_policy` 与 `reference_length_mm` 也一致；24TK_1324-1 派生 761 站耗时 22.3 s，
同件真测 324 s 起。派生结果在 `analysis_quality.end_treatment.derived_from` 记来源口径，
下游据此可分辨「派生」与「首测」。`force` 只作废**目标口径已有**的缓存：目标文件本来不存在时，
即使点「重新测量 v1」也走派生（要真正独立复测，先删掉对应缓存文件）。

输出字段：

| 字段 | 内容 |
| --- | --- |
| `centerline` | `length_mm / points[] / point_count / method`；method 固定为「中心线 = 各截面外轮廓面积形心」 |
| `wall` | `min_mm / max_mm / mean_mm / median_mm / count / method / segments[]`；每段 `{start_mm, end_mm, thickness_mm}`，相邻壁厚相对变化 >5% 切段，最多 40 段 |
| `diameter` | `max_mm / min_mm / equivalent_max_mm`；等效直径优先用 `equivalent_diameter`，否则周长/π |
| `sections` | `count / step_mm` |
| `design` | `id / display_name / pp_method / fitness / updated_at / params / steps` |
| `drawing` | `normal / tp / taper` 三段，各 `{label, passes[]}`；`passes` 项 `{no, d_in, d_out, t_in, t_out, length}` |
| `route` | `recommended / reason / fitness / options[]`；由 `recommend_pp_method(长度, 最大径, 最小径, L4)` 给出 |
| `sources` | `measured_path / design_id` |
| `part` / `generated_at` | 件号与生成时间 |

- **不造数**：抽管道次只从设计参数读（`params` / `params_json` / `scalars` 三种都认），缺哪个
  字段留 `None`；三类抽管都没推导出道次时 `drawing.note` 写明「设计记录中没有对应参数…」。
- 缺长度/直径时 `route.reason` = `缺少实测数据（中心线长度 / 外径），未做路线推荐`，不硬凑标签。
- 报告集成：`report.py` 把整份 dict 写进 `report.json` 的 `process_summary` 字段，并在
  `report.md` 末尾追加 `## 工艺摘要`（`process_summary_markdown`）；编排器生成摘要失败只
  `log` warn，**不让整条流水线失败**。

## 15. c1 分型：对称轴极点、121 与手动方向（2026-11-06）

c1 路的分型缝必须落在**对称轴**上。实现上不靠「猜一条线」，而是把分型方向投影进每个截面平面
当 a 轴，缝落在该方向的 **±a 极点**（`prepare_sections(half_samples=...)` 先按 ±side 取极点、
再按弧长重采）。产物里的 `seam_anchor` 标出这次到底落哪了：

- `symmetry_poles`：缝已对齐到对称轴极点（日志文案「对称轴极点」）；
- `raw`：仍用实测点序（「实测点序，未落在对称轴极点」），几何不掰。

`direction` 产物字段带 `label / pair / rule / vector / source / conflict / notes / seam_anchor /
prepared`，报告与流程页直接引用。三种来源：

1. **auto（默认）**：先判断「平面弯」——对截面质心做 PCA，`s1/s0 > 1e-4` 且 `s2/s0 ≤ 5e-3`
   时取**弯曲平面法向 n**（`directions.planar_bend_normal`），`rule="bend_plane"`，跳过量纲硬约束；
   直管（`s1/s0 ≤ 1e-4`）或空间弯（`s2/s0 > 5e-3`）回退 §7 的规则方向（成型类三向镜像评分取最小、
   并列 Z > Y > X；扩口取最小、并列 Y；压扁取截面长轴；压弯取弯曲平面法向 `n`）。成型类三向评分
   都超过容差会报错；规则方向与实测帧冲突时**回落实测帧**（几何不掰），冲突写进 `notes`。
2. **手动 X / Y / Z 或角度**：优先于 auto，直接重排每个截面的 a 轴，把缝挪到该方向的 ±极点
   （与 selfref 同一套 `_apply_direction`）。手动方向是人工判断，`rule='fixed'`（`angle:` 记
   `selfref/manual`），note 会写「人工指定方向，未做对称自检」；覆盖动作记在
   `artifacts.direction_override`。也接受 `-Y`、`[x,y,z]` 这类写法；**`angle:<deg>` 表示绕 +Z、
   从 +X 逆时针的角度**（−27 约等于本案弯平面法向），流程页「方向 → 手动角度（绕 Z）」+ 角度
   输入框即是这条路径，持久化时规范为 `angle:%g`。判据不是「缝到弯曲平面距离≈0」（缝在 ±n 极点，
   距平面约等于半径），而是**缝点是否为截面沿 ±n 的支撑点**（`seam_u / ring_umax ≈ 1`）。
3. **121 = 参考保形**：不再做 C1 外推，改走 `routes/selfref.py` 的保形重建，参考件就是**本件刚
   导出的形腔 STEP**（`spec.reference_step or spec.step`）。因为 121 要 v1 口径的测量缓存，
   解析成 121 会把整件的 `end_treatment` 抬成 `v1`；这是 §13.3 里 `impl=121` 会连带
   `effective.end_treatment == "v1"` 的原因。参考件 `24TK_2052-2` 的 `成型_外径形腔` 默认走 121。

`/flow` 的 `c1_impl` 直接由 `orchestrator.c1_impl()` 给出：参考路线（含显式 121）→ `121保形`，
模型定义 `c1_kind == "selfref"` → `自参考保形`，否则 `C1外推`。偏好里写 `c1` 会显式压掉
参考路线（该件改走 C1 外推、端部口径回 `c1`），写 `121` 则强制保形——两者都以
`_config/route_prefs.json` 为准，命令行/接口不需要另传参数。

## 16. 2026-11-06 追加：只输出推荐路线 + 多文件并行上传

本轮改造后，流水线默认**不再自动补算工艺设计/道次**（/run 的 ensure_design 默认 false），只输出推荐工艺路线；人工判断参数后再导入数据库即可生成正式路线。上传接口支持一次多文件（iles 字段）并默认 2 路并行测量（GTAPP_UPLOAD_PARALLEL，1..8），单文件失败不拖累其它。接口字段、状态机与 measure_only 细节见 docs/ENTITY_PIPELINE_补充_2026-11-06.md。

> 注：2026-11-11 起「2 路」不再是硬默认——站内并行上线后改为按核数自动分配（见 §19），本节的「默认 2」仅保留为历史记录。

## 17. 2026-11-08：页面状态保留、隐藏 CMD、分析后自动生成

- 前端 `AppLayout.vue` 用 `<keep-alive :max="12">` 包住 `<router-view>`：切换菜单不再销毁页面，上传/分析/优化的进度、结果与轮询得以保留（最多缓存 12 个页面）。
- `vendor/pss/headless.py` 新增 `_hidden_window_kwargs()`：Windows 下 Popen / probe 传 `STARTUPINFO`（`STARTF_USESHOWWINDOW` + `SW_HIDE`）与 `CREATE_NO_WINDOW`，硬模成型/一体化调 `freecadcmd.exe` 时不再弹 CMD 窗口；非 Windows 返回空 dict。
- `EntityPipeline.vue` 的 `loadFlow()` 末尾调用 `maybeAutoGenerate()`：当前件已有测量结果（`process_summary` 且无 error）、5 类模型都没有可用产物、且没有运行中任务时，自动 `startRun()` 一次（按件去重，失败不记，等上传测量结束后再由 `loadFlow` 重试）。上传完成后的轮询同样会走到这里，因此「上传分析 → 一体化自动生成 5 类实体」不需要再手点运行。

## 18. 2026-11-08：成型页 121 = v1 测量口径 + 121 保形

硬模成型页的 `121` 与流水线的 121 对齐：**先确保该件有 v1 测量缓存，再用本件 v1 口径导出的外径形腔 STEP 做保形重建**。

- 设计记录 → 原始管件：`backend/api/rfm.py::_v1_source()` 读 `pp_design.source_file_name/source_file_path`，件号取文件名 stem。v1 缓存路径由 `settings.measured_path(part, "v1")` 给出（`<entity_out>/_cache/<件>_measured_v1.json`）。
- 后台测量：`POST /api/rfm/v1/prepare {session_id, force}` → `_start_v1()`；缓存存在且非 force 直接返回 `status="cached"`；否则建 `v1_<12hex>` 任务，线程里调 `prepare_proc.run_prepare(..., end_treatment="v1", measure_only=True, timeout=7200)`，不占用 HTTP 连接。`GET /api/rfm/v1/status/<task_id>` 返回进度（从测量日志的 `a/b` 或百分比解析）与 `measured_path`；`POST /api/rfm/v1/cancel/<task_id>` 置停止事件，状态 `cancelling → cancelled`。同一时间只允许一个 v1 测量。
- 生成：`POST /api/rfm/generate {entity_route:"121"}` 在成型工序走 `_v1_121_shape()`：`giant_adapter.export_models(part, cache, tmp, models=["成型_外径形腔"], naming="key")` → `rfm_bridge._build_selfref()`（自动弯曲平面法向）→ `checks.read_step()` 替换预览/下载；`entity.caliber="v1"`、`measured_path` 写进返回。
- 前端 `RfmShell.vue`：选 121 时 `handleGenerate()` 先 `ensureV1(sid)`；缺缓存则进度条 + 取消，测完自动继续生成；有缓存提示复用。路线行另有「重新测量 v1」按钮（`force=true`，需已有一次生成的 session）。扩口/压扁/压弯的 121 仍按当前工序外形，不触发 v1 测量。
- 实测（H0A3Z2-DT，design 1）：v1 测量 101,315,016 B / 约 17 min；页面 121 产出 4 面 / 1 实体 / 2 分型边 / 0 自由边 / valid，体积 1,024,836.221 mm³，分型方向 (0.8918, −0.4525, 0)，与流水线 v1+121 完全一致；约 58 s（含 c1 预处理）。取消路径实测 `cancelling → cancelled`，无残留 worker。

## 19. 2026-11-11：测量提速（站位并行 + 粗段偏转 + OCCT 并行布尔）

测量是整条流水线最贵的一步（单件 1–58 min），但它的成本结构是「约 1391 次截面调用占 98%」：读 STEP、拟合、端部处理、统计、写缓存合计只有几秒。把站位切面并行起来，总时长几乎线性下降。

- **站位并行**（新增 `backend/core/pp/tube/section_pool.py`）：每个站位只依赖 `(shape, origin, normal)`，不读写共享状态，结果按索引回填，所以并行不改变任何读数。`analyze_brep_tube()` 在整次测量期间复用**同一个** `ProcessPoolExecutor`（Windows 无 fork：每 worker 重 import OCP 约 3.7 s + 读一次 STEP，用 `initializer` 只付一次）。进程数 `GTAPP_MEASURE_WORKERS`，默认 = **min(物理核数, 8, 可用提交内存允许数)**：物理核用 `GetLogicalProcessorInformationEx` 探测（上限 32），内存按每进程 `WORKER_MEMORY_MB=900`、预留 `MEMORY_RESERVE_MB=2560` 估算（`GlobalMemoryStatusEx().ullAvailPageFile`）。本机 8C16T / 13 GB 可用 → 8；16 GB 笔记本可用 4–6 GB 时会自动压到 1–4，避免子进程在 import OCP + 读 GEOM 阶段整批死掉（否则「成功 0 站 / 失败 3 次」判池坏、退回串行）。不用逻辑核是因为超线程的两个线程抢同一浮点/内存端口——实测 16 进程 69.4 ms/站反而慢于 8 进程 55.7 ms/站，这也是 8 这个上限的来历；要更多得显式设 `GTAPP_MEASURE_WORKERS`。`GTAPP_MEASURE_PARALLEL=0` 整体回退串行（排查用）。每个 worker 各持一份 shape（约几百 MB），工作站上先看余量再决定是否手动压一档。单站失败（子进程崩溃 / OCC 异常）在父进程内串行补算，连续 3 次 0 成功则整批回退串行——不会让某站静默变空。
- **五个可并行阶段**：S1 种子 151、S2–S4 校正各 151、S5 精测 750，共 1354 次调用；阶段之间的 `_fit` 是 barrier。端部探测（0.25 mm 步进、「第一个无效就停」）改成**投机并行**：每次按 64 个候选一次发出，逐个判 `valid`，遇无效即停，语义不变（原 37 次串行 → 1–2 批）。
- **粗段偏转**：`details=False` 的定位/校正阶段偏转从 0.003 放宽到 `GTAPP_COARSE_DEFLECTION`（默认 **0.01**）；这些读数只用来算形心，不进最终结果。**精测偏转固定 0.003 不动**——实测 3 µm→8 µm 会让壁厚中位数从 2.0707 掉到 1.9886（−4%），属于读数级错误。
- **OCCT 并行布尔**：`brep_sections._RUN_PARALLEL`（环境变量 `GTAPP_OCCT_PARALLEL`，默认开）打开 `BRepAlgoAPI_Section.SetRunParallel`，布尔阶段 228 ms → 107 ms；逐站面积/周长/点数/壁厚与关闭时逐位一致。

实测（`24TK_1324-1.stp`，761 站，A 算法，c1 口径，16 逻辑核）：

| 配置 | 耗时 | 相对 |
| --- | --- | --- |
| 串行 + BOP 关（历史口径，粗段 0.003） | 392.6 s | 1.00× |
| 串行 + BOP 关（粗段 0.01） | 263.7 s | 1.49× |
| 8 进程 × BOP 开（粗段 0.01） | **47.9 s** | **8.2×** |

- **并行 vs 同代码串行**（逐站对拍）：站数 761 对 761、中心线 757.272662 对 757.272662、体积 260054.005 对 260054.005 mm³；`cumulative_length / perimeter / wall_thickness / position` 最大偏差均为 **0.000000000**，状态不一致 0 站 → 并行是逐位等价，不是「近似」。
- **相对历史串行基线（粗段 0.003）**：站数、体积完全相同；中心线 757.267961 → 757.272662（+0.0047 mm）；长度坐标原点平移 0.009 mm；按平移对齐后周长插值残差 max 0.020 mm（mean 0.0008 mm）、壁厚 max 0.00018 mm → 属于微米级口径漂移，被用户接受。
- 回归：`ENTITY_SKIP_SLOW=1 pytest backend/tests/entity -q` → 93 passed / 10 skipped / 76 subtests，11.4 s。

已知代价与坑：

- 8 worker 各自持一份 OCCT shape，每个 worker 都要 import OCP + 读一次 STEP（4–5 s），所以池必须整次复用，别按阶段反复建。
- 进程数 × OCCT 内部线程池会超订：8 进程开 BOP / 4 进程开 BOP / 8 进程关 BOP 实测 55.7 / 56.1 / 58.7 ms/站，12–16 进程反而变慢（61.8 / 69.4 ms/站）；默认 8 进程是拐点。
- 上传区的并行度与站内并行是**两个旋钮**，两者相乘应约等于物理核数：`_upload_parallel()` 缺省取 `max(1, 物理核数 // min(8, 单件进程数))`（本机 8 核 → 1 件，16 核 → 2 件，32 核 → 4 件），也可以用 `GTAPP_UPLOAD_PARALLEL` 直接钉死（夹在 1..8）。`GET /api/entity/status` 会回 `upload_parallel`，页面就显示这个生效值。
- spawn 子进程会重新 import 入口模块：`prepare_worker.py` / `run_entity.py` 模块级是干净的；`.tmp/serve_repo.py` 已把 `create_app()` 移进 `__main__`，否则每个 worker 都会重建一次 Flask app（含 SQLite 迁移）。
- 进度回调：并行时按「完成数/n」推进，S1 5–25%、S2–S4 25–55%、端部 55%、S5 56–98%，与串行文案一致（页面上看到的仍是 `精确截面测量 a/b`）。

