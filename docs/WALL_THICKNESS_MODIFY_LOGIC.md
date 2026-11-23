# 工艺规划 — 壁厚修改逻辑

> 对应代码：`backend/core/pp/modify_thick.py`。  
> 记录当前代码**实际如何改壁厚、如何出分段表**，不以老项目注释或页面文案为准。  
> 入口：`POST /api/pp/design/preview`、`design/save` 调用 `apply_modify_thick`。

工艺优化求出方案（D0 / Dn / D2 / L1 / L2 / L3 …）之后，需要把「原始形线壁厚」改写成「该方案加工后的设计壁厚」，并给出总表用的 `T*`、`TL*`、`TLM*`。这件事全部在本文件完成。

---

## 1. 输入 / 输出

### 输入

| 名 | 来源 | 用途 |
|---|---|---|
| `wall_thickness_data` | 管件分析曲线 | 列：`中心线长度(mm)`、`壁厚(mm)` |
| `solution` | 优化方案 | `D0 D1 D2 D3 Dn L1 L2 L3` |
| `pp_method` | 当前工艺路线 | 决定走哪套改写 |
| `Taper_thick` | 前端开关「是否将 Taper 段壁厚处理成过渡段」 | 线性过渡 vs 整段按直径比缩放 |
| `segments` | 原始 / 手工分段 | 仅「不改壁厚」路线可能原样沿用 |

直径缺省：

- 扩口缩放用径 `D_kuo`：**有 Dn（>0）用 Dn，否则 D0**
- Taper 缩放用径 `D_taper`：**有 D2（>0）用 D2，否则 D0**

若 `D0 / D1 / D3` 任一为 0，整段不改，返回原曲线和原 `segments`。

### 输出

`(modified_df, segments2)`

- `modified_df`：修改后壁厚曲线（列同输入）。除「仅TP抽 / 普通抽+TP抽」外，数值会 `round` 到 **3 位小数**。
- `segments2`：改写后再识别的分段，供总参数表优先取值。

`segments2` 字段：

| 键 | 含义 |
|---|---|
| T1–T4 | 各常值平台壁厚（识别后 **1 位小数**） |
| TL1–TL4 | 各常值平台长度（平台终点 − 起点） |
| TLM1–TLM3 | 平台之间的过渡段长度 |

总表 `_build_alldata_table`：**同一参数名优先 `segments2`，没有才用方案 `solution`**。因此勾选 Taper 作过渡后，过渡长度必须写进 `TLM*`，否则总表为空。

---

## 2. 路线分发

`apply_modify_thick` 按 `pp_method` 选类：

| 工艺路线 | 类 | 方法 | 是否改曲线 |
|---|---|---|---|
| Taper+TP抽+扩口 | `Modify_thick` | `modify_thick` | 是 |
| 普通抽+TP抽+Taper+扩口 | `Modify_thick` | `modify_thick` | 是 |
| 加长Taper+TP抽+扩口 | `Modify_thick` | `modify_thick` | 是 |
| 仅TP抽 | `Modify_thick_pt` | `no_modify_thick` | **否** |
| 普通抽+TP抽 | `Modify_thick_pt` | `no_modify_thick` | **否** |
| 普通抽+扩口 | `Modify_thick_pt` | `modify_thick`（L3=0） | 是 |
| TP抽+扩口 | `Modify_thick_pt` | `modify_thick`（L3=0） | 是 |
| 普通抽+Taper | `Modify_thick_pt` | `modify_thick_taper_only` | 是 |
| 普通抽+Taper+扩口 | `Modify_thick_pt` | `modify_thick`（L3 用方案值） | 是 |
| 其它 / 异常 | — | 原样返回 | 否 |

`Modify_thick` 里若 `L2 ≤ 5`，会把 L2 **抬成 50 mm**（扩口后接到 Taper 的最短过渡）。

---

## 3. 共用公式与检测

### 3.1 扩口段常值壁厚

先把 `[0, L1]` 写成扩口后壁厚：

```text
T_kuo = T_first × D1 / D_kuo
D_kuo = Dn  （无 Dn 时 D0）
```

`T_first` 是原曲线上第一个「连续相同且点数 > 5」的壁厚（对单壁厚管就是 T1）。

### 3.2 Taper 末端目标壁厚

```text
T_taper_end = T_last × D3 / D_taper
D_taper = D2  （无 D2 时 D0）
```

`T_last` 取原曲线最后一个常值平台壁厚（单壁厚管仍是 T1）。

未勾选 `Taper_thick` 时：Taper 区间内每点 `T(s) × D3 / D_taper`（相对原曲线等比缩放）。  
勾选后：在指定区间内从起点壁厚 **线性插值** 到 `T_taper_end`，不再保留 Taper 段常值平台。

### 3.3 常值平台检测

`_find_duplicates(x2)`：从左到右扫，连续相同壁厚且 **点数 > 5** 的值记入 `thick_num`（这就是后面的 n = 1/2/3/4）。

`_find_segments_numeric`：在这些常值上切出平台：

```text
TL_sec = [第1平台起点, 第1平台终点, 第2平台起点, 第2平台终点, …]
TL_seg = 各平台长度（终点 − 起点）
TLM_seg = 相邻两平台之间的空隙
         TLM_k = 下一平台起点 − 上一平台终点
```

线性过渡段每个点壁厚都在变，**构不成平台**，因此空隙检测经常拿不到 Taper 过渡长。勾选后的补写见第 6 节。

### 3.4 线性插值（`Modify_thick_pt._calculate_x2_values`）

在下标 `[i0, i1)` 上：

```text
T[i] = thick − (i − i0) / (i1 − i0) × (thick − thick2)
```

若 `i1 < n`，再把 `T[i1] = thick2` 以保证接点连续。  
`i1 ≤ i0` 时强制 `i1 = min(i0+50, n)`，避免扩口平区与后方硬跳。

`Modify_thick`（GA 三条）不用这个函数，自己按点写线性公式，语义相同。

---

## 4. `Modify_thick_pt.modify_thick`

用于：**普通抽+扩口、TP抽+扩口、普通抽+Taper+扩口**。

共同前置：

1. 检测原曲线 `thick_num`、`TL_sec`
2. `L1_index` = 中心线坐标 ≤ L1 的最近下标
3. `[0, L1_index]` 写成 `T_kuo`

然后按 **原曲线平台个数 n** 和 **方案 L3** 分支。

### 4.1 L3 = 0（普通抽+扩口 / TP抽+扩口）

扩口段之后接一段过渡，接到后方原壁厚，**不做 Taper 缩放**。

| n | 过渡终点 | 终点目标壁厚 |
|---|---|---|
| 4 / 3 / 2 | 下一平台起点；没有则 L1 后 50 mm | 终点处的**原**壁厚 |
| 1 | L1 后 50 mm（不超过管尾） | 原 `thick_num[0]` |

勾选 `Taper_thick` 在这条路上**不改变曲线**（因为没有 Taper 段）。若检测出两段平台，`TLM1` 就是这段 50 mm 级过渡。

### 4.2 L3 ≠ 0（普通抽+Taper+扩口）

扩口之后的部分按 Taper 处理。`D_taper` 规则见 3.2。

**未勾选 `Taper_thick`：**

1. 扩口后先做短过渡：目标壁厚 = 第二平台（或仅有的平台）× D3/D_taper；终点优先下一平台起点，n=1 时固定 L1 后 50 mm。
2. 过渡终点之后直到原曲线最后平台终点：每点 `原壁厚 × D3/D_taper`（常值管上就是一段新的常值平台）。

因此单壁厚管典型结果：

```text
[0, L1]     T_kuo 常值
[L1, L1+50] 短过渡
[L1+50, 尾] T1×D3/D2 常值

TL1 = L1
TLM1 = 50
TL2 = 尾 − (L1+50)
```

**勾选 `Taper_thick`：**

从 `L1_index` 一直线性插到原曲线最后平台终点（即管尾）：

```text
起点壁厚 = T_kuo
终点壁厚 = 原最后平台 × D3/D_taper
```

n = 4/3/2/1 只影响终点用哪一档原壁厚（`thick_num[3/2/1/0]`），插值区间相同：L1 → 管尾。

单壁厚管典型结果：

```text
[0, L1]   T_kuo 常值
[L1, 尾]  线性过渡（不再有第二平台）

TL1  = L1
TLM1 = L3     ← 见第 6 节补写
TL2  = 0
```

---

## 5. `Modify_thick_pt.modify_thick_taper_only`

用于：**普通抽+Taper**（无扩口）。不做 50 mm 短过渡。

- 起点下标 0，终点 = 原最后平台终点 + 1。
- **未勾选**：整段 `原壁厚 × D3/D_taper`。
- **勾选**：从原第一平台壁厚线性到 `T_last × D3/D_taper`。勾选后整管常不是平台，`TLM1` 补写为 L3。

---

## 6. 勾选 Taper 作过渡后如何写 TLM

平台检测只认常值空隙。勾选后 Taper 是斜线，第二平台经常消失，`TLM` 会变成 0。

补写规则（`_fill_taper_transition_tlm`），**仅 `Taper_thick=True` 时**：

1. 若 `TLM1/2/3` 已有非 0 值（两平台之间已经检出空隙）→ **不覆盖**。
2. 看改写后最后一个常值平台是否接到管尾。
3. 未接到（或整管都没有平台，即整段线性）→ 把过渡长写入 **TLM1**。
4. 长度 **优先用方案 L3**；L3 缺失或 ≤0 时，用 `管尾中心线 − 最后平台终点`（整管线性时用全长）。

这保证总表「过渡段长度」与方案「Taper 长度 L3」一致。  
1324-3（`普通抽+Taper+扩口`，D0=46、Dn=45、L1=97、L3=514）勾选后：`TL1=97`，`TLM1=514`。

未勾选不走补写，1324-3 保持 `TLM1=50`（扩口后短过渡）。

---

## 7. `Modify_thick.modify_thick`（GA 三条）

用于：**Taper+TP抽+扩口、普通抽+TP抽+Taper+扩口、加长Taper+TP抽+扩口**。

共同前置：

1. 检测原曲线平台。
2. `L1_index` ← L1；`L2_index` ← L1+L2（L2 已被抬到至少 50）。
3. `[0, L1]` 写成 `T_kuo`。
4. 扩口后到 TP/中间平台再接一段线性（接到 `thick_num[k] × D3/D2` 或原平台）。
5. **从 L2（或更后的平台起点）到管尾** 按是否勾选处理 Taper：
   - 未勾选：该区间 `原壁厚 × D3/D2`
   - 勾选：该区间从 Taper 大端壁厚线性到小端壁厚

具体走哪条子分支，看 **原曲线平台个数 n**，以及 **L1+L2 落在原 `TL_sec` 的哪一段之前**。`TL_sec` 下标约定：偶数是平台起点，奇数是平台终点。

### 7.1 n = 4（四段原壁厚）

| 条件 | 扩口后接到哪 | Taper 区间起点 |
|---|---|---|
| L1+L2 < TL_sec[2]（第二平台起点） | L1 → 第二平台起点 | L2 → 尾 |
| L1+L2 < TL_sec[3] | L1 → L2 | L2 → 尾 |
| L1+L2 < TL_sec[4] | L1 → 第二平台起点；第三空隙再接到第四平台 | 第四平台起点 → 尾 |
| L1+L2 < TL_sec[5] | L1 → 第二平台起点；第三平台 → L2 | L2 → 尾 |
| L1+L2 < TL_sec[6] / [7] | 类似，更靠后的平台之间过渡 | 更后的 Taper 区间；这两支 **不看** `Taper_thick`，Taper 区始终等比缩放 |

勾选时 Taper 线性两端：大端 `thick_num[1]×D3/D2`（或更后一档 `thick_num[2]`），小端 `thick_num[3]×D3/D2`。

### 7.2 n = 3

| 条件 | 扩口后接到哪 | Taper 区间 |
|---|---|---|
| L1+L2 < TL_sec[1] | L1 → L2 | L2 → 尾（勾选时公式用 `thick_num[0]` 与 `thick_num[2]`） |
| L1+L2 < TL_sec[2] | L1 → 第二平台起点 | L2 → 尾 |
| L1+L2 < TL_sec[3] | L1 → L2 | L2 → 尾 |
| L1+L2 < TL_sec[4] / [5] | 平台之间过渡 | Taper 区等比缩放，**不看** `Taper_thick` |

### 7.3 n = 2

| 条件 | 扩口后接到哪 | Taper 区间 |
|---|---|---|
| L1+L2 < TL_sec[1] | L1 → L2 | L2 → 尾；仅这一支看 `Taper_thick` |
| L1+L2 < TL_sec[2] | L1 → 第二平台起点 | L2 → 尾，等比缩放 |
| L1+L2 < TL_sec[3] | L1 → L2 | L2 → 尾，等比缩放 |

### 7.4 n = 1（整管原壁厚一段，1324-3 属此类）

无论是否勾选，都会：

```text
[0, L1]           T_kuo 常值
[L1, L1+L2]       线性接到 T1×D3/D2
[L1+L2, 尾]       未勾选：T1×D3/D2 常值
                  勾选：再从该值线性变到 T1×D3/D2 − (T1 − T1×D3/D2)
```

勾选后 L1 之后整段都在变，检不出第二平台，`TLM1` 按第 6 节补写为方案 L3。

### 7.5 扩口用径

GA 三条同样走 `T_kuo = T_first × D1 / D_kuo`。方案带 Dn 时用 Dn，否则 D0。  
Taper 缩放在本类里写死为 `D3/D2`（调用方应保证 D2>0；`apply_modify_thick` 在 D2=0 时仍会进本类，可能除零，被外层 `try/except` 吃掉后返回原曲线）。

---

## 8. `no_modify_thick`（不改曲线）

**仅TP抽 / 普通抽+TP抽**：设计壁厚 = 原壁厚，禁止 round、禁止缩放。

分段：

- 若传入 `segments` 里至少 2 个 T，且已有任一 TL/TLM → **原样沿用**（尊重手工分段）。
- 否则对曲线副本 round 到 3 位再跑平台检测，得到 `segments2`；`D1/D3` 若输入已有则拷回。

前端预览对这两条路线还会强制「修改后壁厚」列等于原壁厚。

---

## 9. 改写后再识别分段

除 `no_modify_thick` 外，改完曲线都：

1. `x2 = round(x2, 3)`
2. 再跑 `_find_duplicates` + `_find_segments_numeric`
3. `_build_segments2` 填 T/TL/TLM
4. 若勾选 `Taper_thick`，按第 6 节补 TLM

T 只保留 1 位小数，所以 1.801 会显示成 T1=1.8。

---

## 10. 与总表、前端的对应

| 界面 | 字段 | 本文件对应 |
|---|---|---|
| 总参数表 T1–T4 | 改后常值平台壁厚 | `segments2.T*` |
| 总参数表 TL1–TL4 | 改后常值平台长 | `segments2.TL*` |
| 总参数表 TLM1–TLM3 | 过渡段长 | `segments2.TLM*`（勾选 Taper 后含 L3） |
| 总参数表 L1 | 扩口后保留长度 | 方案 `solution.L1`（segments2 无此键） |
| 总参数表 L3 | Taper 长度 | 方案 `solution.L3` |
| 壁厚表「修改后壁厚」 | 改写曲线 | `modified_df` |
| 开关「Taper 段壁厚处理成过渡段」 | `taper_thick` | `Taper_thick` |

工艺规划运行总览见 `docs/PROCESS_PLANNING_RUNTIME_LOGIC.md`。

---

## 11. 1324-3 数值对照（`普通抽+Taper+扩口`）

管件：`24TK_1324-31`，T1=1.7，D1=47.67，D3=32.88，LL=611。  
方案：D0=46，Dn=45，D2=45，L1=97，L3=514。

| | TL1 | TLM1 | TL2 | 曲线 |
|---|---|---|---|---|
| 未勾选 | 97 | 50 | 465 | 扩口常值 → 50 mm 过渡 → 常值 Taper 壁厚 |
| 勾选 | 97 | **514** | 0 | 扩口常值 → 线性到管尾 |

形线最后一点在 612 mm，线性跨度 515 mm，与 L3 差 1 mm（总长 611 vs 形线 0–612）。总表按 L3 写 514，与 LL 对齐。
