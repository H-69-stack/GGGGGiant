#!/usr/bin/env python
"""Build the self-contained Windows delivery package (portable Python + app).

Produced layout (unzip anywhere, double-click 启动.bat):

    GiantTubeApp/
      python/            portable CPython 3.11 + all site-packages (no install needed)
      backend/           Flask app + databases
      frontend/dist/     built SPA
      freecad_addon/     FreeCAD addon for the pipe route
      gnt管件/            source STEP files
      entity_out/        entity products + measurement cache
      license/ tools/ docs/
      .env 启动.bat 停止.bat 交付说明.txt

Usage (Windows, from the repo root):

    python deploy\\make_portable_package.py
    python deploy\\make_portable_package.py --no-zip
    python deploy\\make_portable_package.py --out D:\\somewhere\\GiantTubeApp

Runtime sources default to this machine's Anaconda base + the ``occ`` venv
site-packages; override with --python-base / --site-packages.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

BACKEND_EXCLUDES = {
    "__pycache__",
    ".pytest_cache",
    "logs",
    "rfm_sessions",
    "rfm_belling",
    "rfm_bending",
    "rfm_flattening",
    "rfm_shaping",
}

START_BAT = r"""@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if exist "%~dp0freecad\bin\freecadcmd.exe" set "FREECAD_HOME=%~dp0freecad"
if exist "%~dp0freecad\bin\FreeCADCmd.exe" set "FREECAD_HOME=%~dp0freecad"
echo ============================================================
echo  GiantTubeApp  http://127.0.0.1:5890
echo  Login: admin / Admin@2027
echo ============================================================
start "" http://127.0.0.1:5890
"%~dp0python\python.exe" "%~dp0backend\run_release.py"
echo.
echo Server stopped. Press any key to close.
pause >nul
"""

STOP_BAT = r"""@echo off
setlocal
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5890" ^| findstr "LISTENING"') do taskkill /F /PID %%p >nul 2>&1
echo Stopped.
timeout /t 2 >nul
"""

ENV_FILE = """# Giant-Unified run environment (read by run_release.py at startup)
# No license.dat on this machine -> dev mode disables the license gate.
# For a licensed deployment remove GTAPP_LICENSE_GATE and import license.dat in the UI.
GTAPP_LICENSE_GATE=0
APP_PORT=5890
GTAPP_OPEN_BROWSER=0
"""

README_TXT = """GiantTubeApp - 交付说明

一、怎么启动
  1. 把整个 GiantTubeApp 文件夹解压到任意目录（建议非系统盘，例如 D:\\GiantTubeApp）。
  2. 双击「启动.bat」，几秒后浏览器自动打开 http://127.0.0.1:5890
     （若没自动开，手动输入该地址）。
  3. 登录：admin / Admin@2027（首次登录后请改密码）。
  4. 关闭：关掉命令行窗口，或双击「停止.bat」。

本包已自带 Python 运行时（python\\ 目录），客户机无需安装 Python、无需 pip install。

二、FreeCAD（pipe 路线：扩口/压扁/压弯的 4 面实体）
  - 需要 FreeCAD 1.1.4 的 freecadcmd.exe。把 FreeCAD 解压到本文件夹下的 freecad\\
    （应含 freecad\\bin\\freecadcmd.exe），或设置环境变量 FREECAD_HOME 指向它。
  - 没有 FreeCAD 时：程序仍能启动、测量、出「成型_外径形腔」（c1 路线），
    其余模型会提示「FreeCADCmd 未找到」。

三、目录说明
  python\\            自带 Python 运行时（勿删）
  backend\\           后端与数据库（backend\\database 内是工艺设计与测量数据）
  frontend\\dist\\     前端页面
  freecad_addon\\     FreeCAD 插件
  gnt管件\\           6 个原始管件 STEP
  entity_out\\        产物与测量缓存
                       <件号>\\00_models 5 类派生模型
                       <件号>\\10_c1     4 面实体（c1 路线）
                       <件号>\\20_pipe   4 面实体（pipe 路线，需 FreeCAD）
                       _cache\\          测量缓存（删掉会重新测量，每件约 5 分钟）
                       _report\\        汇总报告
  license\\           正式授权时放 license.dat
  docs\\              说明文档
  .env               端口 5890、开发态关闭许可证门禁

四、常见问题
  1. 端口占用：记事本打开 .env，把 APP_PORT 改成 5891 等，再启动。
  2. 页面打不开：看命令行窗口是否报错；检查端口占用。
  3. 正式授权：拿到 license.dat 后删掉 .env 里的 GTAPP_LICENSE_GATE=0，
     启动后在「激活」页导入。
  4. 清空数据：删除 entity_out 目录即可，重跑会重建（测量耗时较长）。
"""

STDLIB_SKIP = {
    "site-packages",
    "test",
    "tests",
    "idlelib",
    "tkinter",
    "turtledemo",
    "lib2to3",
    "ensurepip",
    "pydoc_data",
}

EXTRA_DLLS = (
    "ffi-8.dll",
    "ffi-7.dll",
    "ffi.dll",
    "libssl-1_1-x64.dll",
    "libcrypto-1_1-x64.dll",
    "sqlite3.dll",
    "libbz2.dll",
    "liblzma.dll",
    "libxml2.dll",
    "zlib.dll",
    "libzstd.dll",
    "liblz4.dll",
    "libexpat.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)


def log(msg: str) -> None:
    print("==> " + msg, flush=True)


def copy_tree(src: Path, dst: Path, exclude_names: set[str] | None = None) -> None:
    exclude_names = exclude_names or set()
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in exclude_names and d != "__pycache__"]
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(Path(root) / name, dst / rel / name)


def build_stdlib_zip(python_exe: Path, lib_dir: Path, target: Path) -> None:
    code = f"""
import os, zipfile
src = r'{lib_dir}'
dst = r'{target}'
skip = {sorted(STDLIB_SKIP)!r}
with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        parts = [] if rel == '.' else rel.split(os.sep)
        if parts and parts[0] in skip:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d != '__pycache__' and not (not parts and d in skip)]
        for f in files:
            if not f.endswith(('.py', '.pyc', '.pyd', '.dll')):
                continue
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, src))
print('stdlib zipped')
"""
    subprocess.run([str(python_exe), "-c", code], check=True)


def build_runtime(python_base: Path, site_packages: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in ("python.exe", "pythonw.exe", "python311.dll"):
        src = python_base / name
        if src.is_file():
            shutil.copy2(src, target / name)
    for dll in python_base.glob("*.dll"):
        shutil.copy2(dll, target / dll.name)
    if (python_base / "DLLs").is_dir():
        shutil.copytree(python_base / "DLLs", target / "DLLs", dirs_exist_ok=True)
    build_stdlib_zip(python_base / "python.exe", python_base / "Lib", target / "python311.zip")
    sp_dst = target / "Lib" / "site-packages"
    log("copying site-packages (this takes a few minutes)")
    copy_tree(site_packages, sp_dst, exclude_names={"__pycache__"})
    bin_src = python_base / "Library" / "bin"
    if bin_src.is_dir():
        lib_bin = target / "Library" / "bin"
        lib_bin.mkdir(parents=True)
        for name in EXTRA_DLLS:
            src = bin_src / name
            if src.is_file():
                shutil.copy2(src, lib_bin / name)
                shutil.copy2(src, target / name)
                shutil.copy2(src, target / "DLLs" / name)
    (target / "python311._pth").write_text(
        "python311.zip\nLib\\site-packages\nDLLs\n.\nimport site\n", encoding="ascii"
    )


def write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode(encoding))


def build(repo: Path, out: Path, python_base: Path, site_packages: Path, make_zip: bool) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    log(f"staging {out}")

    backend_src, backend_dst = repo / "backend", out / "backend"
    for root, dirs, files in os.walk(backend_src):
        rel = Path(root).relative_to(backend_src)
        dirs[:] = [
            d
            for d in dirs
            if d not in BACKEND_EXCLUDES
            and not (rel.parts[:1] == ("data",) and d.startswith("rfm_"))
        ]
        (backend_dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(Path(root) / name, backend_dst / rel / name)

    copy_tree(repo / "frontend" / "dist", out / "frontend" / "dist")
    for name in ("freecad_addon", "license", "tools", "docs", "entity_out"):
        src = repo / name
        if src.is_dir():
            copy_tree(src, out / name, exclude_names={"_logs", "_scratch"})
    # 机器锚点记录的是「打包这台机器」的标识，绝不能随交付包发给客户。
    stale_anchor = out / "license" / "machine_anchor.json"
    if stale_anchor.exists():
        stale_anchor.unlink()
    for src in repo.glob("gnt*"):
        if src.is_dir() and any(src.glob("*.stp")):
            copy_tree(src, out / src.name)
    for name in ("VERSION", "README_UNIFIED.md", "ENTITY_PIPELINE.md"):
        src = repo / name
        if src.is_file():
            shutil.copy2(src, out / name)

    log("building portable python runtime")
    build_runtime(python_base, site_packages, out / "python")

    write_text(out / ".env", ENV_FILE)
    write_text(out / "启动.bat", START_BAT, "ascii")
    write_text(out / "停止.bat", STOP_BAT, "ascii")
    write_text(out / "交付说明.txt", README_TXT)

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    log(f"package: {size / 1048576:.1f} MB")

    if make_zip:
        zip_path = out.with_name(out.name + "_win64.zip")
        if zip_path.exists():
            zip_path.unlink()
        log(f"zipping -> {zip_path}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in sorted(out.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(out.parent))
        log(f"zip: {zip_path.stat().st_size / 1048576:.1f} MB")
        return zip_path
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--out", default=str(REPO / "dist" / "GiantTubeApp"))
    ap.add_argument("--python-base", default=r"D:\Anaconda3")
    ap.add_argument("--site-packages", default=r"D:\python\envs\occ\Lib\site-packages")
    ap.add_argument("--no-zip", action="store_true")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / "backend" / "run_release.py").is_file():
        print(f"not a Giant-Unified repo: {repo}", file=sys.stderr)
        return 2
    python_base = Path(args.python_base)
    site_packages = Path(args.site_packages)
    if not (python_base / "python.exe").is_file():
        print(f"python base not found: {python_base}", file=sys.stderr)
        return 2
    if not site_packages.is_dir():
        print(f"site-packages not found: {site_packages}", file=sys.stderr)
        return 2

    build(repo, Path(args.out).resolve(), python_base, site_packages, not args.no_zip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
