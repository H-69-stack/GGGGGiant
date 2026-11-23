# entity 测试（S10）

`backend\tests\entity\` 是 Giant-Unified 实体化流水线的回归测试集合，覆盖
`backend\core\entity\` 的契约、分型方向、体检、报告与两条路线的金标准。

## 0. 先决条件

| 依赖 | 用途 |
|---|---|
| `D:\python\envs\occ\Scripts\python.exe` | 主测试解释器（Python 3.11 + numpy + OCP 7.7.2） |
| `D:\Freecad\bin\freecadcmd.exe` | `pipe` 路金标准慢测（缺失则相关用例 skip） |
| `entity_out\` | 体检/复检类用例优先读本流水线 c1 交付件；`entity_out` 不存在时回落历史 c2 交付件；两者都无则 skip |
| `run_20261103_新逻辑\03_c2输出\output\` | 可选的历史回落基准（干净机器上不必存在） |

## 1. 怎么跑

```powershell
cd D:\AAAGNT-1\Giant-Unified

# 全量（含金标准慢测，约 100 s）
D:\python\envs\occ\Scripts\python.exe -m unittest discover -s backend\tests\entity -t backend -v

# 快速子集（跳过金标准慢测，约 9 s）
$env:ENTITY_SKIP_SLOW='1'
D:\python\envs\occ\Scripts\python.exe -m unittest discover -s backend\tests\entity -t backend -v

# 单个文件
D:\python\envs\occ\Scripts\python.exe -m unittest backend.tests.entity.test_checks -v
```

预期（本机实测）：全量 **80 条，79 OK + 1 skipped，约 103 s**（其中 `GoldenRouteTest` 约 100 s；
不设 `ENTITY_SKIP_SLOW` 时默认包含 slow 组）；设 `ENTITY_SKIP_SLOW=1` 后 **76 条，约 9 s**。

> **报错但实际通过**：`unittest` 把进度写到 stderr，PowerShell 可能显示
> `NativeCommandError` / `[exit code: 1]`；以结尾的 `Ran N tests ... OK` 为准。

端到端脚本在仓库根：

```powershell
# PowerShell 5.1 默认 AllSigned，必须显式 Bypass
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_unified_e2e.ps1 -Out .tmp\s10\e2e
```

## 2. 文件与覆盖

| 文件 | 条数 | 覆盖内容 |
|---|---|---|
| `test_contracts.py` | 19 | `MODELS` 6 项注册表、端部口径（2052-2 走 v1）、参考路判定、`ModelSpec.out_path()` 文件名规则、状态常量 |
| `test_directions.py` | 26（1 skip） | `score_axes` 三向镜像评分、四类方向规则（成型/扩口/压扁/压弯）、`per_section_axes` 双模式；裸 ndarray 环输入是已知缺口 |
| `test_checks.py` | 10 | 交付件体检（4 面/1 实体/0 自由边/2 分型边/valid/体积）、762 面反例必须判不合规、体检异常不抛、规范常量 |
| `test_report.py` | 16 | `write_part_report` / `write_summary` 落盘契约、状态计数（ok/cached/error/skipped/**stopped**）、中文对齐与 UTF-8 无 BOM、参数反序兼容 |
| `test_route_golden.py` | 9 | 金标准两格（1324-1 外径形腔 c1=1,881,257.533、pipe≈1,881,548.95）、`pipe` 交付件主进程复检字段、`cached` 硬/软问题分级 |

### 金标准（§8.1，c1 外径形腔，mm³）

| 件号 | 体积 |
|---|---|
| 24TK_1324-1 | 1,881,257.533 |
| 24TK_1324-2 | 2,449,483.308 |
| 24TK_1324-31 | 690,851.228 |
| 24TK_1442-1 | 1,562,196.022 |
| 24TK_2052-2 | 135,077.517（走口径保形） |
| H0A2Z3-DT | 1,108,104.392 |

## 3. 已知跳过

- `test_directions.RingInputTest` 的裸 ndarray 环用例：上游只接受 `{"points": Nx3}` 包装，补偿措施是 try/except → skip；上游补齐后自动变绿。
- `test_checks` / `PipeRecheckTest` 在既无 `entity_out` 产物、又无历史 c2 交付件时 skip（干净机器）。
- `pipe` 金标准慢测在找不到 `freecadcmd.exe` 时 skip。

## 4. 流水线已知问题（测试按「只记录不判失败」对待）

| 位置 | 现象 | 处置 |
|---|---|---|
| `24TK_1324-2 / 扩口_扩口芯轴` | 两路都是 4 条分型边（其余 58 格为 2 条） | 软问题：写 `problems`，状态仍 ok/cached |
| `summary.json` 的 `cells[]` | 已含 `solids`（2026-11-03 补） | 需要更细的 bbox/方向/耗时仍看件级 `report.json` |

> 原 `24TK_2052-2 / 成型_实心内径 / c1` 的 +2.365% 口径差记录已随「实心内径」下线一并移除。

## 5. 相关文档

- 契约与数据流：`ENTITY_PIPELINE.md`
- 使用与首跑结果：`README_UNIFIED.md`
