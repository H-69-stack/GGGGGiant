# Giant-Unified 构建与交付说明

> 面向接手开发/打包的同学。目标：**从这份源码，构建出「客户机免装 Python」的交付包**。
> 已经构建好的成品见 `D:\AAAGNT-1\_deliver\`（GiantTubeApp_win64.zip，857 MB）。

## 1. 目录速览

```
Giant-Unified\
  backend\             Flask 后端（原 Giant 业务 + core\entity 一体化流水线 + api\entity.py）
  frontend\            Vue 3 + Vite 前端（src\ + dist\；dist 已构建）
  freecad_addon\       FreeCAD 插件 PipeSolidBridge（pipe 路线用，需装到 %APPDATA%\FreeCAD\v1-1\Mod）
  gnt管件\             6 个原始管件 STEP（流水线输入）
  entity_out\          流水线输出（30 格产物 + 测量缓存）；可删，重跑会重建
  deploy\              打包脚本（make_portable_package.py 等）
  docs\ tools\ license\ deploy\windows\
  start_unified.bat / start_unified.ps1   本机开发/自测启动
  stop_unified.bat  / stop_unified.ps1    停止
  README_UNIFIED.md   功能说明
  ENTITY_PIPELINE.md  流水线设计与契约（改代码前先读）
  VERSION             版本号
```

端口：Web **5890**、dev **5060**、Vite **3001**、FreeCAD 桥 **18766**（与旧程序 5880/5050/3000/18765 全部错开）。

## 2. 构建机需要装什么

| 组件 | 版本 | 用途 | 客户机是否需要 |
|---|---|---|---|
| Python | **3.11.4**（3.11.x 即可） | 后端运行；装依赖 | ❌ 包内自带便携 Python |
| Python 依赖 | `backend\requirements.txt`（numpy 1.26.4 / scipy 1.14.1 / **cadquery-ocp 7.7.2** / Flask 3.1.3 …） | OCP 几何内核 | ❌ 已打进包 |
| Node.js + npm | 18+ | 只在改前端时需要重新 `npm run build` | ❌ dist 已构建 |
| FreeCAD | **1.1.4** | pipe 路线（扩口/压扁/压弯）调 `freecadcmd.exe` | 可选，见 §5 |
| Inno Setup 6 | 可选 | 想做成 .exe 安装包时 | — |

> `cadquery` 与 `nlopt` 需要 `pip install --no-deps`（这两个包会硬拉二进制依赖），参考 `docker\backend.Dockerfile`。

## 3. 本机开发运行

```powershell
# 1) 确认解释器路径（start_unified.ps1 第 27 行附近）
#    $py = 'D:\python\envs\occ\Scripts\python.exe'   ← 改成你自己装了依赖的环境

# 2) 启动（前台，自动开浏览器）
.\start_unified.bat

# 3) 登录：admin / Admin@2027（首次登录请改密）
#    浏览器 http://127.0.0.1:5890
```

`.env`（仓库根，`run_release.py` 启动时自动读取）：

```ini
GTAPP_LICENSE_GATE=0   # 开发态免激活；正式交付删掉这行，用界面「激活」导入 license.dat
APP_PORT=5890
GTAPP_OPEN_BROWSER=0
# ENTITY_OUT=...        # 默认 <仓库根>\entity_out，一般不用配
```

`start_unified.ps1` 里还有 `$env:FREECAD_HOME = 'D:\Freecad'`，按你机器改；没装 FreeCAD 也能启动，
只是「扩口/压扁/压弯」的 pipe 路线会报「FreeCADCmd 不可用」，成型（c1）不受影响。

## 4. 改前端后重新构建

esbuild 往系统 TEMP 写临时文件可能被拒，必须把 TEMP 指到仓库内：

```powershell
cd frontend
npm install            # 首次
$env:TEMP = 'D:\AAAGNT-1\Giant-Unified\.tmp'; $env:TMP = $env:TEMP
npm run build          # 产物 → frontend\dist
```

## 5. 打「客户机免装 Python」的交付包

```powershell
cd D:\AAAGNT-1\Giant-Unified
& 'D:\python\envs\occ\Scripts\python.exe' deploy\make_portable_package.py `
    --repo . `
    --out  D:\AAAGNT-1\_deliver `
    --python-base D:\Anaconda3 `
    --site-packages D:\python\envs\occ\Lib\site-packages
```

产出：`<out>\GiantTubeApp\`（约 2.29 GB）+ `<out>\GiantTubeApp_win64.zip`（约 857 MB）。

包结构：`GiantTubeApp\{python, backend, frontend\dist, freecad_addon, license, tools, docs, gnt管件, entity_out}\ + .env + 启动.bat + 停止.bat + 交付说明.txt`。

原理（不要改坏）：
- `backend\run_release.py` 以「自身所在目录的上一级」为安装根（`INSTALL_ROOT`），据此推导
  `frontend\dist`、`license\license.dat`，并把 `python\`、`python\Library\bin` 加进 DLL 搜索路径；
- `backend\core\entity\settings.py` 的 `PY_OCC` 默认取 `sys.executable`（包内就是 `python\python.exe`），
  `FREECAD_HOME` 依次找 `<根>\freecad` → `<根上级>\freecad` → `D:\Freecad`。

FreeCAD 默认**不打进包**（会多 2.1 GB）。要支持 pipe 路线，把 FreeCAD 1.1.4 解压到
`GiantTubeApp\freecad\`（内含 `bin\freecadcmd.exe`）或设环境变量 `FREECAD_HOME`。

## 6. 常见坑（都踩过）

1. **PowerShell 脚本必须 UTF-8 BOM 或纯 ASCII**：PS 5.1 会把无 BOM 的 UTF-8 当 ANSI，中文注释会吃掉引号 → 语法错误。
   生成含中文的文件（.bat/.env/说明）请用编辑器或 Python 写，别经 PowerShell 字面量。
2. `.bat` 用 **CRLF + 纯 ASCII**，首行 `chcp 65001 >nul`；用 `powershell` 而不是 `pwsh`（客户机未必装 PS7）。
3. 改端口别只改一处：`run_release.py`（默认 5890）、`run_dev.py`（5060）、`vite.config.js`（3001 + proxy）、
   `start_unified.ps1`、`.env`、`settings.py` 的 `BRIDGE_PORT`（18766）。
4. Windows 允许 `SO_REUSEADDR`，两个 `run_release.py` 能同时绑 5890，请求会随机落到旧进程 —— 自测前先
   `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` 清干净。
5. `entity_out` 是产物目录：交付包里带它是为了让客户打开就有数据；删掉也能用，只是首跑要重新测量（约 5 min/件）。

## 7. 打包前自检（1 分钟）

```powershell
& '...\python.exe' -m pytest backend\tests\entity -q          # 期望 85 passed / 1 skipped
cd backend; $env:GTAPP_LICENSE_GATE='0'; & '...\python.exe' run_release.py
# 另开一个窗口：
#   GET /api/version                 → 2.0.1.5.1123
#   POST /api/auth/login {id,password} → 200
#   GET /api/entity/status           → parts=6 models=5
#   GET /api/entity/plan             → count=30
```

## 8. 交付给客户前必须处理

- **许可证**：本仓库只有公钥，签不出 `license.dat`。要正式授权，必须用厂家私钥签发，或重新生成密钥对
  （会让现有客户许可证全部失效）。开发/试用可以保持 `GTAPP_LICENSE_GATE=0`。
- **不要带的东西**：`logs\`、`backend\data\logs\`、`__pycache__\`、`.tmp\`、`.pytest_cache\`、`giant_tube.pid`、
  以及 `backend\data\rfm_sessions\`（每次生成几 MB 的会话 JSON，批量跑会涨到几 GB）。
- **机器码锚点与测试许可证**：`license\machine_anchor.json` 只属于本机，**绝不能进安装包**；`license\license.dat`
  若是本机测试授权，交付前删除，只留 `PUT_LICENSE_HERE.txt` 占位。
- `backend\database\`（`tube_analysis.db` + `pp_design.db`）含本机测量与工艺设计数据；要给客户干净库，先清空。
  一键预览/清理：`python tools\clean_package_data.py`（预览），确认后 `python tools\clean_package_data.py --apply`。
  它会清 `entity_out`、上传件、测量缓存、日志、`.tmp`、锚点、测试许可证，并清空两个业务库后 VACUUM。
- 若希望客户打开就有示例产物，可在清理后只保留 `entity_out\<样件>\` 的 `00_models` 与最终 STEP，不带 `_cache`。
