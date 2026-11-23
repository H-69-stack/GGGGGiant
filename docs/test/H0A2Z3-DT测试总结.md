# H0A2Z3-DT.stp 管件分析测试总结

- **测试日期**：2026-08-25
- **测试对象**：`H0A2Z3-DT.stp`（毫米制 STEP B-rep，单实体）
- **源文件**：`D:\桌面\Funio\测试管件stp\H0A2Z3-DT.stp`（大小 2,638,928 字节，mtime 2026-05-20）
- **插件**：cad-tube-centerline-thickness（CAD 管件形线与壁厚）
- **技能版本**：0.2.1 ｜ 引擎版本：0.2.1 ｜ 契约版本：2.0.0
- **运行时**：win-amd64-cp312-v1（Python 3.12.13，已就绪，无需 bootstrap）
- **最终结论**：**FAIL（fail-closed）**，未生成 PASS 工程产物
- API：deepseek v4 Flash
- 总结：末端5mm左右没有识别到内外壁，分析终止
---

## 1. 测试目标

对 STEP 管件执行名义几何分析，预期产出：

| 产物 | 说明 |
|---|---|
| `shape-line.csv` | 中心形线 C(s)、单位切向、rotation-minimizing frame（RMF） |
| `axial-thickness.csv` | 沿弧长的壁厚统计线 + OCCT 精确外环周长 `outer_perimeter_mm` |
| `thickness-field.csv` | 完整周向壁厚场 t(s, θ) |
| `acceptance.json` | 独立验收结果（唯一 PASS 权威） |
| `result-manifest.json` | 输入 lineage、版本、坐标系、哈希、限制 |
| `<job_id>-result.zip` | 上述产物的确定性归档 |

> 注：结果描述的是 **CAD 名义壁厚**，不等于制造后实测壁厚。

---

## 2. 使用的 API

### 2.1 插件正式 API（MCP 工具）

| 工具 | 参数 | 结果 |
|---|---|---|
| `cad-tube-centerline-thickness_runtime_info` | `{}` | 运行时已就绪：win-amd64-cp312-v1 |
| `cad-tube-centerline-thickness_analyze` | `{source: {kind: "session-file", fileId: "sf_8d2ca44150713dcd"}, job_id: "H0A2Z3-DT"}` | **ERROR** `SECTION_FAILURE` |
| `cad-tube-centerline-thickness_analyze` | 同上，`action: "inspect"` | 预检 **READY**（端点弦长 632.483506 mm） |

### 2.2 诊断用内部 API（Python 引擎，仅本次排障使用）

运行于插件私有 venv：

```
C:\Users\linglu\.fuinno-agent\plugin-data\cad-tube-centerline-thickness\runtime\win-amd64-cp312-v1\venv\Scripts\python.exe
```

- `tube_analysis.engine._validate_job(job)` — job 契约校验
- `tube_analysis.engine._load_and_inspect(job)` — 加载 STEP 并预检，返回 Inspection（端点、弦轴、安全区间、扫描记录）
- `tube_analysis.engine.seed_and_refine(...)` — 种子截面扫描 + 局部法向中心线定点迭代
- `tube_analysis.engine.section_exact(...)` — 精确 B-rep 法向截面，要求恰好 2 个闭合环（外环+内环）
- `tube_analysis.engine.fit_centerline(...)` / `spline_position` / `spline_tangent` — 中心线样条拟合与求值
- `tube_analysis.engine.build_complete_centerline(...)` / `complete_position` / `complete_tangent` — 完整中心线构建（CubicSpline + 端点切向约束）
- `tube_analysis.engine._wire_loop_pair(...)` — 内外环配对与质心计算
- 辅助：`cadquery`（`importStep` / `Face.makePlane` / `intersect` / `Wire.combine`）、`OCP.BRepAlgoAPI.BRepAlgoAPI_Section`、`shapely`、`numpy`、`scipy`

诊断脚本（可复现，见 §5）：
- `diagnose_h0a2z3.py` — 复现引擎管线至失败点，逐 jitter 检查失败截面闭合环数
- `diagnose_h0a2z3_end.py` — 端面信息、末端面清单、弦向截面扫描
- `diagnose_h0a2z3_faces.py` — 全 22 面盘点、端面等效直径、末端细扫（0.25 mm 步进）

---

## 3. 失败详情

### 3.1 错误码与位置

```
status: ERROR
code:   SECTION_FAILURE
message: "No annular local-normal recentering section"
details:
  recenter_iteration: 1
  s_mm: 628.6242114661493
```

- 失败阶段：最终中心线 **重定心（recenter）第一轮**
- 失败位置：弧长 s = 628.62 mm，对应端点弦坐标 chord ≈ 627.28 mm
- 端点弦长：632.48 mm → 失败点距末端端面仅约 **4.5 mm**

### 3.2 引擎行为说明

引擎在重定心阶段要求**每个**法向截面都恰好产生 2 个闭合环（外环 + 内环）的环形结构。失败点处截面只有 **1 个闭合环**（6 条边、轮廓约 50×55 mm），不满足环形假设，返回 None，触发 fail-closed。

对全部 11 个 jitter 偏移（0 至 ±0.071 mm）逐一复测，均只得到 1 个闭合环，排除数值抖动因素。

---

## 4. 根因分析：末端存在非环形异形整形段

### 4.1 预检与正式分析的差异

- `inspect`（预检）只验证基本可分析性 → **READY**
- `analyze`（正式）逐截面重定心，要求全程严格环形 → **FAIL**
- 预检通过不构成壁厚 PASS，两者不矛盾

### 4.2 弦向截面扫描证据

沿端点弦轴以 0.5 mm 步进扫描（588 ~ 632.5 mm）：

| 弦坐标区间 | 截面形态 |
|---|---|
| 4.0 ~ 628.5 mm | 标准双环（外环 + 内环），环形截面 |
| 630.5 mm | 仍为双环（外周长约 212.09 mm） |
| 631.0 mm 起 | **单环**，轮廓变为约 80×50 mm 大扁块，非管壁环 |

细扫（626 ~ 631.5 mm，0.25 mm 步进）确认：环形截面有效区在 **chord ≈ 628.5 mm 处截止**，之后截面退化为单环。起点端亦有约 4 mm 非环形边界（有效扫描区间 [4.015, 628.47] mm）。

### 4.3 端面几何证据

| 端面 | 中心 | 等效外径 | 等效内径 | 环厚 | 端面 bbox |
|---|---|---|---|---|---|
| START | (3.442, 0.000, -0.409) | 44.24 mm | 41.95 mm | ≈1.15 mm | 正常圆管口 |
| FINISH | (27.939, 0.000, 631.600) | 62.16 mm | 59.54 mm | ≈1.31 mm | **85×50 mm（压扁/椭圆化）** |

### 4.4 几何面盘点

全实体共 22 个面。末端区域（z = 486.6 ~ 631.6）存在两块面积 12,702 mm² 的上下大平面及两块 12,152 mm² 的左右曲面，管体在末端呈铲形展开，**不是简单圆管**。

### 4.5 中心线形态

失败点局部切线方向 ≈ (0.2895, 0.0002, 0.9572)，相对端点弦轴偏转约 17°。中心线在末端明显弯曲，斜切平面落入异形整形段，截面失去内外双环结构。

### 4.6 结论判定

该件为**两端异形（起点端轻微、末端重度压扁/扩口）的变径下管**。引擎要求全程每个法向截面严格环形，末端整形段击穿该假设。

- 这是**设计上的 fail-closed 行为**，非阈值过严、非采样不足
- **不通过改 profile 参数、不改阈值、不补几何、不猜测内壁来强行出 PASS**（契约要求）
- 任何未通过 mandatory check 的结果只能报告 FAIL

---

## 5. 可复现诊断

### 5.1 一键复现失败

调用插件正式 API 即可复现：

```
cad-tube-centerline-thickness_analyze
  source  = {kind: "session-file", fileId: "<H0A2Z3-DT.stp 的 fileId>"}
  job_id  = "H0A2Z3-DT"
→ ERROR SECTION_FAILURE, s_mm=628.6242, recenter_iteration=1
```

### 5.2 诊断脚本输出要点

- **失败截面逐 jitter 检查**：11 个偏移全部为 1 个闭合环、6 条边、轮廓 bbox 约 (9.8~64.5, ±25, 615~631.6)
- **扫描记录**：`scan_count=161, valid_count=159, valid_index_range=[1,159], valid_coordinate_range_mm=[4.015, 628.468], safe_coordinate_range_mm=[7.968, 624.516]`
- **中心线迭代**：iter1 rms=0.2511 → iter2 rms=0.0139 → iter3 rms=0.0068 → iter4 rms=0.0052（收敛正常）
- **完整中心线长度**：633.91 mm

---

## 6. 后续建议

1. **确认末端整形段是否为工艺真实状态**：若 H0A2Z3-DT 末端扩口/压扁段（最后约 4~5 mm）本就属于异形口，则超出本插件契约边界，不强行出结果。
2. **提供管体段 STEP**：若上游存在去掉整形段的纯管体模型（或经确认按某端面位置裁剪后），可对管体段跑完整流程：中心形线、轴向壁厚、周向壁厚场、OCCT 精确外周长 + 独立 QA。
3. **存档本 FAIL**：错误码、位置、根因已归档，后续拿到几何一致的修正件可直接对照复验。

---

## 7. 限制声明

- 本测试为 CAD 名义几何分析，非实物测厚或无损检测
- 只接受单位明确为毫米、完整内外壁、单一实体、恰好两个环形平面终端的 STEP
- 分叉、封闭环、多腔、强凹、非平面端口、仅外表面等均不支持
- 结果声明不得与制造后实测壁厚混淆
